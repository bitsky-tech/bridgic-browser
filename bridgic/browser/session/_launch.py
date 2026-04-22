"""Chrome launch helpers: system-Chrome detection, debug-log writer, retriable launch.

These helpers are shared by ``Browser._start()`` and are intentionally
module-level so they stay importable from tests without constructing a
``Browser`` instance.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .._constants import BRIDGIC_TMP_DIR

logger = logging.getLogger(__name__)


_LAUNCH_DEBUG_LOG = str(BRIDGIC_TMP_DIR / "launch-debug.json")


def _detect_system_chrome() -> bool:
    """Check if system Google Chrome is installed.

    Used to auto-switch from Playwright's bundled "Chrome for Testing" (which
    Google blocks for OAuth login) to the real system Chrome in headed mode.
    """
    if sys.platform == "darwin":
        # System-wide install is the common case; ~/Applications covers the
        # user-level install path (drag-and-drop by non-admin users).
        candidates = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"),
        )
        return any(os.path.isfile(c) for c in candidates)
    elif sys.platform == "linux":
        import shutil
        # Any Chromium-based browser satisfies the "system Chrome present"
        # check: Playwright's channel="chrome" picks up whatever is on PATH,
        # and OAuth distinguishes Chrome-for-Testing only by binary signature.
        # Snap installs land in /snap/bin (normally in $PATH). Flatpak wrappers
        # require `flatpak run …` so are NOT picked up here — that case is
        # covered by the scan-dir list, not this detector.
        _LINUX_CHROME_BINARIES = (
            "google-chrome",
            "google-chrome-stable",
            "google-chrome-beta",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "microsoft-edge-stable",
            "brave-browser",
            "brave",
        )
        return any(shutil.which(b) for b in _LINUX_CHROME_BINARIES)
    elif sys.platform == "win32":
        for env_var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_var, "")
            if base:
                path = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                if os.path.isfile(path):
                    return True
        # PATH fallback — covers non-standard installs (e.g. Chocolatey
        # shims) and Docker Windows Nano images that strip PROGRAMFILES(X86).
        import shutil
        for candidate in ("chrome.exe", "chrome"):
            found = shutil.which(candidate)
            if found and os.path.isfile(found):
                return True
        # Registry App Paths — Chrome installer writes this key on every
        # install; most reliable signal when env vars / PATH are missing
        # (WSL proxied shells, Nano images, custom installers).
        try:
            import winreg  # type: ignore[import-not-found]
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    ) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        if value and os.path.isfile(value):
                            return True
                except OSError:
                    continue
        except ImportError:  # pragma: no cover — winreg is always present on win32
            pass
    return False


def _write_launch_debug_log(options: Dict[str, Any], mode: str) -> None:
    """Write Chrome launch args to launch-debug.json for debugging."""
    import datetime, json as _json
    try:
        os.makedirs(os.path.dirname(_LAUNCH_DEBUG_LOG), exist_ok=True)
        record = {
            "time": datetime.datetime.now().isoformat(),
            "mode": mode,
            "args": options.get("args", []),
            "ignore_default_args": options.get("ignore_default_args", []),
            "headless": options.get("headless"),
            "channel": options.get("channel"),
            "executable_path": str(options["executable_path"]) if options.get("executable_path") else None,
        }
        with open(_LAUNCH_DEBUG_LOG, "w", encoding="utf-8") as f:
            _json.dump(record, f, indent=2)
    except Exception as e:
        logger.warning("Failed to write launch debug log: %s", e)


_LAUNCH_RETRY_DELAYS = (0.0, 1.0, 2.5)
"""Back-off schedule for :func:`_retriable_launch`. Three attempts total."""

_RETRIABLE_LAUNCH_TOKENS = (
    "singleton lock",
    "singletonlock",  # Chrome's on-disk file is literally `SingletonLock` (no space)
    "target page, context or browser has been closed",
    "process unexpectedly closed",
)
"""Substrings in Playwright launch errors that indicate a transient failure
(typically: the previous Chromium process is still releasing the user-data-dir
singleton lock). Matched case-insensitively against ``str(exc)``.

Intentionally narrow: bare phrases like ``"has been closed"`` or
``"target closed"`` appear in many permanent failures (e.g. "Executable
... has been closed" on a bad binary path) and would cause spurious
retries. Only the full Playwright transient-launch phrase is matched."""


def _is_retriable_launch_exc(exc: BaseException) -> bool:
    """Return True if *exc* looks like a transient Playwright launch failure.

    Uses isinstance on Playwright's Error class as the primary signal — this
    stays correct when Playwright reworks its message strings between
    releases. Token matching is kept as a fallback so non-Playwright
    wrappers (e.g. custom asyncio layers) that still carry the known phrase
    are covered.
    """
    msg_lower = str(exc).lower()
    token_match = any(tok in msg_lower for tok in _RETRIABLE_LAUNCH_TOKENS)
    try:
        from playwright.async_api import Error as _PwError  # type: ignore
    except ImportError:
        _PwError = None  # type: ignore[assignment]
    if _PwError is not None and isinstance(exc, _PwError):
        if not token_match:
            logger.debug(
                "[_is_retriable_launch_exc] Playwright error not matched by any "
                "known retriable token: %s", msg_lower,
            )
        return token_match
    return token_match


async def _retriable_launch(launch_callable, *, mode: str):
    """Call ``launch_callable()`` with exponential back-off.

    Retries on errors classified as transient by :func:`_is_retriable_launch_exc`.
    Non-transient failures (e.g. bad executable path) raise immediately.

    Parameters
    ----------
    launch_callable : Callable[[], Awaitable]
        Zero-arg thunk that returns a fresh coroutine on each call.
        Typically a ``lambda: playwright.chromium.launch_persistent_context(**opts)``.
    mode : str
        Human-readable label for logs (``"persistent_context"`` / ``"launch"``).

    Returns
    -------
    Any
        Whatever the underlying callable returns on success.
    """
    last_exc: Optional[BaseException] = None
    for attempt, delay in enumerate(_LAUNCH_RETRY_DELAYS):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            return await launch_callable()
        except Exception as e:
            last_exc = e
            retriable = _is_retriable_launch_exc(e)
            is_last = attempt == len(_LAUNCH_RETRY_DELAYS) - 1
            will_retry = retriable and not is_last
            logger.warning(
                "[_retriable_launch] %s attempt %d/%d failed "
                "(retriable=%s, will_retry=%s): %s",
                mode, attempt + 1, len(_LAUNCH_RETRY_DELAYS),
                retriable, will_retry, e,
            )
            if not will_retry:
                raise
    raise AssertionError(
        "_retriable_launch exited its loop without returning or raising — "
        f"last_exc={last_exc!r}"
    )
