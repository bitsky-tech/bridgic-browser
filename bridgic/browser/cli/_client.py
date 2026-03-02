"""
Bridgic Browser CLI client — connects to the daemon Unix socket and sends
a single JSON command, returning the result string.

If the daemon is not running, it is spawned automatically and we wait for
the READY_SIGNAL before proceeding.
"""
from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from ._daemon import SOCKET_PATH, READY_SIGNAL


# ---------------------------------------------------------------------------
# Low-level socket helpers
# ---------------------------------------------------------------------------

async def _send_command_async(command: str, args: Dict[str, Any]) -> str:
    """Connect to the daemon, send one command, return the result string.

    Raises ``ConnectionRefusedError`` / ``FileNotFoundError`` if the socket
    is not available.
    """
    reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
    try:
        req = json.dumps({"command": command, "args": args}) + "\n"
        writer.write(req.encode())
        await writer.drain()

        raw = await reader.readline()
        if not raw:
            raise RuntimeError("Daemon closed connection without a response")

        resp = json.loads(raw.decode())
        if resp.get("status") == "error":
            raise RuntimeError(resp.get("result", "Unknown error from daemon"))
        return resp.get("result", "")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def send_command(
    command: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    start_if_needed: bool = True,
) -> str:
    """Send *command* with *args* to the daemon.

    Parameters
    ----------
    start_if_needed:
        If True (default), auto-start the daemon when it is not running.
        Pass False for commands like ``close`` that should not spawn a new
        daemon just to immediately shut it down.
    """
    if args is None:
        args = {}
    if start_if_needed:
        ensure_daemon_running()
    elif not os.path.exists(SOCKET_PATH):
        raise RuntimeError(
            "No browser session is running. "
            "Use 'bridgic-browser open <url>' to start one."
        )
    return asyncio.run(_send_command_async(command, args))


# ---------------------------------------------------------------------------
# Daemon lifecycle helpers
# ---------------------------------------------------------------------------

def _spawn_daemon() -> None:
    """Spawn the daemon as a detached subprocess and wait for its READY_SIGNAL.

    Uses ``select`` so the 30-second timeout is always honoured — the
    previous ``proc.stdout.read(n)`` approach blocked indefinitely.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "bridgic.browser", "daemon"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        # Detach from our process group so it survives when we exit
        start_new_session=True,
    )

    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    deadline = time.monotonic() + 30  # 30-second startup timeout
    buf = ""

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        # select with a short poll interval so we can recheck the deadline
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))

        if not ready:
            # Nothing to read yet; check if the daemon process already died
            if proc.poll() is not None:
                break
            continue

        try:
            chunk = os.read(fd, 64)
        except OSError:
            break

        if not chunk:  # EOF — daemon exited without the ready signal
            break

        buf += chunk.decode(errors="replace")
        if READY_SIGNAL.strip() in buf:
            proc.stdout.close()
            return

    proc.stdout.close()
    raise RuntimeError(
        "Daemon did not send ready signal within 30 seconds. "
        "Check that Playwright browsers are installed (`make playwright-install`)."
    )


def ensure_daemon_running() -> None:
    """Start the daemon if it is not already running."""
    if os.path.exists(SOCKET_PATH):
        # Quick probe: try to open a connection
        try:
            asyncio.run(_probe_socket())
            return  # Already running
        except Exception:
            # Stale socket — remove it and respawn
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass

    _spawn_daemon()


async def _probe_socket() -> None:
    """Try to open a connection to the socket (raises on failure)."""
    reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
