"""Unit tests for daemon CDP env resolution (C-5)."""

from unittest.mock import patch

import pytest

from bridgic.browser.cli._daemon import _resolve_cdp_url_from_env


# N3: patch `bridgic.browser.cli._daemon.resolve_cdp_input` — the binding
# that `_resolve_cdp_url_from_env` actually resolves through its local
# `from ... import` at line 1081. Patching the source module
# (`bridgic.browser.session._browser.resolve_cdp_input`) is brittle: when
# the daemon does `from bridgic.browser.session._browser import
# resolve_cdp_input`, the daemon binds the symbol at import time and any
# later patch on the source module has no effect on the daemon's local
# binding. Tests that rely on the source-module patch only pass today by
# accident and break under minor refactors.
_RESOLVE_IN_DAEMON = "bridgic.browser.cli._daemon.resolve_cdp_input"
_PROBE_IN_DAEMON = "bridgic.browser.cli._daemon._probe_ws_reachable"


class TestResolveCdpUrlFromEnv:
    """C-5: ``_resolve_cdp_url_from_env`` short-circuits on ws:// to avoid
    re-parsing the CLI-resolved URL inside the daemon.

    The CLI client calls ``resolve_cdp_input`` once (hits the Chrome
    ``/json/version`` endpoint, picks a tab, returns a ws URL) and injects
    that URL into the daemon via ``BRIDGIC_CDP``.  If the daemon re-ran the
    resolver, any future divergence between CLI and daemon parsing would
    silently break CDP connections.
    """

    def test_none_returns_none(self) -> None:
        assert _resolve_cdp_url_from_env(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _resolve_cdp_url_from_env("") is None

    @pytest.mark.parametrize(
        "url",
        [
            "ws://localhost:9222/devtools/page/abc123",
            "ws://127.0.0.1:9222/devtools/browser/xyz",
            "wss://remote-host/devtools/page/abc",
            "wss://cloud-browser.example.com:443/devtools/browser/uuid",
            "WS://host:9222/devtools/",
            "Ws://host:9222/x",
            "WSS://host:9222/y",
        ],
        ids=[
            "ws-localhost",
            "ws-127-0-0-1",
            "wss-remote",
            "wss-cloud-service",
            "ws-uppercase",
            "ws-mixed-case",
            "wss-uppercase",
        ],
    )
    def test_ws_url_branches_always_probe(self, url: str) -> None:
        """I4: every ws:///wss:// branch MUST call ``_probe_ws_reachable``.

        This locks in the invariant documented at ``_daemon.py``'s
        ``_resolve_cdp_url_from_env`` — skipping the probe would reintroduce
        the hang on stale BRIDGIC_CDP values pointing at a dead browser.
        """
        with patch(_RESOLVE_IN_DAEMON) as mock_resolve, patch(
            _PROBE_IN_DAEMON
        ) as mock_probe:
            result = _resolve_cdp_url_from_env(url)

        assert result == url
        # Every ws branch short-circuits before resolve_cdp_input.
        mock_resolve.assert_not_called()
        # Every ws branch MUST probe reachability exactly once.
        mock_probe.assert_called_once_with(url)

    @pytest.mark.parametrize(
        "url",
        [
            "ws://localhost:9222/devtools/browser/dead",
            "wss://remote-dead/devtools/browser/gone",
            "WS://HOST/devtools/page/stale",
        ],
    )
    def test_ws_url_stale_env_probe_fails_fast(self, url: str) -> None:
        """I4: a ws:// env value pointing at a dead browser must fail fast
        with a ``RuntimeError`` carrying a friendly hint, instead of
        hanging inside ``connect_over_cdp``.
        """
        with patch(
            _PROBE_IN_DAEMON,
            side_effect=ConnectionError("target unreachable"),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                _resolve_cdp_url_from_env(url)
        msg = str(excinfo.value)
        assert "Failed to establish CDP connection" in msg
        assert "target unreachable" in msg

    def test_port_string_calls_resolver(self) -> None:
        """Bare ports from shell must still flow through resolve_cdp_input."""
        with patch(
            _RESOLVE_IN_DAEMON,
            return_value="ws://localhost:9222/devtools/page/xyz",
        ) as mock_resolve:
            result = _resolve_cdp_url_from_env("9222")
        assert result == "ws://localhost:9222/devtools/page/xyz"
        mock_resolve.assert_called_once_with("9222")

    def test_auto_calls_resolver(self) -> None:
        """``auto`` keyword must flow through resolve_cdp_input."""
        with patch(
            _RESOLVE_IN_DAEMON,
            return_value="ws://localhost:9222/devtools/page/pqr",
        ) as mock_resolve:
            assert _resolve_cdp_url_from_env("auto") is not None
        mock_resolve.assert_called_once_with("auto")

    def test_resolver_failure_wrapped_as_runtime_error(self) -> None:
        """Underlying errors must produce a friendly RuntimeError with hints."""
        with patch(
            _RESOLVE_IN_DAEMON,
            side_effect=ConnectionError("ECONNREFUSED"),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                _resolve_cdp_url_from_env("9222")
        msg = str(excinfo.value)
        assert "Failed to establish CDP connection" in msg
        assert "ECONNREFUSED" in msg
        assert "--remote-debugging-port" in msg
