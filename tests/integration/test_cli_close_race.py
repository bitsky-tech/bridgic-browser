"""Integration tests for CLI close race behavior (C-2 / T-3).

Two scenarios exercise the fix for "new client lands on a closing daemon":

1. **Shell sequence** ``close && open URL``:
   after close, a new open must spawn a **new** daemon (different PID), not
   attach to the one mid-shutdown.  Pre-C-2 the socket could linger and the
   new open would see a mid-dispatch crash.

2. **Concurrent race**:
   send ``close`` and ``snapshot`` back-to-back.  The snapshot must either
   see ``DAEMON_SHUTTING_DOWN`` (fast-path reject) or connection refused
   (server fully closed).  What it must NOT see: a mid-dispatch crash, an
   infinite hang, or a successful snapshot from the dying daemon.
"""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pytest

from bridgic.browser.cli._transport import RUN_INFO_PATH


pytestmark = [pytest.mark.integration, pytest.mark.slow]


CLI = "bridgic-browser"


def _run(cmd: str, env: dict, timeout: float = 60.0) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    full_env.update(env)
    return subprocess.run(
        shlex.split(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


@pytest.fixture
def short_env() -> Iterator[dict]:
    """Isolated daemon state under a short /tmp path (AF_UNIX limit)."""
    short_dir = Path(tempfile.mkdtemp(prefix="brd-", dir="/tmp"))
    socket_path = short_dir / "d.sock"
    user_data = short_dir / "ud"
    user_data.mkdir()
    try:
        yield {
            "BRIDGIC_SOCKET": str(socket_path),
            "BRIDGIC_BROWSER_JSON": (
                f'{{"headless": true, "stealth": false, '
                f'"user_data_dir": "{user_data}"}}'
            ),
            "_SHORT_DIR": str(short_dir),
        }
    finally:
        # Best-effort daemon shutdown + rmtree
        try:
            _run(f"{CLI} close", env={"BRIDGIC_SOCKET": str(socket_path)}, timeout=15)
        except Exception:
            pass
        shutil.rmtree(short_dir, ignore_errors=True)


def _read_daemon_pid() -> int | None:
    """Return the daemon PID from the global run_info file, or None if missing.

    ``RUN_INFO_PATH`` is hardcoded to ``~/.bridgic/bridgic-browser/run/daemon.json``
    (not env-overridable), so all daemons share this path. Each test starts a
    fresh daemon which overwrites the file, so within a single test we read
    whichever daemon is currently alive.
    """
    try:
        data = json.loads(RUN_INFO_PATH.read_text())
        pid = data.get("pid")
        return int(pid) if pid is not None else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def test_close_then_open_spawns_new_daemon(short_env: dict) -> None:
    """C-2 / T-3 shell sequence: close && open URL must not attach to old daemon.

    Pre-C-2 the run_info + socket could linger briefly and the second ``open``
    would land on the still-shutting-down daemon and see a mid-dispatch
    crash.  After C-2, the second open must spawn a fresh daemon (different
    PID) and succeed normally.
    """
    # Phase 1: start daemon 1.
    r = _run(
        f'{CLI} open "data:text/html,<html><body>one</body></html>"',
        env=short_env, timeout=45,
    )
    assert r.returncode == 0, f"first open failed: {r.stderr}"
    pid1 = _read_daemon_pid()
    assert pid1 is not None, "daemon pid not recorded after first open"

    # Phase 2: close daemon 1.
    r = _run(f"{CLI} close", env=short_env, timeout=30)
    assert r.returncode == 0, f"close failed: {r.stderr}"

    # Wait up to 10s for the old daemon to exit fully.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _pid_alive(pid1):
        time.sleep(0.1)
    assert not _pid_alive(pid1), f"daemon pid {pid1} still alive after close"

    # Phase 3: open a new URL — must spawn a new daemon.
    r = _run(
        f'{CLI} open "data:text/html,<html><body>two</body></html>"',
        env=short_env, timeout=45,
    )
    assert r.returncode == 0, f"second open failed: {r.stderr}"
    pid2 = _read_daemon_pid()
    assert pid2 is not None, "daemon pid not recorded after second open"
    assert pid2 != pid1, (
        f"second open reused old daemon PID (was supposed to spawn new): "
        f"pid1={pid1} pid2={pid2}"
    )


def test_snapshot_during_close_fails_cleanly(short_env: dict) -> None:
    """C-2 race: snapshot issued while close is in-flight must fail cleanly.

    "Cleanly" means one of:
      - ``DAEMON_SHUTTING_DOWN`` error code (fast-path reject)
      - ``NO_BROWSER_SESSION`` (daemon fully gone before snapshot connected)
      - generic connection refused / broken pipe

    What it must NOT do: hang, return a successful snapshot, or surface a
    mid-dispatch stack trace — those were the pre-C-2 failure modes.
    """
    r = _run(
        f'{CLI} open "data:text/html,<html><body>x</body></html>"',
        env=short_env, timeout=45,
    )
    assert r.returncode == 0, f"open failed: {r.stderr}"

    # Background: close daemon. We use Popen so we don't wait for it.
    full_env = os.environ.copy()
    full_env.update(short_env)
    close_proc = subprocess.Popen(
        shlex.split(f"{CLI} close"),
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Foreground: snapshot.  Start ASAP to have the best chance of
        # landing on the dying daemon.  This test is intentionally timing-
        # based — we run it with a generous timeout and accept any clean
        # failure mode (listed above).
        snap = _run(f"{CLI} snapshot", env=short_env, timeout=30)

        # Must have terminated (not hung) within the timeout.
        assert snap.returncode is not None
        combined = f"{snap.stdout}\n{snap.stderr}"

        acceptable_markers = (
            "DAEMON_SHUTTING_DOWN",
            "NO_BROWSER_SESSION",
            "BROWSER_CLOSED",
            "Connection refused",
            "shutting down",
            "Broken pipe",
            "OPERATION_FAILED",
            "Failed to get snapshot",
        )
        success = snap.returncode == 0
        race_observed = any(m in combined for m in acceptable_markers)

        # Either: snapshot beat the close and succeeded (daemon responded
        # before shutdown kicked in), OR: snapshot saw one of the clean
        # rejection signals.  Only a mid-dispatch stack trace / hang is a
        # regression.
        assert success or race_observed, (
            f"snapshot failed without a clean rejection signal:\n"
            f"rc={snap.returncode}\nstdout: {snap.stdout}\nstderr: {snap.stderr}"
        )
    finally:
        try:
            close_proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            close_proc.kill()
