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
import socket
import subprocess
import time
import urllib.request
from pathlib import Path


def find_chrome_binary() -> str | None:
    """Return the path to a Chrome / Chromium binary, or *None* if not found.

    The returned binary must support ``--remote-debugging-port``.
    """
    # 1. Explicit override ─────────────────────────────────────────────────
    env_bin = os.environ.get("CHROME_BIN")
    if env_bin and os.path.isfile(env_bin) and _is_viable_browser_binary(env_bin):
        return env_bin

    system = platform.system()

    # GitHub Actions Linux runners already install Playwright's Chromium via CI
    # setup; prefer it there to avoid distro wrapper binaries that exist on
    # disk but fail to expose a usable CDP endpoint.
    if system == "Linux" and os.environ.get("GITHUB_ACTIONS") == "true":
        playwright_bin = _find_playwright_chromium(system)
        if playwright_bin and _is_viable_browser_binary(playwright_bin):
            return playwright_bin

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
        if os.path.isfile(path) and _is_viable_browser_binary(path):
            return path

    # 3. shutil.which() fallback ───────────────────────────────────────────
    names = (
        ["chrome.exe", "chromium.exe"]
        if system == "Windows"
        else ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"]
    )
    for name in names:
        found = shutil.which(name)
        if found and _is_viable_browser_binary(found):
            return found

    # 4. Playwright's bundled Chromium ─────────────────────────────────────
    playwright_bin = _find_playwright_chromium(system)
    if playwright_bin and _is_viable_browser_binary(playwright_bin):
        return playwright_bin
    return None


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


# ── CDP Chrome process helpers (shared by lifecycle integration tests) ──────


def pick_free_port() -> int:
    """Bind to port 0, return the assigned port, then release it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def wait_for_chrome(port: int, timeout: float = 20.0) -> None:
    """Block until Chrome's /json/version endpoint on *port* is reachable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=3
            ):
                return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"Chrome did not start on port {port}")


def launch_chrome(
    chrome_bin: str,
    port: int,
    user_data_dir: Path,
    *,
    extra_args: list[str] | None = None,
) -> subprocess.Popen:
    """Launch a headless Chrome bound to *port* and wait until CDP is live."""
    args = [
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--headless=new",
        "about:blank",
    ]
    if os.name != "nt":
        args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
    if extra_args:
        args.extend(extra_args)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_chrome(port, timeout=25.0)
    except Exception:
        proc.kill()
        raise
    return proc


def kill_chrome(proc: subprocess.Popen) -> None:
    """Terminate the Chrome process *proc* and reap it."""
    proc.kill()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        pass


def _is_viable_browser_binary(path: str) -> bool:
    """Return True when *path* looks executable and responds to --version."""
    if not os.access(path, os.X_OK):
        return False
    try:
        result = subprocess.run(
            [path, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
