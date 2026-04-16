"""Unit tests for daemon CDP env resolution (C-5)."""

from unittest.mock import patch

import pytest

from bridgic.browser.cli._daemon import _resolve_cdp_url_from_env


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

    def test_ws_url_short_circuits(self) -> None:
        """ws:// URL must bypass resolve_cdp_input entirely."""
        url = "ws://localhost:9222/devtools/page/abc123"
        with patch(
            "bridgic.browser.session._browser.resolve_cdp_input"
        ) as mock_resolve:
            result = _resolve_cdp_url_from_env(url)
        assert result == url
        mock_resolve.assert_not_called()

    def test_wss_url_short_circuits(self) -> None:
        """wss:// (TLS CDP over SSH tunnels) must also short-circuit."""
        url = "wss://remote-host/devtools/page/abc"
        with patch(
            "bridgic.browser.session._browser.resolve_cdp_input"
        ) as mock_resolve:
            result = _resolve_cdp_url_from_env(url)
        assert result == url
        mock_resolve.assert_not_called()

    def test_ws_url_case_insensitive(self) -> None:
        """WS:// / Ws:// should also short-circuit."""
        for url in ("WS://host:9222/devtools/", "Ws://host:9222/x"):
            with patch(
                "bridgic.browser.session._browser.resolve_cdp_input"
            ) as mock_resolve:
                assert _resolve_cdp_url_from_env(url) == url
                mock_resolve.assert_not_called()

    def test_port_string_calls_resolver(self) -> None:
        """Bare ports from shell must still flow through resolve_cdp_input."""
        with patch(
            "bridgic.browser.session._browser.resolve_cdp_input",
            return_value="ws://localhost:9222/devtools/page/xyz",
        ) as mock_resolve:
            result = _resolve_cdp_url_from_env("9222")
        assert result == "ws://localhost:9222/devtools/page/xyz"
        mock_resolve.assert_called_once_with("9222")

    def test_auto_calls_resolver(self) -> None:
        """``auto`` keyword must flow through resolve_cdp_input."""
        with patch(
            "bridgic.browser.session._browser.resolve_cdp_input",
            return_value="ws://localhost:9222/devtools/page/pqr",
        ) as mock_resolve:
            assert _resolve_cdp_url_from_env("auto") is not None
        mock_resolve.assert_called_once_with("auto")

    def test_resolver_failure_wrapped_as_runtime_error(self) -> None:
        """Underlying errors must produce a friendly RuntimeError with hints."""
        with patch(
            "bridgic.browser.session._browser.resolve_cdp_input",
            side_effect=ConnectionError("ECONNREFUSED"),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                _resolve_cdp_url_from_env("9222")
        msg = str(excinfo.value)
        assert "Failed to establish CDP connection" in msg
        assert "ECONNREFUSED" in msg
        assert "--remote-debugging-port" in msg
