import asyncio
import base64
import json
import logging
import os
import signal
import sys
import tempfile
import time
from urllib.parse import urlparse, urlunparse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Sequence, Union, NoReturn

if TYPE_CHECKING:
    try:
        from bridgic.llms.openai import OpenAILlm  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError:  # pragma: no cover - optional dependency
        OpenAILlm = Any  # type: ignore[misc,assignment]

from .._constants import BRIDGIC_TMP_DIR, BRIDGIC_SNAPSHOT_DIR, BRIDGIC_USER_DATA_DIR

from playwright.async_api import (
    async_playwright,
    Playwright,
    Browser as PlaywrightBrowser,
    BrowserContext,
    Page,
    Locator,
    ProxySettings,
    TimeoutError as PlaywrightTimeoutError,
)
from pydantic import BaseModel

from ._snapshot import EnhancedSnapshot, SnapshotGenerator, SnapshotOptions
from ._browser_model import FullPageInfo, PageDesc, PageInfo, PageSizeInfo
from ._stealth import StealthConfig, StealthArgsBuilder
from ._download import DownloadManager, DownloadedFile
from . import _video_recorder as _video_recorder_mod
from ..utils import find_page_by_id, generate_page_id, model_to_llm_string
from ..errors import (
    BridgicBrowserError,
    InvalidInputError,
    OperationError,
    StateError,
    VerificationError,
)

logger = logging.getLogger(__name__)

_DEFAULT_SNAPSHOT_LIMIT = 10000


# Chromium-based browser profile directories per platform.
# Used by find_cdp_url(mode="scan") to auto-discover a running browser.
# Source: https://chromium.googlesource.com/chromium/src/+/main/docs/user_data_dir.md
_CDP_SCAN_DIRS: Dict[str, List[tuple]] = {
    "darwin": [
        # (browser_label, profile_base_path)
        ("Chrome",        "~/Library/Application Support/Google/Chrome"),
        ("Chrome Canary", "~/Library/Application Support/Google/Chrome Canary"),
        ("Chromium",      "~/Library/Application Support/Chromium"),
        ("Brave",         "~/Library/Application Support/BraveSoftware/Brave-Browser"),
    ],
    "linux": [
        # Native packages (apt / dnf / pacman / AUR etc.)
        ("Chrome",         "~/.config/google-chrome"),
        ("Chrome Canary",  "~/.config/google-chrome-unstable"),
        ("Chrome Beta",    "~/.config/google-chrome-beta"),
        ("Chromium",       "~/.config/chromium"),
        ("Brave",          "~/.config/BraveSoftware/Brave-Browser"),
        ("Edge",           "~/.config/microsoft-edge"),
        # Snap packages — Snap redirects $XDG_CONFIG_HOME so native paths
        # above won't find them; must scan inside ~/snap/.
        ("Chromium (Snap)", "~/snap/chromium/common/chromium"),
        ("Brave (Snap)",    "~/snap/brave/current/.config/BraveSoftware/Brave-Browser"),
        # Flatpak packages — same reasoning; sandboxed under ~/.var/app/.
        ("Chrome (Flatpak)",   "~/.var/app/com.google.Chrome/config/google-chrome"),
        ("Chromium (Flatpak)", "~/.var/app/org.chromium.Chromium/config/chromium"),
        ("Brave (Flatpak)",    "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"),
        ("Edge (Flatpak)",     "~/.var/app/com.microsoft.Edge/config/microsoft-edge"),
    ],
    "win32": [
        ("Chrome",        r"%LOCALAPPDATA%\Google\Chrome\User Data"),
        ("Chrome Canary", r"%LOCALAPPDATA%\Google\Chrome SxS\User Data"),
        ("Chromium",      r"%LOCALAPPDATA%\Chromium\User Data"),
        ("Brave",         r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data"),
    ],
}


def _read_devtools_active_port(base: str) -> Optional[str]:
    """Return the ws:// URL from a DevToolsActivePort file, or None if absent/invalid.

    Validates that line 1 is a numeric port and line 2 begins with ``/`` —
    without this, a corrupted/rotated file (e.g. a leftover PID file sharing
    the same name) would produce a nonsense URL like ``ws://localhost:abcdef``
    that fails much later inside Playwright with an opaque error.
    """
    port_file = os.path.join(base, "DevToolsActivePort")
    try:
        with open(port_file) as f:
            lines = f.read().strip().splitlines()
        if len(lines) < 2:
            return None
        port_str, path = lines[0].strip(), lines[1].strip()
        if not port_str.isdigit() or not path.startswith("/"):
            return None
        return f"ws://localhost:{port_str}{path}"
    except (OSError, ValueError):
        pass
    return None


def _probe_cdp_alive(ws_url: str, timeout: float = 2.0) -> bool:
    """Return True if the CDP HTTP endpoint behind ``ws_url`` answers /json/version.

    Chrome normally removes its DevToolsActivePort file on graceful exit, but a
    crash or ``kill -9`` leaves it behind. Without a liveness probe, scan/file
    mode would return a stale ws URL and callers would only see a confusing
    connection error much later from ``connect_over_cdp``.
    """
    import urllib.error
    import urllib.request

    try:
        parsed = urlparse(ws_url)
    except Exception:
        return False
    host = parsed.hostname or "localhost"
    port = parsed.port
    if port is None:
        return False
    host_in_url = f"[{host}]" if ":" in host else host
    probe_url = f"http://{host_in_url}:{port}/json/version"
    host_lower = host.lower()
    is_loopback = host_lower in ("localhost", "127.0.0.1", "::1")
    try:
        if is_loopback:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            )
            resp = opener.open(probe_url, timeout=timeout)
        else:
            resp = urllib.request.urlopen(probe_url, timeout=timeout)
        return bool(resp.read(1))
    except (urllib.error.URLError, OSError):
        return False


def find_cdp_url(
    mode: str = "port",
    port: int = 9222,
    host: str = "localhost",
    user_data_dir: Optional[str] = None,
    channel: str = "stable",
    ws_endpoint: Optional[str] = None,
) -> str:
    """Resolve a Chrome CDP WebSocket URL.

    Parameters
    ----------
    mode:
        - ``"port"`` *(recommended)*: HTTP GET ``/json/version`` on ``host:port``.
          Works for both local and remote Chrome, regardless of install path.
          Chrome must be started with ``--remote-debugging-port=PORT``.
        - ``"file"``: Read ``DevToolsActivePort`` from the Chrome profile directory.
          Use ``user_data_dir`` to specify the exact profile path; falling back to
          the ``channel`` guess is unreliable with custom installs or multiple instances.
        - ``"scan"``: Auto-discover a running Chromium-based browser by scanning all
          known profile directories on the current machine (Chrome, Chrome Canary,
          Chromium, Brave). Returns the first active one found.
          Raises ``RuntimeError`` with instructions if none are running with CDP enabled.
        - ``"service"``: Return ``ws_endpoint`` directly (cloud providers such as
          Browserless or Steel that give you a ``wss://`` URL).
    port:
        Debugging port (``"port"`` / ``"file"`` modes). Default 9222.
    host:
        Server address (``"port"`` mode). Default ``"localhost"``.
    user_data_dir:
        Explicit Chrome profile directory (``"file"`` mode).
    channel:
        Chrome channel for built-in path lookup when ``user_data_dir`` is not given
        (``"file"`` mode). Values: ``"stable"``, ``"beta"``, ``"canary"``.
    ws_endpoint:
        Full ``ws://`` or ``wss://`` address (``"service"`` mode).
    """
    import urllib.error
    import urllib.request

    if mode == "service":
        if not ws_endpoint:
            raise ValueError("ws_endpoint is required when mode='service'")
        return ws_endpoint

    if mode == "port":
        # Strip user-supplied brackets so we never double-bracket IPv6 hosts
        # (e.g. caller passes "[::1]" → don't produce "[[::1]]").
        # Lowercase for canonical form: HTTP hostnames and DNS names are
        # case-insensitive per RFC, and Chrome always reports "localhost"
        # lowercase in webSocketDebuggerUrl — a mixed-case override would make
        # the returned URL look wrong.
        host_clean = (host or "").strip("[]").lower()
        # Bracket IPv6 hosts so the URL stays parseable
        # (e.g. ``::1`` → ``[::1]``).  Plain IPv4 / hostnames pass through
        # unchanged.
        host_in_url = f"[{host_clean}]" if ":" in host_clean else host_clean
        url = f"http://{host_in_url}:{port}/json/version"
        try:
            # Bypass system HTTP proxy for loopback hosts. macOS reads system
            # network preferences (proxy_bypass_macosx_sysconf) and may NOT
            # bypass localhost even though it should — when a system proxy is
            # active, probes return misleading "HTTP 502 Bad Gateway" instead
            # of the real "Connection refused" / "Connection timed out".
            # Remote hosts (cloud browser services, SSH-tunneled CDP, etc.)
            # MUST keep proxy support, so this branch is loopback-only.
            # host_clean is already lowercased above, so no second .lower() needed
            is_loopback = host_clean in ("localhost", "127.0.0.1", "::1")
            if is_loopback:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({})
                )
                resp = opener.open(url, timeout=5)
            else:
                resp = urllib.request.urlopen(url, timeout=5)
            data = json.loads(resp.read())
            ws_url: str = data["webSocketDebuggerUrl"]
        except urllib.error.URLError as exc:
            # URLError is the parent of HTTPError; catches connection refused,
            # timeouts, DNS failures, and HTTP error responses alike. OSError
            # subclasses (e.g. raw socket errors) also flow through URLError
            # in practice via urlopen, so this single clause is sufficient.
            raise ConnectionError(
                f"Cannot reach Chrome debugging interface at {url}: {exc}\n"
                f"Make sure Chrome was started with --remote-debugging-port={port}"
            ) from exc
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to parse /json/version response: {exc}") from exc
        # Always rewrite the ws URL netloc to (host_in_url, port) so SSH
        # tunnels, container port-forwards, and reverse proxies work
        # correctly.  Chrome embeds its own bound address in
        # webSocketDebuggerUrl ("ws://localhost:9222/..."), but we know the
        # address that actually got us a /json/version response — that's
        # the address the caller can also reach for the WebSocket.
        # Use urlparse to swap only the netloc component (a naive string
        # replace could match "localhost" / "9222" inside a path).
        _parsed_ws = urlparse(ws_url)
        _new_netloc = f"{host_in_url}:{port}"
        ws_url = urlunparse(_parsed_ws._replace(netloc=_new_netloc))
        return ws_url

    if mode == "scan":
        _platform = sys.platform
        candidates = _CDP_SCAN_DIRS.get(_platform, [])
        if not candidates:
            raise RuntimeError(f"Auto-scan is not supported on platform: {_platform}")
        for label, raw_path in candidates:
            base = os.path.expandvars(os.path.expanduser(raw_path))
            ws_url = _read_devtools_active_port(base)
            if not ws_url:
                continue
            # Skip stale DevToolsActivePort files: Chrome removes them on
            # graceful shutdown, but a crash / kill -9 leaves them behind,
            # pointing at a dead port. Try the next candidate instead.
            if not _probe_cdp_alive(ws_url):
                logger.debug(
                    "find_cdp_url(scan): skipping %s (%s) — stale DevToolsActivePort (port not reachable)",
                    label, base,
                )
                continue
            logger.info("find_cdp_url(scan): found active CDP port via %s (%s)", label, base)
            return ws_url
        # Nothing found — build a helpful error with instructions
        _browsers = ", ".join(label for label, _ in candidates)
        raise RuntimeError(
            "No locally running browser with remote debugging enabled was found.\n"
            f"Scanned profiles for: {_browsers}.\n\n"
            "To enable remote debugging, start your browser with:\n"
            "  --remote-debugging-port=9222\n\n"
            "Examples:\n"
            '  # macOS Chrome\n'
            '  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\\n'
            '      --remote-debugging-port=9222 --user-data-dir=/tmp/cdp-profile\n\n'
            '  # Or connect to a cloud browser service:\n'
            '  bridgic-browser open <url> --cdp "wss://<service>/chromium/playwright?token=..."'
        )

    if mode != "file":
        raise ValueError(
            f"Unknown mode {mode!r}. Valid modes: 'port', 'file', 'scan', 'service'."
        )

    if user_data_dir:
        base = os.path.expanduser(str(user_data_dir))
    else:
        _dirs: Dict[str, Dict[str, str]] = {
            "darwin": {
                "stable": "~/Library/Application Support/Google/Chrome",
                "beta":   "~/Library/Application Support/Google/Chrome Beta",
                "canary": "~/Library/Application Support/Google/Chrome Canary",
            },
            "linux": {
                "stable": "~/.config/google-chrome",
                "beta":   "~/.config/google-chrome-beta",
                "canary": "~/.config/google-chrome-unstable",
            },
            "win32": {
                "stable": r"%LOCALAPPDATA%\Google\Chrome\User Data",
                "beta":   r"%LOCALAPPDATA%\Google\Chrome Beta\User Data",
                "canary": r"%LOCALAPPDATA%\Google\Chrome SxS\User Data",
            },
        }
        if sys.platform not in _dirs:
            raise RuntimeError(f"Unsupported platform for mode='file': {sys.platform}")
        _platform_dirs = _dirs[sys.platform]
        if channel not in _platform_dirs:
            raise ValueError(
                f"Unknown channel '{channel}' for platform '{sys.platform}'. "
                f"Valid options: {list(_platform_dirs)}"
            )
        base = os.path.expandvars(os.path.expanduser(_platform_dirs[channel]))

    port_file = os.path.join(base, "DevToolsActivePort")
    if not os.path.exists(port_file):
        extra = "" if user_data_dir else "\nOr specify user_data_dir explicitly instead of relying on channel path."
        raise FileNotFoundError(
            f"DevToolsActivePort not found: {port_file}\n"
            f"Make sure Chrome has remote debugging enabled." + extra
        )
    # Delegate parsing + validation to the shared helper so scan-mode and
    # file-mode treat malformed files identically.  The helper returns None
    # on any parse failure (missing lines, non-numeric port, non-/ path).
    ws_url: Optional[str] = _read_devtools_active_port(base)
    if ws_url is None:  # pyright: ignore[reportUnnecessaryComparison]
        raise ValueError(
            f"DevToolsActivePort file is malformed or unreadable: {port_file}"
        )
    # Catch stale DevToolsActivePort files (Chrome crashed / was killed with -9
    # and the file wasn't cleaned up). Without this probe, callers get an
    # opaque connect_over_cdp error much later.
    if not _probe_cdp_alive(ws_url):
        # Extract port number purely for the error message.
        _parsed_port = urlparse(ws_url).port
        raise ConnectionError(
            f"DevToolsActivePort exists at {port_file} but Chrome is not "
            f"accepting CDP connections on port {_parsed_port}. The browser may "
            f"have crashed or been killed. Restart Chrome with "
            f"--remote-debugging-port=PORT and try again."
        )
    return ws_url


def resolve_cdp_input(value: str) -> str:
    """Resolve a user-supplied CDP value to a WebSocket URL.

    Parameters
    ----------
    value:
        Accepted formats:

        - ``"9222"``                  — local Chrome on port 9222; queries /json/version
        - ``"ws://..."`` / ``"wss://..."``  — used as-is (raw CDP or Playwright WS protocol)
        - ``"http://host:port"``       — HTTP discovery; queries /json/version on that host
        - ``"auto"`` / ``"scan"``      — auto-scan known Chrome/Chromium/Brave profile dirs (+ Canary variants)

    Returns
    -------
    str
        A ``ws://`` or ``wss://`` WebSocket URL ready to pass to ``Browser(cdp_url=...)``.

    Raises
    ------
    ValueError
        Input format is not recognised.
    RuntimeError
        ``auto``/``scan`` mode: no running browser with CDP found.
    ConnectionError
        Port/HTTP mode: cannot reach Chrome at the specified address.
    """
    v = value.strip()
    # Auto-scan all known local Chrome/Chromium/Brave profile directories
    # (matches _CDP_SCAN_DIRS, including Canary variants)
    if v.lower() in ("auto", "scan"):
        return find_cdp_url(mode="scan")
    # Direct WebSocket URL — pass through unchanged
    if v.startswith("ws://") or v.startswith("wss://"):
        return v
    # HTTP discovery endpoint — extract host/port and query /json/version
    if v.startswith("http://") or v.startswith("https://"):
        parsed = urlparse(v)
        host = parsed.hostname or "localhost"
        port = parsed.port or 9222
        return find_cdp_url(mode="port", host=host, port=port)
    # Bare port number — localhost auto-discover via /json/version
    if v.isdigit():
        return find_cdp_url(mode="port", host="localhost", port=int(v))
    raise ValueError(
        f"Invalid --cdp value: {v!r}.\n"
        "Accepted formats:\n"
        "  9222              — local Chrome on port 9222\n"
        "  ws://host:port/…  — WebSocket URL (raw CDP or Playwright WS protocol)\n"
        "  http://host:port  — HTTP discovery endpoint\n"
        "  auto              — auto-scan local Chrome/Chromium/Brave profiles (+ Canary variants)"
    )

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
        # Snap installs land in /snap/bin (normally in $PATH).  Flatpak wrappers
        # require `flatpak run …` so are NOT picked up here — that case is
        # covered by the scan-dir list, not this detector.
        _LINUX_CHROME_BINARIES = (
            "google-chrome",
            "google-chrome-stable",
            "google-chrome-beta",
            "chromium",
            "chromium-browser",   # Debian/Ubuntu package wrapper
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


def _strip_playwright_call_log(message: str) -> str:
    marker = "Call Log:"
    idx = message.find(marker)
    if idx == -1:
        marker = "Call log:"
        idx = message.find(marker)
    if idx == -1:
        return message
    return message[:idx].rstrip()


def _raise_invalid_input(
    message: str,
    *,
    code: str = "INVALID_INPUT",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = False,
) -> NoReturn:
    raise InvalidInputError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )


def _raise_state_error(
    message: str,
    *,
    code: str = "INVALID_STATE",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = True,
) -> NoReturn:
    raise StateError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )


def _raise_operation_error(
    message: str,
    *,
    code: str = "OPERATION_FAILED",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = False,
) -> NoReturn:
    current_exc = sys.exc_info()[1]
    if isinstance(current_exc, BridgicBrowserError):
        raise current_exc

    message = _strip_playwright_call_log(message)
    raise OperationError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )


def _raise_verification_error(
    message: str,
    *,
    code: str = "VERIFICATION_FAILED",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = False,
) -> NoReturn:
    current_exc = sys.exc_info()[1]
    if isinstance(current_exc, BridgicBrowserError):
        raise current_exc

    message = _strip_playwright_call_log(message)
    raise VerificationError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )

def _get_page_key(page) -> str:
    """Get a unique key for a page."""
    return str(id(page))


def _get_context_key(context) -> str:
    """Get a unique key for a context."""
    return str(id(context))


def _css_attr_equals(name: str, value: str) -> str:
    """Build a CSS attribute selector with basic quote escaping."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"[{name}='{escaped}']"


async def _prefer_visible_locators(locators: list) -> list:
    """Keep only visible locators when possible, otherwise preserve original order."""
    results = await asyncio.gather(
        *[locator.is_visible() for locator in locators],
        return_exceptions=True,
    )
    visible = [loc for loc, r in zip(locators, results) if r is True]
    return visible or locators


async def _get_dropdown_option_locators(page, locator) -> list:
    """Resolve option locators for native, embedded, and portalized dropdowns."""
    options = await locator.locator("option").all()
    if options:
        return options

    options = await locator.locator("[role='option']").all()
    if options:
        return await _prefer_visible_locators(options)

    if page is None:
        return []

    # Portalized dropdowns often link the trigger to the listbox via aria-controls
    # or aria-owns. Prefer that container before scanning the whole page.
    controlled_ids = []
    for attr_name in ("aria-controls", "aria-owns"):
        attr_value = await locator.get_attribute(attr_name)
        if attr_value:
            controlled_ids.extend(part for part in attr_value.split() if part)

    for controlled_id in controlled_ids:
        container = page.locator(_css_attr_equals("id", controlled_id))
        if await container.count() > 0:
            options = await container.locator("option, [role='option']").all()
            if options:
                return await _prefer_visible_locators(options)

    # Conservative fallback: if exactly one visible listbox is open, use it.
    listboxes = await page.locator("[role='listbox']").all()
    visible_listboxes = await _prefer_visible_locators(listboxes)
    if len(visible_listboxes) == 1:
        options = await visible_listboxes[0].locator("option, [role='option']").all()
        if options:
            return await _prefer_visible_locators(options)

    return []


async def _is_native_checkbox_or_radio(locator) -> bool:
    """Return True when locator points to <input type=checkbox|radio>.

    Uses ``get_attribute("type")`` instead of ``evaluate()`` to avoid
    Playwright's ``_mainContext()`` hang on pre-existing CDP tabs.
    Only ``<input type=checkbox|radio>`` elements carry those type values, so
    the tagName check is redundant.  A custom element with an explicit
    ``type="checkbox"`` attribute would be misidentified, but this is
    vanishingly rare in practice.
    """
    try:
        input_type = (await locator.get_attribute("type") or "").strip().lower()
        return input_type in {"checkbox", "radio"}
    except Exception:
        return False


async def _is_checked(locator) -> bool:
    """Check both native .checked and aria-checked state.

    Uses ``is_checked()`` (CDP-backed, has timeout) plus ``get_attribute``
    instead of ``evaluate()`` to avoid the ``_mainContext()`` hang on
    pre-existing CDP tabs.
    """
    try:
        if await locator.is_checked():
            return True
    except Exception:
        pass
    try:
        aria = (await locator.get_attribute("aria-checked") or "").strip().lower()
        return aria == "true"
    except Exception:
        return False


async def _cdp_evaluate_on_element(cdp_context, page, locator, code: str) -> Any:
    """Evaluate *code* (an arrow function ``el => ...``) on the DOM element
    identified by *locator*, using a raw CDPSession.

    Resolves the element via bounding-box coordinates + ``document.elementFromPoint``
    so it bypasses Playwright's ``_mainContext()`` which hangs on pre-existing
    CDP-borrowed tabs.  Raises on any failure (caller must handle).

    Scroll-race detection: the locator's bbox is re-acquired after the
    ``elementFromPoint`` call and compared with the pre-call bbox. If the
    page scrolled in between, the coordinates resolved to a different
    element — we raise a clear error so the caller can retry instead of
    silently executing JS on the wrong node.
    """
    bbox = await locator.bounding_box()
    if bbox is None:
        raise RuntimeError("Element has no bounding box — cannot resolve via CDPSession")
    cx = int(bbox["x"] + bbox["width"] / 2)
    cy = int(bbox["y"] + bbox["height"] / 2)
    session = await cdp_context.new_cdp_session(page)
    try:
        # Step 1: get the element's objectId via Runtime.evaluate (works in CDP borrowed mode)
        elem_result = await asyncio.wait_for(
            session.send("Runtime.evaluate", {
                "expression": f"document.elementFromPoint({cx},{cy})",
                "returnByValue": False,
            }),
            timeout=5.0,
        )
        object_id = elem_result.get("result", {}).get("objectId")
        if not object_id:
            raise RuntimeError("No element found at coordinates via CDPSession")
        # Scroll-race post-check: re-acquire bbox and compare. If the element
        # moved significantly in the viewport, the page scrolled between the
        # first bbox() and the elementFromPoint call — the objectId we hold
        # points at a different element than the locator resolves now.
        bbox_after = await locator.bounding_box()
        if bbox_after is None:
            raise RuntimeError(
                "Element disappeared during CDP resolution — possible scroll race"
            )
        if (
            abs(bbox_after["x"] - bbox["x"]) > 1
            or abs(bbox_after["y"] - bbox["y"]) > 1
            or abs(bbox_after["width"] - bbox["width"]) > 1
            or abs(bbox_after["height"] - bbox["height"]) > 1
        ):
            raise RuntimeError(
                f"Element moved during CDP resolution — scroll race detected "
                f"(bbox before={bbox}, after={bbox_after})"
            )
        # Step 2: call the user's arrow function with the element as the first
        # argument (matching Playwright's locator.evaluate() calling convention).
        # objectId is used as the execution context; arguments[0] passes it as
        # the first parameter so ``(el) => el.value`` receives the element.
        call_result = await asyncio.wait_for(
            session.send("Runtime.callFunctionOn", {
                "functionDeclaration": code,
                "objectId": object_id,
                "arguments": [{"objectId": object_id}],
                "returnByValue": True,
                "awaitPromise": True,
            }),
            timeout=30.0,
        )
        if call_result.get("exceptionDetails"):
            raise RuntimeError(f"JS exception: {call_result['exceptionDetails']}")
        return call_result.get("result", {}).get("value")
    finally:
        try:
            await session.detach()
        except Exception:
            pass


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


async def _retriable_launch(launch_callable, *, mode: str):
    """Call ``launch_callable()`` with exponential back-off.

    Retries on error messages that match :data:`_RETRIABLE_LAUNCH_TOKENS`.
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
            msg_lower = str(e).lower()
            retriable = any(tok in msg_lower for tok in _RETRIABLE_LAUNCH_TOKENS)
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
    # Defensive guard: the loop above always either returns on success or
    # raises on the final attempt. If control reaches here, something is
    # deeply wrong (e.g. _LAUNCH_RETRY_DELAYS was mutated to empty) — surface
    # it as an AssertionError rather than silently swallowing the issue.
    raise AssertionError(
        "_retriable_launch exited its loop without returning or raising — "
        f"last_exc={last_exc!r}"
    )


_DEFAULT_VIDEO_WIDTH = 1280
_DEFAULT_VIDEO_HEIGHT = 720
"""Fallback video recording dimensions used when both CDP
``Page.getLayoutMetrics`` and ``page.viewport_size`` fail to report usable
values. 1280x720 is a common default that keeps frames legible without being
wasteful. VP8 requires even width/height, and both values are already even."""


_DEFAULT_CLICK_TIMEOUT_MS = 10000
"""Hard ceiling for locator.click / dblclick / check / uncheck.

Playwright defaults to 30s and retries ``visible, enabled, stable`` up to the
deadline. On Vue/React SPA pages Chrome can judge a freshly-scrolled element
as *still* outside viewport (e.g. because a sticky header or transform occupies
the slot), and the retry loop spins for the full 30s — blocking every other
CLI command queued on the daemon. Capping at 10s keeps the CLI responsive;
the dispatch_event fallback below recovers the common case."""


async def _locator_action_with_fallback(
    locator,
    *,
    action: str,
    fallback_event: str = "click",
    timeout_ms: int = _DEFAULT_CLICK_TIMEOUT_MS,
) -> None:
    """Invoke ``locator.<action>`` with a hard timeout and dispatch_event fallback.

    Parameters
    ----------
    locator : Locator
        Playwright locator to act on.
    action : str
        Method name on the locator: ``"click"``, ``"dblclick"``, ``"check"``,
        or ``"uncheck"``.
    fallback_event : str, default ``"click"``
        DOM event to dispatch when the primary action times out. For ``check``
        and ``uncheck`` on custom ARIA widgets, ``"click"`` is the right event;
        ``dblclick`` uses ``"dblclick"``.
    timeout_ms : int, default :data:`_DEFAULT_CLICK_TIMEOUT_MS`
        Explicit timeout passed to Playwright. Shorter than the default 30s
        so a stuck actionability retry loop cannot freeze the CLI.

    Notes
    -----
    ``dispatch_event`` bypasses Playwright's actionability checks and directly
    fires the DOM event on the element. It is the right fallback when the
    element is logically interactive but geometrically confusing to
    Playwright (sticky/transform/absolute positioning, SPA layout quirks).
    """
    method = getattr(locator, action)
    try:
        await method(timeout=timeout_ms)
    except PlaywrightTimeoutError as e:
        logger.warning(
            "[_locator_action_with_fallback] %s timed out after %dms; "
            "falling back to dispatch_event(%r). Underlying: %s",
            action, timeout_ms, fallback_event, e,
        )
        await locator.dispatch_event(fallback_event)


async def _check_element_covered(locator, cx: float, cy: float, cdp_context=None) -> bool:
    """Return True when another element sits on top of (cx, cy).

    In CDP borrowed mode (``cdp_context`` provided) ``locator.evaluate()``
    hangs because Playwright's ``_mainContext()`` never resolves for
    pre-existing tabs.  We return ``False`` immediately so callers fall
    through to ``locator.click()`` which uses the utility world and handles
    overlays internally.
    """
    if cdp_context is not None:
        return False
    try:
        return await asyncio.wait_for(
            locator.evaluate(
                f"(el) => {{ if (window.parent !== window) return false; "
                f"const t = document.elementFromPoint({cx}, {cy}); "
                f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
            ),
            timeout=10.0,
        )
    except Exception:
        return False


async def _click_covering_element(page, locator, cx: float, cy: float, cdp_context=None) -> None:
    """Click the element that covers position (cx, cy).

    In CDP borrowed mode (``cdp_context`` provided) uses a raw CDPSession
    ``Runtime.evaluate`` to click the topmost element at the coordinates,
    bypassing ``page.evaluate()`` which hangs on pre-existing tabs.
    Falls back to ``locator.dispatch_event("click")`` on any failure.
    """
    if cdp_context is not None:
        session = None
        try:
            session = await cdp_context.new_cdp_session(page)
            expr = f"document.elementFromPoint({cx}, {cy})?.click()"
            await asyncio.wait_for(
                session.send("Runtime.evaluate", {"expression": expr}),
                timeout=5.0,
            )
        except Exception:
            await locator.dispatch_event("click")
        finally:
            if session:
                try:
                    await session.detach()
                except Exception:
                    pass
        return
    try:
        await asyncio.wait_for(
            page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()"),
            timeout=10.0,
        )
    except Exception:
        await locator.dispatch_event("click")


