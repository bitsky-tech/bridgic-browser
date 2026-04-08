"""
Unit tests for bridgic.browser._config and Browser config integration.

Coverage:
  _load_config_sources  — priority chain (user → local → env), error handling
  load_browser_config   — defaults + sources + overrides
  Browser.__init__      — auto config loading, explicit param override
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from bridgic.browser._config import _load_config_sources, load_browser_config


def _no_config_patches(fake_browser_home):
    """Context manager that patches config sources to return nothing."""
    mock_local = MagicMock()
    mock_local.is_file.return_value = False
    return (
        patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
        patch("bridgic.browser._config.Path", return_value=mock_local),
    )


# ── _load_config_sources ─────────────────────────────────────────────

class TestLoadConfigSources:
    """Tests for _load_config_sources() — file + env only, no defaults."""

    def test_empty_when_no_sources(self, tmp_path):
        """Returns empty dict when no config files or env var exist."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)  # patches BRIDGIC_BROWSER_HOME
        with p1, p2, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            cfg = _load_config_sources()

        assert cfg == {}

    def test_user_config_loaded(self, tmp_path):
        """User config ~/.bridgic/bridgic-browser/bridgic-browser.json is loaded."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"channel": "chrome", "locale": "zh-CN"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            cfg = _load_config_sources()

        assert cfg["channel"] == "chrome"
        assert cfg["locale"] == "zh-CN"

    def test_local_config_overrides_user(self, tmp_path):
        """Project-local config overrides user config for same keys."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"channel": "chrome", "headless": False})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = True
        mock_local.read_text.return_value = json.dumps({"channel": "msedge"})
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            cfg = _load_config_sources()

        assert cfg["channel"] == "msedge"  # local overrides user
        assert cfg["headless"] is False     # from user config (not overridden)

    def test_env_var_overrides_files(self, tmp_path):
        """BRIDGIC_BROWSER_JSON overrides both config files."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"channel": "chrome"})
        )
        env_json = json.dumps({"channel": "chromium", "locale": "de-DE"})

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": env_json}, clear=False),
        ):
            cfg = _load_config_sources()

        assert cfg["channel"] == "chromium"
        assert cfg["locale"] == "de-DE"

    def test_invalid_user_config_ignored(self, tmp_path):
        """Malformed user config is silently ignored."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text("not valid json")

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            cfg = _load_config_sources()

        assert cfg == {}

    def test_invalid_env_var_ignored(self, tmp_path):
        """Malformed BRIDGIC_BROWSER_JSON is silently ignored."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)  # patches BRIDGIC_BROWSER_HOME
        with p1, p2, patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": "{bad"}, clear=False):
            cfg = _load_config_sources()

        assert cfg == {}

    def test_non_dict_user_config_ignored(self, tmp_path):
        """User config with non-dict JSON (e.g. array) is ignored."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text('[1, 2, 3]')

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            cfg = _load_config_sources()

        assert cfg == {}

    def test_non_dict_env_var_ignored(self, tmp_path):
        """BRIDGIC_BROWSER_JSON with non-dict JSON (e.g. string) is ignored."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)
        with p1, p2, patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": '"just a string"'}, clear=False):
            cfg = _load_config_sources()

        assert cfg == {}


# ── load_browser_config ───────────────────────────────────────────────

class TestLoadBrowserConfig:
    """Tests for load_browser_config() — includes defaults and overrides."""

    def test_defaults_headless_true(self, tmp_path):
        """With no sources, headless defaults to True."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)  # patches BRIDGIC_BROWSER_HOME
        with p1, p2, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            kwargs = load_browser_config()

        assert kwargs["headless"] is True

    def test_overrides_beat_everything(self, tmp_path):
        """Explicit overrides have highest priority."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"headless": False, "channel": "chrome"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            kwargs = load_browser_config(headless=True, channel="msedge")

        assert kwargs["headless"] is True
        assert kwargs["channel"] == "msedge"

    def test_headed_mode_chromium_sandbox(self, tmp_path):
        """Headed mode auto-sets chromium_sandbox=True."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)  # patches BRIDGIC_BROWSER_HOME
        with p1, p2, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            kwargs = load_browser_config(headless=False)

        assert kwargs["chromium_sandbox"] is True

    def test_headed_preserves_explicit_sandbox_false(self, tmp_path):
        """Explicit chromium_sandbox=False is not overridden by headed mode."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)  # patches BRIDGIC_BROWSER_HOME
        with p1, p2, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            kwargs = load_browser_config(headless=False, chromium_sandbox=False)

        assert kwargs["chromium_sandbox"] is False

    def test_headless_mode_no_chromium_sandbox(self, tmp_path):
        """Headless mode (default) does NOT auto-set chromium_sandbox."""
        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)
        with p1, p2, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            kwargs = load_browser_config()

        assert "chromium_sandbox" not in kwargs


# ── Browser.__init__ config integration ───────────────────────────────

class TestBrowserConfigIntegration:
    """Tests for Browser() auto-loading config files."""

    def test_no_config_defaults(self, tmp_path):
        """Browser() with no config files defaults to headless=True, stealth enabled."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()

        p1, p2 = _no_config_patches(fake_browser_home)  # patches BRIDGIC_BROWSER_HOME
        # Also patch for the Browser.__init__ import path
        with p1, p2, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser._headless is True
        assert browser.stealth_enabled is True

    def test_config_headless_false(self, tmp_path):
        """Browser() picks up headless=false from config file."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"headless": False})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser._headless is False

    def test_explicit_headless_overrides_config(self, tmp_path):
        """Browser(headless=True) overrides config's headless=false."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"headless": False})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser(headless=True)

        assert browser._headless is True

    def test_config_channel_applied(self, tmp_path):
        """Browser() picks up channel from config file."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"channel": "chrome"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser._channel == "chrome"

    def test_explicit_channel_overrides_config(self, tmp_path):
        """Browser(channel='msedge') overrides config's channel."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"channel": "chrome"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser(channel="msedge")

        assert browser._channel == "msedge"

    def test_config_stealth_false(self, tmp_path):
        """Browser() picks up stealth=false from config file."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"stealth": False})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser.stealth_enabled is False

    def test_explicit_stealth_overrides_config(self, tmp_path):
        """Browser(stealth=True) overrides config's stealth=false."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"stealth": False})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser(stealth=True)

        assert browser.stealth_enabled is True

    def test_config_passthrough_params(self, tmp_path):
        """Browser() picks up pass-through params like chromium_sandbox from config."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"chromium_sandbox": True})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser._extra_kwargs.get("chromium_sandbox") is True

    def test_config_viewport(self, tmp_path):
        """Browser() picks up viewport from config file."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"viewport": {"width": 1280, "height": 720}})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser._viewport == {"width": 1280, "height": 720}

    def test_env_var_overrides_config_in_browser(self, tmp_path):
        """BRIDGIC_BROWSER_JSON overrides config files in Browser()."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"channel": "chrome"})
        )
        env_json = json.dumps({"channel": "msedge"})

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": env_json}, clear=False),
        ):
            browser = Browser()

        assert browser._channel == "msedge"

    def test_explicit_params_do_not_leak_to_extra_kwargs(self, tmp_path):
        """Explicit params must not leak config values into _extra_kwargs."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"headless": False, "channel": "chrome", "locale": "zh-CN"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            # Override headless and channel explicitly
            browser = Browser(headless=True, channel="msedge")

        # Explicit values should win
        assert browser._headless is True
        assert browser._channel == "msedge"
        assert browser._locale == "zh-CN"  # from config (not overridden)
        # Named-param keys must NOT appear in _extra_kwargs
        assert "headless" not in browser._extra_kwargs
        assert "channel" not in browser._extra_kwargs
        assert "locale" not in browser._extra_kwargs

    def test_config_cdp_url_loaded(self, tmp_path):
        """Browser() picks up cdp_url from config file."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"cdp_url": "ws://localhost:9222/devtools/browser/abc"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser._cdp_url == "ws://localhost:9222/devtools/browser/abc"
        assert "cdp_url" not in browser._extra_kwargs

    def test_explicit_cdp_url_overrides_config(self, tmp_path):
        """Browser(cdp_url=...) overrides config's cdp_url."""
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"cdp_url": "ws://localhost:9222/devtools/browser/old"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/new")

        assert browser._cdp_url == "ws://localhost:9222/devtools/browser/new"
        assert "cdp_url" not in browser._extra_kwargs

    # ── M2: cdp_url normalization in __init__ ─────────────────────────

    def test_config_cdp_url_port_string_normalized(self, tmp_path):
        """Browser() should normalize a bare port number from config to ws:// URL.

        Regression guard for M2: previously, a config like ``{"cdp_url":"9222"}``
        was passed unchanged to Playwright's connect_over_cdp(), which crashes
        deep in the driver because the value is not a WebSocket URL.
        """
        from bridgic.browser.session._browser import Browser

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"cdp_url": "9222"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
            patch(
                "bridgic.browser.session._browser.find_cdp_url",
                return_value="ws://localhost:9222/devtools/browser/zzz",
            ),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            browser = Browser()

        assert browser._cdp_url == "ws://localhost:9222/devtools/browser/zzz"

    def test_config_cdp_url_invalid_raises(self, tmp_path):
        """Browser() with malformed cdp_url in config should raise InvalidInputError."""
        from bridgic.browser.session._browser import Browser
        from bridgic.browser.errors import InvalidInputError

        fake_browser_home = tmp_path / ".bridgic"
        fake_browser_home.mkdir()
        (fake_browser_home / "bridgic-browser.json").write_text(
            json.dumps({"cdp_url": "this-is-not-valid"})
        )

        mock_local = MagicMock()
        mock_local.is_file.return_value = False
        with (
            patch("bridgic.browser._config.BRIDGIC_BROWSER_HOME", fake_browser_home),
            patch("bridgic.browser._config.Path", return_value=mock_local),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BRIDGIC_BROWSER_JSON", None)
            with pytest.raises(InvalidInputError, match="Failed to resolve cdp_url"):
                Browser()

    def test_explicit_cdp_url_port_normalized(self, monkeypatch):
        """Browser(cdp_url='9222') as an explicit argument is also normalized."""
        from bridgic.browser.session._browser import Browser

        monkeypatch.setattr(
            "bridgic.browser.session._browser.find_cdp_url",
            lambda mode, host, port: f"ws://{host}:{port}/devtools/browser/normalized",
        )
        browser = Browser(cdp_url="9222")
        assert browser._cdp_url == "ws://localhost:9222/devtools/browser/normalized"
