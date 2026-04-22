"""Integration: CDP auto-reconnect survives a Chrome process restart (H02).

Scenario:
  1. Start Chrome with ``--remote-debugging-port=<port>``.
  2. ``bridgic-browser open --cdp <port>`` — attaches the daemon.
  3. Kill that Chrome process, restart a fresh Chrome on the same port.
  4. Run a non-``open`` command (``snapshot``). The daemon must detect the
     dead CDP connection, re-resolve ``resolve_cdp_input(<port>)`` to get a
     fresh ws URL (new browser UUID), reconnect, and complete the command.
  5. daemon.log must contain ``cdp_reconnect: reconnected successfully`` and
     must NOT show ``404 Not Found`` — the latter indicates the reconnect
     reused the stale ws URL containing the old UUID.

This test is the regression lock for the H02 bug where non-``open`` commands
used to get classified as ``OPERATION_FAILED`` because:
  (a) ``Browser._start`` only re-resolved the ws URL when ``_cdp_resolved``
      was falsy, and ``_cdp_reconnect`` never cleared it, so the reconnect
      issued ``connect_over_cdp(ws://...old-UUID)`` → Playwright 404;
  (b) the CLI client pre-resolved the port to a ws URL before sending, so
      even if (a) was fixed the daemon never saw the bare-port form needed
      to re-resolve against the new Chrome.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pytest

from ._chrome_utils import (
    find_chrome_binary,
    kill_chrome,
    launch_chrome,
    pick_free_port,
)


pytestmark = [pytest.mark.integration, pytest.mark.slow]


CLI = "bridgic-browser"
CHROME_BIN: str | None = find_chrome_binary()


# ── helpers ──────────────────────────────────────────────────────────────


def _launch(port: int, user_data_dir: Path) -> subprocess.Popen:
    """Thin wrapper that binds the module-level CHROME_BIN."""
    assert CHROME_BIN is not None, "Chrome binary required"
    return launch_chrome(CHROME_BIN, port, user_data_dir)


def _cli(*args: str, env: dict | None = None, timeout: int = 45) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [CLI, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


def _find_daemon_log(socket_path: Path) -> Path:
    """Daemon log lives next to the socket directory under ``logs/daemon.log``."""
    return socket_path.parent.parent / "logs" / "daemon.log"


# ── fixture ──────────────────────────────────────────────────────────────


DAEMON_LOG_PATH = Path.home() / ".bridgic" / "bridgic-browser" / "logs" / "daemon.log"


@pytest.fixture
def isolated_daemon_env() -> Iterator[dict]:
    """Give each test its own short-path socket.

    AF_UNIX path length is capped ~104 chars on macOS; pytest's tmp_path
    nests under ``/private/var/folders/...`` which blows past it. Use
    ``/tmp/brd-cdp-restart-*`` to stay safely under the limit.

    The daemon log path is global (``~/.bridgic/.../logs/daemon.log``) and
    not env-configurable; the test reads it with a byte offset so it only
    inspects this run's output.
    """
    tmp_root = None if os.name == "nt" else "/tmp"
    short_dir = Path(tempfile.mkdtemp(prefix="brd-cdp-restart-", dir=tmp_root))
    (short_dir / "run").mkdir()
    socket_path = short_dir / "run" / "d.sock"
    user_data = short_dir / "ud"
    user_data.mkdir()
    try:
        yield {
            "BRIDGIC_SOCKET": str(socket_path),
            "BRIDGIC_BROWSER_JSON": (
                f'{{"headless": true, "stealth": false, '
                f'"user_data_dir": "{user_data}"}}'
            ),
        }
    finally:
        # Best-effort: shut the daemon down so the next test gets a clean slate.
        try:
            _cli("close", env={
                "BRIDGIC_SOCKET": str(socket_path),
            }, timeout=15)
        except Exception:
            pass
        shutil.rmtree(short_dir, ignore_errors=True)


def _read_log_tail_since(offset: int) -> str:
    """Read daemon.log content added since byte *offset*."""
    if not DAEMON_LOG_PATH.exists():
        return ""
    with open(DAEMON_LOG_PATH, "rb") as f:
        f.seek(offset)
        return f.read().decode(errors="replace")


def _daemon_log_size() -> int:
    if not DAEMON_LOG_PATH.exists():
        return 0
    return DAEMON_LOG_PATH.stat().st_size


# ── test ─────────────────────────────────────────────────────────────────


@pytest.mark.skipif(CHROME_BIN is None, reason="Chrome binary not found")
def test_cdp_reconnect_after_chrome_restart(isolated_daemon_env: dict) -> None:
    """H02: snapshot must succeed after killing + restarting Chrome on same port."""
    port = pick_free_port()
    tmp_root = None if os.name == "nt" else "/tmp"
    profile_root = Path(tempfile.mkdtemp(prefix="brd-cdp-profile-", dir=tmp_root))
    chrome1: subprocess.Popen | None = None
    chrome2: subprocess.Popen | None = None

    try:
        # Snapshot daemon.log size so assertions only inspect THIS run's tail,
        # unaffected by other daemons that may have written to the same file.
        log_offset = _daemon_log_size()

        # ── Act 1: original Chrome ──────────────────────────────────────
        chrome1 = _launch(port, profile_root / "profile1")

        r = _cli(
            "open", "https://example.com", "--cdp", str(port),
            env=isolated_daemon_env,
        )
        assert r.returncode == 0, (
            f"initial open failed: rc={r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

        # ── Act 2: kill & relaunch on the same port ─────────────────────
        kill_chrome(chrome1)
        chrome1 = None
        # Give the OS a beat to release the port.
        time.sleep(0.5)
        chrome2 = _launch(port, profile_root / "profile2")

        # ── Assert: non-open command triggers reconnect and succeeds ────
        # ``snapshot -i`` is a good probe: it requires an attached page, so
        # if reconnect silently failed we'd get OPERATION_FAILED / error.
        r = _cli("snapshot", "-i", env=isolated_daemon_env, timeout=60)
        assert r.returncode == 0, (
            f"snapshot after Chrome restart failed (expected auto-reconnect):\n"
            f"  rc={r.returncode}\n  stdout: {r.stdout[:500]}\n"
            f"  stderr: {r.stderr[:500]}"
        )

        # ── Assert: daemon.log captures the successful reconnect ────────
        log_tail = _read_log_tail_since(log_offset)
        assert "cdp_reconnect: reconnected successfully" in log_tail, (
            "reconnect success not logged — reconnect path likely never ran.\n"
            f"tail:\n{log_tail[-2000:]}"
        )
        # The H02 bug signature: stale ws URL 404s against the new Chrome.
        # After the fix, _cdp_resolved is cleared before _start and the bare
        # port is re-resolved against the restarted Chrome — no 404 should
        # appear in THIS run's log tail.
        assert "404 Not Found" not in log_tail, (
            "daemon.log shows a 404 Not Found in this run — stale ws URL "
            "regression (H02).\n"
            f"tail:\n{log_tail[-2000:]}"
        )

        # Follow-up: a fresh `open --cdp <port>` also works (covers Task §2.4 path).
        r = _cli(
            "open", "https://example.com", "--cdp", str(port),
            env=isolated_daemon_env,
        )
        assert r.returncode == 0, (
            f"second open after restart failed: rc={r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
    finally:
        if chrome1 is not None:
            kill_chrome(chrome1)
        if chrome2 is not None:
            kill_chrome(chrome2)
        shutil.rmtree(profile_root, ignore_errors=True)


@pytest.mark.skipif(CHROME_BIN is None, reason="Chrome binary not found")
def test_cdp_close_does_not_kill_remote_chrome(isolated_daemon_env: dict) -> None:
    """task.md §2.3: ``close`` must be pure-disconnect; Chrome keeps running and
    a subsequent ``open --cdp`` re-attaches to the same process.

    The CDP-mode close path explicitly skips ``browser.close()`` on the borrowed
    Chromium (see ``Browser.close``), so the remote PID must survive.
    """
    port = pick_free_port()
    tmp_root = None if os.name == "nt" else "/tmp"
    profile_root = Path(tempfile.mkdtemp(prefix="brd-cdp-close-", dir=tmp_root))
    chrome: subprocess.Popen | None = None

    try:
        chrome = _launch(port, profile_root / "profile")
        chrome_pid = chrome.pid

        r = _cli(
            "open", "https://example.com", "--cdp", str(port),
            env=isolated_daemon_env,
        )
        assert r.returncode == 0, (
            f"initial open failed: rc={r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

        r = _cli("close", env=isolated_daemon_env, timeout=30)
        assert r.returncode == 0, (
            f"close failed: rc={r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

        # Chrome must still be alive. ``os.kill(pid, 0)`` is a cheap liveness
        # probe that raises ``OSError`` (ESRCH) when the process is gone.
        try:
            os.kill(chrome_pid, 0)
        except OSError as exc:
            pytest.fail(
                f"Chrome (pid={chrome_pid}) was killed by bridgic close; "
                f"CDP close must be disconnect-only. err={exc}"
            )

        # Re-attach must succeed without relaunching Chrome.
        r = _cli(
            "open", "https://example.org", "--cdp", str(port),
            env=isolated_daemon_env,
        )
        assert r.returncode == 0, (
            f"re-attach after close failed: rc={r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
    finally:
        if chrome is not None:
            kill_chrome(chrome)
        shutil.rmtree(profile_root, ignore_errors=True)