async def _click_checkable_target(page, locator, bbox, cdp_context=None) -> None:
    """Click a checkable target with overlay handling and shadow DOM fallback."""
    if bbox is not None:
        cx = bbox["x"] + bbox["width"] / 2
        cy = bbox["y"] + bbox["height"] / 2
        if not await locator.is_visible():
            logger.debug("_click_checkable_target: bbox present but is_visible()=False; using dispatch_event click")
            await locator.dispatch_event("click")
            return

        covered = await _check_element_covered(locator, cx, cy, cdp_context=cdp_context)
        if covered:
            logger.debug("_click_checkable_target: covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
            if page:
                await _click_covering_element(page, locator, cx, cy, cdp_context=cdp_context)
            else:
                await locator.dispatch_event("click")
        else:
            await locator.click()
        return

    if await locator.is_visible():
        await locator.click()
    else:
        logger.debug("_click_checkable_target: no bbox and is_visible()=False; using dispatch_event click")
        await locator.dispatch_event("click")


# Type aliases for Playwright types
ViewportSize = Dict[str, int]  # {"width": int, "height": int}
Geolocation = Dict[str, float]  # {"latitude": float, "longitude": float, "accuracy"?: float}
HttpCredentials = Dict[str, Any]  # {"username": str, "password": str, ...}
ClientCertificate = Dict[str, Any]


class Browser:
    """Browser wrapper for Playwright with automatic launch mode selection.

    Automatically loads configuration from config files and environment
    variables on instantiation (same priority chain as the ``bridgic-browser``
    CLI): ``~/.bridgic/bridgic-browser/bridgic-browser.json`` → ``./bridgic-browser.json`` →
    ``BRIDGIC_BROWSER_JSON`` env var. Explicit constructor parameters override
    config values.

    This class automatically chooses between ``launch_persistent_context`` and
    ``launch`` + ``new_context`` based on the ``clear_user_data`` parameter.

    - ``clear_user_data=False`` (default): Uses ``launch_persistent_context`` for
      session persistence. Uses the explicit ``user_data_dir`` if provided, otherwise
      defaults to ``~/.bridgic/bridgic-browser/user_data/``.
    - ``clear_user_data=True``: Uses ``launch`` + ``new_context`` for ephemeral sessions
      (no persistent profile; ``user_data_dir`` is ignored).

    Parameters
    ----------
    headless : bool, optional
        Whether to run browser in headless mode. Defaults to None (resolved
        from config files or True if no config present).
    viewport : ViewportSize, optional
        Viewport size. Defaults to {"width": 1600, "height": 900}.
    user_data_dir : str | Path, optional
        Path to user data directory for persistent context. Only used when
        ``clear_user_data=False`` (the default). When not provided, defaults to
        ``~/.bridgic/bridgic-browser/user_data/``. Ignored when ``clear_user_data=True``.
    clear_user_data : bool, optional
        If True, start an ephemeral browser session (``launch`` + ``new_context``,
        no persistent profile; ``user_data_dir`` is ignored). If False (default),
        use ``launch_persistent_context`` with a persistent profile. Defaults to
        None (resolved from config files or False if no config present).
    stealth : bool | StealthConfig, optional
        Stealth mode for bypassing bot detection. Defaults to None (resolved
        from config files or True if no config present).
        - True: Enable stealth with optimal StealthConfig
        - False: Disable stealth mode completely
        - StealthConfig: Custom stealth configuration

        Stealth mode includes:
        - 50+ Chrome args to disable automation detection
        - Ignoring Playwright's automation-revealing default args
    channel : str, optional
        Browser distribution channel. Use "chrome", "chrome-beta", "msedge", etc.
        for branded browsers, or "chromium" for new headless mode.
    executable_path : str | Path, optional
        Path to a browser executable to run instead of the bundled one.
    proxy : ProxySettings, optional
        Network proxy settings: {"server": str, "bypass"?: str, "username"?: str, "password"?: str}.
    timeout : float, optional
        Maximum time in seconds to wait for browser to start. Default 30.
    slow_mo : float, optional
        Slows down Playwright operations by specified milliseconds. Useful for debugging.
    args : Sequence[str], optional
        Additional arguments to pass to the browser instance.
    ignore_default_args : bool | Sequence[str], optional
        If True, only use custom args. If array, filter out specified default args.
    downloads_path : str | Path, optional
        Directory for accepted downloads.
    devtools : bool, optional
        **Chromium-only** Auto-open Developer Tools panel. Sets headless=False.
    user_agent : str, optional
        Specific user agent string for this context.
    locale : str, optional
        User locale (e.g., "en-GB", "de-DE"). Affects navigator.language.
    timezone_id : str, optional
        Timezone ID (e.g., "America/New_York"). Affects Date/time functions.
    ignore_https_errors : bool, optional
        Whether to ignore HTTPS errors. Default False.
    extra_http_headers : Dict[str, str], optional
        Additional HTTP headers sent with every request.
    offline : bool, optional
        Emulate network being offline. Default False.
    color_scheme : Literal["dark", "light", "no-preference", "null"], optional
        Emulates prefers-color-scheme media feature. Default "light".
    **kwargs : Any
        Additional Playwright launch/context parameters. These are passed directly
        to the underlying Playwright methods.

        For `launch` mode, additional options include:
        - handle_sigint, handle_sigterm, handle_sighup: Signal handling
        - env: Environment variables for browser
        - traces_dir: Directory for traces
        - chromium_sandbox: Enable Chromium sandboxing
        - firefox_user_prefs: Firefox user preferences

        For `launch_persistent_context` mode, additional options include all
        launch options plus context options:
        - screen, no_viewport: Screen/viewport settings
        - java_script_enabled, bypass_csp: JS and CSP settings
        - geolocation, permissions: Location and permissions
        - http_credentials: HTTP authentication
        - device_scale_factor, is_mobile, has_touch: Device emulation
        - reduced_motion, forced_colors, contrast: Accessibility
        - accept_downloads: Auto-accept downloads
        - record_har_*: HAR recording options
        - base_url, strict_selectors, service_workers: Navigation/selector options
        - client_certificates: TLS client authentication

    Examples
    --------
    # Default: headless with stealth (stealth is ON by default)
    >>> browser = Browser()  # stealth=True, headless=True

    # Non-headless with stealth
    >>> browser = Browser(headless=False)

    # Persistent session with stealth
    >>> browser = Browser(
    ...     headless=False,
    ...     user_data_dir="~/.browser_data",
    ...     channel="chrome",
    ... )

    # With proxy and custom viewport
    >>> browser = Browser(
    ...     viewport={"width": 1280, "height": 720},
    ...     proxy={"server": "http://proxy:8080"},
    ... )

    # Mobile emulation
    >>> browser = Browser(
    ...     viewport={"width": 375, "height": 812},
    ...     user_agent="Mozilla/5.0 (iPhone; ...)",
    ...     is_mobile=True,
    ...     has_touch=True,
    ... )

    # Disable stealth if needed
    >>> browser = Browser(stealth=False)

    # Custom stealth config
    >>> browser = Browser(
    ...     stealth=StealthConfig(
    ...         disable_security=True,    # For testing only
    ...     ),
    ... )
    """

    def __init__(
        self,
        # === Common frequently used parameters ===
        headless: Optional[bool] = None,
        viewport: Optional[ViewportSize] = None,
        user_data_dir: Optional[Union[str, Path]] = None,
        clear_user_data: Optional[bool] = None,
        # === Stealth mode (enabled by default for best anti-detection) ===
        stealth: Union[bool, StealthConfig, None] = None,
        # === CDP connection (connect to an existing Chrome instance) ===
        cdp_url: Optional[str] = None,
        # === Browser launch parameters (commonly used) ===
        channel: Optional[str] = None,
        executable_path: Optional[Union[str, Path]] = None,
        proxy: Optional[ProxySettings] = None,
        timeout: Optional[float] = None,
        slow_mo: Optional[float] = None,
        args: Optional[Sequence[str]] = None,
        ignore_default_args: Optional[Union[bool, Sequence[str]]] = None,
        downloads_path: Optional[Union[str, Path]] = None,
        devtools: Optional[bool] = None,
        # === Context parameters (commonly used) ===
        user_agent: Optional[str] = None,
        locale: Optional[str] = None,
        timezone_id: Optional[str] = None,
        ignore_https_errors: Optional[bool] = None,
        extra_http_headers: Optional[Dict[str, str]] = None,
        offline: Optional[bool] = None,
        color_scheme: Optional[Literal["dark", "light", "no-preference", "null"]] = None,
        # === All other parameters via kwargs ===
        **kwargs: Any,
    ):
        # --- Load config from files and environment ---
        from .._config import _load_config_sources
        _cfg = _load_config_sources()

        # Resolve parameters: explicit (non-None) > config > default.
        # Always pop named-param keys from _cfg so they don't leak into
        # _extra_kwargs (which would corrupt get_config() and Playwright options).
        cdp_url = cdp_url if cdp_url is not None else _cfg.pop('cdp_url', None)
        # Normalize cdp_url for *all* sources (config file, explicit ctor arg).
        # The CLI client and the daemon already run resolve_cdp_input() before
        # they pass us a value, but a config file like {"cdp_url": "9222"} or
        # {"cdp_url": "auto"} would otherwise reach Playwright's
        # connect_over_cdp() unchanged and crash deep in the driver. ws://
        # and wss:// inputs short-circuit (no extra work, no I/O).
        if cdp_url is not None and not (
            isinstance(cdp_url, str)
            and (cdp_url.startswith("ws://") or cdp_url.startswith("wss://"))
        ):
            try:
                cdp_url = resolve_cdp_input(str(cdp_url))
            except (RuntimeError, ValueError, ConnectionError) as exc:
                raise InvalidInputError(
                    f"Failed to resolve cdp_url={cdp_url!r}: {exc}",
                    code="INVALID_CDP_URL",
                    details={"cdp_url": cdp_url, "source": "config_or_argument"},
                ) from exc
        headless = headless if headless is not None else _cfg.pop('headless', True)
        stealth = stealth if stealth is not None else _cfg.pop('stealth', True)
        viewport = viewport if viewport is not None else _cfg.pop('viewport', None)
        user_data_dir = user_data_dir if user_data_dir is not None else _cfg.pop('user_data_dir', None)
        clear_user_data = clear_user_data if clear_user_data is not None else _cfg.pop('clear_user_data', False)
        channel = channel if channel is not None else _cfg.pop('channel', None)
        executable_path = executable_path if executable_path is not None else _cfg.pop('executable_path', None)
        proxy = proxy if proxy is not None else _cfg.pop('proxy', None)
        timeout = timeout if timeout is not None else _cfg.pop('timeout', None)
        slow_mo = slow_mo if slow_mo is not None else _cfg.pop('slow_mo', None)
        args = args if args is not None else _cfg.pop('args', None)
        ignore_default_args = ignore_default_args if ignore_default_args is not None else _cfg.pop('ignore_default_args', None)
        downloads_path = downloads_path if downloads_path is not None else _cfg.pop('downloads_path', None)
        devtools = devtools if devtools is not None else _cfg.pop('devtools', None)
        user_agent = user_agent if user_agent is not None else _cfg.pop('user_agent', None)
        locale = locale if locale is not None else _cfg.pop('locale', None)
        timezone_id = timezone_id if timezone_id is not None else _cfg.pop('timezone_id', None)
        ignore_https_errors = ignore_https_errors if ignore_https_errors is not None else _cfg.pop('ignore_https_errors', None)
        extra_http_headers = extra_http_headers if extra_http_headers is not None else _cfg.pop('extra_http_headers', None)
        offline = offline if offline is not None else _cfg.pop('offline', None)
        color_scheme = color_scheme if color_scheme is not None else _cfg.pop('color_scheme', None)
        # Remove any named-param keys that were skipped above (explicit value won)
        for _named_key in (
            'cdp_url', 'headless', 'stealth', 'viewport', 'user_data_dir',
            'clear_user_data', 'channel', 'executable_path', 'proxy', 'timeout',
            'slow_mo', 'args', 'ignore_default_args', 'downloads_path', 'devtools',
            'user_agent', 'locale', 'timezone_id', 'ignore_https_errors',
            'extra_http_headers', 'offline', 'color_scheme',
        ):
            _cfg.pop(_named_key, None)

        # Merge remaining config into kwargs (pass-through params like chromium_sandbox)
        for k, v in _cfg.items():
            kwargs.setdefault(k, v)

        # Headed mode: auto-set chromium_sandbox=True to prevent --no-sandbox warning
        if headless is False:
            kwargs.setdefault('chromium_sandbox', True)

        # Store all parameters
        self._headless = headless
        self._no_viewport = bool(kwargs.get("no_viewport", False))
        if devtools:
            self._headless = False
        if self._no_viewport:
            if viewport is not None:
                raise InvalidInputError(
                    "viewport must be None when no_viewport=True",
                    code="VIEWPORT_CONFLICT",
                    details={"viewport": viewport},
                )
            self._viewport = None
        else:
            self._viewport = viewport or {"width": 1600, "height": 900}
        self._user_data_dir = Path(user_data_dir).expanduser() if user_data_dir else None
        self._clear_user_data: bool = clear_user_data

        # Stealth configuration
        self._stealth_config: Optional[StealthConfig] = None
        self._stealth_builder: Optional[StealthArgsBuilder] = None

        self._preallocated_trace_path: Optional[str] = None
        self._close_session_dir: Optional[str] = None

        if stealth is True:
            self._stealth_config = StealthConfig()
        elif isinstance(stealth, dict):
            # Config files pass stealth as a dict (e.g. {"disable_security": true}).
            # Filter out unknown keys for backwards compatibility (e.g. removed
            # "enable_extensions" from older config files).
            import dataclasses as _dc
            _known = {f.name for f in _dc.fields(StealthConfig)}
            _filtered = {k: v for k, v in stealth.items() if k in _known}
            self._stealth_config = StealthConfig(**_filtered)
        elif isinstance(stealth, StealthConfig):
            self._stealth_config = stealth

        if self._stealth_config and self._stealth_config.enabled:
            self._stealth_builder = StealthArgsBuilder(self._stealth_config)

        # CDP connection URL (if set, connect_over_cdp() is used instead of launch)
        self._cdp_url = cdp_url
        # Whether bridgic created the CDP context (vs borrowing an existing one).
        # When True, close() will close the context; when False it only disconnects.
        self._cdp_context_owned = False

        # Browser launch parameters
        self._channel = channel
        self._executable_path = Path(executable_path).expanduser() if executable_path else None
        self._proxy = proxy
        self._timeout = timeout
        self._slow_mo = slow_mo
        self._args = args
        self._ignore_default_args = ignore_default_args
        self._downloads_path = Path(downloads_path).expanduser() if downloads_path else None
        self._devtools = devtools

        # Context parameters
        self._user_agent = user_agent
        self._locale = locale
        self._timezone_id = timezone_id
        self._ignore_https_errors = ignore_https_errors
        self._extra_http_headers = extra_http_headers
        self._offline = offline
        self._color_scheme = color_scheme

        # Store additional kwargs for pass-through
        self._extra_kwargs = kwargs

        # Playwright instances
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[PlaywrightBrowser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        # C2: set synchronously at the top of close() (before any await) so
        # concurrent dispatchers can short-circuit with BROWSER_CLOSED rather
        # than hit a misleading NO_ACTIVE_PAGE when `_page` is mid-teardown.
        self._closing: bool = False

        # Download manager - handles saving files with correct filenames
        self._download_manager: Optional[DownloadManager] = None
        if self._downloads_path:
            self._download_manager = DownloadManager(downloads_path=self._downloads_path)

        # Cache for last snapshot
        self._last_snapshot: Optional[EnhancedSnapshot] = None
        self._last_snapshot_url: Optional[str] = None
        self._snapshot_generator: Optional[SnapshotGenerator] = None
        self._snapshot_lock = asyncio.Lock()
        # Background snapshot pre-warm (kicked off after navigate_to).
        # Uses a dedicated generator so it never races with _snapshot_generator.
        self._prefetch_snapshot: Optional[EnhancedSnapshot] = None
        self._prefetch_options: Optional[SnapshotOptions] = None
        self._prefetch_url: Optional[str] = None
        self._prefetch_task: Optional[asyncio.Task] = None
        self._prefetch_generator: Optional[SnapshotGenerator] = None
        # Monotonic generation counter — bumped by `_cancel_prefetch()` on
        # every navigation / tab switch. Each prefetch task captures the
        # current value at launch and MUST verify it still matches before
        # committing its result under `_snapshot_lock`. Without this a task
        # returning from its await between cancel and commit could clobber
        # a fresh page's cache with a stale snapshot. (C4.)
        self._prefetch_gen: int = 0
        # Artifacts auto-saved during shutdown (trace/video)
        self._last_shutdown_artifacts: Dict[str, List[str]] = {"trace": [], "video": []}
        self._last_shutdown_errors: List[str] = []

        # Page-scoped state (keyed by _get_page_key)
        self._console_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._network_requests: Dict[str, List[Dict[str, Any]]] = {}
        self._console_handlers: Dict[str, Any] = {}
        self._network_handlers: Dict[str, Any] = {}
        self._dialog_handlers: Dict[str, Any] = {}
        # Context-scoped state (keyed by _get_context_key)
        self._tracing_state: Dict[str, bool] = {}
        self._video_state: Dict[str, bool] = {}
        # Single-stream video recording: one ffmpeg process records the
        # active tab. When the user switches tabs the screencast source
        # is hot-swapped via VideoRecorder.switch_page().
        self._video_recorder: Optional["_video_recorder_mod.VideoRecorder"] = None
        # When a recording session is active, holds {"width", "height",
        # "context", "page_listener"}.  None means no active session.
        self._video_session: Optional[Dict[str, Any]] = None

    # ==================== Properties ====================

    @property
    def use_persistent_context(self) -> bool:
        """Whether to use persistent context mode (unrelated to headless/headed mode).

        Priority (highest to lowest):
        - cdp_url is set        → always False (connect to existing browser)
        - clear_user_data=True  → always False (fresh launch+new_context, user_data_dir ignored)
        - clear_user_data=False → always True (persistent; user_data_dir if set, else default dir)
        """
        # CDP mode: connect to existing browser, never use persistent context
        if self._cdp_url is not None:
            return False

        return not self._clear_user_data

    @property
    def stealth_enabled(self) -> bool:
        """Whether stealth mode is enabled."""
        return self._stealth_config is not None and self._stealth_config.enabled

    @property
    def stealth_config(self) -> Optional[StealthConfig]:
        """Current stealth configuration, or None if stealth is disabled."""
        return self._stealth_config

    @property
    def download_manager(self) -> Optional[DownloadManager]:
        """Download manager for handling file downloads with correct filenames."""
        return self._download_manager

    @property
    def downloaded_files(self) -> List[DownloadedFile]:
        """Get list of all downloaded files in this session."""
        if self._download_manager:
            return self._download_manager.downloaded_files
        return []

    @property
    def headless(self) -> bool:
        """Whether the user requested a windowless (headless) browser.

        Reflects the *user's intent*, not the internal Playwright ``headless``
        flag.  When stealth's new-headless mode is active, Playwright receives
        ``headless=False`` internally so it selects the full Chromium binary,
        but this property still returns ``True`` because Chrome itself runs
        with ``--headless=new`` and has no visible window.
        """
        return self._headless

    @property
    def viewport(self) -> Optional[ViewportSize]:
        """Current viewport size configuration (None when no_viewport=True)."""
        return self._viewport

    @property
    def user_data_dir(self) -> Optional[Path]:
        """User data directory path, or None if not using persistent context."""
        return self._user_data_dir

    @property
    def clear_user_data(self) -> bool:
        """Whether user data is cleared on each browser start (ephemeral mode)."""
        return self._clear_user_data

    @property
    def channel(self) -> Optional[str]:
        """Browser distribution channel."""
        return self._channel

    @property
    def last_close_artifacts(self) -> Dict[str, List[str]]:
        """Trace and video paths produced by the most recent ``close()`` call.

        Returns
        -------
        Dict[str, List[str]]
            ``{"trace": [...], "video": [...]}``. The lists are empty
            when ``close()`` ran but produced no artifacts, and also
            when ``close()`` has never been called on this instance.

        Notes
        -----
        Returns a fresh shallow copy on every access — mutating the
        returned dict (or its inner lists) does not affect the
        browser's internal state, and a subsequent ``close()`` will
        not clobber the copy you already hold.
        """
        src = self._last_shutdown_artifacts or {}
        return {
            "trace": list(src.get("trace", [])),
            "video": list(src.get("video", [])),
        }

    @property
    def last_close_errors(self) -> List[str]:
        """Warnings/errors collected during the most recent ``close()`` call.

        Returns
        -------
        List[str]
            One entry per cleanup step that raised. Empty when
            ``close()`` succeeded cleanly or has never been called.

        Notes
        -----
        Returns a fresh copy on every access; mutating it does not
        affect the browser's internal state.
        """
        return list(self._last_shutdown_errors or [])

    def get_config(self) -> Dict[str, Any]:
        """Get all current browser configuration.

        Returns
        -------
        Dict[str, Any]
            Dictionary containing all browser configuration options.
        """
        config = {
            "headless": self._headless,
            "viewport": self._viewport,
            "no_viewport": self._no_viewport,
            "user_data_dir": str(self._user_data_dir) if self._user_data_dir else None,
            "clear_user_data": self._clear_user_data,
            "stealth_enabled": self.stealth_enabled,
            "channel": self._channel,
            "executable_path": str(self._executable_path) if self._executable_path else None,
            "proxy": self._proxy,
            "timeout": self._timeout,
            "slow_mo": self._slow_mo,
            "args": list(self._args) if self._args else None,
            "ignore_default_args": self._ignore_default_args,
            "downloads_path": str(self._downloads_path) if self._downloads_path else None,
            "devtools": self._devtools,
            "user_agent": self._user_agent,
            "locale": self._locale,
            "timezone_id": self._timezone_id,
            "ignore_https_errors": self._ignore_https_errors,
            "extra_http_headers": self._extra_http_headers,
            "offline": self._offline,
            "color_scheme": self._color_scheme,
            "cdp_url": self._cdp_url,
            "use_persistent_context": self.use_persistent_context,
            **self._extra_kwargs,
        }
        # Remove None values for cleaner output
        return {k: v for k, v in config.items() if v is not None}

    # ==================== Internal Configuration ====================

    def _get_launch_options(self) -> Dict[str, Any]:
        """Get options for browser.launch() method.

        Merges user options with stealth options when stealth is enabled.

        Returns
        -------
        Dict[str, Any]
            Options dict for playwright.chromium.launch()
        """
        options: Dict[str, Any] = {}

        # Build args list (merge stealth args with user args)
        args_list: List[str] = []

        # When using system Chrome (channel or executable_path), skip stealth
        # Chrome args — many stealth flags cause "unsupported flag" warnings.
        # Anti-detection still works via ignore_default_args
        # (removes --enable-automation) and the JS init script (patches
        # navigator.webdriver, plugins, chrome object, etc.).
        _is_system_chrome = bool(self._channel or self._executable_path)

        # In headed mode, auto-switch to system Chrome to avoid Google blocking
        # "Chrome for Testing" (the Playwright-bundled binary) for OAuth login.
        # System Chrome shows as a normal browser in the Dock (no "test" label)
        # and passes Google's browser safety checks.
        _auto_system_chrome = (
            not self._headless
            and self.stealth_enabled
            and not _is_system_chrome
            and _detect_system_chrome()
        )
        if _auto_system_chrome:
            options["channel"] = "chrome"
            logger.info(
                "Headed mode: auto-switching to system Chrome for anti-detection "
                "(Chrome for Testing is blocked by Google OAuth)"
            )

        # Add stealth args first (if enabled).
        # When user explicitly set channel/executable_path (_is_system_chrome),
        # skip stealth args entirely (existing behaviour).
        # When auto-switched to system Chrome, still apply the minimal headed
        # stealth args (they're compatible with system Chrome).
        if self._stealth_builder and not _is_system_chrome:
            fallback_viewport = {"width": 1600, "height": 900}
            viewport = self._viewport or fallback_viewport
            viewport_width = viewport.get("width", 1600)
            viewport_height = viewport.get("height", 900)
            stealth_args = self._stealth_builder.build_args(
                viewport_width,
                viewport_height,
                headless_intent=self._headless,
                locale=self._locale,
            )
            if _auto_system_chrome:
                # System Chrome shows a "unsupported command-line flag" warning
                # banner for --disable-blink-features.  --test-type= (empty value)
                # tells Chrome to suppress all bad-flag warnings without adding
                # any web-detectable side effects.
                stealth_args.append("--test-type=")
            args_list.extend(stealth_args)

        # Add user-provided args (can override/extend stealth args)
        if self._args:
            args_list.extend(self._args)

        if args_list:
            options["args"] = args_list

        # Build ignore_default_args (merge stealth with user)
        ignore_args: List[str] = []
        if self._stealth_builder:
            ignore_args.extend(self._stealth_builder.get_ignore_default_args())

        if self._ignore_default_args is True:
            # User wants to ignore ALL default args
            options["ignore_default_args"] = True
        elif isinstance(self._ignore_default_args, (list, tuple)):
            # Merge user's ignore list with stealth ignore list
            ignore_args.extend(self._ignore_default_args)
            if ignore_args:
                options["ignore_default_args"] = list(set(ignore_args))
        elif ignore_args:
            # Only stealth ignore args
            options["ignore_default_args"] = ignore_args

        # Add non-None launch parameters
        if self._executable_path is not None:
            options["executable_path"] = self._executable_path
        if self._channel is not None:
            options["channel"] = self._channel
        if self._timeout is not None:
            options["timeout"] = self._timeout * 1000.0
        if self._headless is not None:
            # When the user wants no window + stealth is active, redirect Playwright
            # to the full chromium binary by passing headless=False.  The actual
            # "no window" behaviour comes from --headless=new added in build_args().
            #
            #   self._headless      → user intent   (hide the window?)
            #   options["headless"] → Playwright arg (which binary to pick?)
            #
            # chromium-headless-shell is a stripped binary with detectable
            # fingerprint differences; full chromium + --headless=new avoids that.
            _use_full_binary = (
                self._headless is True
                and not _is_system_chrome       # system Chrome picks its own binary
                and not _auto_system_chrome      # auto-switched system Chrome too
                and self._stealth_config is not None
                and self._stealth_config.enabled
                and self._stealth_config.use_new_headless
            )
            options["headless"] = False if _use_full_binary else self._headless
        if self._devtools is not None:
            options["devtools"] = self._devtools
        if self._proxy is not None:
            options["proxy"] = self._proxy
        # NOTE: We intentionally do NOT pass downloads_path to Playwright.
        # Playwright uses CDP `Browser.setDownloadBehavior(allowAndName)` to
        # intercept all downloads, which breaks Chrome's native download UI
        # (e.g. "Show in Folder" does nothing).  This is a known Chromium bug:
        # https://issues.chromium.org/issues/324282051
        # Instead, DownloadManager uses download.save_as() to copy files with
        # correct filenames to the user's downloads_path.
        if self._slow_mo is not None:
            options["slow_mo"] = self._slow_mo

        # Extract launch-specific kwargs
        launch_keys = {
            "handle_sigint", "handle_sigterm", "handle_sighup",
            "env", "traces_dir", "chromium_sandbox", "firefox_user_prefs"
        }
        for key in launch_keys:
            if key in self._extra_kwargs:
                options[key] = self._extra_kwargs[key]

        return options

    def _get_context_options(self) -> Dict[str, Any]:
        """Get options for browser.new_context() method.

        Merges user options with stealth options when stealth is enabled.

        Returns
        -------
        Dict[str, Any]
            Options dict for browser.new_context()
        """
        options: Dict[str, Any] = {}

        # Add stealth context options first (if enabled)
        if self._stealth_builder:
            stealth_context_opts = self._stealth_builder.get_context_options()
            options.update(stealth_context_opts)

            # Add screen size to match viewport for correct window.screen values.
            # Fall back to a standard desktop resolution when no_viewport=True.
            if self._viewport:
                options["screen"] = self._viewport.copy()
            else:
                options["screen"] = {"width": 1600, "height": 900}

        # Add non-None context parameters (user values override stealth defaults)
        if self._viewport is not None and not self._no_viewport:
            options["viewport"] = self._viewport
        if self._user_agent is not None:
            options["user_agent"] = self._user_agent
        if self._locale is not None:
            options["locale"] = self._locale
        if self._timezone_id is not None:
            options["timezone_id"] = self._timezone_id
        if self._ignore_https_errors is not None:
            options["ignore_https_errors"] = self._ignore_https_errors
        if self._extra_http_headers is not None:
            options["extra_http_headers"] = self._extra_http_headers
        if self._offline is not None:
            options["offline"] = self._offline
        if self._color_scheme is not None:
            options["color_scheme"] = self._color_scheme

        # Auto-enable downloads if downloads_path is configured
        if self._downloads_path and "accept_downloads" not in self._extra_kwargs:
            options["accept_downloads"] = True

        # Extract context-specific kwargs (user values override everything)
        context_keys = {
            "screen", "no_viewport", "java_script_enabled", "bypass_csp",
            "geolocation", "permissions", "http_credentials",
            "device_scale_factor", "is_mobile", "has_touch",
            "reduced_motion", "forced_colors", "contrast",
            "accept_downloads", "base_url", "strict_selectors", "service_workers",
            "record_har_path", "record_har_omit_content", "record_har_url_filter",
            "record_har_mode", "record_har_content",
            "client_certificates"
        }
        for key in context_keys:
            if key in self._extra_kwargs:
                options[key] = self._extra_kwargs[key]

        return options

    def _get_persistent_context_options(self) -> Dict[str, Any]:
        """Get options for launch_persistent_context() method.

        Combines launch options, context options, and user_data_dir.

        Returns
        -------
        Dict[str, Any]
            Options dict for playwright.chromium.launch_persistent_context()
        """
        # Start with launch options
        options = self._get_launch_options()

        # Add context options
        options.update(self._get_context_options())

        # Determine user_data_dir (only reached when clear_user_data=False)
        if self._user_data_dir:
            options["user_data_dir"] = str(self._user_data_dir)
        else:
            # No custom path: use the default persistent profile directory.
            BRIDGIC_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            options["user_data_dir"] = str(BRIDGIC_USER_DATA_DIR)
            logger.info(f"Using default user data dir: {BRIDGIC_USER_DATA_DIR}")

        return options

    # ==================== Lifecycle ====================

    async def _apply_debugger_skip_pauses(self, context: "BrowserContext", page: "Page") -> None:
        """Tell CDP to skip debugger pauses on ``page``.

        Playwright enables the Debugger domain internally; any ``debugger``
        statement would fire Debugger.paused events whose CDP round-trip
        delay can be timed by devtools-detector (>100 ms → "open").
        Invoked from all three start modes (launch / persistent / CDP) so
        the anti-detection surface stays symmetric.
        """
        if not self.stealth_enabled or page is None:
            return
        try:
            _dbg = await context.new_cdp_session(page)
            await _dbg.send("Debugger.setSkipAllPauses", {"skip": True})
            await _dbg.detach()
        except Exception:
            logger.debug("Failed to set Debugger.setSkipAllPauses", exc_info=True)

    async def _start(self) -> None:
        """Start the browser.

        Automatically chooses between two launch modes:
        - If `clear_user_data=False` (default): Uses `launch_persistent_context` (persistent profile)
        - If `clear_user_data=True`: Uses `launch` + `new_context` (ephemeral, no profile)

        When stealth mode is enabled, anti-detection args are automatically
        applied.
        """
        if self._playwright is not None:
            logger.warning("Playwright has already been started")
            return

        logger.info("Starting playwright")
        if self.stealth_enabled:
            logger.info("Stealth mode enabled")

        try:
            self._playwright = await async_playwright().start()

            if self._cdp_url:
                # Mode 0: Connect to an already-running Chrome via raw CDP.
                # Stealth launch args and extensions cannot be applied to an existing
                # browser process, so they are skipped here.  The JS init script is
                # still registered so that new pages opened in this session receive it.
                logger.info("Using CDP connect mode (url=%s)", self._cdp_url)
                self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)
                # Playwright invariant for connect_over_cdp() (verified
                # against playwright-core 1.57):
                #   chromium.ts _connectOverCDPImpl always passes
                #   persistent={noDefaultViewport: true}, so
                #   crBrowser.ts:_connect skips the early `if (!options.persistent)`
                #   branch and creates `_defaultContext`. The Node-side
                #   browserDispatcher then dispatches it as a `context`
                #   event, which the Python client appends to
                #   `Browser._contexts`.
                # Net effect: ``self._browser.contexts`` is never empty
                # in current Playwright versions. The else branch below
                # is a defensive fallback in case this invariant ever
                # changes upstream.
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                    self._cdp_context_owned = False
                else:
                    self._context = await self._browser.new_context(**self._get_context_options())
                    self._cdp_context_owned = True

                # Inject JS stealth patches only in headless mode.  Headed mode
                # skips the script to avoid breaking Cloudflare Turnstile (same
                # rationale as the non-CDP code path below).
                if self._stealth_builder and self._headless:
                    init_script = self._stealth_builder.get_init_script(locale=self._locale)
                    if init_script:
                        await self._context.add_init_script(init_script)

                # Anti devtools-detector init script: safe for both headed and
                # headless (it only patches timing probes, not window.chrome or
                # WebGL identity that Turnstile checks).  Without this the CDP
                # entry point would be detectably weaker than launch/persistent.
                if self._stealth_builder:
                    _adt_script = self._stealth_builder.get_anti_devtools_script()
                    if _adt_script:
                        await self._context.add_init_script(_adt_script)

                # Always create a new tab for bridgic to drive.  We never
                # reuse an existing user tab — the very next navigate_to()
                # would otherwise overwrite whatever the user was looking at.
                # In owned-context mode the new context is empty anyway, so
                # this is a no-op cost.
                existing_count = len(self._context.pages)
                self._page = await self._context.new_page()
                logger.info(
                    "[CDP] connected; created new bridgic tab "
                    "(borrowed_context=%s, preserved_existing_tabs=%d)",
                    not self._cdp_context_owned,
                    existing_count,
                )

                # Parity with non-CDP: make the Debugger domain skip pauses on
                # the bridgic page so devtools-detector cannot time the CDP
                # round-trip of Debugger.paused events.
                await self._apply_debugger_skip_pauses(self._context, self._page)

                # Download manager attachment strategy (CDP):
                # - Owned context (bridgic created it): attach to the whole
                #   context — all pages in it belong to bridgic anyway.
                # - Borrowed context (user's): attach ONLY to the bridgic tab.
                #   attaching to the context would hijack download events from
                #   the user's pre-existing tabs (they'd land in our
                #   downloads_path instead of Chrome's default behaviour).
                if self._download_manager:
                    if self._cdp_context_owned:
                        self._download_manager.attach_to_context(self._context)
                    else:
                        self._download_manager.attach_to_page(self._page)

                logger.info("Playwright started (mode=cdp, stealth_js=%s)", self.stealth_enabled)
                return

            elif self.use_persistent_context:
                # Mode 1: Persistent context (clear_user_data=False)
                logger.info("Using persistent context mode")
                persistent_options = self._get_persistent_context_options()
                logger.debug(f"Persistent context options: {persistent_options}")
                _write_launch_debug_log(persistent_options, mode="persistent_context")
                self._context = await _retriable_launch(
                    lambda: self._playwright.chromium.launch_persistent_context(
                        **persistent_options
                    ),
                    mode="persistent_context",
                )
                self._browser = self._context.browser
            else:
                # Mode 2: Ephemeral launch + new_context (clear_user_data=True)
                logger.info("Using normal launch mode")
                launch_options = self._get_launch_options()
                logger.debug(f"Launch options: {launch_options}")
                _write_launch_debug_log(launch_options, mode="launch")
                self._browser = await _retriable_launch(
                    lambda: self._playwright.chromium.launch(**launch_options),
                    mode="launch",
                )

                context_options = self._get_context_options()
                logger.debug(f"Context options: {context_options}")
                self._context = await self._browser.new_context(**context_options)

            # Inject JS stealth patches before any page script runs.
            # Headed mode (self._headless=False) skips the init script entirely
            # so Cloudflare Turnstile's challenge iframe sees original, unmodified
            # browser APIs — the same as playwright CLI (which injects nothing).
            # context.add_init_script() runs in ALL frames including challenge
            # iframes; patching window.chrome (configurable:false),
            # navigator.permissions.query, and WebGL prototype inside the
            # Turnstile iframe causes detectable API inconsistencies that fail
            # the challenge even when the browser binary is fine.
            if self._stealth_builder and self._headless:
                init_script = self._stealth_builder.get_init_script(locale=self._locale)
                if init_script:
                    await self._context.add_init_script(init_script)

            # Anti devtools-detector: inject patches safe for both modes.
            if self._stealth_builder:
                _adt_script = self._stealth_builder.get_anti_devtools_script()
                if _adt_script:
                    await self._context.add_init_script(_adt_script)

            # Auto create a new page if no page is open
            pages = self._context.pages
            if len(pages) > 0:
                self._page = pages[0]
            else:
                self._page = await self._context.new_page()

            # Anti devtools-detector: skip all debugger-statement pauses.
            # Playwright enables the Debugger domain internally; debugger
            # statements would fire Debugger.paused events whose CDP
            # round-trip delay the debuggerChecker in devtools-detector
            # can measure (>100 ms => "open").
            await self._apply_debugger_skip_pauses(self._context, self._page)

            # Attach download manager to handle downloads with correct filenames
            if self._download_manager:
                self._download_manager.attach_to_context(self._context)
                logger.info(
                    f"Download manager attached, saving to: {self._download_manager.downloads_path}"
                )

            logger.info(
                f"Playwright started (persistent_context={self.use_persistent_context}, "
                f"stealth={self.stealth_enabled})"
            )
        except BaseException:
            logger.exception("Failed to start browser; rolling back partial startup state")
            try:
                await self.close()
            except BaseException:
                logger.exception("Failed to roll back browser startup state")
            raise

    async def _ensure_started(self) -> None:
        """Auto-start the browser if not yet started.

        Guarantees that both ``_playwright`` and ``_context`` are initialised
        after this call returns.  If ``_playwright`` is set but ``_context`` is
        None (inconsistent state caused by an external browser crash or a
        partial ``close()``), the browser is fully reset before restarting.
        """
        if self._playwright is None:
            await self._start()
        elif self._context is None:
            # _playwright exists but context was lost — do a clean reset.
            logger.warning(
                "[_ensure_started] inconsistent state: _playwright set but _context is None; "
                "performing clean restart"
            )
            await self.close()
            await self._start()

    # Timeout (seconds) applied to individual page.close() calls during
    # shutdown so that a hung beforeunload handler cannot block forever.
    _PAGE_CLOSE_TIMEOUT = 5.0
    _TRACE_STOP_TIMEOUT = 30.0
    _CONTEXT_CLOSE_TIMEOUT = 15.0
    _BROWSER_CLOSE_TIMEOUT = 15.0
    _PLAYWRIGHT_STOP_TIMEOUT = 15.0
    _VIDEO_PREPARE_STOP_TIMEOUT = 15.0  # single recorder prepare_stop() in close()
    _VIDEO_FINALIZE_TIMEOUT = 30.0     # single ffmpeg finalize() in close()

    @staticmethod
    async def _force_kill_playwright_driver(pw: Any) -> None:
        """Force-kill the Playwright Node driver process (and its process group when safe).

        On macOS/Linux we attempt to kill the entire process group so that
        Chrome child processes are also terminated (killing only the Node driver
        leaves Chrome as orphans on macOS).

        Safety guard — same-pgid check:
            The daemon is spawned with start_new_session=True (setsid), so its
            pgid equals its own pid. The Node driver inherits that same pgid.
            Calling killpg(pgid) without the guard would SIGKILL the daemon
            itself, aborting close-report writes and leaving the socket file
            behind. When the driver shares our pgid we fall back to killing only
            the driver process (original behaviour — Chrome children remain
            orphans in this case, but that is unavoidable without psutil).

        Windows: os.getpgid / os.killpg are POSIX-only; on Windows we always
        fall back to proc.kill() directly.

        Accesses internal Playwright transport — best-effort: silently ignored
        if internals have changed.
        """
        try:
            proc = pw._connection._transport._proc  # type: ignore[union-attr]
            if proc and proc.returncode is None:
                killed_via_group = False
                if sys.platform != "win32":
                    try:
                        pgid = os.getpgid(proc.pid)
                        # Guard: do NOT send SIGKILL to our own process group.
                        # The daemon (and direct SDK callers) share the same pgid
                        # as the Node driver because the driver is spawned without
                        # start_new_session=True and inherits the caller's pgrp.
                        if pgid != os.getpgid(os.getpid()):
                            os.killpg(pgid, signal.SIGKILL)
                            killed_via_group = True
                            logger.debug(
                                "Force-killed Playwright driver process group (pgid=%d)", pgid
                            )
                    except (ProcessLookupError, OSError):
                        pass  # process already gone or pgid lookup failed
                if not killed_via_group:
                    proc.kill()
                    logger.debug("Force-killed Playwright driver process only")
                # Short timeout: SIGKILL is immediate, but wait() depends on
                # the event loop's child watcher which may misbehave at teardown.
                await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception as exc:
            logger.debug("_force_kill_playwright_driver skipped: %s", exc)

    def _write_close_report(self, errors: List[str]) -> None:
        """Write close-report.json into the close session directory."""
        session_dir = self._close_session_dir
        if not session_dir:
            return
        from datetime import datetime, timezone

        if errors:
            all_timeouts = all("timeout after" in e.lower() for e in errors)
            status = "success_with_timeouts" if all_timeouts else "error"
        else:
            status = "success"

        report = {
            "status": status,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "trace_paths": self._last_shutdown_artifacts.get("trace", []),
            "video_paths": self._last_shutdown_artifacts.get("video", []),
            "warnings": [],
            "errors": list(errors),
        }
        report_path = Path(session_dir) / "close-report.json"
        try:
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
            )
            logger.info("close-report written: %s", report_path)
        except Exception as exc:
            logger.warning("failed to write close-report.json: %s", exc)

    def _clear_page_scoped_state(self, page: Optional[Page], errors: Optional[List[str]] = None) -> None:
        """Detach page-scoped listeners and drop cached state for one page."""
        if page is None:
            return

        page_key = _get_page_key(page)

        if page_key in self._console_handlers:
            handler = self._console_handlers.pop(page_key)
            try:
                page.remove_listener("console", handler)
            except Exception as e:
                if errors is not None:
                    errors.append(f"console.remove_listener: {e}")
        self._console_messages.pop(page_key, None)

        if page_key in self._network_handlers:
            handler = self._network_handlers.pop(page_key)
            try:
                page.remove_listener("request", handler)
            except Exception as e:
                if errors is not None:
                    errors.append(f"network.remove_listener: {e}")
        self._network_requests.pop(page_key, None)

        if page_key in self._dialog_handlers:
            handler = self._dialog_handlers.pop(page_key)
            try:
                page.remove_listener("dialog", handler)
            except Exception as e:
                if errors is not None:
                    errors.append(f"dialog.remove_listener: {e}")

    def inspect_pending_close_artifacts(self) -> Dict[str, Any]:
        """Create a unique close-session directory and pre-allocate artifact paths.

        Called by the daemon before background teardown so paths can be reported
        immediately to the client. Stores state for browser.close() and the
        post-close report writer to consume.

        Returns
        -------
        Dict with keys:
          session_dir : str         — unique per-close directory under
                                      BRIDGIC_TMP_DIR, or "" when no
                                      artifact will be produced
          trace       : List[str]   — pre-created trace path (if tracing is active)
          video       : List[str]   — pre-allocated video paths in session dir

        Notes
        -----
        We deliberately skip creating the session directory when no
        tracing/video session is active. Otherwise every SDK ``close()``
        call would leak an empty ``close-<ts>-<rand>`` directory under
        ``BRIDGIC_TMP_DIR``, which previously accumulated indefinitely.
        """
        artifacts: Dict[str, Any] = {
            "session_dir": "",
            "trace": [],
            "video": [],
        }

        if not self._context:
            return artifacts

        context_key = _get_context_key(self._context)

        tracing_active = bool(self._tracing_state.get(context_key))
        video_count = 1 if self._video_recorder is not None else 0
        if not tracing_active and video_count == 0:
            # Nothing to write — don't create a directory.
            return artifacts

        import random
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_name = f"close-{ts}-{random.randint(0, 0xffff):04x}"
        session_dir = Path(str(BRIDGIC_TMP_DIR)) / session_name
        session_dir.mkdir(parents=True, exist_ok=True)
        self._close_session_dir = str(session_dir)
        artifacts["session_dir"] = str(session_dir)

        # Pre-allocate trace path inside session dir
        if tracing_active:
            trace_path = str(session_dir / "trace.zip")
            Path(trace_path).touch()          # create empty file; tracing.stop() will overwrite
            self._preallocated_trace_path = trace_path
            artifacts["trace"].append(trace_path)

        # Pre-allocate one video path per active recorder.  Multi-page
        # recording produces N files: video.webm, video-1.webm, ...
        for i in range(video_count):
            if i == 0:
                video_path = str(session_dir / "video.webm")
            else:
                video_path = str(session_dir / f"video-{i}.webm")
            artifacts["video"].append(video_path)

        return artifacts

    async def close(self) -> str:
        """Close the browser.

        Stops the browser and cleans up all resources. Automatically removes
        active page-scoped event listeners (console capture, network capture,
        dialog handlers) — no need to call ``stop_*`` / ``remove_*`` methods
        beforehand. Active tracing/video sessions are auto-finalized and their
        paths included in the result.

        **CDP mode**: only disconnects the Playwright session from the remote
        browser — pages, tabs, and contexts are left intact.

        Safe to call even when the browser was never started — returns
        ``"Browser closed."`` immediately without raising.

        Returns
        -------
        str
            Operation result message. Includes auto-saved trace/video paths
            when active sessions were finalized during close.
        """
        if self._playwright is None:
            return "Browser closed."

        # Publish the closing sentinel SYNCHRONOUSLY — before any await — so
        # the CLI daemon can short-circuit concurrent dispatches with a clean
        # BROWSER_CLOSED response instead of the handler racing against the
        # teardown and emitting NO_ACTIVE_PAGE. Critical: do not move this
        # below any `await`.
        self._closing = True

        # Ensure a close session directory exists so trace/video artifacts are
        # grouped together (e.g. close-{ts}-{rand}/trace.zip, video_1.webm).
        # The CLI daemon calls inspect_pending_close_artifacts() before close(),
        # but SDK users call close() directly — auto-create for them.
        if not self._close_session_dir:
            self.inspect_pending_close_artifacts()

        errors: List[str] = []
        shutdown_artifacts: Dict[str, List[str]] = {"trace": [], "video": []}
        context_key: Optional[str] = None
        # Recorder whose prepare_stop() has run but finalize() is deferred
        # until after Chrome exits (two-phase video shutdown).
        # Currently only one single-stream recorder is supported.
        _deferred_recorder: Optional[Any] = None
        # Deferred re-raise: if CancelledError / KeyboardInterrupt arrives during any
        # cleanup await we record it here, finish ALL cleanup steps, then re-raise at
        # the very end.  This ensures no Playwright/Chromium process is left orphaned
        # just because one step was interrupted.
        _pending_cancel: Optional[BaseException] = None
        _is_cdp = self._cdp_url is not None

        # Auto-stop active tracing before context/page teardown so trace data is saved.
        if self._context:
            context_key = _get_context_key(self._context)
            if self._tracing_state.get(context_key):
                output_path: Optional[str] = None
                try:
                    # Reuse pre-allocated path from inspect_pending_close_artifacts() if available
                    output_path = self._preallocated_trace_path
                    self._preallocated_trace_path = None
                    if output_path is None:
                        os.makedirs(BRIDGIC_TMP_DIR, exist_ok=True)
                        fd, output_path = tempfile.mkstemp(
                            suffix=".zip",
                            prefix="browser_trace_",
                            dir=str(BRIDGIC_TMP_DIR),
                        )
                        os.close(fd)
                    await asyncio.wait_for(
                        self._context.tracing.stop(path=output_path),
                        timeout=self._TRACE_STOP_TIMEOUT,
                    )
                    shutdown_artifacts["trace"].append(os.path.abspath(output_path))
                except asyncio.TimeoutError:
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception as cleanup_exc:
                            errors.append(f"tracing.tmp_cleanup: {cleanup_exc}")
                    errors.append(
                        f"tracing.stop: timeout after {self._TRACE_STOP_TIMEOUT:.1f}s"
                    )
                except Exception as e:
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception as cleanup_exc:
                            errors.append(f"tracing.tmp_cleanup: {cleanup_exc}")
                    errors.append(f"tracing.stop: {e}")
                except BaseException as e:
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception as cleanup_exc:
                            errors.append(f"tracing.tmp_cleanup: {cleanup_exc}")
                    errors.append(f"tracing.stop: {e}")
                    if _pending_cancel is None:
                        _pending_cancel = e
                finally:
                    self._tracing_state[context_key] = False

            # Two-phase video recorder shutdown.
            #
            # Phase 1 (here, before Chrome exits): prepare_stop() each
            # recorder — stops the CDP screencast, pads frames, detaches
            # the CDP session.  Fast (~milliseconds per recorder).
            #
            # Phase 2 (after Chrome exits): finalize() each recorder —
            # flushes the frame queue to ffmpeg and waits for the process
            # to write the .webm file.  Slow (seconds), but Chrome is
            # already dead so user_data_dir is released.
            #
            # Why two phases: the old single-phase stop() held Chrome
            # alive while 50 ffmpeg processes fought for CPU, blocking
            # user_data_dir release.  Splitting lets Chrome exit ASAP.
            #
            # Why we snapshot the dict before awaiting:
            #   stop_video() and close() can race in the daemon flow. We
            #   clear the dict first so the other path observes "no work
            #   left" and skips the duplicate stop() call.
            if self._video_recorder is not None or self._video_session is not None:
                # Detach the context "page" listener so new pages aren't
                # auto-started during shutdown.
                if self._video_session:
                    _listener = self._video_session.get("page_listener")
                    if _listener is not None:
                        try:
                            self._context.remove_listener("page", _listener)
                        except Exception:
                            pass
                _recorder = self._video_recorder
                self._video_recorder = None
                self._video_session = None

                # Phase 1: prepare_stop() the single recorder (fast).
                if _recorder is not None:
                    try:
                        await asyncio.wait_for(
                            _recorder.prepare_stop(),
                            timeout=self._VIDEO_PREPARE_STOP_TIMEOUT,
                        )
                    except Exception as _pr:
                        logger.warning(
                            "[close] prepare_stop failed: %s(%r)",
                            type(_pr).__name__, str(_pr),
                        )
                        _recorder._is_stopped = True
                        _recorder._cdp_session = None
                    except BaseException as _pr:
                        logger.warning("[close] prepare_stop cancelled: %s", _pr)
                        _recorder._is_stopped = True
                        _recorder._cdp_session = None
                        if _pending_cancel is None:
                            _pending_cancel = _pr

                    # Stash for Phase 2 (runs after Chrome exits).
                    _deferred_recorder = _recorder

            logger.debug("[close] Phase 1 done, clearing page state")
            # Always clear page-scoped listeners/caches for every context page.
            for page in list(self._context.pages):
                self._clear_page_scoped_state(page, errors)
        else:
            self._clear_page_scoped_state(self._page, errors)

        logger.debug("[close] disconnecting browser")
        # Detach download manager before context closes to remove handlers.
        # Mirror the attach strategy:
        # - Borrowed CDP context: handler was page-scoped on the bridgic tab,
        #   so detach at the page level (detach_from_context would no-op
        #   since the context was never attached).
        # - All other modes: handler was context-scoped, detach at context.
        if self._download_manager:
            if _is_cdp and not self._cdp_context_owned and self._page:
                try:
                    self._download_manager.detach_from_page(self._page)
                except Exception as e:
                    errors.append(f"download_manager.detach_page: {e}")
            elif self._context:
                try:
                    self._download_manager.detach_from_context(self._context)
                except Exception as e:
                    errors.append(f"download_manager.detach: {e}")

        # Close every page in parallel.
        # CDP mode: skip page cleanup entirely — just disconnect.
        #   The remote browser manages its own tab lifecycle.
        # Launch / persistent: close all pages explicitly before context close.
        #
        # C2: `self._page` is NOT nulled here; we keep the reference alive
        # until all page.close() awaits return. Nulling early was the root
        # cause of NO_ACTIVE_PAGE races with in-flight dispatch. Now any
        # tool method that still sees `self._page` will hit Playwright's
        # "Target closed" error (mapped to BROWSER_CLOSED by the daemon).
        if self._context and not _is_cdp:
            all_pages = list(self._context.pages)
            if all_pages:
                page_results = await asyncio.gather(
                    *(asyncio.wait_for(
                        p.close(run_before_unload=False),
                        timeout=self._PAGE_CLOSE_TIMEOUT,
                    ) for p in all_pages),
                    return_exceptions=True,
                )
                for r in page_results:
                    if isinstance(r, BaseException):
                        if not isinstance(r, Exception) and _pending_cancel is None:
                            _pending_cancel = r
                        elif isinstance(r, Exception):
                            errors.append(f"page.close: {r}")
        # All pages are now closed at Playwright level. Safe to release our
        # own handle — no dispatch can mistake this for a "not yet started"
        # state because `_closing` has been True since the very top.
        self._page = None

        # Close context.
        # - Launch / persistent: close context (auto-closes browser).
        # - CDP owned (`_cdp_context_owned=True`): bridgic created the context
        #   in _start() because `browser.contexts` was empty on connect. Close
        #   it explicitly; otherwise the context leaks on the remote Chrome for
        #   its entire lifetime (frequent connect/disconnect cycles = OOM).
        # - CDP borrowed (`_cdp_context_owned=False`): the user owns the
        #   context — release the local reference but never close it, so their
        #   existing tabs survive the disconnect.
        _close_context_now = bool(self._context) and (
            not _is_cdp or self._cdp_context_owned
        )
        if _close_context_now:
            _context = self._context
            self._context = None
            try:
                await asyncio.wait_for(
                    _context.close(),
                    timeout=self._CONTEXT_CLOSE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                errors.append(
                    f"context.close: timeout after {self._CONTEXT_CLOSE_TIMEOUT:.1f}s"
                )
                # Launch / persistent only: context.close() hung — force-kill
                # the Playwright driver so browser.close() / playwright.stop()
                # don't cascade.  In CDP mode the driver and remote Chrome
                # share the same WS channel; killing it would orphan the remote
                # browser from future disconnect signals.
                if not _is_cdp and self._playwright:
                    _playwright = self._playwright
                    self._playwright = None
                    self._browser = None  # browser dies with driver
                    await self._force_kill_playwright_driver(_playwright)
            except Exception as e:
                errors.append(f"context.close: {e}")
            except BaseException as e:
                errors.append(f"context.close: {e}")
                if _pending_cancel is None:
                    _pending_cancel = e
        elif self._context:
            # CDP borrowed mode: release reference without closing.
            self._context = None

        # Close browser.
        # - Normal launch mode: closes browser process.
        # - Persistent context mode: browser is None or already closed via context.
        # - CDP mode: close() disconnects the Playwright session without killing the
        #   remote Chrome process (the process continues running after disconnect).
        if self._browser:
            _browser = self._browser
            self._browser = None
            try:
                await asyncio.wait_for(
                    _browser.close(),
                    timeout=self._BROWSER_CLOSE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                errors.append(
                    f"browser.close: timeout after {self._BROWSER_CLOSE_TIMEOUT:.1f}s"
                )
            except Exception as e:
                errors.append(f"browser.close: {e}")
            except BaseException as e:
                errors.append(f"browser.close: {e}")
                if _pending_cancel is None:
                    _pending_cancel = e

        if self._playwright:
            _playwright = self._playwright
            self._playwright = None
            try:
                await asyncio.wait_for(
                    _playwright.stop(),
                    timeout=self._PLAYWRIGHT_STOP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                errors.append(
                    f"playwright.stop: timeout after {self._PLAYWRIGHT_STOP_TIMEOUT:.1f}s"
                )
                await self._force_kill_playwright_driver(_playwright)
            except Exception as e:
                errors.append(f"playwright.stop: {e}")
            except BaseException as e:
                errors.append(f"playwright.stop: {e}")
                if _pending_cancel is None:
                    _pending_cancel = e

        # Phase 2: finalize() the deferred video recorder.
        # Chrome is dead, user_data_dir is released.  Now flush the ffmpeg
        # frame queue.
        if _deferred_recorder is not None:
            logger.info("[close] Phase 2: finalize single recorder")
            try:
                rec_path: str = await asyncio.wait_for(
                    _deferred_recorder.finalize(),
                    timeout=self._VIDEO_FINALIZE_TIMEOUT,
                )
                if self._close_session_dir:
                    dest = os.path.join(self._close_session_dir, "video.webm")
                    self._move_video_local(Path(rec_path), dest)
                    shutdown_artifacts["video"].append(dest)
                else:
                    shutdown_artifacts["video"].append(rec_path)
            except asyncio.TimeoutError:
                errors.append(
                    f"video_recorder.finalize: timeout after "
                    f"{self._VIDEO_FINALIZE_TIMEOUT:.1f}s"
                )
            except Exception as _fin_err:
                errors.append(f"video_recorder.finalize: {_fin_err}")
            except BaseException as _fin_err:
                errors.append(f"video_recorder.finalize: {_fin_err}")
                if _pending_cancel is None:
                    _pending_cancel = _fin_err
            if context_key is not None:
                self._video_state.pop(context_key, None)

        # Clear snapshot cache
        self._last_snapshot = None
        self._last_snapshot_url = None
        self._cancel_prefetch()
        self._last_shutdown_artifacts = shutdown_artifacts
        self._last_shutdown_errors = list(errors)

        # Clear context-scoped state caches once the context is gone.
        if context_key is not None:
            self._tracing_state.pop(context_key, None)
            self._video_state.pop(context_key, None)

        # Flush all remaining state so a stopped instance holds no stale refs.
        # NOTE: _close_session_dir is intentionally preserved (like
        # _last_shutdown_artifacts / _last_shutdown_errors) so the daemon's
        # _write_close_report() can read it after close() returns.
        self._console_messages.clear()
        self._network_requests.clear()
        self._console_handlers.clear()
        self._network_handlers.clear()
        self._dialog_handlers.clear()
        self._tracing_state.clear()
        self._video_state.clear()

        trace_paths = shutdown_artifacts.get("trace", [])
        video_paths = shutdown_artifacts.get("video", [])
        if errors:
            lines = ["Browser closed with warnings", "Shutdown warnings:"]
            lines.extend(errors)
        else:
            lines = ["Browser closed successfully"]
        if trace_paths:
            lines.append("Auto-saved trace files:")
            lines.extend(trace_paths)
        if video_paths:
            lines.append("Auto-saved video files:")
            lines.extend(video_paths)
        result = "\n".join(lines)

        if errors:
            logger.warning(f"Browser closed with errors: {errors}")
        else:
            logger.info("Browser closed")

        # Write close-report.json into the session dir so SDK and CLI
        # produce identical artifacts.  The daemon's _write_close_report()
        # may overwrite this later with additional daemon-level info
        # (e.g. browser.close() overall timeout).
        self._write_close_report(errors)

        if _pending_cancel is not None:
            raise _pending_cancel

        return result

    async def __aenter__(self) -> "Browser":
        """Async context manager entry - starts the browser.

        Usage:
            async with Browser(headless=True) as browser:
                await browser.navigate_to("https://example.com")
                # Browser is automatically closed when exiting the context
        """
        await self._start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - closes the browser."""
        await self.close()

    # ==================== Page Management ====================

    async def navigate_to(
        self,
        url: str,
        wait_until: Literal["domcontentloaded", "load", "networkidle", "commit"] = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Navigate to URL in current tab.

        Parameters
        ----------
        url : str
            URL to navigate to. Auto-prepends "http://" if missing protocol.
            Schemes "data:", "about:", "javascript:", and "vbscript:" are passed
            through unchanged. URLs starting with "/" are passed as-is.
        wait_until : str, default "domcontentloaded"
            When to consider navigation complete.
            - "domcontentloaded": DOM is parsed (fast, recommended for SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds. Defaults to Playwright's 30s.

        Returns
        -------
        str
            "Navigated to: <actual_url>" where actual_url is the final URL
            after any redirects.

        Raises
        ------
        InvalidInputError
            If url is empty.
        StateError
            If context is unavailable after auto-start (should not normally occur).
        OperationError
            If navigation fails (network error, timeout, etc.).
        """
        try:
            await self._ensure_started()
            logger.info(f"[navigate_to] start url={url}")

            url = url.strip()
            if not url:
                _raise_invalid_input("URL cannot be empty", code="URL_EMPTY")

            url_lower = url.lower()
            has_scheme = "://" in url or url_lower.startswith(("data:", "about:", "javascript:", "vbscript:"))
            if not has_scheme:
                if not url.startswith("/"):
                    url = f"http://{url}"
                # else: URLs starting with '/' are absolute paths; passed as-is and will
                # fail at navigation time with a clear Playwright error (intentional).

            if not self._page:
                # All tabs were closed (e.g. via close_tab); _context is still alive.
                logger.info("No page is open, creating a new page in existing context")
                self._page = await self._context.new_page()
                await self._switch_video_to_page(self._page)

            kwargs: Dict[str, Any] = {"wait_until": wait_until}
            if timeout is not None:
                kwargs["timeout"] = timeout * 1000.0
            await self._page.goto(url, **kwargs)
            # Invalidate snapshot cache and any in-flight pre-warm.
            self._last_snapshot = None
            self._last_snapshot_url = None
            self._cancel_prefetch()
            page = await self.get_current_page()
            actual_url = page.url if page else url
            result = f"Navigated to: {actual_url}"
            logger.info(f"[navigate_to] done {result}")

            # Kick off background snapshot pre-warm so the first snapshot
            # call after navigation returns instantly (cache hit).
            if self._page is not None:
                self._prefetch_options = SnapshotOptions(interactive=True, full_page=True)
                self._prefetch_url = actual_url
                # Snapshot the gen AT SCHEDULING TIME so the task can detect a
                # subsequent _cancel_prefetch (which bumps the gen) and refuse
                # to commit its stale result.
                _my_gen = self._prefetch_gen
                try:
                    self._prefetch_task = asyncio.ensure_future(
                        self._pre_warm_snapshot(self._page, _my_gen)
                    )
                except Exception as _e:
                    # Non-fatal: pre-warm is best-effort (e.g., no running loop in tests)
                    logger.debug("[navigate_to] pre-warm scheduling failed: %s", _e)

            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Navigation failed: {str(e)}"
            logger.error(f"[navigate_to] {error_msg}")
            _raise_operation_error(error_msg)

    async def _new_page(
        self,
        url: Optional[str] = None,
        wait_until: Literal["domcontentloaded", "load", "networkidle", "commit"] = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> Page:
        if self._context is None:
            _raise_state_error(
                "No browser context is open. Use navigate_to() to start the browser first.",
                code="NO_BROWSER_CONTEXT",
            )
        self._page = await self._context.new_page()
        await self._switch_video_to_page(self._page)
        if url:
            await self.navigate_to(url, wait_until=wait_until, timeout=timeout)
        await self._page.bring_to_front()
        return self._page

    async def _cdp_navigate_history(self, page: "Page", delta: int) -> None:
        """Navigate browser history by *delta* (-1 = back, +1 = forward) using a
        raw CDPSession, bypassing ``page.go_back/forward()`` which relies on
        Playwright's ``_mainContext()`` tracking.  That tracking can hang on tabs
        opened before bridgic attached (CDP borrowed mode).
        """
        session = None
        try:
            session = await self._context.new_cdp_session(page)
            history = await asyncio.wait_for(
                session.send("Page.getNavigationHistory"),
                timeout=5.0,
            )
            current_idx = history.get("currentIndex", 0)
            entries = history.get("entries", [])
            target_idx = current_idx + delta
            if target_idx < 0 or target_idx >= len(entries):
                direction = "back" if delta < 0 else "forward"
                _raise_state_error(
                    f"Cannot navigate {direction}: no history entry",
                    code="NO_HISTORY_ENTRY",
                    retryable=False,
                )
            entry_id = entries[target_idx]["id"]
            await asyncio.wait_for(
                session.send("Page.navigateToHistoryEntry", {"entryId": entry_id}),
                timeout=15.0,
            )
        finally:
            if session:
                try:
                    await session.detach()
                except Exception:
                    pass
        # Wait for page to reach domcontentloaded; ignore timeout (navigation may
        # already be complete when we get here for cached/fast pages).
        try:
            await asyncio.wait_for(
                page.wait_for_load_state("domcontentloaded"),
                timeout=10.0,
            )
        except Exception:
            pass

    async def _get_page_title(self, page: Page) -> str:
        """Return the title of *page*, handling CDP borrowed-mode pages correctly.

        ``page.title()`` internally calls Playwright's ``frame._mainContext()``,
        which waits on a Promise that is resolved when Playwright sees the CDP
        ``Runtime.executionContextCreated`` event.  For **pre-existing tabs**
        when bridgic connects via ``connect_over_cdp()``, Playwright may have
        missed that event (it fired before Playwright registered its listener),
        so the Promise never resolves and ``page.title()`` hangs indefinitely.

        In CDP borrowed-mode we bypass Playwright's context-tracking entirely by
        opening a fresh ``CDPSession`` directly to the target and sending
        ``Runtime.evaluate`` ourselves.  Chrome responds immediately regardless
        of Playwright's internal state.  For pages that genuinely cannot run JS
        (e.g. ``chrome://`` internal pages) we fall back to the URL.
        """
        if self._cdp_url and not self._cdp_context_owned and self._context:
            session = None
            try:
                session = await self._context.new_cdp_session(page)
                result = await asyncio.wait_for(
                    session.send(
                        "Runtime.evaluate",
                        {"expression": "document.title", "returnByValue": True},
                    ),
                    timeout=5.0,
                )
                return result.get("result", {}).get("value", "") or page.url
            except Exception:
                return page.url
            finally:
                if session:
                    try:
                        await session.detach()
                    except Exception:
                        pass
        return await page.title()

    async def get_page_desc(self, page: Optional[Page] = None) -> Optional[PageDesc]:
        if not page:
            page = self._page
        if not page:
            logger.warning("No page is open")
            return None
        page_id = generate_page_id(page)
        title = await self._get_page_title(page)
        page_desc = PageDesc(
            url=page.url,
            title=title,
            page_id=page_id,
        )
        return page_desc

    async def get_all_page_descs(self) -> List[PageDesc]:
        pages = self.get_pages()
        if not pages:
            return []

        async def _safe_desc(p: Page) -> Optional[PageDesc]:
            try:
                page_id = generate_page_id(p)
                title = await self._get_page_title(p)
                return PageDesc(url=p.url, title=title, page_id=page_id)
            except Exception:
                return None

        results = await asyncio.gather(*(_safe_desc(p) for p in pages))
        return [d for d in results if d is not None]

    def get_pages(self) -> List[Page]:
        """Return all pages in the current browser context.

        In CDP mode bridgic operates as a guest on the remote browser, so all
        tabs — including pre-existing user tabs and pop-ups spawned by pages
        bridgic was driving — are part of the session and are reachable via
        ``get_tabs`` / ``switch_tab``.
        """
        if not self._context:
            return []
        return self._context.pages

    async def switch_to_page(self, page_id: str) -> tuple[bool, str]:
        """Switch to a page by its page_id.

        Parameters
        ----------
        page_id : str
            The page identifier of the target page.

        Returns
        -------
        tuple[bool, str]
            A tuple of ``(success, message)``.
        """
        if not self._context:
            logger.warning("No context is open, can't switch to page")
            return False, "No context is open, can't switch to page"
        pages = self.get_pages()
        page = find_page_by_id(pages=pages, page_id=page_id)
        if not page:
            logger.warning(f"Page with page_id '{page_id}' not found")
            return False, f"Page with page_id '{page_id}' not found"
        await page.bring_to_front()
        self._page = page
        await self._switch_video_to_page(page)
        # Clear snapshot cache after switching pages
        self._last_snapshot = None
        self._last_snapshot_url = None
        self._cancel_prefetch()
        title = await self._get_page_title(page)
        return True, f"Switched to tab {page_id}: {page.url} (title: {title})"

    async def _close_page(self, page: Page | str) -> tuple[bool, str]:
        """Close a page by Page object or page_id.

        Parameters
        ----------
        page : playwright.async_api.Page | str
            Either a `Page` object or a page_id string.

        Returns
        -------
        tuple[bool, str]
            A tuple of ``(success, message)``.
        """
        if not self._context:
            logger.warning("No context is open, can't close page")
            return False, "No context is open, can't close page"
        if isinstance(page, str):
            page_id = page
            pages = self.get_pages()
            page = find_page_by_id(pages=pages, page_id=page_id)
            if not page:
                logger.warning(f"Page with page_id '{page_id}' not found")
                return False, f"Page with page_id '{page_id}' not found"
        else:
            # If a Page object is passed, generate page_id
            page_id = generate_page_id(page)
        if not page:
            logger.warning("Page is None, can't close")
            return False, "Page is None, can't close"

        # If the page being closed is the one currently recorded,
        # switch the single-stream recorder to a remaining page BEFORE
        # closing — the CDP session is bound to this page and will die
        # once the page is gone.
        if (
            self._video_recorder is not None
            and not self._video_recorder.is_stopped
            and self._video_recorder.current_page == page
        ):
            remaining = [p for p in self.get_pages() if p != page and not p.is_closed()]
            if remaining:
                try:
                    await self._video_recorder.switch_page(remaining[0])
                    logger.debug("[_close_page] video switched to remaining page")
                except Exception as e:
                    logger.debug("[_close_page] video switch error: %s", e)
            else:
                # Last page — stop screencast but keep ffmpeg alive for finalize.
                await self._video_recorder.detach_screencast()

        await page.close()

        # If the closed page is the current page, switch to another.
        if self._page == page:
            pages = self.get_pages()
            self._page = pages[0] if pages else None
            # Clear snapshot cache
            self._last_snapshot = None
            self._last_snapshot_url = None
            self._cancel_prefetch()

        if self._page:
            now_id = generate_page_id(self._page)
            now_title = await self._get_page_title(self._page)
            return True, f"Closed tab {page_id}. Now on {now_id}: {self._page.url} (title: {now_title})"
        return True, f"Closed tab {page_id}. No tabs remaining"

    async def get_page_size_info(self) -> Optional[PageSizeInfo]:
        if not self._page:
            logger.warning("No page is open")
            return None
        if not self._context:
            logger.warning("No context is open")
            return None
        try:
            # Use CDP Page.getLayoutMetrics directly — avoids page.evaluate() which hangs
            # indefinitely on pre-existing tabs in CDP borrowed mode (Playwright misses the
            # Runtime.executionContextCreated event for those tabs).
            session = None
            try:
                session = await self._context.new_cdp_session(self._page)
                metrics = await asyncio.wait_for(
                    session.send("Page.getLayoutMetrics"),
                    timeout=5.0,
                )
            finally:
                if session:
                    try:
                        await session.detach()
                    except Exception:
                        pass

            layout = metrics.get("cssLayoutViewport", {})
            content = metrics.get("cssContentSize", {})

            viewport_width = layout.get("clientWidth", 0)
            viewport_height = layout.get("clientHeight", 0)
            page_width = content.get("width", 0)
            page_height = content.get("height", 0)
            scroll_x = layout.get("pageX", 0)
            scroll_y = layout.get("pageY", 0)
            logger.debug("Page size info via CDP: vp=%dx%d page=%dx%d scroll=(%d,%d)",
                         viewport_width, viewport_height, page_width, page_height, scroll_x, scroll_y)

            pixels_above = scroll_y
            pixels_below = max(0, page_height - viewport_height - scroll_y)
            pixels_left = scroll_x
            pixels_right = max(0, page_width - viewport_width - scroll_x)
            
            return PageSizeInfo(
                viewport_width=viewport_width,
                viewport_height=viewport_height,
                page_width=page_width,
                page_height=page_height,
                scroll_x=scroll_x,
                scroll_y=scroll_y,
                pixels_above=pixels_above,
                pixels_below=pixels_below,
                pixels_left=pixels_left,
                pixels_right=pixels_right,
            )
        except Exception as e:
            logger.debug(f"Failed to get page size info: {e}")
            return None
    
    async def get_current_page(self) -> Optional[Page]:
        return self._page
    
    def get_current_page_url(self) -> Optional[str]:
        return self._page.url if self._page else None
    
    async def get_current_page_title(self) -> Optional[str]:
        """Get the title of the current page.

        Returns
        -------
        Optional[str]
            Page title, or None if no page is open.
        """
        if not self._page:
            return None
        return await self._get_page_title(self._page)

    async def _get_page_info(self) -> Optional[PageInfo]:
        if not self._page:
            logger.warning("No page is open")
            return None

        page_size_info, title = await asyncio.gather(self.get_page_size_info(), self.get_current_page_title())

        if page_size_info is None:
            logger.warning("Failed to get page size info")
            return None
        page_info = PageInfo(
            url=self.get_current_page_url(),
            title=title,
            **page_size_info.model_dump(),
        )
        return page_info

    async def get_full_page_info(self,
        interactive: bool = False,
        full_page: bool = True,
    ) -> Optional[FullPageInfo]:
        if not self._page:
            logger.warning("No page is open, can't get full page info")
            return None
        try:
            snapshot, page_info = await asyncio.gather(
                self.get_snapshot(interactive=interactive, full_page=full_page),
                self._get_page_info(),
                return_exceptions=True,
            )
            if isinstance(snapshot, BaseException) or snapshot is None:
                logger.warning("Failed to get snapshot")
                return None
            if isinstance(page_info, BaseException) or page_info is None:
                logger.warning("Failed to get page info")
                return None
            full_page_info = FullPageInfo(**page_info.model_dump(), tree=snapshot.tree)
            return full_page_info
        except Exception as e:
            logger.debug(f"Failed to get full page info: {e}")
            return None
    

    #########################################################
    # screenshot
    #########################################################
    async def _take_screenshot_raw(
        self,
        path: Optional[str | Path] = None,
        full_page: bool = False,
        **kwargs,
    ) -> Optional[bytes]:
        """Take a screenshot of the current page (raw bytes).

        Parameters
        ----------
        path : Optional[str | pathlib.Path], optional
            Optional file path to save the screenshot.
        full_page : bool, optional
            Whether to capture the full page or just the viewport. Default is False.
        **kwargs
            Additional screenshot options forwarded to Playwright.

        Returns
        -------
        Optional[bytes]
            Screenshot bytes, or None if no page is open.
        """
        if not self._page:
            logger.warning("No page is open, can't take screenshot")
            return None
        screenshot = await self._page.screenshot(
            path=path,
            full_page=full_page,
            **kwargs
        )
        return screenshot

    # ==================== Snapshot & Element Refs ====================

    async def get_snapshot(
        self,
        interactive: bool = False,
        full_page: bool = True,
    ) -> EnhancedSnapshot:
        """Get accessibility snapshot of the current page (low-level API).

        This is the underlying snapshot method.  For LLM agents and CLI use,
        prefer :meth:`get_snapshot_text` which returns a formatted, paginated
        string with a page header and truncation notice.

        The result's ``refs`` dict is the source of truth for all ``*_by_ref``
        tools.  After this call, element refs in the returned snapshot can be
        passed to :meth:`get_element_by_ref`, :meth:`click_element_by_ref`, etc.

        Parameters
        ----------
        interactive : bool, default False
            If True, only include interactive elements (buttons, links, inputs,
            checkboxes, elements with cursor:pointer, etc.) with a flattened
            single-level output.  Best for action selection.
        full_page : bool, default True
            If True (default), include all elements regardless of viewport
            position.  If False, only include elements within the viewport.

        Returns
        -------
        EnhancedSnapshot
            Object with:

            - ``.tree`` : str — accessibility tree as a multi-line string
              (lines like ``- button "Submit" [ref=8d4b03a9]``).
            - ``.refs`` : Dict[str, RefData] — maps ref IDs to locator data.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If snapshot generation fails.
        """
        try:
            if not self._page:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
            _wait_t0 = time.monotonic()
            async with self._snapshot_lock:
                _wait_elapsed = time.monotonic() - _wait_t0
                if _wait_elapsed > 0.1:
                    # Surfaces "command N was stuck behind snapshot of command N-1"
                    # situations in the log — the typical second-snapshot-back-to-back
                    # case on a large page.
                    logger.info(
                        "[get_snapshot] waited %.3fs for _snapshot_lock", _wait_elapsed
                    )
                options = SnapshotOptions(
                    interactive=interactive,
                    full_page=full_page,
                )
                if self._snapshot_generator is None:
                    self._snapshot_generator = SnapshotGenerator()
                current_url = self.get_current_page_url()

                # Check if the background pre-warm already computed this snapshot.
                if (self._prefetch_snapshot is not None
                        and self._prefetch_options == options
                        and self._prefetch_url == current_url):
                    logger.info("[get_snapshot] pre-warm cache hit — returning instantly")
                    cached = self._prefetch_snapshot
                    # One-shot: clear so the next call recomputes fresh.
                    self._prefetch_snapshot = None
                    self._last_snapshot = cached
                    self._last_snapshot_url = current_url
                    return cached

                # Pre-warm miss (either still running or different options).
                # If the task is for the same options and URL, wait for it
                # instead of duplicating the work.
                prefetch_task = self._prefetch_task
                if (prefetch_task is not None
                        and not prefetch_task.done()
                        and self._prefetch_options == options
                        and self._prefetch_url == current_url):
                    logger.info("[get_snapshot] pre-warm in progress — waiting for it")
                    try:
                        await prefetch_task
                        if (self._prefetch_snapshot is not None
                                and self._prefetch_options == options
                                and self._prefetch_url == current_url):
                            cached = self._prefetch_snapshot
                            self._prefetch_snapshot = None
                            self._last_snapshot = cached
                            self._last_snapshot_url = current_url
                            return cached
                    except Exception:
                        pass  # pre-warm failed; fall through to normal computation

                self._last_snapshot = await self._snapshot_generator.get_enhanced_snapshot_async(
                    self._page, options
                )
                self._last_snapshot_url = current_url
                return self._last_snapshot
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get snapshot: {str(e)}"
            logger.error(f"[get_snapshot] {error_msg}", exc_info=True)
            _raise_operation_error(error_msg)
    
    def _cancel_prefetch(self) -> None:
        """Cancel any in-flight pre-warm task and clear prefetch state.

        Must be called whenever navigation or page-switch invalidates the
        current page's snapshot (i.e. everywhere _last_snapshot is set to None).
        Uses getattr throughout so it is safe on Browser instances created via
        Browser.__new__() (test helpers that bypass __init__).

        Also bumps ``_prefetch_gen`` so any pre-warm task that returns from
        its await AFTER this point will see a stale generation and discard
        its result rather than clobber the new page's cache. (C4.)
        """
        self._prefetch_gen = getattr(self, '_prefetch_gen', 0) + 1
        task = getattr(self, '_prefetch_task', None)
        if task is not None and not task.done():
            task.cancel()
        self.__dict__.update(
            _prefetch_task=None,
            _prefetch_snapshot=None,
            _prefetch_options=None,
            _prefetch_url=None,
        )

    async def _pre_warm_snapshot(self, page: "AsyncPage", my_gen: int) -> None:  # type: ignore[name-defined]
        """Background task: compute interactive snapshot after navigation.

        Uses a dedicated _prefetch_generator instance so it never conflicts
        with the user-triggered _snapshot_generator (which is serialised by
        _snapshot_lock).  Result is written to _prefetch_snapshot; get_snapshot
        consumes it on a cache hit.

        The commit is guarded by two checks:

        1. ``my_gen == self._prefetch_gen`` — a monotonic counter bumped by
           ``_cancel_prefetch()``.  If a navigation/tab-switch happened while
           this task was awaiting, the generation differs and we discard.
        2. ``page.url == target_url`` and ``self._page is page`` — belt-and-
           suspenders identity check for the rare case where the page object
           is reused by Playwright across URL changes.

        The commit acquires ``_snapshot_lock`` so the writes happen atomically
        w.r.t. ``get_snapshot`` consumers.

        This is best-effort — any exception or cancellation is silently ignored.
        """
        try:
            # Brief settle: let DOMContentLoaded side-effects stabilize.
            await asyncio.sleep(0.5)

            options = SnapshotOptions(interactive=True, full_page=True)
            target_url = page.url

            if self._prefetch_generator is None:
                self._prefetch_generator = SnapshotGenerator()

            logger.info("[pre_warm] starting snapshot for %s", target_url)
            snapshot = await self._prefetch_generator.get_enhanced_snapshot_async(
                page, options
            )

            async with self._snapshot_lock:
                if my_gen != self._prefetch_gen:
                    logger.debug(
                        "[pre_warm] generation mismatch (own=%d current=%d); discarding result",
                        my_gen, self._prefetch_gen,
                    )
                    return
                if page.url != target_url or self._page is not page:
                    logger.debug("[pre_warm] URL changed during pre-warm; discarding result")
                    return
                self._prefetch_snapshot = snapshot
                self._prefetch_options = options
                self._prefetch_url = target_url
                logger.info("[pre_warm] snapshot ready for %s", target_url)
        except asyncio.CancelledError:
            logger.debug("[pre_warm] cancelled (navigation superseded)")
        except Exception as e:
            logger.debug("[pre_warm] failed (best-effort): %s", e)

    async def get_element_by_ref(self, ref: str, _fallback_depth: int = 0) -> Optional[Locator]:
        """Resolve a snapshot ref to a Playwright Locator.

        Parameters
        ----------
        ref : str
            Element ref from the last snapshot (e.g., "1f79fe5e", "8d4b03a9").
            Obtain refs by calling :meth:`get_snapshot` or :meth:`get_snapshot_text` first.
        _fallback_depth : int, optional
            Internal recursion guard for the recovery path. Do not pass this parameter.

        Returns
        -------
        Optional[Locator]
            Resolved Playwright ``Locator``, or ``None`` when:
            - No page is open (``start()`` not called or browser closed).
            - No snapshot has been taken yet.
            - ``ref`` is not present in the last snapshot.
            Returns ``None`` instead of raising so callers can decide how to handle
            a stale or unknown ref.

        Notes
        -----
        - When multiple elements share the same role+name, an automatic recovery
          path selects the nth visible match from the snapshot; a fresh snapshot
          via :meth:`get_snapshot` is the preferred fix for persistent ambiguity.
        - For elements inside iframes, the locator is scoped through the correct
          ``frame_locator`` chain derived from ``RefData.frame_path``.
        """
        if not self._page:
            logger.warning("No page is open, can't get element by ref")
            return None
        if self._last_snapshot is None:
            logger.warning("No snapshot is available, can't get element by ref, please get snapshot first")
            return None
        try:
            if self._snapshot_generator is None:
                self._snapshot_generator = SnapshotGenerator()

            ref_data = self._last_snapshot.refs.get(ref)

            # ── aria-ref fast-path ─────────────────────────────────────────────────
            # Playwright's aria-ref engine maps ephemeral IDs (e.g. "e369", "f1e5")
            # directly to live DOM element pointers populated during snapshotForAI.
            # O(1) lookup — no CSS reconstruction needed.
            #
            # Each frame stores its own _lastAriaSnapshotForQuery keyed by the FULL
            # prefixed ref (e.g. L1 frame stores "f1e5" → element).  For iframe
            # elements we therefore scope the locator to the correct frame first via
            # frame_locator chain — this ensures locator.evaluate() and all other
            # locator operations run in the element's own frame context, not the
            # main frame.  Main-frame elements (frame_path=None) use page directly.
            #
            # Falls through silently if stale (count=0) or engine unavailable.
            if ref_data and ref_data.playwright_ref:
                try:
                    ar_scope = self._page
                    if ref_data.frame_path:
                        for local_nth in ref_data.frame_path:
                            ar_scope = ar_scope.frame_locator("iframe").nth(local_nth)
                    ar_locator = ar_scope.locator(f"aria-ref={ref_data.playwright_ref}")
                    ar_count = await ar_locator.count()
                    if ar_count == 1:
                        logger.debug(
                            "[get_element_by_ref] aria-ref fast-path hit: ref=%s playwright_ref=%s frame_path=%s",
                            ref, ref_data.playwright_ref, ref_data.frame_path,
                        )
                        return ar_locator
                    # ar_count == 0 → snapshot is stale (DOM changed) — fall through
                    # ar_count > 1  → should never happen for a direct pointer — fall through
                    logger.debug(
                        "[get_element_by_ref] aria-ref stale (count=%d), falling through to CSS: ref=%s playwright_ref=%s",
                        ar_count, ref, ref_data.playwright_ref,
                    )
                except Exception as _ar_exc:
                    logger.debug(
                        "[get_element_by_ref] aria-ref exception (%s), falling through to CSS: ref=%s",
                        _ar_exc, ref,
                    )
            # ── aria-ref fast-path end ─────────────────────────────────────────────

            if ref_data is None:
                logger.debug("[get_element_by_ref] ref not found in snapshot: %s", ref)
            else:
                logger.debug(
                    "[get_element_by_ref] CSS path: ref=%s role=%s name=%r nth=%s frame_path=%s",
                    ref,
                    ref_data.role,
                    ref_data.name,
                    ref_data.nth,
                    ref_data.frame_path,
                )
            locator = self._snapshot_generator.get_locator_from_ref_async(
                self._page, ref, self._last_snapshot.refs
            )
            if locator:
                # Validate locator and expose ambiguity explicitly for debugging.
                count = await locator.count()
                if count == 1:
                    return locator
                elif count > 1:
                    can_recover_by_role_name = (
                        bool(ref_data and ref_data.name)
                        and ref_data.role not in SnapshotGenerator.ROLE_TEXT_MATCH_ROLES
                        and ref_data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES
                        and ref_data.role not in SnapshotGenerator.TEXT_LEAF_ROLES
                    )
                    if can_recover_by_role_name and ref_data:
                        scope = self._page
                        if ref_data.frame_path:
                            for local_nth in ref_data.frame_path:
                                scope = scope.frame_locator("iframe").nth(local_nth)
                        role_name_locator = scope.get_by_role(
                            ref_data.role,
                            name=ref_data.name,
                            exact=True,
                        )
                        role_name_count = await role_name_locator.count()
                        if role_name_count == 1:
                            logger.warning(
                                "Ref %s resolved to %d elements; recovered unique locator via role+name",
                                ref,
                                count,
                            )
                            return role_name_locator
                        if (
                            role_name_count > 1
                            and ref_data.nth is not None
                            and ref_data.nth < role_name_count
                        ):
                            logger.warning(
                                "Ref %s resolved to %d elements; recovered locator via role+name nth=%d",
                                ref,
                                count,
                                ref_data.nth,
                            )
                            return role_name_locator.nth(ref_data.nth)

                    # Only apply nth fallback when the locator key space matches
                    # the role:name key space used to compute nth.  For unnamed
                    # STRUCTURAL_NOISE_ROLES (child_text anchor) and TEXT_LEAF_ROLES
                    # the locator key space doesn't match.  Named STRUCTURAL_NOISE
                    # elements use CSS-scoped locators with nth already applied,
                    # so they won't reach this recovery path (count will be 0 or 1).
                    nth_keyspace_matches = (
                        ref_data
                        and ref_data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES
                        and ref_data.role not in SnapshotGenerator.TEXT_LEAF_ROLES
                    )
                    if (
                        nth_keyspace_matches
                        and ref_data.nth is not None
                        and ref_data.nth < count
                    ):
                        logger.warning(
                            "Ref %s resolved to %d elements; using snapshot nth=%d",
                            ref,
                            count,
                            ref_data.nth,
                        )
                        return locator.nth(ref_data.nth)

                    visible_matches: List[Locator] = []
                    for idx in range(count):
                        candidate = locator.nth(idx)
                        try:
                            if await candidate.is_visible():
                                visible_matches.append(candidate)
                        except Exception:
                            # Ignore transient visibility failures and keep probing.
                            continue

                    if len(visible_matches) == 1:
                        logger.warning(
                            "Ref %s resolved to %d elements; using the only visible match",
                            ref,
                            count,
                        )
                        return visible_matches[0]
                    if len(visible_matches) > 1:
                        logger.warning(
                            "Ref %s resolved to %d elements (%d visible); using first visible match",
                            ref,
                            count,
                            len(visible_matches),
                        )
                        return visible_matches[0]

                    logger.warning(
                        "Ref %s resolved to %d elements with no visible match; using first match",
                        ref,
                        count,
                    )
                    return locator.first
                else:
                    logger.warning("No element found by ref: %s (count=0)", ref)
                    if _fallback_depth == 0:
                        return await self._fallback_to_child_ref(ref)
                    return None
            else:
                logger.warning(f"Failed to get locator by ref: {ref}")
                if _fallback_depth == 0:
                    return await self._fallback_to_child_ref(ref)
                return None
        except Exception as e:
            logger.debug(f"Failed to get element by ref: {e}")
            return None

    async def _fallback_to_child_ref(self, parent_ref: str) -> Optional[Locator]:
        """Try to find a usable child ref when the parent ref's locator fails.

        Only activates for structural noise roles (generic, group, etc.)
        without name/text, where the locator is inherently fragile.
        """
        if self._last_snapshot is None:
            return None
        refs = self._last_snapshot.refs
        parent_data = refs.get(parent_ref)
        if not parent_data:
            return None

        has_text_signal = bool(parent_data.name or parent_data.text_content)
        if parent_data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES or has_text_signal:
            return None

        children = [
            (child_ref, child_data)
            for child_ref, child_data in refs.items()
            if child_data.parent_ref == parent_ref
        ]
        if not children:
            return None

        def _score(data) -> int:
            """Higher = better candidate for interaction."""
            s = 0
            if data.role in SnapshotGenerator.INTERACTIVE_ROLES:
                s += 10
            if data.name:
                s += 5
            elif data.text_content:
                s += 3
            if data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES:
                s += 2
            return s

        children.sort(key=lambda c: _score(c[1]), reverse=True)
        best_ref, best_data = children[0]

        if _score(best_data) == 0:
            return None

        if len(children) > 1 and _score(children[0][1]) == _score(children[1][1]):
            candidates = ", ".join(
                f"{r} ({d.name or d.text_content or d.role})"
                for r, d in children
                if _score(d) == _score(children[0][1])
            )
            logger.warning(
                "Ref %s (container) failed; multiple child candidates with equal priority: %s",
                parent_ref,
                candidates,
            )

        logger.info(
            "Ref %s (container) failed; falling back to child ref %s (%s)",
            parent_ref,
            best_ref,
            best_data.name or best_data.text_content or best_data.role,
        )
        return await self.get_element_by_ref(best_ref, _fallback_depth=1)

    async def get_element_by_prompt(self, prompt: str, llm: "OpenAILlm") -> Optional[Locator]:
        """Find element by natural language prompt and return Locator.

        Parameters
        ----------
        prompt : str
            Natural language description of the element to find
        llm : OpenAILlm
            LLM instance for element finding

        Returns
        -------
        Optional[Locator]
            Found element Locator, or None if not found
        """
        try:
            from bridgic.core.model.protocols import PydanticModel  # pyright: ignore[reportMissingImports]
            from bridgic.core.model.types import Message, Role  # pyright: ignore[reportMissingImports]
        except ModuleNotFoundError as exc:
            logger.warning(
                "get_element_by_prompt unavailable: missing module '%s'; "
                "install bridgic-core to enable prompt-based lookup.",
                exc.name or "bridgic.core",
            )
            return None
        except ImportError as exc:
            logger.warning(
                "get_element_by_prompt unavailable: failed to import bridgic.core model types: %s",
                exc,
            )
            return None
        
        snapshot = await self.get_snapshot()
        if snapshot is None:
            logger.warning(
                "get_element_by_prompt aborted: snapshot unavailable (prompt_len=%d)",
                len(prompt),
            )
            return None
        browser_state = snapshot.tree
        
        system_prompt = """You are an AI created to find an element on a page by a prompt.
<browser_state>
Interactive Elements: All interactive elements will be provided in format as:
- role "name" [ref=ref_id]

Examples:
- button "Submit" [ref=8d4b03a9]
- textbox "Email" [ref=d6a530b4]
- link "Learn more" [ref=1f79fe5e]

Note that:
- Only elements with [ref=...] are interactive
- ref is the identifier you should return
- The format is: - role "name" [ref=ref_id]
</browser_state>

Your task is to find an element ref (if any) that matches the prompt (written in <prompt> tag).

If none of the elements matches, return None.

Before you return the element ref, reason about the state and elements for a sentence or two."""
        
        class ElementResponse(BaseModel):
            element_ref: Optional[str] = None
        
        user_message = f"""<browser_state>
{browser_state}
</browser_state>
<prompt>
{prompt}
</prompt>
"""
        
        messages = [
            Message.from_text(system_prompt, role=Role.SYSTEM),
            Message.from_text(user_message, role=Role.USER),
        ]
        
        result = await llm.astructured_output(
            messages=messages,
            constraint=PydanticModel(model=ElementResponse),
        )
        
        element_ref = result.element_ref
        if element_ref is None:
            return None
        
        return await self.get_element_by_ref(element_ref)

    # ==================== State Tool ====================

    async def get_snapshot_text(
        self,
        limit: int = _DEFAULT_SNAPSHOT_LIMIT,
        interactive: bool = False,
        full_page: bool = True,
        file: Optional[str] = None,
    ) -> str:
        """Get the page accessibility tree as a formatted string with element refs.

        **Call this first** to obtain element refs (e.g., ``1f79fe5e``) before
        using any action tool (``click_element_by_ref``, ``input_text_by_ref``,
        etc.).  The returned string is what LLM agents and CLI users should
        consume; for the raw ``EnhancedSnapshot`` object see :meth:`get_snapshot`.

        Output format example::

            [Page: https://example.com | Example Domain]
            - heading "Example Domain" [ref=a1b2c3d4]
            - button "Submit" [ref=8d4b03a9]
            - textbox "Email" [ref=d6a530b4]

        Parameters
        ----------
        limit : int, optional
            Maximum number of characters to return.  Must be >= 1.
            Default is 10 000.  When the snapshot exceeds this limit,
            the full content is written to a file and only a notice with
            the file path is returned (no snapshot content).
        interactive : bool, optional
            If True, only include clickable/editable elements (buttons, links,
            inputs, checkboxes, elements with cursor:pointer, etc.).
            Best for action selection. Default is False.
        full_page : bool, optional
            If True (default), include elements outside the viewport.
            If False, only include elements within the current viewport.
        file : str or None, optional
            File path to write the full snapshot.  When provided, the
            snapshot is always saved to this file regardless of whether
            content exceeds ``limit``, and only a notice with the file
            path is returned (no snapshot content).  When ``None``
            (default), file is only written if content exceeds ``limit``,
            using an auto-generated path under
            ``~/.bridgic/bridgic-browser/snapshot/``.

        Returns
        -------
        str
            Page header followed by the accessibility tree.  Lines with
            ``[ref=...]`` are interactive elements.

            When the snapshot exceeds ``limit`` or ``file`` is provided,
            a ``[notice]`` with the file path is returned instead of
            the snapshot content.

        Raises
        ------
        InvalidInputError
            If ``limit`` is less than 1, or ``file`` is empty/whitespace-only,
            contains null bytes, or points to an existing directory.
        OperationError
            If snapshot generation fails.
        """
        try:
            if limit < 1:
                _raise_invalid_input(
                    "limit must be >= 1",
                    code="INVALID_LIMIT",
                    details={"limit": limit},
                )

            if file is not None:
                if not file.strip():
                    _raise_invalid_input(
                        "file path must not be empty",
                        code="INVALID_FILE_PATH",
                        details={"file": file},
                    )
                if "\x00" in file:
                    _raise_invalid_input(
                        "file path must not contain null bytes",
                        code="INVALID_FILE_PATH",
                        details={"file": repr(file)},
                    )
                if Path(file).is_dir():
                    _raise_invalid_input(
                        f"file path is an existing directory: {file}",
                        code="INVALID_FILE_PATH",
                        details={"file": file},
                    )

            _page = getattr(self, "_page", None)

            async def _get_title() -> str:
                if not _page:
                    return ""
                return await self._get_page_title(_page)

            snapshot, page_title = await asyncio.gather(
                self.get_snapshot(interactive=interactive, full_page=full_page),
                _get_title(),
                return_exceptions=True,
            )
            if isinstance(snapshot, BaseException) or snapshot is None:
                _raise_operation_error("Failed to get snapshot")
            if isinstance(page_title, BaseException):
                page_title = ""
            page_url = _page.url if _page else ""
            header = f"[Page: {page_url} | {page_title}]\n"
            full_text = snapshot.tree

            total_length = len(full_text)

            if total_length > limit or file:
                file_content = header + full_text
                total_chars = len(file_content)
                total_lines = file_content.count("\n") + (1 if file_content and not file_content.endswith("\n") else 0)
                snapshot_file = self._write_snapshot_file(file_content, file)
                notice = (
                    f"[notice] Snapshot file ({total_chars} characters, {total_lines} lines) "
                    f"saved to: {snapshot_file}\n"
                )
                logger.info("[get_snapshot_text] Snapshot saved to %s", snapshot_file)
                return header + notice

            logger.info("[get_snapshot_text] Successfully retrieved interface information")
            return header + full_text
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get interface information: {e}"
            logger.error(f"[get_snapshot_text] {error_msg}")
            _raise_operation_error(error_msg)

    def _write_snapshot_file(self, content: str, file: Optional[str] = None) -> str:
        """Write snapshot content to a file and return the absolute path.

        Callers must validate ``file`` before calling (get_snapshot_text does
        this).  When ``file`` is None, an auto-generated path under
        BRIDGIC_SNAPSHOT_DIR is used.
        """
        import random
        from datetime import datetime

        if file:
            filepath = Path(file)
        else:
            snapshot_dir = BRIDGIC_SNAPSHOT_DIR
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            rand_suffix = f"{random.randint(0, 0xffff):04x}"
            filename = f"snapshot-{ts}-{rand_suffix}.txt"
            filepath = snapshot_dir / filename

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        if sys.platform != "win32":
            try:
                filepath.chmod(0o600)
            except OSError:
                pass
        return str(filepath.resolve())

    # ==================== Navigation Tools ====================

    async def search(
        self,
        query: str,
        engine: str = "duckduckgo",
        wait_until: str = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Search using a search engine.

        Parameters
        ----------
        query : str
            Query string to search.
        engine : str, optional
            "duckduckgo" (default), "google", or "bing".
        wait_until : str, default "domcontentloaded"
            When to consider navigation complete:
            - "domcontentloaded": DOM parsed (fast, good for modern SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds. Defaults to Playwright's 30s.

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info(f"[search] start engine={engine} query={query!r}")

            query = query.strip()
            if not query:
                _raise_invalid_input("Search query cannot be empty", code="QUERY_EMPTY")
            engine = engine.strip().lower() if engine else "duckduckgo"

            import urllib.parse

            encoded_query = urllib.parse.quote_plus(query)

            search_engines = {
                'duckduckgo': f'https://duckduckgo.com/?q={encoded_query}',
                'google': f'https://www.google.com/search?q={encoded_query}&udm=14',
                'bing': f'https://www.bing.com/search?q={encoded_query}',
            }

            if engine not in search_engines:
                error_msg = f'Unsupported search engine: {engine}. Options: duckduckgo, google, bing'
                logger.error(f'[search] {error_msg}')
                _raise_invalid_input(
                    error_msg,
                    code="UNSUPPORTED_SEARCH_ENGINE",
                    details={"engine": engine},
                )

            search_url = search_engines[engine]

            try:
                await self.navigate_to(search_url, wait_until=wait_until, timeout=timeout)
                result = f"Searched on {engine.title()}: '{query}'"
                logger.info(f"[search] done {result}")
                return result
            except BridgicBrowserError:
                raise
            except Exception as e:
                logger.error(f"[search] failed engine={engine} error={type(e).__name__}: {e}")
                error_msg = f'Search on {engine} failed for "{query}": {str(e)}'
                _raise_operation_error(error_msg)
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Search failed: {str(e)}"
            logger.error(f"[search] failed error={type(e).__name__}: {error_msg}")
            _raise_operation_error(error_msg)

    async def go_back(self) -> str:
        """Navigate back to the previous page in the tab's history.

        Returns
        -------
        str
            "Navigated back to: <url>" on success.

        Raises
        ------
        StateError
            If no active page is available, or if there is no previous page
            in history (error code "NO_HISTORY_ENTRY", retryable=False).
        OperationError
            If navigation fails for another reason.
        """
        try:
            logger.info(f"[go_back] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            if self._cdp_url and not self._cdp_context_owned and self._context:
                # CDP borrowed mode: page.go_back() hangs because Playwright's
                # navigation tracking relies on _mainContext() which is broken for
                # pre-existing tabs. Use CDPSession to navigate directly.
                await self._cdp_navigate_history(page, delta=-1)
            else:
                await asyncio.wait_for(
                    page.go_back(wait_until="domcontentloaded"),
                    timeout=20.0,
                )
            result = f"Navigated back to: {page.url}"
            logger.info(f"[go_back] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to navigate back: {str(e)}"
            logger.error(f"[go_back] {error_msg}")
            if "Cannot navigate" in str(e) or "no previous entry" in str(e):
                result = "Cannot navigate back: no previous page in history"
                logger.info(f"[go_back] {result}")
                _raise_state_error(result, code="NO_HISTORY_ENTRY", retryable=False)
            _raise_operation_error(error_msg)

    async def go_forward(self) -> str:
        """Navigate forward to the next page in the tab's history.

        Returns
        -------
        str
            "Navigated forward to: <url>" on success.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If navigation fails (e.g., no forward history entry).
        """
        try:
            logger.info(f"[go_forward] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
            if self._cdp_url and not self._cdp_context_owned and self._context:
                await self._cdp_navigate_history(page, delta=+1)
            else:
                await asyncio.wait_for(
                    page.go_forward(wait_until="domcontentloaded"),
                    timeout=20.0,
                )
            result = f"Navigated forward to: {page.url}"
            logger.info(f"[go_forward] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to navigate forward: {str(e)}"
            logger.error(f"[go_forward] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Page and Tab Management Tools ====================

    async def reload_page(
        self,
        wait_until: str = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Reload the current page.

        Parameters
        ----------
        wait_until : str, default "domcontentloaded"
            When to consider reload complete:
            - "domcontentloaded": DOM parsed (fast, good for modern SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds. Defaults to Playwright's 30s.

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info("[reload_page] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
            kwargs: Dict[str, Any] = {"wait_until": wait_until}
            if timeout is not None:
                kwargs["timeout"] = timeout * 1000.0
            await page.reload(**kwargs)
            title = await self._get_page_title(page)
            result = f"Page reloaded: {page.url} (title: {title})"
            logger.info(f"[reload_page] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to reload page: {str(e)}"
            logger.error(f"[reload_page] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_current_page_info(self) -> str:
        """Get current page info: URL, title, viewport size, scroll position.

        Returns
        -------
        str
            A single-line string in the format::

                url='<url>', title='<title>', viewport=<W>x<H>, page=<PW>x<PH>, scroll=(<x>,<y>)

            where ``viewport`` is the visible area (pixels), ``page`` is the
            total scrollable content size (pixels), and ``scroll`` is the
            current scroll offset from the top-left corner (pixels).

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If page info retrieval fails.
        """
        try:
            logger.info(f"[get_current_page_info] start")

            page_info = await self._get_page_info()
            if page_info is None:
                error_msg = "No active page available"
                logger.error(f"[get_current_page_info] {error_msg}")
                _raise_operation_error(error_msg)
            result = (
                f"url={page_info.url!r}, title={page_info.title!r}, "
                f"viewport={page_info.viewport_width}x{page_info.viewport_height}, "
                f"page={page_info.page_width}x{page_info.page_height}, "
                f"scroll=({page_info.scroll_x},{page_info.scroll_y})"
            )
            logger.info(f"[get_current_page_info] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get current page info: {str(e)}"
            logger.error(f"[get_current_page_info] {error_msg}")
            _raise_operation_error(error_msg)

    async def press_key(self, key: str) -> str:
        """Press a keyboard key or combination (e.g., "Enter", "Control+A").

        Parameters
        ----------
        key : str
            Key name or combination (e.g., "Tab", "Control+C", "Shift+Tab").

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info(f"[press_key] start key={key}")

            key = key.strip()
            if not key:
                _raise_invalid_input("Key name cannot be empty", code="KEY_EMPTY")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.keyboard.press(key)
            result = f"Pressed key: {key}"
            logger.info(f"[press_key] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to press key: {str(e)}"
            logger.error(f"[press_key] {error_msg}")
            _raise_operation_error(error_msg)

    async def scroll_to_text(self, text: str) -> str:
        """Scroll the page to make the specified text visible.

        Finds the first occurrence of the text on the page and scrolls it
        into view.  Unlike :meth:`scroll_element_into_view_by_ref`, this
        method locates elements by their visible text content rather than a
        snapshot ref.  When the text is not found or has no bounding box,
        a "not found" message is returned (no exception is raised).

        Parameters
        ----------
        text : str
            Text string to find and scroll to (case-sensitive, substring match).

        Returns
        -------
        str
            "Scrolled to text: <text>" on success, or
            "Text not found: <text>" / "Text '<text>' not found or not visible"
            when the text cannot be located.

        Raises
        ------
        InvalidInputError
            If ``text`` is empty.
        StateError
            If no active page is available.
        OperationError
            If an unexpected error occurs.
        """
        try:
            logger.info(f"[scroll_to_text] start text={text!r}")

            text = text.strip()
            if not text:
                _raise_invalid_input("Text to find cannot be empty", code="TEXT_EMPTY")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            try:
                locator = page.get_by_text(text, exact=False).first
                bounding_box = await locator.bounding_box(timeout=5000)
                if bounding_box:
                    await locator.scroll_into_view_if_needed()
                    result = f'Scrolled to text: {text}'
                    logger.info(f"[scroll_to_text] done {result}")
                    return result
                else:
                    result = f'Text not found: {text}'
                    logger.warning(f"[scroll_to_text] done {result}")
                    return result
            except Exception:
                result = f"Text '{text}' not found or not visible"
                logger.info(f"[scroll_to_text] done {result}")
                return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to scroll to text: {str(e)}"
            logger.error(f"[scroll_to_text] {error_msg}")
            _raise_operation_error(error_msg)

    async def evaluate_javascript(self, code: str) -> str:
        """Execute JavaScript in page context. **Only run trusted code.**

        Parameters
        ----------
        code : str
            Arrow function format, e.g., "() => document.title".

        Returns
        -------
        str
            Execution result as string.
        """
        try:
            logger.info(f"[evaluate_javascript] start code_preview={code[:100] if code and len(code) > 100 else code!r}")

            code = code.strip()
            if not code:
                _raise_invalid_input("JavaScript code cannot be empty", code="CODE_EMPTY")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            if self._cdp_url and not self._cdp_context_owned and self._context:
                # CDP borrowed mode: page.evaluate() hangs on pre-existing tabs
                # because _mainContext() never resolves.  Use a raw CDPSession
                # Runtime.evaluate call — Chrome responds immediately.
                session = None
                try:
                    session = await self._context.new_cdp_session(page)
                    raw = await asyncio.wait_for(
                        session.send(
                            "Runtime.evaluate",
                            {"expression": code, "returnByValue": True},
                        ),
                        timeout=30.0,
                    )
                    result = raw.get("result", {}).get("value")
                finally:
                    if session:
                        try:
                            await session.detach()
                        except Exception:
                            pass
            else:
                result = await page.evaluate(code)

            if isinstance(result, bool):
                result_str = "True" if result else "False"
                logger.info(f"[evaluate_javascript] done result={result_str!r}")
                return result_str
            elif result is None:
                logger.info(f"[evaluate_javascript] done result=None")
                return "None"
            elif isinstance(result, (int, float)):
                result_str = str(result)
                logger.info(f"[evaluate_javascript] done result={result_str!r}")
                return result_str
            else:
                result_str = str(result)
                logger.info(f"[evaluate_javascript] done result_preview={result_str[:200]!r} result_len={len(result_str)}")
                return result_str
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to execute JavaScript: {str(e)}"
            logger.error(f"[evaluate_javascript] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Tab Management ====================

    async def new_tab(
        self,
        url: Optional[str] = None,
        wait_until: str = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Create a new browser tab and optionally navigate to a URL.

        The new tab becomes the active tab.  Use :meth:`get_tabs` to list all
        open tabs and retrieve the new tab's page_id.

        Parameters
        ----------
        url : Optional[str], optional
            URL to open in the new tab. Auto-prepends "http://" if the
            protocol is missing. If None or empty, creates a blank tab.
        wait_until : str, default "domcontentloaded"
            When to consider navigation complete (only used when url is provided):
            - "domcontentloaded": DOM parsed (fast, good for modern SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds for navigation. Defaults to Playwright's 30s.

        Returns
        -------
        str
            "Opened new tab <page_id> at <url>" when url is provided, or
            "Created new blank tab <page_id>" for a blank tab.

        Raises
        ------
        StateError
            If the browser has not been started yet. Call ``navigate_to()``
            first to open a page, then use this method to create additional tabs.
        OperationError
            If tab creation or navigation fails.
        """
        if self._playwright is None:
            _raise_state_error(
                "Browser is not started. Use navigate_to() to open a page first, then you can create additional tabs.",
                code="BROWSER_NOT_STARTED",
            )

        try:
            logger.info(f"[new_tab] start url={url}")

            if url is not None:
                url = url.strip()
                if not url:
                    url = None

            if url:
                url_lower = url.lower()
                has_scheme = "://" in url or url_lower.startswith(("data:", "about:"))
                if not has_scheme:
                    if not url.startswith("/"):
                        url = f"http://{url}"
                    # else: URLs starting with '/' are absolute paths; passed as-is and will
                    # fail at navigation time with a clear Playwright error (intentional).

            page = await self._new_page(url, wait_until=wait_until, timeout=timeout)
            page_id = generate_page_id(page)
            if url:
                result = f"Opened new tab {page_id} at {page.url}"
            else:
                result = f"Created new blank tab {page_id}"
            logger.info(f"[new_tab] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to create new tab: {str(e)}"
            logger.error(f"[new_tab] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_tabs(self) -> str:
        """Get information about all open tabs.

        Returns
        -------
        str
            Newline-separated list of tab info strings, each containing
            page_id, url, and title. The active tab is marked with "(active)".
        """
        try:
            logger.info(f"[get_tabs] start")

            current_page = await self.get_current_page()
            current_id = generate_page_id(current_page) if current_page else None
            page_descs = await self.get_all_page_descs()
            lines = []
            for desc in page_descs:
                line = model_to_llm_string(desc)
                if desc.page_id == current_id:
                    line += " (active)"
                lines.append(line)
            logger.info(f"[get_tabs] done tabs={len(lines)}")
            if not lines:
                return "No open tabs"
            return "\n".join(lines)
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get tabs info: {str(e)}"
            logger.error(f"[get_tabs] {error_msg}")
            _raise_operation_error(error_msg)

    async def switch_tab(self, page_id: str) -> str:
        """Switch to specified tab.

        Parameters
        ----------
        page_id : str
            Target tab's page_id, format: "page_xxxx".

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        The page_id format is "page_xxxx" where xxxx is a unique identifier.
        Use get_tabs() to retrieve available page_ids.
        """
        try:
            logger.info(f"[switch_tab] start page_id={page_id}")

            success, result = await self.switch_to_page(page_id)
            if not success:
                logger.error(f"[switch_tab] {result}")
                _raise_state_error(
                    result,
                    code="TAB_NOT_FOUND" if "not found" in result.lower() else "INVALID_STATE",
                    details={"page_id": page_id},
                )
            logger.info(f"[switch_tab] done page_id={page_id}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to switch tab: {str(e)}"
            logger.error(f"[switch_tab] {error_msg}")
            _raise_operation_error(error_msg)

    async def close_tab(self, page_id: Optional[str] = None) -> str:
        """Close a tab.

        Parameters
        ----------
        page_id : Optional[str], optional
            page_id of the tab to close. If None, closes the current tab.
            Format: "page_xxxx".

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        If the closed tab is the current tab, the browser will automatically
        switch to another open tab if available.
        """
        try:
            logger.info(f"[close_tab] start page_id={page_id}")

            result = ""
            if page_id is None:
                page = await self.get_current_page()
                if page is None:
                    _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
                success, closed_result = await self._close_page(page)
                if not success:
                    logger.error(f"[close_tab] {closed_result}")
                    _raise_state_error(
                        closed_result,
                        code="TAB_CLOSE_FAILED",
                        details={"page_id": page_id},
                    )
                result = closed_result
            else:
                success, closed_result = await self._close_page(page_id)
                if not success:
                    logger.error(f"[close_tab] {closed_result}")
                    _raise_state_error(
                        closed_result,
                        code="TAB_NOT_FOUND" if "not found" in closed_result.lower() else "TAB_CLOSE_FAILED",
                        details={"page_id": page_id},
                    )
                result = closed_result

            logger.info(f"[close_tab] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to close tab: {str(e)}"
            logger.error(f"[close_tab] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Browser Control Tools ====================

    async def browser_resize(self, width: int, height: int) -> str:
        """Resize the browser viewport.

        Parameters
        ----------
        width : int
            New viewport width in pixels.
        height : int
            New viewport height in pixels.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[browser_resize] start width={width} height={height}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.set_viewport_size({"width": width, "height": height})

            result = f"Browser viewport resized to {width}x{height}"
            logger.info(f"[browser_resize] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to resize browser: {str(e)}"
            logger.error(f"[browser_resize] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Wait ====================

    async def _is_text_visible_in_any_frame(
        self, page: "Page", text: str, exact: bool = False,
    ) -> bool:
        """Check whether *text* is visible in any frame (main + all iframes).

        In CDP borrowed mode, ``locator.count()`` and ``locator.is_visible()``
        call into Playwright's ``_mainContext()`` which never resolves for
        pre-existing tabs (see :meth:`_get_page_title` for the full explanation).
        We bypass this by using a raw CDPSession ``Runtime.evaluate`` call that
        queries ``document.body.innerText`` directly from Chrome — no Playwright
        context tracking needed.
        """
        if self._cdp_url and not self._cdp_context_owned and self._context:
            # Iterate every frame (main + all iframes) to match the non-CDP path.
            #
            # ``new_cdp_session(child_frame)`` silently fails for same-process iframes
            # (same-origin / file://) because they share the page's CDP target and have
            # no separate Target to attach to.  Instead we use two CDP page-level calls:
            #
            #   1. ``Page.getFrameTree()``        — enumerate all frame IDs recursively
            #   2. ``Page.createIsolatedWorld()`` — create a JS world IN that specific
            #                                       frame (independent of Playwright's
            #                                       _mainContext() tracking)
            #   3. ``Runtime.evaluate()`` with ``contextId`` — run in the frame's world
            #
            # This avoids the ``_mainContext()`` hang because Page/Runtime CDP commands
            # do not go through Playwright's context-tracking machinery.
            session = None
            try:
                session = await self._context.new_cdp_session(page)
                # Step 1: collect all frame IDs in document order.
                frame_tree_result = await asyncio.wait_for(
                    session.send("Page.getFrameTree"),
                    timeout=5.0,
                )
                frame_ids: list[str] = []

                def _collect_frame_ids(node: dict) -> None:
                    fid = node.get("frame", {}).get("id")
                    if fid:
                        frame_ids.append(fid)
                    for child in node.get("childFrames", []):
                        _collect_frame_ids(child)

                _collect_frame_ids(frame_tree_result.get("frameTree", {}))

                needle = json.dumps(text if exact else text.lower())
                expr = (
                    "(function(){"
                    "  var t = document.body ? document.body.innerText : '';"
                    + ("  return t.includes(" + needle + ");}" if exact
                       else "  return t.toLowerCase().includes(" + needle + ");}")
                    + ")()"
                )
                # Step 2+3: for each frame, create an isolated world and evaluate.
                for frame_id in frame_ids:
                    try:
                        world_result = await asyncio.wait_for(
                            session.send("Page.createIsolatedWorld", {
                                "frameId": frame_id,
                                "worldName": "bridgic-text-search",
                                "grantUniversalAccess": False,
                            }),
                            timeout=5.0,
                        )
                        ctx_id = world_result.get("executionContextId")
                        if ctx_id is None:
                            continue
                        result = await asyncio.wait_for(
                            session.send("Runtime.evaluate", {
                                "expression": expr,
                                "contextId": ctx_id,
                                "returnByValue": True,
                            }),
                            timeout=5.0,
                        )
                        if bool(result.get("result", {}).get("value", False)):
                            return True
                    except Exception:
                        continue
            except Exception:
                return False
            finally:
                if session:
                    try:
                        await session.detach()
                    except Exception:
                        pass
            return False

        for frame in page.frames:
            try:
                locator = frame.get_by_text(text, exact=exact)
                if await locator.count() > 0 and await locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_text_across_frames(
        self,
        page: "Page",
        text: str,
        *,
        gone: bool = False,
        exact: bool = False,
        timeout_ms: float = 30000.0,
    ) -> None:
        """Poll all frames (main + iframes) until *text* appears or disappears.

        Raises ``TimeoutError`` if the condition is not met within *timeout_ms*.
        """
        import time as _time

        no_timeout = timeout_ms <= 0
        deadline = _time.monotonic() + timeout_ms / 1000.0
        poll_interval = 0.2  # 200 ms

        while True:
            found = await self._is_text_visible_in_any_frame(page, text, exact=exact)
            if not gone and found:
                return
            if gone and not found:
                return
            if not no_timeout and _time.monotonic() >= deadline:
                action = "disappear" if gone else "appear"
                raise TimeoutError(
                    f"Locator.wait_for: Timeout {timeout_ms:.0f}ms exceeded. "
                    f"Text '{text}' did not {action}."
                )
            await asyncio.sleep(poll_interval)

    async def wait_for(
        self,
        time_seconds: Optional[float] = None,
        text: Optional[str] = None,
        text_gone: Optional[str] = None,
        selector: Optional[str] = None,
        state: str = "visible",
        timeout: float = 30.0,
    ) -> str:
        """Wait for a condition: time delay, text appearance/disappearance, or element state.

        **Priority**: Only ONE condition is used: time_seconds > text > text_gone > selector.

        Parameters
        ----------
        time_seconds : float, optional
            Fixed delay in SECONDS (e.g., 2.5 = 2.5 seconds, max 60).
            If provided, ignores all other parameters.
        text : str, optional
            Wait until this text appears and is visible on the page.
        text_gone : str, optional
            Wait until this text disappears from the page.
        selector : str, optional
            CSS selector to wait for (e.g., "#submit-btn", ".loading-spinner").
        state : str, optional
            Element state when using selector: "visible" (default), "hidden",
            "attached", "detached".
        timeout : float, optional
            Maximum wait time in SECONDS for text/selector conditions.
            Default is 30.0. Does not apply to ``time_seconds``.
            Setting ``timeout=0`` disables the timeout (waits indefinitely).

        Returns
        -------
        str
            Success: "Waited for X seconds" or "Text 'X' appeared on the page"
            Failure: "Wait condition not met: {error}"

        Examples
        --------
        wait_for(time_seconds=3)  # Wait 3 seconds
        wait_for(text="Success")  # Wait for "Success" to appear
        wait_for(text_gone="Loading...")  # Wait for loading text to disappear
        wait_for(selector=".modal", state="visible")  # Wait for modal
        """
        try:
            logger.info(f"[wait_for] start time_seconds={time_seconds} text={text} text_gone={text_gone} selector={selector}")

            if time_seconds is not None:
                actual_seconds = min(max(float(time_seconds), 0), 60)
                await asyncio.sleep(actual_seconds)
                result = f"Waited for {actual_seconds} seconds"
                logger.info(f"[wait_for] done {result}")
                return result

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            timeout_ms = timeout * 1000.0

            if text is not None:
                await self._wait_for_text_across_frames(
                    page, text, gone=False, timeout_ms=timeout_ms,
                )
                result = f"Text '{text}' appeared on the page"
                logger.info(f"[wait_for] done {result}")
                return result

            if text_gone is not None:
                await self._wait_for_text_across_frames(
                    page, text_gone, gone=True, timeout_ms=timeout_ms,
                )
                result = f"Text '{text_gone}' disappeared from the page"
                logger.info(f"[wait_for] done {result}")
                return result

            if selector is not None:
                locator = page.locator(selector)
                await locator.first.wait_for(state=state, timeout=timeout_ms)
                result = f"Selector '{selector}' reached state '{state}'"
                logger.info(f"[wait_for] done {result}")
                return result

            _raise_invalid_input("No wait condition specified", code="INVALID_WAIT_CONDITION")
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Wait condition not met: {str(e)}"
            logger.error(f"[wait_for] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Element Action Tools (by ref) ====================

    async def input_text_by_ref(
        self,
        ref: str,
        text: str,
        clear: bool = True,
        is_secret: bool = False,
        slowly: bool = False,
        submit: bool = False,
    ) -> str:
        """Input text into a specific element identified by its snapshot ref.

        This is the primary text-input tool for interacting with form fields by
        ref.  Unlike :meth:`type_text` which types into the currently focused
        element, this method targets the element directly via its ref and
        handles both visible and hidden (shadow-DOM) inputs.

        Comparison:

        - ``input_text_by_ref`` — target by ref; clears first; handles hidden
          inputs via JS; fires ``input``/``change`` events; **preferred**.
        - :meth:`type_text` — no ref; types into focused element
          character-by-character via ``keyboard.press``; triggers per-character
          ``keydown``/``keyup`` events (needed for autocomplete widgets).

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9"). Obtain refs by
            calling :meth:`get_snapshot_text` first.
        text : str
            Text to input. An empty string clears the field when ``clear=True``.
        clear : bool, optional
            Clear existing field content before typing. Default True.
            When False, text is appended to whatever is already in the field.
        is_secret : bool, optional
            When True, the result message shows a generic confirmation instead
            of the actual text (for passwords and tokens). Default False.
        slowly : bool, optional
            When True, types character-by-character with ~100 ms delay between
            keystrokes, triggering per-character ``keydown``/``keyup`` events.
            Use for fields with live key-event handlers (e.g. autocomplete).
            Falls back to JS value-set if the element is not visible. Default False.
        submit : bool, optional
            Press Enter after typing to submit the form. Default False.

        Returns
        -------
        str
            "Input text '<text>'" on success, or "Successfully input sensitive
            information" when ``is_secret=True``.  Appended with " and
            submitted" when ``submit=True``.

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If text input fails.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[input_text_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            is_vis = await locator.is_visible()

            _js_set_value = (
                "(el, v) => {"
                "  if ('value' in el) {"
                f"    el.value = {'el.value + v' if not clear else 'v'};"
                "    el.dispatchEvent(new Event('input', {bubbles: true}));"
                "    el.dispatchEvent(new Event('change', {bubbles: true}));"
                "  } else if (el.isContentEditable) {"
                f"    el.textContent = {'el.textContent + v' if not clear else 'v'};"
                "    el.dispatchEvent(new Event('input', {bubbles: true}));"
                "  }"
                "}"
            )

            _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None

            if clear:
                if is_vis:
                    await locator.clear()
                elif _cdp_ctx is not None:
                    # CDP borrowed mode: locator.evaluate() (main world) hangs.
                    # locator.fill("") clears via the utility world and also
                    # dispatches input/change events — equivalent behaviour.
                    logger.debug("[input_text_by_ref] CDP mode + is_visible()=False; clearing via locator.fill('')")
                    await locator.fill("")
                else:
                    logger.debug("[input_text_by_ref] is_visible()=False; clearing via JS")
                    await asyncio.wait_for(
                        locator.evaluate(
                            "(el) => { if ('value' in el) el.value = ''; "
                            "else if (el.isContentEditable) el.textContent = ''; }"
                        ),
                        timeout=10.0,
                    )

            if slowly:
                if is_vis:
                    await locator.focus()
                    await locator.type(text, delay=100)
                elif _cdp_ctx is not None:
                    logger.debug("[input_text_by_ref] CDP mode + is_visible()=False; using locator.fill() (slowly unavailable)")
                    await locator.fill(text)
                else:
                    logger.debug("[input_text_by_ref] is_visible()=False; setting value via JS (slowly mode unavailable)")
                    await locator.focus()
                    await asyncio.wait_for(locator.evaluate(_js_set_value, text), timeout=10.0)
            else:
                if is_vis and clear:
                    await locator.fill(text)
                elif _cdp_ctx is not None:
                    # CDP borrowed mode: use fill() (utility world) for hidden elements too.
                    if not is_vis:
                        logger.debug("[input_text_by_ref] CDP mode + is_visible()=False; using locator.fill()")
                    await locator.fill(text)
                else:
                    if not is_vis:
                        logger.debug("[input_text_by_ref] is_visible()=False; setting value via JS")
                    await asyncio.wait_for(locator.evaluate(_js_set_value, text), timeout=10.0)

            if submit:
                if not is_vis:
                    await locator.focus()
                page = await self.get_current_page()
                if page:
                    await page.keyboard.press("Enter")

            msg = f"Input text '{text}'"
            if is_secret:
                msg = "Successfully input sensitive information"
            if submit:
                msg += " and submitted"

            logger.info(f'[input_text_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[input_text_by_ref] Failed to input text: {type(e).__name__}: {e}')
            error_msg = f'Failed to input text to element {ref}: {e}'
            _raise_operation_error(error_msg)

    async def click_element_by_ref(self, ref: str) -> str:
        """Click an element identified by its snapshot ref.

        Prefer this over :meth:`mouse_click` for accessible elements — it uses
        the snapshot ref to target the element rather than screen coordinates,
        which is more reliable when pages scroll or re-render.

        Handles covered and hidden elements automatically:

        - If the element is covered by another element (e.g. a Stripe accordion
          overlay), the intercepting element is clicked instead.
        - If the element has a bounding box but ``is_visible()`` is False
          (shadow-DOM slot), a ``click`` event is dispatched directly.
        - If the element has no bounding box and is not visible, a ``click``
          event is dispatched.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Clicked element <ref>" on success.

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If the click fails.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[click_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            bbox, is_vis = await asyncio.gather(
                locator.bounding_box(),
                locator.is_visible(),
            )
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                if not is_vis:
                    logger.debug(
                        "[click_element_by_ref] element has bbox but is_visible()=False "
                        "(likely shadow-DOM slot); using dispatch_event click"
                    )
                    await locator.dispatch_event("click")
                else:
                    _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
                    covered = await _check_element_covered(locator, cx, cy, cdp_context=_cdp_ctx)
                    if covered:
                        logger.debug("[click_element_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                        page = await self.get_current_page()
                        if page:
                            await _click_covering_element(page, locator, cx, cy, cdp_context=_cdp_ctx)
                        else:
                            await locator.dispatch_event("click")
                    else:
                        await _locator_action_with_fallback(locator, action="click")
            else:
                if not is_vis:
                    logger.debug("[click_element_by_ref] bbox=None and is_visible()=False; using dispatch_event click")
                    await locator.dispatch_event("click")
                else:
                    await _locator_action_with_fallback(locator, action="click")

            msg = f'Clicked element {ref}'
            logger.info(f'[click_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[click_element_by_ref] Failed to click element: {type(e).__name__}: {e}')
            error_msg = f'Failed to click element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def get_dropdown_options_by_ref(self, ref: str) -> str:
        """Get all options from a dropdown/select element.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9").

        Returns
        -------
        str
            Numbered list: "1. Option Text (value: val)"
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[get_dropdown_options_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            page = await self.get_current_page()
            options = await _get_dropdown_option_locators(page, locator)
            if not options:
                _raise_state_error('This dropdown has no options', code='ELEMENT_STATE_ERROR')

            # Detect currently selected option(s)
            selected_values: set = set()
            _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
            if _cdp_ctx is not None:
                # CDP borrowed mode: locator.evaluate() hangs. Skip — callers
                # get no [selected] markers, which is a minor cosmetic loss.
                pass
            else:
                try:
                    selected_values = set(await asyncio.wait_for(
                        locator.evaluate(
                            "el => el.tagName === 'SELECT' ? Array.from(el.selectedOptions).map(o => o.value) : []"
                        ),
                        timeout=10.0,
                    ))
                except Exception:
                    pass

            option_texts = []
            # Fetch text and value for all options in parallel (two awaits per
            # option reduced to one asyncio.gather per option).
            _text_value_pairs = await asyncio.gather(
                *(asyncio.gather(option.text_content(), option.get_attribute("value"))
                  for option in options)
            )
            for i, (text, value) in enumerate(_text_value_pairs):
                if text:
                    line = f"{i + 1}. {text.strip()}" + (f" (value: {value})" if value else "")
                    if value in selected_values:
                        line += " [selected]"
                    option_texts.append(line)

            result = '\n'.join(option_texts) if option_texts else 'Unable to get dropdown options'
            logger.info(f'[get_dropdown_options_by_ref] Retrieved dropdown options')
            return result

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[get_dropdown_options_by_ref] Failed to get dropdown options: {type(e).__name__}: {e}')
            error_msg = f'Failed to get dropdown options for element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def select_dropdown_option_by_ref(self, ref: str, text: str) -> str:
        """Select an option from a dropdown element by its visible text or value.

        Supports native ``<select>`` elements and custom ARIA listbox/option
        dropdowns (including portalized ones linked via ``aria-controls`` or
        ``aria-owns``).

        Matching order for custom dropdowns (non-native ``<select>``):

        1. Exact match on option visible text.
        2. Exact match on option ``value`` attribute.
        3. Case-insensitive match on visible text.
        4. Case-insensitive match on ``value`` attribute.

        For native ``<select>`` elements, Playwright's ``select_option`` is
        used (tries ``value`` first, then ``label``).

        Call :meth:`get_dropdown_options_by_ref` first to see available options
        and their values.

        Parameters
        ----------
        ref : str
            Element ref of the dropdown from snapshot (e.g., "1f79fe5e").
        text : str
            Visible option text or ``value`` attribute to select.

        Returns
        -------
        str
            "Selected option: <text>" on success.

        Raises
        ------
        StateError
            If the ref cannot be resolved.
        OperationError
            If no matching option is found or the click fails.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[select_dropdown_option_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
            if _cdp_ctx is not None:
                # CDP borrowed mode: locator.evaluate() (main world) hangs.
                # locator.select_option() uses the utility world and works correctly.
                # Try it first; if the element is not a native <select> it raises,
                # and we fall through to the custom dropdown path (tag_name = "").
                try:
                    try:
                        await locator.select_option(value=text)
                    except Exception:
                        await locator.select_option(label=text)
                    msg = f'Selected option: {text}'
                    logger.info(f'[select_dropdown_option_by_ref] {msg} (CDP native-select path)')
                    return msg
                except Exception:
                    tag_name = ""  # not a native <select>; fall through to custom path
            else:
                try:
                    tag_name = await asyncio.wait_for(
                        locator.evaluate("el => el.tagName.toLowerCase()"),
                        timeout=10.0,
                    )
                except Exception:
                    tag_name = ""

            if tag_name == "select":
                try:
                    await locator.select_option(value=text)
                except Exception:
                    await locator.select_option(label=text)
            else:
                normalized_target = text.strip()
                page = await self.get_current_page()
                options = await _get_dropdown_option_locators(page, locator)

                if not options:
                    if await locator.is_visible():
                        await locator.click()
                    else:
                        await locator.dispatch_event("click")
                    options = await _get_dropdown_option_locators(page, locator)

                if not options:
                    _raise_operation_error(
                        f'Failed to find dropdown options for element {ref}',
                        code='ELEMENT_STATE_ERROR',
                        details={"ref": ref, "text": text},
                    )

                chosen_option = None
                for option in options:
                    option_text = (await option.text_content() or "").strip()
                    option_value = (await option.get_attribute("value") or "").strip()
                    if option_text == normalized_target or option_value == normalized_target:
                        chosen_option = option
                        break

                if chosen_option is None:
                    lowered_target = normalized_target.lower()
                    for option in options:
                        option_text = (await option.text_content() or "").strip()
                        option_value = (await option.get_attribute("value") or "").strip()
                        if option_text.lower() == lowered_target or option_value.lower() == lowered_target:
                            chosen_option = option
                            break

                if chosen_option is None:
                    _raise_operation_error(f'Failed to find dropdown option "{text}" for element {ref}', code='ELEMENT_STATE_ERROR')

                if await chosen_option.is_visible():
                    await chosen_option.click()
                else:
                    await chosen_option.dispatch_event("click")

            msg = f'Selected option: {text}'
            logger.info(f'[select_dropdown_option_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[select_dropdown_option_by_ref] Failed to select dropdown option: {type(e).__name__}: {e}')
            error_msg = f'Failed to select dropdown option "{text}" for element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def hover_element_by_ref(self, ref: str) -> str:
        """Hover mouse over an element by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "d8ae31b4").

        Returns
        -------
        str
            Result message.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[hover_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            bbox, is_vis = await asyncio.gather(
                locator.bounding_box(),
                locator.is_visible(),
            )
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                if not is_vis:
                    logger.debug(
                        "[hover_element_by_ref] element has bbox but is_visible()=False "
                        "(likely shadow-DOM slot); moving mouse to coordinates directly"
                    )
                    page = await self.get_current_page()
                    if page:
                        await page.mouse.move(cx, cy)
                    else:
                        await locator.hover(force=True)
                else:
                    _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
                    covered = await _check_element_covered(locator, cx, cy, cdp_context=_cdp_ctx)
                    if covered:
                        logger.debug("[hover_element_by_ref] covered at (%.1f, %.1f), moving mouse to coordinates", cx, cy)
                        page = await self.get_current_page()
                        if page:
                            await page.mouse.move(cx, cy)
                        else:
                            await locator.hover(force=True)
                    else:
                        await locator.hover()
            else:
                if not is_vis:
                    msg = (
                        f'Could not hover element {ref}: element is not visible and has '
                        'no screen coordinates'
                    )
                    logger.warning(f'[hover_element_by_ref] {msg}')
                    _raise_operation_error(
                        msg,
                        code="ELEMENT_NOT_VISIBLE",
                        details={"ref": ref},
                    )
                else:
                    await locator.hover()

            msg = f'Hovered over element ref {ref}'
            logger.info(f'[hover_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[hover_element_by_ref] Failed to hover element: {type(e).__name__}: {e}')
            error_msg = f'Failed to hover element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def focus_element_by_ref(self, ref: str) -> str:
        """Focus an element by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1fe9cf5e").

        Returns
        -------
        str
            Result message.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[focus_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            if await locator.is_visible():
                await locator.focus()
            else:
                logger.debug(
                    "[focus_element_by_ref] is_visible()=False (likely shadow-DOM slot); "
                    "using el.focus() via focus() to properly update document.activeElement"
                )
                # locator.focus() has a built-in timeout (unlike evaluate which has none).
                await locator.focus()

            msg = f'Focused element ref {ref}'
            logger.info(f'[focus_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[focus_element_by_ref] Failed to focus element: {type(e).__name__}: {e}')
            error_msg = f'Failed to focus element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def evaluate_javascript_on_ref(self, ref: str, code: str) -> str:
        """Execute JavaScript on an element.

        The element is passed as the first argument to the function.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9").
        code : str
            Arrow function receiving the element as first arg, e.g., "el => el.textContent".

        Returns
        -------
        str
            Execution result as string.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[evaluate_javascript_on_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            if self._cdp_url and not self._cdp_context_owned and self._context:
                # CDP borrowed mode: try native evaluate first (works on pages
                # navigated via page.goto() — including iframe elements).
                # Falls back to CDPSession bypass only on truly pre-existing
                # tabs where _mainContext() hangs.
                try:
                    result = await asyncio.wait_for(locator.evaluate(code), timeout=5.0)
                except Exception as native_err:
                    if isinstance(native_err, asyncio.TimeoutError):
                        logger.debug(
                            f'[evaluate_javascript_on_ref] native evaluate timed out '
                            f'(pre-existing tab?), falling back to CDPSession bypass'
                        )
                    else:
                        logger.debug(
                            f'[evaluate_javascript_on_ref] native evaluate failed: '
                            f'{type(native_err).__name__}: {native_err}, '
                            f'falling back to CDPSession bypass'
                        )
                    ref_data = self._last_snapshot.refs.get(ref) if self._last_snapshot else None
                    if ref_data is not None and ref_data.frame_path:
                        _raise_operation_error(
                            f"eval-on does not support iframe elements on pre-existing "
                            f"CDP tabs (ref={ref}, frame_path={ref_data.frame_path}). "
                            f"Navigate to the page first with 'open', or use 'eval' with "
                            f"contentDocument.querySelector() as a workaround.",
                            code="IFRAME_EVAL_NOT_SUPPORTED",
                        )
                    page = await self.get_current_page()
                    result = await _cdp_evaluate_on_element(self._context, page, locator, code)
            else:
                result = await asyncio.wait_for(locator.evaluate(code), timeout=30.0)

            if result is None:
                result_str = "null"
            elif isinstance(result, str):
                result_str = result
            else:
                result_str = str(result)

            logger.info(f'[evaluate_javascript_on_ref] Execution successful, result length: {len(result_str)}')
            return result_str

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[evaluate_javascript_on_ref] Failed to execute JavaScript: {type(e).__name__}: {e}')
            error_msg = f'Failed to execute JavaScript on element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def upload_file_by_ref(self, ref: str, file_path: str) -> str:
        """Upload a file to a file input element by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1f79fe5e").
        file_path : str
            Path to the file to upload.

        Returns
        -------
        str
            Result message.
        """
        try:
            if not os.path.exists(file_path):
                msg = f'File {file_path} does not exist'
                logger.error(f'[upload_file_by_ref] {msg}')
                _raise_operation_error(msg, code="NOT_FOUND", details={"path": file_path})

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[upload_file_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            # Determine tag and type to verify this is a file input.
            # In CDP borrowed mode use get_attribute() (utility world) instead of
            # locator.evaluate() which hangs. get_attribute("type") works reliably
            # because Playwright's attribute queries use the utility world.
            if self._cdp_url and not self._cdp_context_owned:
                # get_attribute returns None for elements that don't have the attribute,
                # and '' for elements that have it but with no value. A file input
                # always has an explicit type="file" so a None/non-"file" result means
                # this isn't a direct file input — fall through to nested-search path.
                input_type_attr = await locator.get_attribute("type")
                if input_type_attr and input_type_attr.lower() == "file":
                    tag_name, input_type = "input", "file"
                else:
                    tag_name, input_type = "", None
            else:
                try:
                    tag_name = await asyncio.wait_for(
                        locator.evaluate("el => el.tagName.toLowerCase()"),
                        timeout=10.0,
                    )
                except Exception:
                    tag_name = ""
                input_type = await locator.get_attribute("type") if tag_name == "input" else None
            if tag_name != "input" or input_type != "file":
                nested = locator.locator("input[type='file']")
                if await nested.count() > 0:
                    logger.debug(
                        "[upload_file_by_ref] ref %s (%s) is not a file input; "
                        "found nested input[type=file], retargeting",
                        ref, tag_name,
                    )
                    locator = nested.first
                else:
                    msg = f'Element ref {ref} is not a file input element (tag: {tag_name}, type: {input_type})'
                    logger.error(f'[upload_file_by_ref] {msg}')
                    _raise_operation_error(
                        msg,
                        code="ELEMENT_TYPE_MISMATCH",
                        details={"ref": ref, "tag_name": tag_name, "input_type": input_type},
                    )

            await locator.set_input_files(file_path)

            msg = f'Successfully uploaded file to element ref {ref}'
            logger.info(f'[upload_file_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[upload_file_by_ref] Failed to upload file: {type(e).__name__}: {e}')
            error_msg = f'Failed to upload file to element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def drag_element_by_ref(self, start_ref: str, end_ref: str) -> str:
        """Drag element from start_ref and drop on end_ref.

        Parameters
        ----------
        start_ref : str
            Element ref to drag (e.g., "8d4b03a9").
        end_ref : str
            Element ref of drop target (e.g., "1f79fe5e").

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info(f'[drag_element_by_ref] start start_ref={start_ref} end_ref={end_ref}')

            source_locator = await self.get_element_by_ref(start_ref)
            if source_locator is None:
                msg = f'Source element ref {start_ref} is not available - page may have changed.'
                logger.warning(f'[drag_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"start_ref": start_ref})

            target_locator = await self.get_element_by_ref(end_ref)
            if target_locator is None:
                msg = f'Target element ref {end_ref} is not available - page may have changed.'
                logger.warning(f'[drag_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"end_ref": end_ref})

            await source_locator.drag_to(target_locator)

            msg = f'Dragged element {start_ref} to {end_ref}'
            logger.info(f'[drag_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[drag_element_by_ref] Failed to drag element: {type(e).__name__}: {e}')
            error_msg = f'Failed to drag element from {start_ref} to {end_ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def check_checkbox_or_radio_by_ref(self, ref: str) -> str:
        """Check a checkbox or radio button (or ARIA equivalent) by ref.

        Works for:

        - Native ``<input type="checkbox">`` and ``<input type="radio">``
          elements.
        - Custom ARIA checkboxes/toggles (``role="checkbox"`` with
          ``aria-checked``).

        This method is idempotent: if the element is already checked, it
        returns immediately without error (see result message).

        After clicking, the checked state is verified.  If it remains
        unchecked, :exc:`OperationError` is raised.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Checked element <ref> (confirmed: checked=true)" on success, or
            "Checked element <ref> (was already checked)" if already checked.

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If the element state is still unchecked after the interaction.
        """
        try:
            logger.info(f'[check_checkbox_or_radio_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[check_checkbox_or_radio_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            is_native = await _is_native_checkbox_or_radio(locator)
            already_checked = await _is_checked(locator)
            if already_checked:
                msg = f'Checked element {ref} (was already checked)'
                logger.info(f'[check_checkbox_or_radio_by_ref] {msg}')
                return msg

            bbox, is_vis = await asyncio.gather(
                locator.bounding_box(),
                locator.is_visible(),
            )
            if is_native:
                if bbox is not None:
                    cx = bbox["x"] + bbox["width"] / 2
                    cy = bbox["y"] + bbox["height"] / 2

                    if not is_vis:
                        logger.debug(
                            "[check_checkbox_or_radio_by_ref] native input has bbox but is_visible()=False; "
                            "using dispatch_event click"
                        )
                        await locator.dispatch_event("click")
                    else:
                        _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
                        covered = await _check_element_covered(locator, cx, cy, cdp_context=_cdp_ctx)
                        if covered:
                            logger.debug("[check_checkbox_or_radio_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                            page = await self.get_current_page()
                            if page:
                                await _click_covering_element(page, locator, cx, cy, cdp_context=_cdp_ctx)
                            else:
                                await locator.check(force=True, timeout=_DEFAULT_CLICK_TIMEOUT_MS)
                        else:
                            await _locator_action_with_fallback(locator, action="check")
                else:
                    if not is_vis:
                        logger.debug("[check_checkbox_or_radio_by_ref] native input bbox=None and is_visible()=False; using dispatch_event click")
                        await locator.dispatch_event("click")
                    else:
                        await _locator_action_with_fallback(locator, action="check")
            else:
                _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
                page = await self.get_current_page()
                await _click_checkable_target(page, locator, bbox, cdp_context=_cdp_ctx)

            if not await _is_checked(locator):
                msg = f'Failed to check element {ref}: state is still unchecked'
                logger.warning(f'[check_checkbox_or_radio_by_ref] {msg}')
                _raise_operation_error(
                    msg,
                    code="ELEMENT_STATE_ERROR",
                    details={"ref": ref, "expected": "checked"},
                )

            msg = f'Checked element {ref} (confirmed: checked=true)'
            logger.info(f'[check_checkbox_or_radio_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[check_checkbox_or_radio_by_ref] Failed to check element: {type(e).__name__}: {e}')
            error_msg = f'Failed to check element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def uncheck_checkbox_by_ref(self, ref: str) -> str:
        """Uncheck a checkbox by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1f79fe5e").

        Returns
        -------
        str
            Result message.

        Notes
        -----
        This method is idempotent: if the element is already unchecked, it
        returns immediately without error.

        Radio buttons cannot be unchecked directly (they work in exclusive
        groups — selecting another radio in the group is the correct approach).
        If a radio button ref is passed, this method will attempt the action
        but will NOT raise an error if the state remains checked, and will NOT
        confirm the state change.
        """
        try:
            logger.info(f'[uncheck_checkbox_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[uncheck_checkbox_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            is_native = await _is_native_checkbox_or_radio(locator)
            already_checked = await _is_checked(locator)
            if not already_checked:
                msg = f'Unchecked element {ref} (was already unchecked)'
                logger.info(f'[uncheck_checkbox_by_ref] {msg}')
                return msg

            bbox, is_vis = await asyncio.gather(
                locator.bounding_box(),
                locator.is_visible(),
            )
            if is_native:
                if bbox is not None:
                    cx = bbox["x"] + bbox["width"] / 2
                    cy = bbox["y"] + bbox["height"] / 2

                    if not is_vis:
                        logger.debug(
                            "[uncheck_checkbox_by_ref] native input has bbox but is_visible()=False; "
                            "using dispatch_event click"
                        )
                        await locator.dispatch_event("click")
                    else:
                        _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
                        covered = await _check_element_covered(locator, cx, cy, cdp_context=_cdp_ctx)
                        if covered:
                            logger.debug("[uncheck_checkbox_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                            page = await self.get_current_page()
                            if page:
                                await _click_covering_element(page, locator, cx, cy, cdp_context=_cdp_ctx)
                            else:
                                await locator.uncheck(force=True, timeout=_DEFAULT_CLICK_TIMEOUT_MS)
                        else:
                            await _locator_action_with_fallback(locator, action="uncheck")
                else:
                    if not is_vis:
                        logger.debug("[uncheck_checkbox_by_ref] native input bbox=None and is_visible()=False; using dispatch_event click")
                        await locator.dispatch_event("click")
                    else:
                        await _locator_action_with_fallback(locator, action="uncheck")
            else:
                _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
                page = await self.get_current_page()
                await _click_checkable_target(page, locator, bbox, cdp_context=_cdp_ctx)

            is_native_radio = is_native and (await locator.get_attribute("type") or "").strip().lower() == "radio"
            if not is_native_radio and await _is_checked(locator):
                msg = f'Failed to uncheck element {ref}: state is still checked'
                logger.warning(f'[uncheck_checkbox_by_ref] {msg}')
                _raise_operation_error(
                    msg,
                    code="ELEMENT_STATE_ERROR",
                    details={"ref": ref, "expected": "unchecked"},
                )

            msg = f'Unchecked element {ref} (confirmed: checked=false)'
            logger.info(f'[uncheck_checkbox_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[uncheck_checkbox_by_ref] Failed to uncheck element: {type(e).__name__}: {e}')
            error_msg = f'Failed to uncheck element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def double_click_element_by_ref(self, ref: str) -> str:
        """Double-click an element by its snapshot ref.

        Fires a ``dblclick`` event.  Handles covered and hidden elements using
        the same strategy as :meth:`click_element_by_ref`.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "09ea4f1e"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Double-clicked element <ref>".

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If the double-click fails.
        """
        try:
            logger.info(f'[double_click_element_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[double_click_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            bbox, is_vis = await asyncio.gather(
                locator.bounding_box(),
                locator.is_visible(),
            )
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                if not is_vis:
                    logger.debug(
                        "[double_click_element_by_ref] element has bbox but is_visible()=False "
                        "(likely shadow-DOM slot); using dispatch_event dblclick"
                    )
                    await locator.dispatch_event("dblclick")
                else:
                    _cdp_ctx = self._context if (self._cdp_url and not self._cdp_context_owned) else None
                    covered = await _check_element_covered(locator, cx, cy, cdp_context=_cdp_ctx)
                    if covered:
                        logger.debug("[double_click_element_by_ref] covered at (%.1f, %.1f), dispatching dblclick on intercepting element", cx, cy)
                        page = await self.get_current_page()
                        if page:
                            dblclick_expr = (
                                f"(function(){{"
                                f"const el=document.elementFromPoint({cx},{cy});"
                                f"if(el)el.dispatchEvent(new MouseEvent('dblclick',{{bubbles:true,cancelable:true,view:window}}));"
                                f"}})()"
                            )
                            if _cdp_ctx is not None:
                                session = None
                                try:
                                    session = await _cdp_ctx.new_cdp_session(page)
                                    await asyncio.wait_for(
                                        session.send("Runtime.evaluate", {"expression": dblclick_expr}),
                                        timeout=5.0,
                                    )
                                except Exception:
                                    await locator.dispatch_event("dblclick")
                                finally:
                                    if session:
                                        try:
                                            await session.detach()
                                        except Exception:
                                            pass
                            else:
                                try:
                                    await asyncio.wait_for(
                                        page.evaluate(dblclick_expr),
                                        timeout=10.0,
                                    )
                                except Exception:
                                    await locator.dispatch_event("dblclick")
                        else:
                            await locator.dblclick(force=True, timeout=_DEFAULT_CLICK_TIMEOUT_MS)
                    else:
                        await _locator_action_with_fallback(
                            locator, action="dblclick", fallback_event="dblclick"
                        )
            else:
                if not is_vis:
                    logger.debug("[double_click_element_by_ref] bbox=None and is_visible()=False; using dispatch_event dblclick")
                    await locator.dispatch_event("dblclick")
                else:
                    await _locator_action_with_fallback(
                        locator, action="dblclick", fallback_event="dblclick"
                    )

            msg = f'Double-clicked element {ref}'
            logger.info(f'[double_click_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[double_click_element_by_ref] Failed to double-click element: {type(e).__name__}: {e}')
            error_msg = f'Failed to double-click element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def scroll_element_into_view_by_ref(self, ref: str) -> str:
        """Scroll the page until the element identified by its ref is in view.

        Unlike :meth:`scroll_to_text` which searches by visible text,
        this method uses the element's snapshot ref for precise targeting.
        Useful before taking an element screenshot or verifying visibility
        of an off-screen element.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1f79fe5e"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Scrolled element <ref> into view".

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If scrolling fails.
        """
        try:
            logger.info(f'[scroll_element_into_view_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[scroll_element_into_view_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            await locator.scroll_into_view_if_needed()

            msg = f'Scrolled element {ref} into view'
            logger.info(f'[scroll_element_into_view_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[scroll_element_into_view_by_ref] Failed to scroll element into view: {type(e).__name__}: {e}')
            error_msg = f'Failed to scroll element {ref} into view: {str(e)}'
            _raise_operation_error(error_msg)

    # ==================== Mouse Tools (coordinate-based) ====================

    async def mouse_move(self, x: float, y: float) -> str:
        """Move the mouse to specific coordinates.

        Parameters
        ----------
        x : float
            X coordinate (horizontal position from left).
        y : float
            Y coordinate (vertical position from top).

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_move] start x={x} y={y}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.move(x, y)
            result = f"Moved mouse to coordinates ({x}, {y})"
            logger.info(f"[mouse_move] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to move mouse: {str(e)}"
            logger.error(f"[mouse_move] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_click(
        self,
        x: float,
        y: float,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
    ) -> str:
        """Click the mouse at specific viewport coordinates.

        Use this for elements that are not in the accessibility tree (e.g.,
        canvas-based UIs, custom rendered widgets).  For accessible elements
        identified by a snapshot ref, prefer :meth:`click_element_by_ref`
        which handles covered/hidden elements automatically.

        Parameters
        ----------
        x : float
            X coordinate in pixels (horizontal, measured from the left edge
            of the viewport).
        y : float
            Y coordinate in pixels (vertical, measured from the top edge of
            the viewport).
        button : {"left", "right", "middle"}, optional
            Mouse button to click. Default is "left".
        click_count : int, optional
            Number of clicks. Default is 1. Use 2 for a double-click.

        Returns
        -------
        str
            "Mouse clicked at (<x>, <y>) with <button> button" (or
            "double-clicked" when click_count is 2).

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If the click fails.
        """
        try:
            logger.info(f"[mouse_click] start x={x} y={y} button={button} click_count={click_count}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.click(x, y, button=button, click_count=click_count)

            click_type = "double-clicked" if click_count == 2 else "clicked"
            result = f"Mouse {click_type} at ({x}, {y}) with {button} button"
            logger.info(f"[mouse_click] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to click mouse: {str(e)}"
            logger.error(f"[mouse_click] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_drag(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> str:
        """Drag the mouse from one position to another.

        Parameters
        ----------
        start_x : float
            Starting X coordinate.
        start_y : float
            Starting Y coordinate.
        end_x : float
            Ending X coordinate.
        end_y : float
            Ending Y coordinate.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_drag] start from=({start_x}, {start_y}) to=({end_x}, {end_y})")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            await page.mouse.move(end_x, end_y)
            await page.mouse.up()

            result = f"Dragged mouse from ({start_x}, {start_y}) to ({end_x}, {end_y})"
            logger.info(f"[mouse_drag] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to drag mouse: {str(e)}"
            logger.error(f"[mouse_drag] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_down(self, button: Literal["left", "right", "middle"] = "left") -> str:
        """Press and hold a mouse button.

        Parameters
        ----------
        button : {"left", "right", "middle"}, optional
            Mouse button to press. Default is "left".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_down] start button={button}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.down(button=button)
            result = f"Mouse {button} button pressed down"
            logger.info(f"[mouse_down] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to press mouse button: {str(e)}"
            logger.error(f"[mouse_down] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_up(self, button: Literal["left", "right", "middle"] = "left") -> str:
        """Release a mouse button.

        Parameters
        ----------
        button : {"left", "right", "middle"}, optional
            Mouse button to release. Default is "left".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_up] start button={button}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.up(button=button)
            result = f"Mouse {button} button released"
            logger.info(f"[mouse_up] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to release mouse button: {str(e)}"
            logger.error(f"[mouse_up] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_wheel(self, delta_x: float = 0, delta_y: float = 0) -> str:
        """Scroll the mouse wheel at the current mouse position.

        Positive delta_y scrolls down, negative delta_y scrolls up.
        Positive delta_x scrolls right, negative delta_x scrolls left.

        Parameters
        ----------
        delta_x : float, optional
            Horizontal scroll amount in pixels. Positive = right, negative = left.
            Default is 0.
        delta_y : float, optional
            Vertical scroll amount in pixels. Positive = down, negative = up.
            Default is 0.

        Returns
        -------
        str
            "Scrolled mouse wheel: delta_x=<delta_x>, delta_y=<delta_y>".

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If scrolling fails.
        """
        try:
            logger.info(f"[mouse_wheel] start delta_x={delta_x} delta_y={delta_y}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.wheel(delta_x=delta_x, delta_y=delta_y)
            result = f"Scrolled mouse wheel: delta_x={delta_x}, delta_y={delta_y}"
            logger.info(f"[mouse_wheel] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to scroll mouse wheel: {str(e)}"
            logger.error(f"[mouse_wheel] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Keyboard Tools ====================

    async def type_text(self, text: str, submit: bool = False) -> str:
        """Type text into the currently focused element, one character at a time.

        Each character fires ``keydown``, ``keypress``, and ``keyup`` events,
        which is required for fields with per-keystroke handlers such as
        autocomplete widgets.

        An element must already be focused before calling this method (e.g.
        via :meth:`focus_element_by_ref` or by clicking a field first).

        Comparison:

        - :meth:`input_text_by_ref` — target by ref; clears first; handles
          hidden inputs; **preferred** for form filling.
        - ``type_text`` — no ref; requires a pre-focused element; fires per-
          character key events; use when those events are needed.

        Parameters
        ----------
        text : str
            Text to type character by character.
        submit : bool, optional
            Whether to press Enter after typing. Default is False.

        Returns
        -------
        str
            "Typed <N> characters sequentially" (appended with " and submitted"
            when ``submit=True``).

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If typing fails.
        """
        try:
            logger.info(f"[type_text] start text_len={len(text)} submit={submit}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            for char in text:
                await page.keyboard.press(char)

            if submit:
                await page.keyboard.press("Enter")

            submit_msg = " and submitted" if submit else ""
            result = f"Typed {len(text)} characters sequentially{submit_msg}"
            logger.info(f"[type_text] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to type sequentially: {str(e)}"
            logger.error(f"[type_text] {error_msg}")
            _raise_operation_error(error_msg)

    async def key_down(self, key: str) -> str:
        """Press and hold a key.

        Parameters
        ----------
        key : str
            Key name to press. Examples: "Shift", "Control", "Alt", "a", "Enter".

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        Use key_up() to release the key.
        """
        try:
            logger.info(f"[key_down] start key={key}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.keyboard.down(key)
            result = f"Key '{key}' pressed down"
            logger.info(f"[key_down] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to press key down: {str(e)}"
            logger.error(f"[key_down] {error_msg}")
            _raise_operation_error(error_msg)

    async def key_up(self, key: str) -> str:
        """Release a held key.

        Parameters
        ----------
        key : str
            Key name to release. Examples: "Shift", "Control", "Alt", "a", "Enter".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[key_up] start key={key}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.keyboard.up(key)
            result = f"Key '{key}' released"
            logger.info(f"[key_up] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to release key: {str(e)}"
            logger.error(f"[key_up] {error_msg}")
            _raise_operation_error(error_msg)

    async def fill_form(
        self,
        fields: List[Dict[str, str]],
        submit: bool = False,
    ) -> str:
        """Fill multiple form fields at once using their snapshot refs.

        Iterates through the fields list and calls Playwright's ``locator.fill()``
        on each.  Fields that fail are collected and reported rather than
        aborting early.  Unlike :meth:`input_text_by_ref`, this method does not
        apply the slowly/clear/is_secret options and does not fall back to JS
        for hidden inputs — use :meth:`input_text_by_ref` for individual fields
        that need those features.

        Parameters
        ----------
        fields : List[Dict[str, str]]
            List of field specifications. Each dict must have:

            - ``"ref"`` : str — element ref from snapshot (e.g., "8d4a07a9").
            - ``"value"`` : str — text to fill into the field.

        submit : bool, optional
            Press Enter after filling all fields. Default is False.

        Returns
        -------
        str
            Summary message in one of two forms:

            - All succeeded: "Filled <N> fields: [ref1, ref2, ...]"
            - Some failed: "Filled <K>/<N> fields. OK: [ref1]. Failed: [ref2: error]"

            Appended with " and submitted" when ``submit=True``.

        Raises
        ------
        InvalidInputError
            If ``fields`` is empty.
        OperationError
            If an unexpected error occurs (individual field failures are
            collected into the result message, not raised).
        """
        try:
            logger.info(f"[fill_form] start fields_count={len(fields)} submit={submit}")

            if not fields:
                _raise_invalid_input("No fields provided to fill", code="INVALID_FIELDS")

            filled_refs = []
            errors = []

            for field in fields:
                ref = field.get("ref")
                value = field.get("value", "")

                if not ref:
                    errors.append("Field missing 'ref' key")
                    continue

                locator = await self.get_element_by_ref(ref)
                if locator is None:
                    errors.append(f"{ref}: not available")
                    continue

                try:
                    await locator.fill(value)
                    filled_refs.append(ref)
                except BridgicBrowserError:
                    raise
                except Exception as e:
                    errors.append(f"{ref}: {str(e)}")

            if submit and filled_refs:
                page = await self.get_current_page()
                if page:
                    await page.keyboard.press("Enter")

            submit_msg = " and submitted" if submit else ""
            if errors:
                result = (
                    f"Filled {len(filled_refs)}/{len(fields)} fields{submit_msg}. "
                    f"OK: [{', '.join(filled_refs)}]. "
                    f"Failed: [{'; '.join(errors)}]"
                )
            else:
                result = f"Filled {len(filled_refs)} fields{submit_msg}: [{', '.join(filled_refs)}]"

            logger.info(f"[fill_form] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to fill form: {str(e)}"
            logger.error(f"[fill_form] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Screenshot and PDF Tools ====================

    async def take_screenshot(
        self,
        filename: Optional[str] = None,
        ref: Optional[str] = None,
        full_page: bool = False,
        type: Literal["png", "jpeg"] = "png",
        quality: Optional[int] = None,
    ) -> str:
        """Take a screenshot of the page or a specific element.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the screenshot. If not provided, returns base64-encoded
            image data.
        ref : Optional[str], optional
            Element ref from snapshot to screenshot. If provided, captures only
            that element.
        full_page : bool, optional
            Whether to capture the full scrollable page. Default is False.
            Ignored if ref is provided.
        type : {"png", "jpeg"}, optional
            Image format. Default is "png".
        quality : Optional[int], optional
            Quality for JPEG images (0-100). Only applies when type is "jpeg".

        Returns
        -------
        str
            On success:
            - With filename: "Screenshot saved to: /path/to/file.png"
            - Without filename: Base64 data URL "data:image/png;base64,iVBORw0..."
        
        Raises
        ------
        StateError
            If no active page is available or the provided ref cannot be resolved.
        OperationError
            If screenshot capture fails.
        """
        try:
            logger.info(f"[take_screenshot] start filename={filename} ref={ref} full_page={full_page} type={type}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            screenshot_options = {
                "type": type,
                "full_page": full_page if ref is None else False,
            }

            if type == "jpeg" and quality is not None:
                screenshot_options["quality"] = quality

            if ref is not None:
                locator = await self.get_element_by_ref(ref)
                if locator is None:
                    msg = f'Element ref {ref} is not available - page may have changed.'
                    logger.warning(f'[take_screenshot] {msg}')
                    _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})
                target = locator
            else:
                target = page

            if filename:
                if not filename.lower().endswith(f".{type}"):
                    filename = f"{filename}.{type}"

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)

                screenshot_options["path"] = filename
                await target.screenshot(**screenshot_options)
                result = f"Screenshot saved to: {filename}"
            else:
                screenshot_bytes = await target.screenshot(**screenshot_options)
                b64_data = base64.b64encode(screenshot_bytes).decode("utf-8")
                result = f"data:image/{type};base64,{b64_data}"

            logger.info(f"[take_screenshot] done")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to take screenshot: {str(e)}"
            logger.error(f"[take_screenshot] {error_msg}")
            _raise_operation_error(error_msg)

    async def save_pdf(
        self,
        filename: Optional[str] = None,
        display_header_footer: bool = False,
        print_background: bool = True,
        scale: float = 1.0,
        paper_width: Optional[str] = None,
        paper_height: Optional[str] = None,
        margin_top: Optional[str] = None,
        margin_bottom: Optional[str] = None,
        margin_left: Optional[str] = None,
        margin_right: Optional[str] = None,
        landscape: bool = False,
    ) -> str:
        """Save the current page as a PDF file.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the PDF.  The ``.pdf`` extension is added automatically
            when missing.  If not provided, saves to a temporary file and
            returns its path.
        display_header_footer : bool, optional
            Whether to display header and footer. Default is False.
        print_background : bool, optional
            Whether to print background graphics. Default is True.
        scale : float, optional
            Scale of the webpage rendering. Valid range is 0.1–2.0.
            Default is 1.0.
        paper_width : Optional[str], optional
            Paper width with units (e.g., "8.5in", "21cm", "215mm").
            Defaults to US Letter (8.5in) when omitted.
        paper_height : Optional[str], optional
            Paper height with units (e.g., "11in", "29.7cm", "297mm").
            Defaults to US Letter (11in) when omitted.
        margin_top : Optional[str], optional
            Top margin with units (e.g., "1in", "2cm"). Default is "1cm".
        margin_bottom : Optional[str], optional
            Bottom margin with units. Default is "1cm".
        margin_left : Optional[str], optional
            Left margin with units. Default is "1cm".
        margin_right : Optional[str], optional
            Right margin with units. Default is "1cm".
        landscape : bool, optional
            Whether to use landscape orientation. Default is False (portrait).

        Returns
        -------
        str
            "PDF saved to: <path>" on success.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If PDF generation fails.

        Notes
        -----
        PDF generation requires Chromium (headless). It is not supported on
        Firefox or WebKit.
        """
        try:
            logger.info(f"[save_pdf] start filename={filename}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            output_path: Optional[str] = None
            temp_output_created = False
            pdf_options: Dict[str, Any] = {
                "display_header_footer": display_header_footer,
                "print_background": print_background,
                "scale": scale,
                "landscape": landscape,
            }

            if paper_width:
                pdf_options["width"] = paper_width
            if paper_height:
                pdf_options["height"] = paper_height
            if margin_top:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["top"] = margin_top
            if margin_bottom:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["bottom"] = margin_bottom
            if margin_left:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["left"] = margin_left
            if margin_right:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["right"] = margin_right

            if filename:
                if not filename.lower().endswith(".pdf"):
                    filename = f"{filename}.pdf"
                output_path = filename

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
            else:
                fd, output_path = tempfile.mkstemp(suffix=".pdf", prefix="browser_page_")
                os.close(fd)
                temp_output_created = True

            pdf_options["path"] = output_path
            try:
                await page.pdf(**pdf_options)
            except Exception:
                # Clean up only auto-generated temp files on failure.
                if temp_output_created and output_path and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception as cleanup_exc:
                        logger.warning(f"[save_pdf] failed to clean temp file {output_path}: {cleanup_exc}")
                raise

            result = f"PDF saved to: {output_path}"
            logger.info(f"[save_pdf] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to save PDF: {str(e)}"
            logger.error(f"[save_pdf] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Network and Console Tools ====================

    async def start_console_capture(self) -> str:
        """Start capturing console messages from the current page.

        Returns
        -------
        str
            "Console message capture started".

        Notes
        -----
        - Only one capture session per page; calling again resets the capture
        - Use get_console_messages() to retrieve and optionally clear messages
        """
        try:
            logger.info("[start_console_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key in self._console_handlers:
                try:
                    page.remove_listener("console", self._console_handlers[page_key])
                except Exception:
                    pass

            self._console_messages[page_key] = []

            def handle_console(msg):
                if page_key in self._console_messages:
                    self._console_messages[page_key].append({
                        "type": msg.type,
                        "text": msg.text,
                        "location": str(msg.location) if msg.location else None,
                    })

            page.on("console", handle_console)
            self._console_handlers[page_key] = handle_console

            result = "Console message capture started"
            logger.info(f"[start_console_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to start console capture: {str(e)}"
            logger.error(f"[start_console_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def stop_console_capture(self) -> str:
        """Stop capturing console messages and clean up resources.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info("[stop_console_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key not in self._console_handlers:
                _raise_state_error("No active console capture. Use console-start first.", code="NO_ACTIVE_CAPTURE")

            try:
                page.remove_listener("console", self._console_handlers[page_key])
            except Exception:
                pass
            del self._console_handlers[page_key]

            self._console_messages.pop(page_key, None)

            result = "Console capture stopped"
            logger.info(f"[stop_console_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop console capture: {str(e)}"
            logger.error(f"[stop_console_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_console_messages(
        self,
        type_filter: Optional[Literal["log", "debug", "info", "error", "warning", "dir", "trace"]] = None,
        clear: bool = True,
    ) -> str:
        """Get captured console messages.

        Parameters
        ----------
        type_filter : Optional[str], optional
            Filter messages by type. Options: "log", "debug", "info", "error",
            "warning", "dir", "trace". Default is None (return all types).
        clear : bool, optional
            Whether to clear the captured buffer after retrieving. Default
            is True (consume-and-clear pattern).

        Returns
        -------
        str
            JSON array string.  Each element is an object with keys:

            - ``"type"`` : str — console message type (e.g. "log", "error").
            - ``"text"`` : str — message text.
            - ``"location"`` : str | null — source location if available.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If retrieval fails.

        Notes
        -----
        Console capture must be started first with :meth:`start_console_capture`.
        Returns an empty JSON array (``"[]"``) if no messages have been captured.
        """
        try:
            logger.info(f"[get_console_messages] start type_filter={type_filter} clear={clear}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)
            messages = self._console_messages.get(page_key, [])

            if type_filter:
                messages = [m for m in messages if m["type"] == type_filter]

            if clear and page_key in self._console_messages:
                self._console_messages[page_key] = []

            result = json.dumps(messages, indent=2)
            logger.info(f"[get_console_messages] done count={len(messages)}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get console messages: {str(e)}"
            logger.error(f"[get_console_messages] {error_msg}")
            _raise_operation_error(error_msg)

    async def start_network_capture(self) -> str:
        """Start capturing network requests from the current page.

        Returns
        -------
        str
            "Network request capture started".

        Notes
        -----
        - Call BEFORE navigation to capture all requests from page load
        - Use get_network_requests(include_static=False) to filter out images/CSS/JS
        """
        try:
            logger.info("[start_network_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key in self._network_handlers:
                try:
                    page.remove_listener("request", self._network_handlers[page_key])
                except Exception:
                    pass

            self._network_requests[page_key] = []

            def handle_request(request):
                if page_key in self._network_requests:
                    self._network_requests[page_key].append({
                        "url": request.url,
                        "method": request.method,
                        "resource_type": request.resource_type,
                        "headers": dict(request.headers) if request.headers else {},
                        # TODO: What should we do if the requested data volume is too large? Should we implement pagination?
                        "post_data": request.post_data if request.post_data else None,
                    })

            page.on("request", handle_request)
            self._network_handlers[page_key] = handle_request

            result = "Network request capture started"
            logger.info(f"[start_network_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to start network capture: {str(e)}"
            logger.error(f"[start_network_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def stop_network_capture(self) -> str:
        """Stop capturing network requests and clean up resources.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info("[stop_network_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key not in self._network_handlers:
                _raise_state_error("No active network capture. Use network-start first.", code="NO_ACTIVE_CAPTURE")

            try:
                page.remove_listener("request", self._network_handlers[page_key])
            except Exception:
                pass
            del self._network_handlers[page_key]

            self._network_requests.pop(page_key, None)

            result = "Network capture stopped"
            logger.info(f"[stop_network_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop network capture: {str(e)}"
            logger.error(f"[stop_network_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_network_requests(
        self,
        include_static: bool = False,
        clear: bool = True,
    ) -> str:
        """Get captured network requests.

        Parameters
        ----------
        include_static : bool, optional
            Whether to include static resources (images, stylesheets, scripts,
            fonts, media).  Default is False (only document, xhr, and fetch
            requests are returned).
        clear : bool, optional
            Whether to clear the captured buffer after retrieving. Default
            is True (consume-and-clear pattern).

        Returns
        -------
        str
            JSON array string.  Each element is an object with keys:

            - ``"url"`` : str — request URL.
            - ``"method"`` : str — HTTP method (e.g. "GET", "POST").
            - ``"resource_type"`` : str — Playwright resource type (e.g.
              "document", "xhr", "fetch", "image", "stylesheet").
            - ``"headers"`` : dict — request headers.
            - ``"post_data"`` : str | null — request body for POST requests.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If retrieval fails.

        Notes
        -----
        Network capture must be started first with :meth:`start_network_capture`.
        Call :meth:`start_network_capture` BEFORE navigation to capture all
        requests from a page load.  Returns an empty JSON array (``"[]"``) if
        no requests have been captured.
        """
        try:
            logger.info(f"[get_network_requests] start include_static={include_static} clear={clear}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)
            requests = self._network_requests.get(page_key, [])

            if not include_static:
                static_types = {"image", "stylesheet", "script", "font", "media"}
                requests = [r for r in requests if r["resource_type"] not in static_types]

            if clear and page_key in self._network_requests:
                self._network_requests[page_key] = []

            result = json.dumps(requests, indent=2)
            logger.info(f"[get_network_requests] done count={len(requests)}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get network requests: {str(e)}"
            logger.error(f"[get_network_requests] {error_msg}")
            _raise_operation_error(error_msg)

    async def wait_for_network_idle(self, timeout: float = 30.0) -> str:
        """Wait for network to become idle.

        Parameters
        ----------
        timeout : float, optional
            Maximum time to wait in seconds. Default is 30.0.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[wait_for_network_idle] start timeout_seconds={timeout}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            timeout_ms = float(timeout) * 1000.0
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)

            result = "Network is idle"
            logger.info(f"[wait_for_network_idle] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to wait for network idle: {str(e)}"
            logger.error(f"[wait_for_network_idle] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Dialog Tools ====================

    async def setup_dialog_handler(
        self,
        default_action: str = "accept",
        default_prompt_text: Optional[str] = None,
    ) -> str:
        """Set up automatic dialog handling for all future dialogs.

        Parameters
        ----------
        default_action : str, optional
            Action to take on dialogs: "accept" or "dismiss". Default is "accept".
        default_prompt_text : str, optional
            Text to enter for prompt() dialogs. Default is empty string.

        Returns
        -------
        str
            Confirmation message with the configured action.

        Notes
        -----
        - Handler stays active until remove_dialog_handler is called
        - Only one handler per page; calling again replaces the previous
        """
        try:
            logger.info(f"[setup_dialog_handler] start action={default_action}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            async def handle_dialog(dialog):
                dialog_type = dialog.type
                message = dialog.message
                logger.info(f"[dialog_handler] type={dialog_type} message={message}")

                if default_action == "accept":
                    if dialog_type == "prompt" and default_prompt_text is not None:
                        await dialog.accept(default_prompt_text)
                    else:
                        await dialog.accept()
                else:
                    await dialog.dismiss()

            if page_key in self._dialog_handlers:
                page.remove_listener("dialog", self._dialog_handlers[page_key])

            self._dialog_handlers[page_key] = handle_dialog
            page.on("dialog", handle_dialog)

            result = f"Dialog handler set up with default action: {default_action}"
            logger.info(f"[setup_dialog_handler] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to setup dialog handler: {str(e)}"
            logger.error(f"[setup_dialog_handler] {error_msg}")
            _raise_operation_error(error_msg)

    async def handle_dialog(
        self,
        accept: bool,
        prompt_text: Optional[str] = None,
    ) -> str:
        """Handle the next dialog that appears.

        Parameters
        ----------
        accept : bool
            Whether to accept (True) or dismiss (False) the dialog.
        prompt_text : Optional[str], optional
            Text to enter for prompt dialogs. Only used when accept is True.

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        This sets up a one-time handler for the very next dialog.
        Use ``setup_dialog_handler`` for persistent automatic handling.

        If ``setup_dialog_handler`` is already active when this method is
        called, the auto-handler is automatically removed (with a warning)
        so only this one-time handler fires.  Call ``setup_dialog_handler``
        again afterwards if persistent handling should resume.
        """
        try:
            logger.info(f"[handle_dialog] start accept={accept} prompt_text={prompt_text}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            # If an auto-handler (setup_dialog_handler) is already active for
            # this page, both listeners would fire on the same dialog — the
            # second accept()/dismiss() call will throw.  Remove the auto-handler
            # first so only the one-time handler runs.
            page_key = _get_page_key(page)
            if page_key in self._dialog_handlers:
                logger.warning(
                    "[handle_dialog] An auto dialog handler is already active — "
                    "removing it so the one-time handler takes precedence. "
                    "Call setup_dialog_handler() again if you need auto-handling to resume."
                )
                try:
                    page.remove_listener("dialog", self._dialog_handlers[page_key])
                except Exception:
                    pass
                del self._dialog_handlers[page_key]

            handled = {"done": False, "type": None, "message": None}

            async def one_time_handler(dialog):
                if handled["done"]:
                    return

                handled["done"] = True
                handled["type"] = dialog.type
                handled["message"] = dialog.message

                if accept:
                    if dialog.type == "prompt" and prompt_text is not None:
                        await dialog.accept(prompt_text)
                    else:
                        await dialog.accept()
                else:
                    await dialog.dismiss()

            page.once("dialog", one_time_handler)

            action = "accept" if accept else "dismiss"
            result = f"Dialog handler ready to {action} the next dialog"
            logger.info(f"[handle_dialog] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to set up dialog handler: {str(e)}"
            logger.error(f"[handle_dialog] {error_msg}")
            _raise_operation_error(error_msg)

    async def remove_dialog_handler(self) -> str:
        """Remove the automatic dialog handler.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info("[remove_dialog_handler] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key in self._dialog_handlers:
                page.remove_listener("dialog", self._dialog_handlers[page_key])
                del self._dialog_handlers[page_key]
                result = "Dialog handler removed"
            else:
                result = "No dialog handler was set up"

            logger.info(f"[remove_dialog_handler] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to remove dialog handler: {str(e)}"
            logger.error(f"[remove_dialog_handler] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Storage Tools ====================

    async def save_storage_state(self, filename: Optional[str] = None) -> str:
        """Save the browser's storage state to a file.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the storage state. If not provided, saves to a temporary file.

        Returns
        -------
        str
            On success: Returns the file path where state was saved.
        """
        try:
            logger.info(f"[save_storage_state] start filename={filename}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context

            if filename:
                if not filename.lower().endswith(".json"):
                    filename = f"{filename}.json"
                output_path = filename

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
            else:
                fd, output_path = tempfile.mkstemp(suffix=".json", prefix="browser_state_")
                os.close(fd)

            await context.storage_state(path=output_path)

            result = f"Storage state saved to: {output_path}"
            logger.info(f"[save_storage_state] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to save storage state: {str(e)}"
            logger.error(f"[save_storage_state] {error_msg}")
            _raise_operation_error(error_msg)

    async def restore_storage_state(self, filename: str) -> str:
        """Restore browser storage state from a file.

        Parameters
        ----------
        filename : str
            Path to the storage state JSON file.

        Returns
        -------
        str
            On success: Returns a confirmation message.
        """
        try:
            logger.info(f"[restore_storage_state] start filename={filename}")

            if not os.path.exists(filename):
                _raise_operation_error(f"Storage state file not found: {filename}", code="NOT_FOUND")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context

            with open(filename, "r") as f:
                state = json.load(f)

            cookies = state.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)

            _skipped_ls_items: list[str] = []
            origins = state.get("origins", [])
            for origin_data in origins:
                origin = origin_data.get("origin", "")
                local_storage = origin_data.get("localStorage", [])

                if local_storage and origin:
                    if self._cdp_url and not self._cdp_context_owned and self._context:
                        # CDP borrowed mode: page.evaluate() hangs. Use DOMStorage CDP protocol.
                        # DOMStorage.setDOMStorageItem may fail with "Frame not found" when
                        # the target origin has no active frame — this is expected in CDP
                        # borrowed mode.  Collect failures and warn rather than hard-fail,
                        # because cookies have already been restored successfully.
                        session = await self._context.new_cdp_session(page)
                        try:
                            storage_id = {"storageId": {"securityOrigin": origin, "isLocalStorage": True}}
                            for item in local_storage:
                                name = item.get("name", "")
                                value = item.get("value", "")
                                if name:
                                    try:
                                        await asyncio.wait_for(
                                            session.send("DOMStorage.setDOMStorageItem", {
                                                **storage_id,
                                                "key": name,
                                                "value": value,
                                            }),
                                            timeout=5.0,
                                        )
                                    except Exception as _ls_err:
                                        logger.debug(
                                            "[restore_storage_state] localStorage item skipped "
                                            "(origin=%s key=%s): %s",
                                            origin, name, _ls_err,
                                        )
                                        _skipped_ls_items.append(f"{origin}/{name}")
                        finally:
                            try:
                                await session.detach()
                            except Exception:
                                pass
                    else:
                        for item in local_storage:
                            name = item.get("name", "")
                            value = item.get("value", "")
                            if name:
                                await asyncio.wait_for(
                                    page.evaluate(
                                        f"localStorage.setItem({json.dumps(name)}, {json.dumps(value)})"
                                    ),
                                    timeout=10.0,
                                )

            result = f"Storage state restored from: {filename} ({len(cookies)} cookies)"
            if _skipped_ls_items:
                result += (
                    f". Warning: {len(_skipped_ls_items)} localStorage item(s) could not be"
                    " restored in CDP borrowed mode (navigate to the target origin first,"
                    " then call storage-load again to apply localStorage)"
                )
            logger.info(f"[restore_storage_state] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to restore storage state: {str(e)}"
            logger.error(f"[restore_storage_state] {error_msg}")
            _raise_operation_error(error_msg)

    async def clear_cookies(
        self,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        path: Optional[str] = None,
    ) -> str:
        """Clear cookies from the browser context.

        Parameters
        ----------
        name : Optional[str], optional
            Clear only cookies with this exact name. Default clears all.
        domain : Optional[str], optional
            Clear only cookies whose domain contains this string. Default clears all.
        path : Optional[str], optional
            Clear only cookies with this exact path. Default clears all.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[clear_cookies] start name={name} domain={domain} path={path}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            await context.clear_cookies(name=name, domain=domain, path=path)

            if name or domain or path:
                result = "Cookies cleared (filtered)"
            else:
                result = "All cookies cleared"
            logger.info(f"[clear_cookies] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to clear cookies: {str(e)}"
            logger.error(f"[clear_cookies] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_cookies(
        self,
        urls: Optional[list] = None,
        *,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        path: Optional[str] = None,
    ) -> str:
        """Get cookies from the browser context.

        Parameters
        ----------
        urls : Optional[list], optional
            List of URLs to get cookies for. If not provided, returns all cookies.
        name : Optional[str], optional
            Filter cookies by exact name.
        domain : Optional[str], optional
            Filter cookies by domain substring match.
        path : Optional[str], optional
            Filter cookies by path prefix match.

        Returns
        -------
        str
            JSON string containing the cookies.
        """
        try:
            logger.info(
                f"[get_cookies] start urls={urls} name={name} domain={domain} path={path}"
            )

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context

            if urls:
                cookies = await context.cookies(urls)
            else:
                cookies = await context.cookies()

            if name:
                cookies = [cookie for cookie in cookies if cookie.get("name") == name]
            if domain:
                cookies = [
                    cookie
                    for cookie in cookies
                    if domain in (cookie.get("domain") or "")
                ]
            if path:
                cookies = [
                    cookie
                    for cookie in cookies
                    if (cookie.get("path") or "").startswith(path)
                ]

            result = json.dumps(cookies, indent=2)
            logger.info(f"[get_cookies] done count={len(cookies)}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get cookies: {str(e)}"
            logger.error(f"[get_cookies] {error_msg}")
            _raise_operation_error(error_msg)

    async def set_cookie(
        self,
        name: str,
        value: str,
        url: Optional[str] = None,
        domain: Optional[str] = None,
        path: str = "/",
        expires: Optional[float] = None,
        http_only: bool = False,
        secure: bool = False,
        same_site: Optional[str] = None,
    ) -> str:
        """Set a cookie in the browser context.

        Parameters
        ----------
        name : str
            Cookie name.
        value : str
            Cookie value.
        url : Optional[str], optional
            URL to associate the cookie with. Either url or domain must be specified.
        domain : Optional[str], optional
            Cookie domain. Either url or domain must be specified.
        path : str, optional
            Cookie path. Default is "/".
        expires : Optional[float], optional
            Unix timestamp when the cookie expires.
        http_only : bool, optional
            Whether the cookie is HTTP only. Default is False.
        secure : bool, optional
            Whether the cookie requires HTTPS. Default is False.
        same_site : Optional[str], optional
            SameSite attribute. Options: "Strict", "Lax", "None".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[set_cookie] start name={name}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            if not url and not domain:
                page_url = getattr(page, "url", "")
                parsed = urlparse(page_url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    _raise_invalid_input(
                        "Either url or domain must be specified (current page URL has no host)",
                        code="INVALID_COOKIE_TARGET",
                        details={"page_url": page_url},
                    )
                domain = parsed.hostname
            if url and domain:
                _raise_invalid_input("Provide either url or domain, not both", code="INVALID_COOKIE_TARGET")

            context = page.context

            cookie: Dict[str, Any] = {
                "name": name,
                "value": value,
                "httpOnly": http_only,
                "secure": secure,
            }

            if url:
                cookie["url"] = url
            if domain:
                cookie["domain"] = domain
                cookie["path"] = path
            if expires is not None:
                cookie["expires"] = expires
            if same_site:
                cookie["sameSite"] = same_site

            await context.add_cookies([cookie])

            result = f"Cookie '{name}' set successfully"
            logger.info(f"[set_cookie] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to set cookie: {str(e)}"
            logger.error(f"[set_cookie] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Verification Tools ====================

    async def verify_element_visible(
        self,
        role: str,
        accessible_name: str,
        timeout: float = 5.0,
    ) -> str:
        """Verify that an element with the given role and name is visible.

        Parameters
        ----------
        role : str
            ARIA role of the element (e.g., "button", "link", "textbox").
        accessible_name : str
            Accessible name of the element (usually its text content or aria-label).
        timeout : float, optional
            Maximum time to wait for the element in seconds. Default is 5.0.

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the target element is not visible.
        """
        try:
            logger.info(f"[verify_element_visible] start role={role} name={accessible_name}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            locator = page.get_by_role(role, name=accessible_name)

            try:
                await locator.wait_for(state="visible", timeout=timeout * 1000.0)
                result = f"PASS: Element with role '{role}' and name '{accessible_name}' is visible"
                logger.info(f"[verify_element_visible] {result}")
                return result
            except Exception:
                result = f"FAIL: Element with role '{role}' and name '{accessible_name}' is not visible"
                logger.warning(f"[verify_element_visible] {result}")
                _raise_verification_error(
                    result,
                    details={"role": role, "name": accessible_name, "timeout": timeout},
                )
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_element_visible] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_text_visible(
        self,
        text: str,
        exact: bool = False,
        timeout: float = 5.0,
    ) -> str:
        """Verify that specific text is visible on the page.

        Parameters
        ----------
        text : str
            Text to search for on the page.
        exact : bool, optional
            Whether to match the text exactly. Default is False (substring match).
        timeout : float, optional
            Maximum time to wait for the text in seconds. Default is 5.0.

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the target text is not visible.
        """
        try:
            logger.info(f"[verify_text_visible] start text={text!r} exact={exact}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            try:
                await self._wait_for_text_across_frames(
                    page, text, exact=exact, timeout_ms=timeout * 1000.0,
                )
                result = f"PASS: Text '{text}' is visible on the page"
                logger.info(f"[verify_text_visible] {result}")
                return result
            except TimeoutError:
                result = f"FAIL: Text '{text}' is not visible on the page"
                logger.warning(f"[verify_text_visible] {result}")
                _raise_verification_error(
                    result,
                    details={"text": text, "exact": exact, "timeout": timeout},
                )
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_text_visible] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_value(
        self,
        ref: str,
        value: str,
        attribute: str = "value",
    ) -> str:
        """Verify that an element has the expected value or attribute.

        Parameters
        ----------
        ref : str
            Element ref obtained from snapshot refs (e.g., "8d4b03a9").
        value : str
            Expected value.
        attribute : str, optional
            Attribute or property to check. Default is "value".

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If the element ref cannot be resolved.
        VerificationError
            If the actual value/attribute does not match.
        """
        try:
            logger.info(f"[verify_value] start ref={ref} expected={value} attr={attribute}")

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                _raise_state_error(
                    f"Element ref {ref} is not available",
                    code="REF_NOT_AVAILABLE",
                    details={"ref": ref},
                )

            if attribute == "value":
                actual = await locator.input_value()
            elif attribute == "textContent":
                actual = await locator.text_content()
            elif attribute == "innerText":
                actual = await locator.inner_text()
            else:
                actual = await locator.get_attribute(attribute)

            if actual is None:
                actual = ""

            if actual == value:
                result = f"PASS: Element {ref} has {attribute}='{value}'"
                logger.info(f"[verify_value] {result}")
            else:
                result = f"FAIL: Element {ref} {attribute} mismatch. Expected: '{value}', Actual: '{actual}'"
                logger.warning(f"[verify_value] {result}")
                _raise_verification_error(
                    result,
                    details={"ref": ref, "attribute": attribute, "expected": value, "actual": actual},
                )

            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_value] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_element_state(
        self,
        ref: str,
        state: str,
    ) -> str:
        """Verify that an element is in the expected state.

        Parameters
        ----------
        ref : str
            Element ref obtained from snapshot refs (e.g., "1f79fe5e").
        state : str
            Expected state. Options: "visible", "hidden", "enabled",
            "disabled", "checked", "unchecked", "editable".

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        InvalidInputError
            If the requested state is unsupported.
        StateError
            If the element ref cannot be resolved.
        VerificationError
            If the element does not match the expected state.
        """
        try:
            logger.info(f"[verify_element_state] start ref={ref} state={state}")

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                _raise_state_error(
                    f"Element ref {ref} is not available",
                    code="REF_NOT_AVAILABLE",
                    details={"ref": ref},
                )

            result = ""
            try:
                if state == "visible":
                    is_visible = await locator.is_visible()
                    result = f"PASS: Element {ref} is visible" if is_visible else f"FAIL: Element {ref} is not visible"

                elif state == "hidden":
                    is_hidden = await locator.is_hidden()
                    result = f"PASS: Element {ref} is hidden" if is_hidden else f"FAIL: Element {ref} is not hidden"

                elif state == "enabled":
                    is_enabled = await locator.is_enabled()
                    result = f"PASS: Element {ref} is enabled" if is_enabled else f"FAIL: Element {ref} is not enabled"

                elif state == "disabled":
                    is_disabled = await locator.is_disabled()
                    result = f"PASS: Element {ref} is disabled" if is_disabled else f"FAIL: Element {ref} is not disabled"

                elif state == "checked":
                    is_checked = await locator.is_checked()
                    result = f"PASS: Element {ref} is checked" if is_checked else f"FAIL: Element {ref} is not checked"

                elif state == "unchecked":
                    is_checked = await locator.is_checked()
                    result = f"PASS: Element {ref} is unchecked" if not is_checked else f"FAIL: Element {ref} is checked (expected unchecked)"

                elif state == "editable":
                    is_editable = await locator.is_editable()
                    result = f"PASS: Element {ref} is editable" if is_editable else f"FAIL: Element {ref} is not editable"

                else:
                    _raise_invalid_input(
                        f"Unknown state '{state}'",
                        code="INVALID_STATE_VALUE",
                        details={"state": state},
                    )

            except BridgicBrowserError:
                raise
            except Exception as e:
                if isinstance(e, InvalidInputError):
                    raise
                result = f"FAIL: Could not check state '{state}' for element {ref}: {str(e)}"
                _raise_verification_error(
                    result,
                    details={"ref": ref, "state": state},
                )

            logger.info(f"[verify_element_state] {result}")
            if result.startswith("FAIL:"):
                _raise_verification_error(
                    result,
                    details={"ref": ref, "state": state},
                )
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, InvalidInputError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_element_state] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_url(self, expected_url: str, exact: bool = False) -> str:
        """Verify the current page URL.

        Parameters
        ----------
        expected_url : str
            Expected URL or URL substring.
        exact : bool, optional
            When True, the full URL must match exactly.
            When False (default), checks that ``expected_url`` is a substring
            of the actual URL (e.g., ``"/dashboard"`` matches
            ``"https://app.example.com/dashboard?tab=1"``).

        Returns
        -------
        str
            "PASS: URL matches. Current: <actual_url>" on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the URL does not match the expectation, with the message:
            "FAIL: URL mismatch. Expected: '<expected_url>', Actual: '<actual_url>'".
        """
        try:
            logger.info(f"[verify_url] start expected={expected_url} exact={exact}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            actual_url = page.url

            if exact:
                matches = actual_url == expected_url
            else:
                matches = expected_url in actual_url

            if matches:
                result = f"PASS: URL matches. Current: {actual_url}"
                logger.info(f"[verify_url] {result}")
            else:
                result = f"FAIL: URL mismatch. Expected: '{expected_url}', Actual: '{actual_url}'"
                logger.warning(f"[verify_url] {result}")
                _raise_verification_error(
                    result,
                    details={"expected_url": expected_url, "actual_url": actual_url, "exact": exact},
                )

            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_url] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_title(self, expected_title: str, exact: bool = False) -> str:
        """Verify the current page title.

        Parameters
        ----------
        expected_title : str
            Expected title or title pattern.
        exact : bool, optional
            Whether to match exactly. Default is False (contains check).

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the title does not match expectation.
        """
        try:
            logger.info(f"[verify_title] start expected={expected_title} exact={exact}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            actual_title = await self._get_page_title(page)

            if exact:
                matches = actual_title == expected_title
            else:
                matches = expected_title in actual_title

            if matches:
                result = f"PASS: Title matches. Current: '{actual_title}'"
                logger.info(f"[verify_title] {result}")
            else:
                result = f"FAIL: Title mismatch. Expected: '{expected_title}', Actual: '{actual_title}'"
                logger.warning(f"[verify_title] {result}")
                _raise_verification_error(
                    result,
                    details={"expected_title": expected_title, "actual_title": actual_title, "exact": exact},
                )

            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_title] {error_msg}")
            _raise_verification_error(error_msg)

    # ==================== DevTools (Tracing and Video) ====================

    async def start_tracing(
        self,
        screenshots: bool = True,
        snapshots: bool = True,
        sources: bool = False,
    ) -> str:
        """Start browser tracing.

        Parameters
        ----------
        screenshots : bool, optional
            Whether to capture screenshots during trace. Default is True.
        snapshots : bool, optional
            Whether to capture DOM snapshots. Default is True.
        sources : bool, optional
            Whether to include source files. Default is False.

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        Only one trace can be active at a time per browser context.
        """
        try:
            logger.info(f"[start_tracing] start screenshots={screenshots} snapshots={snapshots}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            context_key = _get_context_key(context)

            if context_key in self._tracing_state and self._tracing_state[context_key]:
                _raise_state_error("Tracing is already active. Stop the current trace first.", code="TRACING_ALREADY_ACTIVE")

            await context.tracing.start(
                screenshots=screenshots,
                snapshots=snapshots,
                sources=sources,
            )

            self._tracing_state[context_key] = True

            result = "Tracing started"
            logger.info(f"[start_tracing] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to start tracing: {str(e)}"
            logger.error(f"[start_tracing] {error_msg}")
            _raise_operation_error(error_msg)

    async def stop_tracing(self, filename: Optional[str] = None) -> str:
        """Stop browser tracing and save the trace file.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the trace file. If not provided, saves to a temporary file.

        Returns
        -------
        str
            On success: Returns the file path where trace was saved.
        """
        try:
            logger.info(f"[stop_tracing] start filename={filename}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            context_key = _get_context_key(context)

            if context_key not in self._tracing_state or not self._tracing_state[context_key]:
                _raise_state_error("No active tracing to stop. Start tracing first.", code="NO_ACTIVE_TRACING")

            if filename:
                if not filename.lower().endswith(".zip"):
                    filename = f"{filename}.zip"
                output_path = filename

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
            else:
                fd, output_path = tempfile.mkstemp(suffix=".zip", prefix="browser_trace_")
                os.close(fd)

            await context.tracing.stop(path=output_path)
            self._tracing_state[context_key] = False

            result = f"Trace saved to: {output_path}"
            logger.info(f"[stop_tracing] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop tracing: {str(e)}"
            logger.error(f"[stop_tracing] {error_msg}")
            _raise_operation_error(error_msg)

    @staticmethod
    def _allocate_video_temp_path() -> str:
        """Generate a unique temp .webm path for one page's recording.

        Uses ``tempfile.mkstemp`` (O_EXCL) so the path is guaranteed
        unique even when many recorders are allocated within the same
        second — a previous timestamp+random scheme had a non-zero
        collision risk under burst multi-page start_video() calls.
        We immediately remove the empty file because ffmpeg insists on
        creating the output itself.
        """
        os.makedirs(BRIDGIC_TMP_DIR, exist_ok=True)
        fd, path = tempfile.mkstemp(
            prefix="video_", suffix=".webm", dir=str(BRIDGIC_TMP_DIR)
        )
        os.close(fd)
        try:
            os.unlink(path)
        except OSError:
            pass
        return path

    async def _switch_video_to_page(self, new_page: "Page") -> None:
        """If recording active, switch screencast to *new_page*. No-op otherwise."""
        if self._video_recorder is None or self._video_session is None:
            return
        if self._video_recorder.current_page == new_page:
            return
        if new_page.is_closed():
            return
        try:
            await self._video_recorder.switch_page(new_page)
        except Exception as e:
            logger.warning("[video] switch_page failed: %s", e)

    async def _start_single_video_recorder(self, page: "Page") -> None:
        """Start the single-stream recorder targeting *page*."""
        if self._video_session is None or page.is_closed():
            return
        output_path = self._allocate_video_temp_path()
        w = int(self._video_session["width"])
        h = int(self._video_session["height"])
        recorder = _video_recorder_mod.VideoRecorder(
            page.context, page, output_path, (w, h),
        )
        await recorder.start()
        self._video_recorder = recorder
        logger.info("[start_video] recording active tab → %s", output_path)

    async def start_video(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> str:
        """Start single-stream video recording on the active tab.

        One ffmpeg process records the currently active page. When the
        user switches tabs (via ``switch_tab``, ``new_tab``, etc.) the
        CDP screencast source is hot-swapped to the new page — ffmpeg
        stays alive and the output is a single continuous .webm file.

        Parameters
        ----------
        width : Optional[int], optional
            Video width in pixels. Defaults to the current viewport width
            (rounded down to an even number). Pass an explicit value to
            override — e.g. to downscale a 4K viewport.
        height : Optional[int], optional
            Video height in pixels. Defaults to the current viewport height
            (rounded down to an even number).

        Returns
        -------
        str
            "Video recording started (recording active tab)".
        """
        logger.info(f"[start_video] start width={width} height={height}")

        # Validation runs BEFORE any state mutation so that "already active" /
        # "no active page" errors cannot trigger the rollback path below — that
        # path would otherwise tear down the *previous* successful session.
        page = await self.get_current_page()
        if page is None:
            _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

        context = page.context
        context_key = _get_context_key(context)

        if self._video_session is not None or self._video_state.get(context_key):
            _raise_state_error("Video recording already active", code="VIDEO_ALREADY_ACTIVE")

        # Compute the recording size.
        #
        # NOTE: this intentionally diverges from Playwright's screencast.ts
        # ``startScreencast()`` (lines 90-98), which caps the longest side at
        # 800 px to keep encoder cost low. That cap is the dominant source of
        # blur for bridgic recordings: with a typical 1280×800 viewport, Chrome
        # downsamples to 800×500 *inside the browser* before frames ever reach
        # ffmpeg, so no encoder tuning can recover the lost detail. Bridgic
        # videos are usually replayed by humans inspecting an LLM session where
        # legibility wins over a few extra MB of CPU and disk.
        #
        # Default policy: record at the page's actual CSS pixel dimensions.
        # We query ``window.innerWidth/innerHeight`` directly instead of
        # trusting ``page.viewport_size``:
        #
        #   - launch mode with explicit viewport: both agree
        #   - launch mode without an explicit viewport: both agree
        #   - CDP attach mode: ``page.viewport_size`` is ``None`` because
        #     bridgic never called ``setViewportSize`` on the foreign Chrome.
        #     Falling back to a hard-coded ``800×600`` is almost always wrong:
        #     the real window is wider (typically 16:9), so Chrome downsamples
        #     to fit within 800×600 and ffmpeg's ``scale`` filter stretches
        #     the frame to the target size. Querying
        #     ``window.innerWidth/innerHeight`` returns the true visible area
        #     for any of the three modes.
        # ``& ~1``: round down to an even number — VP8 requires even
        # width and height.
        viewport_width = _DEFAULT_VIDEO_WIDTH
        viewport_height = _DEFAULT_VIDEO_HEIGHT
        try:
            # Use CDP Page.getLayoutMetrics instead of page.evaluate() — avoids the
            # Playwright _mainContext() hang on pre-existing tabs in CDP borrowed mode.
            _session = await self._context.new_cdp_session(page)
            try:
                _metrics = await asyncio.wait_for(
                    _session.send("Page.getLayoutMetrics"),
                    timeout=5.0,
                )
            finally:
                try:
                    await _session.detach()
                except Exception:
                    pass
            # Use cssVisualViewport (not cssLayoutViewport) because it
            # represents the actual visible pixel area after pinch-zoom,
            # matching what Chrome's screencast captures.
            # get_page_size_info() uses cssLayoutViewport for scroll
            # reporting — different purpose, both choices are intentional.
            _vp = _metrics.get("cssVisualViewport", {})
            qw = int(_vp.get("clientWidth") or 0)
            qh = int(_vp.get("clientHeight") or 0)
            if qw > 0 and qh > 0:
                viewport_width = qw
                viewport_height = qh
            else:
                raise ValueError(f"non-positive dimensions from CDP: {_vp}")
        except Exception as exc:
            # Fall back to viewport_size, then the hard default above. Logged
            # but non-fatal so a hardened CSP page can still record.
            logger.warning(
                "[start_video] could not query window dimensions (%s); "
                "falling back to page.viewport_size", exc,
            )
            vp = page.viewport_size
            if vp:
                viewport_width = int(vp["width"]) or viewport_width
                viewport_height = int(vp["height"]) or viewport_height

        w = (width or viewport_width) & ~1
        h = (height or viewport_height) & ~1

        # Build the session record up front so _start_single_video_recorder
        # picks up the parameters. From this point on, any failure must
        # roll back the partially-set-up session state.
        self._video_session = {
            "width": w,
            "height": h,
            "context": context,
            "page_listener": None,
        }
        self._video_recorder = None
        self._video_state[context_key] = True

        # Subscribe to future pages so newly opened tabs auto-switch
        # the screencast source to the new page. Define the listener BEFORE
        # the try so both the happy path and the rollback can reference it.
        def _on_page_created(new_page: Page) -> None:
            try:
                asyncio.get_running_loop().create_task(
                    self._switch_video_to_page(new_page),
                )
            except RuntimeError:
                logger.warning(
                    "[start_video] no running loop to switch video to new page",
                )

        # Sentinel: True once context.on() has attached `_on_page_created`.
        # Required so the rollback path can ALWAYS remove the listener —
        # including the narrow window where attach succeeded but
        # ``self._video_session["page_listener"] = ...`` raised (a real
        # scenario under BaseException / MemoryError between two
        # synchronous lines). Without this sentinel the listener would
        # survive the rollback as a zombie. (C3.)
        _listener_attached = False

        try:
            # Single-stream: start one recorder on the active page.
            await self._start_single_video_recorder(page)
            if self._video_recorder is None:
                raise RuntimeError("Failed to start video recorder on active page")

            context.on("page", _on_page_created)
            _listener_attached = True
            self._video_session["page_listener"] = _on_page_created

            result = "Video recording started (recording active tab)"
            logger.info("[start_video] %s", result)
            return result
        except Exception as e:
            # Rollback the session state we set up above so future
            # start_video() calls are not blocked by a phantom session.
            if _listener_attached:
                try:
                    context.remove_listener("page", _on_page_created)
                except Exception:
                    # Best-effort: a backend that raises on remove_listener
                    # shouldn't mask the original error.
                    pass
            self._video_session = None
            if self._video_recorder is not None:
                try:
                    await self._video_recorder.stop()
                except Exception:
                    pass
                self._video_recorder = None
            self._video_state.pop(context_key, None)
            if isinstance(e, BridgicBrowserError):
                raise
            error_msg = f"Failed to start video: {str(e)}"
            logger.error(f"[start_video] {error_msg}")
            _raise_operation_error(error_msg)

    @staticmethod
    def _resolve_video_dest(filename: str) -> str:
        """Resolve a user-supplied filename to an absolute path.

        Three input shapes are accepted:
          "demo.webm"   → cwd/demo.webm
          "./videos/"   → ./videos/video_<timestamp>.webm  (auto-named)
          "demo"        → cwd/demo.webm  (".webm" suffix auto-added)
        """
        if filename.endswith(os.sep) or filename.endswith("/") or os.path.isdir(filename):
            import time as _time
            dest_dir = os.path.abspath(filename)
            resolved = os.path.join(dest_dir, f"video_{_time.strftime('%Y%m%d_%H%M%S')}.webm")
        else:
            if not filename.lower().endswith(".webm"):
                filename = f"{filename}.webm"
            resolved = os.path.abspath(filename)
        dest_dir = os.path.dirname(resolved)
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        return resolved

    @staticmethod
    def _move_video_local(src: Path, dest: str) -> str:
        """Move a video file locally (rename, falling back to copy).

        Why we do not use Playwright's ``video.save_as()``:
          save_as() streams the file across the Node RPC bridge in 1 MB
          base64 chunks. Large recordings can take tens of seconds or
          even time out. A local ``os.rename`` is O(1); even when we
          fall back to copy2 (cross-device move), it is orders of
          magnitude faster than the RPC stream.
        """
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        try:
            os.rename(str(src), dest)
        except OSError:
            import shutil
            shutil.copy2(str(src), dest)
            try:
                src.unlink(missing_ok=True)
            except Exception:
                pass
        return os.path.abspath(dest)

    @staticmethod
    def _resolve_multi_video_dests(
        filename: Optional[str], count: int,
    ) -> Optional[List[str]]:
        """Build N destination paths for ``count`` recorded video files.

        Parameters
        ----------
        filename : Optional[str]
            User-supplied destination.  ``None`` leaves files in temp dir.
            A directory (``./videos/`` or existing dir) → each file keeps
            its auto-generated basename inside that dir.
            A file path (``./out.webm``) → first file uses the exact path,
            subsequent files get ``-1``, ``-2``, … suffix inserted before
            the extension.
        count : int
            Number of recorded videos.

        Returns
        -------
        Optional[List[str]]
            ``None`` when ``filename`` is ``None`` (keep temp paths),
            otherwise a list of ``count`` destination paths.
        """
        if filename is None:
            return None
        if count == 0:
            return []
        is_dir = (
            filename.endswith(os.sep)
            or filename.endswith("/")
            or os.path.isdir(filename)
        )
        if is_dir:
            import time as _time
            dest_dir = os.path.abspath(filename)
            os.makedirs(dest_dir, exist_ok=True)
            ts = _time.strftime("%Y%m%d_%H%M%S")
            out: List[str] = []
            for i in range(count):
                name = f"video_{ts}.webm" if i == 0 else f"video_{ts}-{i}.webm"
                out.append(os.path.join(dest_dir, name))
            return out
        # Single-file target: use as base name; append -N for extras.
        base = filename if filename.lower().endswith(".webm") else f"{filename}.webm"
        base_abs = os.path.abspath(base)
        dest_dir = os.path.dirname(base_abs)
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        stem, ext = os.path.splitext(base_abs)
        return [base_abs if i == 0 else f"{stem}-{i}{ext}" for i in range(count)]

    async def stop_video(self, filename: Optional[str] = None) -> str:
        """Stop video recording and save the file.

        Files are saved immediately — no need to wait for browser close.

        Parameters
        ----------
        filename : Optional[str], optional
            Destination for the video file.  Accepts a file path
            (``./videos/demo.webm``) or a directory (``./videos/``).
            The ``.webm`` extension is added automatically when missing.
            If not provided, the file stays in the temporary directory.

        Returns
        -------
        str
            Confirmation with the saved file path.
        """
        try:
            logger.info(f"[stop_video] start filename={filename}")

            if self._context is None:
                _raise_state_error("No context is open", code="NO_CONTEXT")
            context_key = _get_context_key(self._context)

            if self._video_session is None and self._video_recorder is None:
                _raise_state_error(
                    "No active video recording. Use video-start first.",
                    code="NO_ACTIVE_RECORDING",
                )

            # Detach page-creation listener so stopping recording in
            # parallel with a tab open doesn't race into a switch.
            if self._video_session is not None:
                listener = self._video_session.get("page_listener")
                if listener is not None:
                    try:
                        self._context.remove_listener("page", listener)
                    except Exception:
                        pass

            # Snap the recorder to a local var so a concurrent close()
            # won't also try to stop it.
            recorder = self._video_recorder
            self._video_recorder = None
            self._video_session = None
            self._video_state[context_key] = False

            if recorder is None:
                return "Video recording stopped (no recorder was active)"

            # Stop the single recorder.
            try:
                temp_path: str = await asyncio.wait_for(
                    recorder.stop(), timeout=30.0,
                )
            except Exception as exc:
                logger.warning("[stop_video] recorder stop failed: %s", exc)
                return "Video recording stopped (file may be incomplete)"

            if not temp_path or not os.path.isfile(temp_path):
                return "Video recording stopped (no file was produced)"

            # Move to user destination if requested.
            if filename is not None:
                dest = self._resolve_video_dest(filename)
                try:
                    self._move_video_local(Path(temp_path), dest)
                    temp_path = dest
                except Exception as move_err:
                    logger.error(
                        "[stop_video] move failed, file stays at: %s (%s)",
                        temp_path, move_err,
                    )

            result = f"Video saved to: {temp_path}"
            logger.info(f"[stop_video] done: {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop video: {str(e)}"
            logger.error(f"[stop_video] {error_msg}")
            _raise_operation_error(error_msg)

    async def add_trace_chunk(self, title: Optional[str] = None) -> str:
        """Add a new chunk to the trace.

        Parameters
        ----------
        title : Optional[str], optional
            Title for the new trace chunk.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[add_trace_chunk] start title={title}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            context_key = _get_context_key(context)

            if context_key not in self._tracing_state or not self._tracing_state[context_key]:
                _raise_state_error("No active tracing. Start tracing first.", code="NO_ACTIVE_TRACING")

            await context.tracing.start_chunk(title=title)

            result = f"New trace chunk started" + (f": {title}" if title else "")
            logger.info(f"[add_trace_chunk] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to add trace chunk: {str(e)}"
            logger.error(f"[add_trace_chunk] {error_msg}")
            _raise_operation_error(error_msg)
