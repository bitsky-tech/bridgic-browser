"""Integration test for CLI dynamic response timeout (C-1 / T-2).

End-to-end: start a bridgic daemon with scaled-down response-timeout
env vars, run ``wait --timeout N`` where N > default, and confirm the
client lets the daemon run to its own timeout instead of aborting early
with DAEMON_RESPONSE_TIMEOUT.

Env scaling (keeps the test under ~15s including browser spawn):
  BRIDGIC_DAEMON_RESPONSE_TIMEOUT=5
  BRIDGIC_DAEMON_RESPONSE_TIMEOUT_BUFFER=3
  wait --timeout 7   (exceeds default, daemon owns the deadline)

Expectation: client waits up to 7 + 3 = 10s, daemon reports its own
timeout around 7s.  stderr must NOT contain ``DAEMON_RESPONSE_TIMEOUT``.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.slow]


CLI = "bridgic-browser"


def _run(cmd: str, env: dict, timeout: float = 60.0) -> subprocess.CompletedProcess:
    """Run a CLI command with the given environment overrides."""
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
def scaled_env() -> Iterator[dict]:
    """Isolated daemon (own socket + user data) with scaled-down timeouts.

    AF_UNIX paths max out at ~104 chars on macOS, so we use /tmp/brd-* (short)
    instead of pytest's default tmp_path (which nests under /private/var/...).
    """
    short_dir = Path(tempfile.mkdtemp(prefix="brd-", dir="/tmp"))
    socket_path = short_dir / "d.sock"
    user_data = short_dir / "ud"
    user_data.mkdir()
    try:
        yield {
            "BRIDGIC_SOCKET": str(socket_path),
            "BRIDGIC_DAEMON_RESPONSE_TIMEOUT": "5",
            "BRIDGIC_DAEMON_RESPONSE_TIMEOUT_BUFFER": "3",
            "BRIDGIC_BROWSER_JSON": (
                f'{{"headless": true, "stealth": false, '
                f'"user_data_dir": "{user_data}"}}'
            ),
        }
    finally:
        shutil.rmtree(short_dir, ignore_errors=True)


def _close_daemon(env: dict) -> None:
    try:
        _run(f"{CLI} close", env=env, timeout=30)
    except Exception:
        pass


def test_wait_with_timeout_longer_than_default_is_honored(scaled_env: dict) -> None:
    """C-1 / T-2: ``wait --timeout 7`` with default 5s must not be truncated.

    ``"never_gonna_match__xyz"`` is a selector that will time out, so the
    daemon reports its own timeout around the 7s mark.  If the client
    were still using a static 5s socket timeout (pre-C-1), it would abort
    at ~5s with DAEMON_RESPONSE_TIMEOUT and the daemon task would be
    orphaned.
    """
    try:
        # Open a trivial page so the browser is up.  data: URL avoids the
        # network so the test doesn't depend on internet access.
        open_res = _run(
            f'{CLI} open "data:text/html,<html><body>hi</body></html>"',
            env=scaled_env, timeout=45,
        )
        assert open_res.returncode == 0, (
            f"open failed: rc={open_res.returncode}\n"
            f"stdout: {open_res.stdout}\nstderr: {open_res.stderr}"
        )

        start = time.monotonic()
        wait_res = _run(
            f'{CLI} wait --timeout 7 "never_gonna_match__xyz"',
            env=scaled_env, timeout=30,
        )
        elapsed = time.monotonic() - start

        combined = f"{wait_res.stdout}\n{wait_res.stderr}"

        # Daemon timeout (~7s) should fire; anything under 6.5s means the
        # client aborted early (bug), anything past ~12s means we blew past
        # the new dynamic timeout (also a bug).
        assert 6.0 <= elapsed <= 12.0, (
            f"elapsed {elapsed:.2f}s outside expected 6-12s window.\n"
            f"stdout: {wait_res.stdout}\nstderr: {wait_res.stderr}"
        )
        # The client must NOT have raised DAEMON_RESPONSE_TIMEOUT — that's
        # the pre-C-1 failure mode.
        assert "DAEMON_RESPONSE_TIMEOUT" not in combined, (
            f"client aborted prematurely:\n{combined}"
        )
        # The wait should fail (selector never appears), but via the daemon's
        # own timeout, not the client socket timeout.
        assert wait_res.returncode != 0, (
            f"wait unexpectedly succeeded:\n{combined}"
        )
    finally:
        _close_daemon(scaled_env)
