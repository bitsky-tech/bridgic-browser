"""CDP WebSocket URL discovery helpers.

Used by ``Browser(cdp=...)`` and the ``bridgic-browser --cdp`` CLI flag to
resolve user input (bare port, ws:// URL, HTTP endpoint, ``auto``/``scan``) to
a concrete WebSocket URL that Playwright's ``connect_over_cdp`` can consume.
"""

import json
import logging
import os
import socket
import sys
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


_CDP_SCAN_DIRS: Dict[str, List[tuple]] = {
    "darwin": [
        ("Chrome",        "~/Library/Application Support/Google/Chrome"),
        ("Chrome Canary", "~/Library/Application Support/Google/Chrome Canary"),
        ("Chromium",      "~/Library/Application Support/Chromium"),
        ("Brave",         "~/Library/Application Support/BraveSoftware/Brave-Browser"),
    ],
    "linux": [
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
    """Return True if the CDP port behind ``ws_url`` is accepting TCP connections.

    Chrome normally removes its DevToolsActivePort file on graceful exit, but a
    crash or ``kill -9`` leaves it behind. Without a liveness probe, scan/file
    mode would return a stale ws URL and callers would only see a confusing
    connection error much later from ``connect_over_cdp``.

    Probe is a bare TCP connect — NOT an HTTP ``/json/version`` request.
    Chrome 144+ lets users enable remote debugging via ``chrome://inspect``,
    which writes DevToolsActivePort but does NOT necessarily expose the HTTP
    ``/json/`` endpoints (DNS-rebinding protection can block them). A TCP
    connect still succeeds in that case, and we only need the port to be
    listening — the actual handshake happens over WebSocket in
    ``connect_over_cdp`` afterwards.
    """
    try:
        parsed = urlparse(ws_url)
    except Exception:
        return False
    host = parsed.hostname or "localhost"
    port = parsed.port
    if port is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except ConnectionRefusedError:
        return False
    except (socket.timeout, TimeoutError):
        # Port is listening but not responding SYN/ACK in time — treat as dead.
        # Prevents callers from then waiting again inside connect_over_cdp with
        # a much more opaque error. Network-latency scenarios can still pass
        # a larger `timeout` argument from the caller.
        return False
    except OSError as exc:
        import errno
        hard_dead = {
            errno.ETIMEDOUT,
            errno.EHOSTUNREACH,
            errno.ENETUNREACH,
            errno.EHOSTDOWN,
        }
        if getattr(exc, "errno", None) in hard_dead:
            return False
        # DNS failures / transient EAGAIN / EINTR: stay tolerant.
        return True


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
            raise ConnectionError(
                f"Cannot reach Chrome debugging interface at {url}: {exc}\n"
                f"Make sure Chrome was started with --remote-debugging-port={port}"
            ) from exc
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to parse /json/version response: {exc}") from exc
        # Always rewrite the ws URL netloc to (host_in_url, port) so SSH
        # tunnels, container port-forwards, and reverse proxies work
        # correctly. Chrome embeds its own bound address in
        # webSocketDebuggerUrl ("ws://localhost:9222/..."), but we know the
        # address that actually got us a /json/version response — that's
        # the address the caller can also reach for the WebSocket.
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
            if not _probe_cdp_alive(ws_url):
                logger.debug(
                    "find_cdp_url(scan): skipping %s (%s) — stale DevToolsActivePort (port not reachable)",
                    label, base,
                )
                continue
            # DevToolsActivePort can outlive the Chrome session that wrote it:
            # a second Chrome instance binding the same port, or Chrome being
            # relaunched without re-writing the file, leaves the file's UUID
            # stale while the port is still alive. A stale UUID makes
            # connect_over_cdp return an opaque 404. Prefer /json/version to
            # get the current UUID; fall back to the file URL only when HTTP
            # is unreachable (Chrome 144+ chrome://inspect mode writes the
            # file but blocks /json/ via DNS-rebinding protection).
            port = urlparse(ws_url).port
            if port is not None:
                try:
                    fresh = find_cdp_url(mode="port", host="localhost", port=port)
                    logger.info(
                        "find_cdp_url(scan): found active CDP via %s (%s), UUID refreshed via /json/version",
                        label, base,
                    )
                    return fresh
                except (ConnectionError, ValueError) as exc:
                    logger.debug(
                        "find_cdp_url(scan): %s /json/version unreachable, using file URL as-is: %s",
                        label, exc,
                    )
            logger.info("find_cdp_url(scan): found active CDP port via %s (%s)", label, base)
            return ws_url
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
    ws_url_opt: Optional[str] = _read_devtools_active_port(base)
    if ws_url_opt is None:
        raise ValueError(
            f"DevToolsActivePort file is malformed or unreadable: {port_file}"
        )
    if not _probe_cdp_alive(ws_url_opt):
        _parsed_port = urlparse(ws_url_opt).port
        raise ConnectionError(
            f"DevToolsActivePort exists at {port_file} but Chrome is not "
            f"accepting CDP connections on port {_parsed_port}. The browser may "
            f"have crashed or been killed. Restart Chrome with "
            f"--remote-debugging-port=PORT and try again."
        )
    return ws_url_opt


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
        A ``ws://`` or ``wss://`` WebSocket URL ready to pass to ``Browser(cdp=...)``.

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
    if v.lower() in ("auto", "scan"):
        return find_cdp_url(mode="scan")
    if v.startswith("ws://") or v.startswith("wss://"):
        return v
    if v.startswith("http://") or v.startswith("https://"):
        parsed = urlparse(v)
        host = parsed.hostname or "localhost"
        port = parsed.port or 9222
        return find_cdp_url(mode="port", host=host, port=port)
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
