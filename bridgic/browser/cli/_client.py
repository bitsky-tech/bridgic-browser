"""
Bridgic Browser CLI client — connects to the daemon and sends a single JSON
command, returning the result string.

If the daemon is not running, it is spawned automatically and we wait for
the READY_SIGNAL before proceeding.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from typing import Any, Dict, Optional

from .. import _timeouts
from ..errors import BridgicBrowserCommandError

logger = logging.getLogger(__name__)
from ._daemon import DAEMON_LOG_PATH, READY_SIGNAL, STREAM_LIMIT
from ._transport import (
    RUN_INFO_PATH,
    _safe_remove_socket,
    get_transport,
    read_run_info,
    remove_run_info,
)

# Thin aliases to the canonical constants in bridgic.browser._timeouts.
# Env overrides resolve inside that module, so changing BRIDGIC_*_TIMEOUT
# env vars reconfigures both SDK and CLI consistently.
_DAEMON_RESPONSE_TIMEOUT = _timeouts.CLIENT_RESPONSE_S
_DAEMON_RESPONSE_TIMEOUT_BUFFER = _timeouts.CLIENT_RESPONSE_BUFFER_S
_DAEMON_READY_TIMEOUT = _timeouts.CLIENT_READY_S

# Argument keys that carry a "how long am I willing to wait" value. Commands
# like wait/wait_network/verify_* accept any of these; keep them in one place
# so adding a new one is a one-line change.
_TIMEOUT_ARG_KEYS: tuple[str, ...] = ("timeout", "seconds", "deadline", "max_wait")

# Commands whose expected wall-clock can legitimately exceed the 90s default:
# downloads over a slow link, video finalize with minutes of footage, storage
# save/load across many origins. A short fallback here would orphan the daemon
# task and confuse the next CLI invocation. See _compute_response_timeout.
_LONG_CMD_FALLBACK_S = _timeouts.CLIENT_LONG_COMMAND_S
_LONG_COMMANDS: frozenset[str] = frozenset({
    "download",
    "wait-download",
    "video-stop",
    "video_stop",
    "storage-save",
    "storage-load",
    "storage_save",
    "storage_load",
})


def _compute_response_timeout(args: Dict[str, Any], command: str = "") -> float:
    """Return the effective client-side socket timeout for a command.

    Commands like ``wait``/``wait_network``/``verify_*`` carry a ``timeout``
    (or ``seconds``) arg that bounds how long the daemon will work.  The
    client socket timeout must exceed that value, otherwise the client
    aborts while the daemon is still running, orphaning the in-flight task
    and confusing the next CLI invocation.

    For commands in ``_LONG_COMMANDS`` a larger fallback applies even when
    no explicit timeout arg is passed, because their natural runtime is far
    beyond the 90s default.
    """
    arg_timeouts: list[float] = []
    for key in _TIMEOUT_ARG_KEYS:
        val = args.get(key)
        if val is None:
            continue
        try:
            arg_timeouts.append(float(val))
        except (TypeError, ValueError):
            continue
    base = _DAEMON_RESPONSE_TIMEOUT
    if command in _LONG_COMMANDS:
        base = max(base, _LONG_CMD_FALLBACK_S)
    if not arg_timeouts:
        return base
    return max(base, max(arg_timeouts) + _DAEMON_RESPONSE_TIMEOUT_BUFFER)


# ---------------------------------------------------------------------------
# Low-level socket helpers
# ---------------------------------------------------------------------------


async def _send_command_async(command: str, args: Dict[str, Any]) -> str:
    """Connect to the daemon, send one command, return the result string.

    Raises ``ConnectionRefusedError`` / ``FileNotFoundError`` if the daemon
    is not reachable.
    """
    transport = get_transport()
    reader, writer = await transport.open_connection(stream_limit=STREAM_LIMIT)
    response_timeout = _compute_response_timeout(args, command)
    try:
        payload = transport.inject_auth({"command": command, "args": args})
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()

        try:
            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=response_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise BridgicBrowserCommandError(
                command=command,
                code="DAEMON_RESPONSE_TIMEOUT",
                message=(
                    f"Timed out waiting for daemon response after "
                    f"{response_timeout:.0f} seconds."
                ),
                retryable=True,
            ) from exc
        if not raw:
            raise BridgicBrowserCommandError(
                command=command,
                code="DAEMON_NO_RESPONSE",
                message="Daemon closed connection without a response",
                retryable=True,
            )

        try:
            resp = json.loads(raw.decode(errors="replace"))
        except json.JSONDecodeError as exc:
            raise BridgicBrowserCommandError(
                command=command,
                code="DAEMON_INVALID_RESPONSE",
                message=f"Daemon returned invalid JSON: {exc}",
                details={"raw": raw.decode(errors="replace")},
                retryable=True,
            ) from exc
        # Prefer machine-readable "success" when present; fall back to legacy status.
        if "success" in resp:
            is_success = bool(resp.get("success"))
        else:
            is_success = resp.get("status") != "error"

        if not is_success:
            message = str(resp.get("result", "Unknown error from daemon"))
            error_code = str(resp.get("error_code") or "DAEMON_ERROR")
            raw_data = resp.get("data")
            details: Dict[str, Any]
            if isinstance(raw_data, dict):
                details = raw_data
            elif raw_data is None:
                details = {}
            else:
                details = {"data": raw_data}
            meta = resp.get("meta") if isinstance(resp.get("meta"), dict) else {}
            raise BridgicBrowserCommandError(
                command=command,
                code=error_code,
                message=message,
                details=details,
                retryable=bool(meta.get("retryable", False)),
                daemon_meta=meta,
            )
        return str(resp.get("result", ""))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _raise_no_browser_session(command: str, *, cause: Optional[str] = None) -> None:
    """Raise a structured no-session error for CLI callers."""
    details: Dict[str, Any] = {"run_info_path": str(RUN_INFO_PATH)}
    if cause:
        details["cause"] = cause
    info = read_run_info()
    if info:
        details.update({k: v for k, v in info.items() if k != "token"})
    raise BridgicBrowserCommandError(
        command=command,
        code="NO_BROWSER_SESSION",
        message=(
            "No browser session is running. "
            "Use `bridgic-browser open <url>` or `bridgic-browser search <query>` to start one."
        ),
        details=details,
        retryable=True,
    )


def send_command(
    command: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    start_if_needed: bool = True,
    headed: bool = False,
    clear_user_data: bool = False,
    cdp: Optional[str] = None,
) -> str:
    """Send *command* with *args* to the daemon.

    Parameters
    ----------
    start_if_needed:
        If True (default), auto-start the daemon when it is not running.
        Pass False for commands like ``close`` that should not spawn a new
        daemon just to immediately shut it down.
    headed:
        If True, start the daemon in headed (non-headless) mode.  Only
        meaningful when *start_if_needed* is True and the daemon is not yet
        running.
    clear_user_data:
        If True, start the daemon with ``clear_user_data=True`` (ephemeral
        mode — no persistent browser profile).  Only meaningful when
        *start_if_needed* is True and the daemon is not yet running.
    cdp:
        If set, connect to an existing Chrome via this CDP WebSocket URL instead
        of launching a new browser.  Only meaningful when the daemon is not yet
        running.
    """
    if args is None:
        args = {}
    if start_if_needed:
        try:
            ensure_daemon_running(
                headed=headed,
                clear_user_data=clear_user_data,
                cdp=cdp,
                command=command,
            )
        except BridgicBrowserCommandError:
            raise
        except Exception as exc:
            raise BridgicBrowserCommandError(
                command=command,
                code="DAEMON_START_FAILED",
                message=str(exc) or "Failed to start daemon",
                details={"run_info_path": str(RUN_INFO_PATH)},
                retryable=True,
            ) from exc
    elif not RUN_INFO_PATH.exists() or not _probe_socket_sync():
        _raise_no_browser_session(command)

    try:
        return asyncio.run(_send_command_async(command, args))
    except BridgicBrowserCommandError:
        raise
    except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
        _raise_no_browser_session(command, cause=str(exc))


# ---------------------------------------------------------------------------
# Daemon lifecycle helpers
# ---------------------------------------------------------------------------

def _spawn_daemon(headed: bool = False, clear_user_data: bool = False, cdp: Optional[str] = None) -> None:
    """Spawn the daemon as a detached subprocess and wait for its READY_SIGNAL.

    Uses a background reader thread so the 30-second timeout is always
    honoured on all platforms (works on Windows where select() is limited).

    Parameters
    ----------
    headed:
        If True, merge ``{"headless": false}`` into ``BRIDGIC_BROWSER_JSON``
        in the daemon environment so the browser launches in headed (visible)
        mode.
    clear_user_data:
        If True, merge ``{"clear_user_data": true}`` into ``BRIDGIC_BROWSER_JSON``
        so the daemon starts with an ephemeral browser profile (no persistence).
    cdp:
        If set, pass the already-resolved ws:// URL to the daemon via
        ``BRIDGIC_CDP`` so it connects to an existing Chrome instance via CDP
        instead of launching a new browser. Overrides any ``BRIDGIC_CDP``
        inherited from the parent shell, which matches the "CLI flag beats
        env var" convention.
    """
    env = os.environ.copy()
    if headed or clear_user_data:
        import json as _json
        existing = _json.loads(env.get("BRIDGIC_BROWSER_JSON", "{}"))
        if headed:
            existing["headless"] = False
        if clear_user_data:
            existing["clear_user_data"] = True
        env["BRIDGIC_BROWSER_JSON"] = _json.dumps(existing)
    if cdp:
        env["BRIDGIC_CDP"] = cdp

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "env": env,
    }
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP detaches the daemon so it survives
        # when the CLI process exits.  start_new_session=True only maps
        # to this flag on Python ≥3.11; explicit creationflags works on
        # all supported Python versions (≥3.10).
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        # POSIX: setsid() — new session, new process group.
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "-m", "bridgic.browser", "daemon"],
        **popen_kwargs,
    )

    assert proc.stdout is not None

    ready_event = threading.Event()
    buf_lines: list[str] = []

    def _reader_thread() -> None:
        try:
            for raw_line in proc.stdout:  # type: ignore[union-attr]
                line = raw_line.decode(errors="replace")
                buf_lines.append(line)
                if READY_SIGNAL.strip() in line:
                    ready_event.set()
                    return
        except (OSError, ValueError):
            pass
        finally:
            ready_event.set()  # unblock caller on EOF or error

    t = threading.Thread(target=_reader_thread, daemon=True)
    t.start()
    ready_event.wait(timeout=_DAEMON_READY_TIMEOUT)
    proc.stdout.close()
    t.join(timeout=1)

    full_output = "".join(buf_lines)
    if READY_SIGNAL.strip() in full_output:
        return

    diagnostics = full_output.strip()
    diagnostics_tail = ""
    if diagnostics:
        diagnostics_tail = "\nDaemon output (tail):\n" + "\n".join(diagnostics.splitlines()[-12:])

    raise RuntimeError(
        f"Daemon did not send ready signal within {_DAEMON_READY_TIMEOUT:.0f} seconds. "
        "Check that Playwright browsers are installed (`python -m playwright install`). "
        "Override via BRIDGIC_DAEMON_READY_TIMEOUT env var for slow/cold-start environments.\n"
        f"Daemon log: {DAEMON_LOG_PATH}"
        + diagnostics_tail
    )


def _probe_socket_sync() -> bool:
    """Return True if the daemon is accepting connections.

    Uses the transport probe so this is safe on all platforms without
    requiring an asyncio event loop.
    """
    return get_transport().probe()


def _requested_mode(
    *, headed: bool, clear_user_data: bool, cdp: Optional[str]
) -> str:
    """Return the daemon mode implied by CLI flags.

    Precedence mirrors Browser.__init__: ``--cdp`` wins over ``--clear-user-data``
    wins over the default persistent profile.
    """
    if cdp:
        return "cdp"
    if clear_user_data:
        return "ephemeral"
    return "persistent"


def _check_mode_mismatch(
    info: Dict[str, Any],
    *,
    headed: bool,
    clear_user_data: bool,
    cdp: Optional[str],
    command: Optional[str],
) -> None:
    """Raise DAEMON_MODE_MISMATCH when user-supplied flags conflict with the
    running daemon.

    Only compares when at least one non-default flag is supplied, so commands
    that never pass flags (``snapshot``, ``click``, …) never trip this check.
    Legacy daemons that predate the ``mode`` field fall through with a WARNING
    so existing sessions keep working until they are closed.
    """
    if not (headed or clear_user_data or cdp):
        return
    if "mode" not in info:
        # Legacy daemon (predates the ``mode`` field). For plain commands we
        # already returned above, so here the user is explicitly asking for a
        # specific mode via ``--cdp`` / ``--clear-user-data`` / ``--headed``.
        # We cannot verify compatibility, and silently continuing would attach
        # to whatever the legacy daemon happens to be — exactly the bug this
        # check exists to prevent. Block and tell the user how to recover.
        raise BridgicBrowserCommandError(
            command=command or "ensure_daemon_running",
            code="DAEMON_MODE_MISMATCH",
            message=(
                "A legacy daemon is running that predates mode tracking, so "
                "the requested flags (--cdp / --clear-user-data / --headed) "
                "cannot be verified against it. Run `bridgic-browser close` "
                "first, then re-run your command."
            ),
            details={
                "requested": {
                    "headed": headed,
                    "clear_user_data": clear_user_data,
                    "cdp": _redact_if_set(cdp),
                },
                "running": {"mode": "<legacy-unknown>"},
            },
        )

    running_mode = info.get("mode")
    running_headed = bool(info.get("headed", False))
    running_cdp = info.get("cdp_url_redacted")

    requested_mode = _requested_mode(
        headed=headed, clear_user_data=clear_user_data, cdp=cdp
    )

    mismatches: list[str] = []
    if requested_mode != running_mode:
        mismatches.append(f"mode (requested={requested_mode}, running={running_mode})")
    if cdp:
        from ._daemon import _redact_cdp_url
        requested_cdp = _redact_cdp_url(cdp)
        if running_cdp and requested_cdp != running_cdp:
            mismatches.append(
                f"cdp target (requested={requested_cdp}, running={running_cdp})"
            )
    if headed and not running_headed:
        mismatches.append("headed=True requested, running headless")

    if not mismatches:
        return

    raise BridgicBrowserCommandError(
        command=command or "ensure_daemon_running",
        code="DAEMON_MODE_MISMATCH",
        message=(
            "A daemon is already running in a different mode ("
            + "; ".join(mismatches)
            + "). Run `bridgic-browser close` first, then re-run your command "
            "with the desired flags."
        ),
        details={
            "requested": {
                "mode": requested_mode,
                "headed": headed,
                "cdp": _redact_if_set(cdp),
            },
            "running": {
                "mode": running_mode,
                "headed": running_headed,
                "cdp_url_redacted": running_cdp,
            },
        },
        retryable=False,
    )


def _redact_if_set(cdp: Optional[str]) -> Optional[str]:
    if not cdp:
        return None
    from ._daemon import _redact_cdp_url
    return _redact_cdp_url(cdp)


def ensure_daemon_running(
    headed: bool = False,
    clear_user_data: bool = False,
    cdp: Optional[str] = None,
    *,
    command: Optional[str] = None,
) -> None:
    """Start the daemon if it is not already running.

    When a daemon is already running and the caller passed non-default
    ``headed`` / ``clear_user_data`` / ``cdp`` flags, raise
    ``DAEMON_MODE_MISMATCH`` instead of silently ignoring them — without this,
    ``bridgic-browser open`` followed by ``bridgic-browser --cdp ... snapshot``
    would appear to succeed but still target the original daemon.
    """
    if RUN_INFO_PATH.exists():
        if _probe_socket_sync():
            info = read_run_info() or {}
            _check_mode_mismatch(
                info,
                headed=headed,
                clear_user_data=clear_user_data,
                cdp=cdp,
                command=command,
            )
            return  # Already running and mode matches

        # Run info exists but daemon is unreachable — stale.
        info = read_run_info()
        if info and info.get("transport") == "unix":
            socket_path = info.get("socket", "")
            if socket_path:
                try:
                    _safe_remove_socket(socket_path)
                except Exception as exc:
                    raise RuntimeError(
                        f"Found stale socket at {socket_path}, but cannot remove it safely: {exc}"
                    ) from exc
        remove_run_info()

    _spawn_daemon(headed=headed, clear_user_data=clear_user_data, cdp=cdp)
