"""
Regression tests for the daemon mode-mismatch guard.

Before this guard, invoking ``bridgic-browser --cdp ws://... snapshot`` against
a daemon that was launched in plain persistent mode would silently pick up the
persistent session instead of connecting to CDP — the ``--cdp`` flag only
affected ``--spawn`` paths. The guard raises ``DAEMON_MODE_MISMATCH`` as soon
as a client realises the running daemon's mode differs from the requested one.
"""
import pytest

from bridgic.browser.cli._client import _check_mode_mismatch, _requested_mode
from bridgic.browser.errors import BridgicBrowserCommandError


class TestRequestedMode:
    def test_cdp_wins_over_everything(self) -> None:
        assert (
            _requested_mode(headed=True, clear_user_data=True, cdp="ws://x")
            == "cdp"
        )

    def test_clear_user_data_is_ephemeral(self) -> None:
        assert (
            _requested_mode(headed=False, clear_user_data=True, cdp=None)
            == "ephemeral"
        )

    def test_default_is_persistent(self) -> None:
        assert (
            _requested_mode(headed=False, clear_user_data=False, cdp=None)
            == "persistent"
        )


class TestCheckModeMismatch:
    def test_no_flags_short_circuits(self) -> None:
        # When the user passes no non-default flags, we never compare.
        _check_mode_mismatch(
            {"mode": "ephemeral"},  # wouldn't match persistent, but skipped
            headed=False,
            clear_user_data=False,
            cdp=None,
            command="snapshot",
        )

    def test_legacy_daemon_missing_mode_field_raises(self) -> None:
        # A legacy daemon exposes no `mode` field. Under the old behavior we
        # logged a warning and silently proceeded, which let a --headed /
        # --cdp / --clear-user-data request run against a daemon that was
        # actually headless / persistent. The new contract treats the
        # ambiguity as DAEMON_MODE_MISMATCH so the caller must explicitly
        # restart the daemon before flags are honoured.
        with pytest.raises(BridgicBrowserCommandError) as exc:
            _check_mode_mismatch(
                {},  # legacy daemon: no `mode` field
                headed=True,
                clear_user_data=False,
                cdp=None,
                command="snapshot",
            )
        assert exc.value.code == "DAEMON_MODE_MISMATCH"
        assert exc.value.retryable is False
        assert "predates mode tracking" in str(exc.value)

    def test_cdp_against_persistent_raises(self) -> None:
        with pytest.raises(BridgicBrowserCommandError) as exc:
            _check_mode_mismatch(
                {"mode": "persistent", "headed": False},
                headed=False,
                clear_user_data=False,
                cdp="ws://127.0.0.1:9222/devtools/abc",
                command="snapshot",
            )
        assert exc.value.code == "DAEMON_MODE_MISMATCH"
        assert exc.value.retryable is False
        assert "persistent" in str(exc.value)

    def test_ephemeral_against_persistent_raises(self) -> None:
        with pytest.raises(BridgicBrowserCommandError) as exc:
            _check_mode_mismatch(
                {"mode": "persistent", "headed": False},
                headed=False,
                clear_user_data=True,
                cdp=None,
                command="snapshot",
            )
        assert exc.value.code == "DAEMON_MODE_MISMATCH"

    def test_headed_against_headless_raises(self) -> None:
        with pytest.raises(BridgicBrowserCommandError) as exc:
            _check_mode_mismatch(
                {"mode": "persistent", "headed": False},
                headed=True,
                clear_user_data=False,
                cdp=None,
                command="snapshot",
            )
        assert exc.value.code == "DAEMON_MODE_MISMATCH"
        assert "headed" in str(exc.value)

    def test_cdp_target_mismatch_raises(self) -> None:
        # Same mode, but different remote CDP target.
        with pytest.raises(BridgicBrowserCommandError) as exc:
            _check_mode_mismatch(
                {
                    "mode": "cdp",
                    "headed": False,
                    "cdp_url_redacted": "wss://cloud-a.example.com",
                },
                headed=False,
                clear_user_data=False,
                cdp="wss://cloud-b.example.com/ws",
                command="snapshot",
            )
        assert exc.value.code == "DAEMON_MODE_MISMATCH"
        assert "cdp target" in str(exc.value)

    def test_matching_mode_passes(self) -> None:
        # Same mode, same redacted CDP — no exception.
        _check_mode_mismatch(
            {
                "mode": "cdp",
                "headed": False,
                "cdp_url_redacted": "9222",
            },
            headed=False,
            clear_user_data=False,
            cdp="ws://localhost:9222/devtools/abc",
            command="snapshot",
        )
