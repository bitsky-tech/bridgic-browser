"""
Bridgic Browser CLI client — connects to the daemon and sends a single JSON
command, returning the result string.

If the daemon is not running, it is spawned automatically and we wait for
the READY_SIGNAL before proceeding.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from typing import Any, Dict, Optional

from ..errors import BridgicBrowserCommandError
from ._daemon import DAEMON_LOG_PATH, READY_SIGNAL, STREAM_LIMIT
from ._transport import (
    RUN_INFO_PATH,
    _safe_remove_socket,
    get_transport,
    read_run_info,
    remove_run_info,
)

_DAEMON_RESPONSE_TIMEOUT = float(os.environ.get("BRIDGIC_DAEMON_RESPONSE_TIMEOUT", "90"))


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
    try:
        payload = transport.inject_auth({"command": command, "args": args})
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()

        try:
            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=_DAEMON_RESPONSE_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise BridgicBrowserCommandError(
                command=command,
                code="DAEMON_RESPONSE_TIMEOUT",
                message=(
                    f"Timed out waiting for daemon response after "
                    f"{_DAEMON_RESPONSE_TIMEOUT:.0f} seconds."
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
            resp = json.loads(raw.decode())
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
    """
    if args is None:
        args = {}
    if start_if_needed:
        try:
            ensure_daemon_running(headed=headed)
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

def _spawn_daemon(headed: bool = False) -> None:
    """Spawn the daemon as a detached subprocess and wait for its READY_SIGNAL.

    Uses a background reader thread so the 30-second timeout is always
    honoured on all platforms (works on Windows where select() is limited).

    Parameters
    ----------
    headed:
        If True, set ``BRIDGIC_HEADLESS=0`` in the daemon environment so the
        browser launches in headed (visible) mode.
    """
    env = os.environ.copy()
    if headed:
        env["BRIDGIC_HEADLESS"] = "0"

    proc = subprocess.Popen(
        [sys.executable, "-m", "bridgic.browser", "daemon"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Detach from our process group so it survives when we exit
        start_new_session=True,
        env=env,
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
    ready_event.wait(timeout=30)
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
        "Daemon did not send ready signal within 30 seconds. "
        "Check that Playwright browsers are installed (`python -m playwright install`).\n"
        f"Daemon log: {DAEMON_LOG_PATH}"
        + diagnostics_tail
    )


def _probe_socket_sync() -> bool:
    """Return True if the daemon is accepting connections.

    Uses the transport probe so this is safe on all platforms without
    requiring an asyncio event loop.
    """
    return get_transport().probe()


def ensure_daemon_running(headed: bool = False) -> None:
    """Start the daemon if it is not already running."""
    if RUN_INFO_PATH.exists():
        if _probe_socket_sync():
            return  # Already running

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

    _spawn_daemon(headed=headed)
