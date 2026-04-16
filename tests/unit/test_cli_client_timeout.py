"""Unit tests for the CLI client's dynamic response timeout (C-1).

Regression: the CLI client's default socket read timeout is 90s.  If the
user passes ``wait --timeout 120``, the client would abort at 90s while
the daemon was still working — orphaning the in-flight task and surfacing
as ``DAEMON_RESPONSE_TIMEOUT`` in the CLI output.  The fix: bump the
client-side timeout based on the command's own ``timeout`` / ``seconds``
arg plus a buffer (30s by default).
"""

from unittest.mock import patch

import pytest

from bridgic.browser.cli._client import (
    _DAEMON_RESPONSE_TIMEOUT,
    _DAEMON_RESPONSE_TIMEOUT_BUFFER,
    _compute_response_timeout,
)


class TestComputeResponseTimeout:
    def test_no_timeout_arg_uses_default(self) -> None:
        """Commands without timeout/seconds use the module default."""
        assert _compute_response_timeout({}) == _DAEMON_RESPONSE_TIMEOUT
        assert _compute_response_timeout({"url": "https://x"}) == _DAEMON_RESPONSE_TIMEOUT

    def test_short_timeout_arg_does_not_shorten_default(self) -> None:
        """args.timeout < default must never shrink the client socket timeout.

        ``verify_*`` defaults to 5s; the client still needs 90s to allow
        the daemon to finish and return properly.
        """
        t = _compute_response_timeout({"timeout": 5})
        assert t == _DAEMON_RESPONSE_TIMEOUT

    def test_long_timeout_arg_extends_with_buffer(self) -> None:
        """C-1 core: wait --timeout 120 must yield 150s client timeout."""
        t = _compute_response_timeout({"timeout": 120})
        assert t == 120 + _DAEMON_RESPONSE_TIMEOUT_BUFFER

    def test_seconds_arg_is_also_respected(self) -> None:
        """``wait_network --seconds 200`` mirrors the ``timeout`` arg."""
        t = _compute_response_timeout({"seconds": 200})
        assert t == 200 + _DAEMON_RESPONSE_TIMEOUT_BUFFER

    def test_non_numeric_arg_is_ignored_safely(self) -> None:
        """Bad input must not raise — fall back to default."""
        t = _compute_response_timeout({"timeout": "not-a-number"})
        assert t == _DAEMON_RESPONSE_TIMEOUT
        t = _compute_response_timeout({"timeout": None})
        assert t == _DAEMON_RESPONSE_TIMEOUT

    def test_both_timeout_and_seconds_picks_max(self) -> None:
        """If both keys appear, use the larger of the two + buffer."""
        t = _compute_response_timeout({"timeout": 60, "seconds": 150})
        assert t == 150 + _DAEMON_RESPONSE_TIMEOUT_BUFFER

    @pytest.mark.parametrize("value", [0, -5, -100])
    def test_non_positive_timeout_does_not_break(self, value: int) -> None:
        """0 / negative inputs get bumped up to the default."""
        t = _compute_response_timeout({"timeout": value})
        # For negative / zero, value + buffer is still < default, so default wins.
        assert t == max(_DAEMON_RESPONSE_TIMEOUT, value + _DAEMON_RESPONSE_TIMEOUT_BUFFER)
