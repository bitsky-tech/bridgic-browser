"""Cross-platform Chrome / Chromium binary discovery for CDP integration tests.

Search order
------------
1. ``CHROME_BIN`` environment variable (explicit override for CI or local dev)
2. Platform-specific well-known system paths
3. ``shutil.which()`` for common executable names
4. Playwright's bundled Chromium (always available after ``playwright install chromium``)
"""

from __future__ import annotations

import os
import platform
import shutil


def find_chrome_binary() -> str | None:
    """Return the path to a Chrome / Chromium binary, or *None* if not found.

    The returned binary must support ``--remote-debugging-port``.
    """
    # 1. Explicit override ─────────────────────────────────────────────────
    env_bin = os.environ.get("CHROME_BIN")
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    system = platform.system()

    # 2. Platform-specific known paths ─────────────────────────────────────
    candidates: list[str] = []
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        candidates = [
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]
    elif system == "Windows":
        for env_key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_key, "")
            if base:
                candidates.append(
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                )

    for path in candidates:
        if os.path.isfile(path):
            return path

    # 3. shutil.which() fallback ───────────────────────────────────────────
    names = (
        ["chrome.exe", "chromium.exe"]
        if system == "Windows"
        else ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"]
    )
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    # 4. Playwright's bundled Chromium ─────────────────────────────────────
    return _find_playwright_chromium(system)


# ── internal ──────────────────────────────────────────────────────────────────


def _find_playwright_chromium(system: str | None = None) -> str | None:
    """Locate Playwright's bundled Chromium binary in its cache directory.

    Handles multiple Playwright versions and architectures:
    - Newer Playwright uses ``Google Chrome for Testing.app`` (macOS) and
      architecture-suffixed directories (``chrome-mac-arm64``, ``chrome-linux64``).
    - Older Playwright uses ``Chromium.app`` and ``chrome-mac`` / ``chrome-linux``.
    """
    if system is None:
        system = platform.system()

    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path:
        cache_dir = browsers_path
    elif system == "Linux":
        cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    elif system == "Darwin":
        cache_dir = os.path.expanduser("~/Library/Caches/ms-playwright")
    elif system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA", "")
        cache_dir = os.path.join(local_app, "ms-playwright") if local_app else ""
    else:
        return None

    if not cache_dir or not os.path.isdir(cache_dir):
        return None

    # Scan for chromium-* dirs (newest first, skip chromium_headless_shell-*)
    try:
        chromium_dirs = sorted(
            (d for d in os.listdir(cache_dir)
             if d.startswith("chromium-") and "headless" not in d),
            reverse=True,
        )
    except OSError:
        return None

    for dirname in chromium_dirs:
        base = os.path.join(cache_dir, dirname)
        for exe in _chromium_exe_candidates(base, system):
            if os.path.isfile(exe):
                return exe

    return None


def _chromium_exe_candidates(base: str, system: str) -> list[str]:
    """Return possible executable paths within a Playwright chromium-XXXX dir."""
    results: list[str] = []

    if system == "Darwin":
        # Scan for chrome-mac* subdirectories (chrome-mac, chrome-mac-arm64, …)
        _scan_mac_app_bundles(base, results)
    elif system == "Linux":
        # chrome-linux64/chrome, chrome-linux/chrome
        for sub in ("chrome-linux64", "chrome-linux"):
            results.append(os.path.join(base, sub, "chrome"))
    elif system == "Windows":
        # chrome-win64/chrome.exe, chrome-win/chrome.exe
        for sub in ("chrome-win64", "chrome-win"):
            results.append(os.path.join(base, sub, "chrome.exe"))

    return results


def _scan_mac_app_bundles(base: str, results: list[str]) -> None:
    """Append all possible macOS .app bundle executables under *base*."""
    try:
        subdirs = [d for d in os.listdir(base) if d.startswith("chrome-mac")]
    except OSError:
        return

    # Prefer arm64 directory on Apple Silicon
    subdirs.sort(key=lambda d: ("arm64" not in d, d))

    for sub in subdirs:
        sub_path = os.path.join(base, sub)
        # Newer Playwright: "Google Chrome for Testing.app"
        results.append(os.path.join(
            sub_path, "Google Chrome for Testing.app",
            "Contents", "MacOS", "Google Chrome for Testing",
        ))
        # Older Playwright: "Chromium.app"
        results.append(os.path.join(
            sub_path, "Chromium.app", "Contents", "MacOS", "Chromium",
        ))
