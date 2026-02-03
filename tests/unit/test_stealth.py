"""
Unit tests for the Stealth module.
"""

import os
import tempfile
from pathlib import Path

import pytest

from bridgic.browser.session import StealthConfig, StealthArgsBuilder, create_stealth_config
from bridgic.browser.session._stealth import (
    CHROME_STEALTH_ARGS,
    CHROME_DISABLED_COMPONENTS,
    CHROME_DOCKER_ARGS,
    CHROME_DISABLE_SECURITY_ARGS,
    CHROME_IGNORE_DEFAULT_ARGS,
    STEALTH_EXTENSIONS,
)


class TestStealthConfig:
    """Tests for StealthConfig dataclass."""

    def test_default_config(self):
        """Test default stealth configuration."""
        config = StealthConfig()

        assert config.enabled is True
        assert config.enable_extensions is True
        assert config.disable_security is False
        assert "clipboard-read" in config.permissions
        assert "clipboard-write" in config.permissions

    def test_custom_config(self):
        """Test custom stealth configuration."""
        config = StealthConfig(
            enabled=True,
            enable_extensions=False,
            disable_security=True,
        )

        assert config.enabled is True
        assert config.enable_extensions is False
        assert config.disable_security is True

    def test_disabled_config(self):
        """Test disabled stealth configuration."""
        config = StealthConfig(enabled=False)

        assert config.enabled is False

    def test_cookie_whitelist_domains(self):
        """Test default cookie whitelist domains."""
        config = StealthConfig()

        assert "nature.com" in config.cookie_whitelist_domains
        assert "qatarairways.com" in config.cookie_whitelist_domains

    def test_custom_cookie_whitelist(self):
        """Test custom cookie whitelist domains."""
        config = StealthConfig(
            cookie_whitelist_domains=["example.com", "test.com"]
        )

        assert config.cookie_whitelist_domains == ["example.com", "test.com"]

    def test_extension_cache_dir_default(self):
        """Test default extension cache directory."""
        config = StealthConfig()

        expected_dir = Path.home() / ".cache" / "bridgic-browser" / "extensions"
        assert config.extension_cache_dir == expected_dir

    def test_can_use_extensions_headless(self):
        """Test that extensions can't be used in headless mode."""
        config = StealthConfig(enable_extensions=True)

        assert config.can_use_extensions(headless=True) is False
        assert config.can_use_extensions(headless=False) is True

    def test_can_use_extensions_disabled(self):
        """Test that extensions can't be used when disabled."""
        config = StealthConfig(enable_extensions=False)

        assert config.can_use_extensions(headless=True) is False
        assert config.can_use_extensions(headless=False) is False

    def test_docker_detection(self):
        """Test Docker environment detection."""
        # This test just verifies the field exists and is boolean
        config = StealthConfig()
        assert isinstance(config.in_docker, bool)


class TestStealthArgsBuilder:
    """Tests for StealthArgsBuilder class."""

    def test_build_args_basic(self):
        """Test building basic stealth args."""
        config = StealthConfig()
        builder = StealthArgsBuilder(config)

        args = builder.build_args()

        assert len(args) > 0
        # Should include stealth args
        assert any("--disable-blink-features=AutomationControlled" in arg for arg in args)

    def test_build_args_with_viewport(self):
        """Test building args with custom viewport."""
        config = StealthConfig()
        builder = StealthArgsBuilder(config)

        args = builder.build_args(viewport_width=1280, viewport_height=720)

        # Should include window-size arg
        assert "--window-size=1280,720" in args

    def test_build_args_disabled(self):
        """Test building args when stealth is disabled."""
        config = StealthConfig(enabled=False)
        builder = StealthArgsBuilder(config)

        args = builder.build_args()

        assert args == []

    def test_build_args_includes_disabled_features(self):
        """Test that args include disabled features."""
        config = StealthConfig()
        builder = StealthArgsBuilder(config)

        args = builder.build_args()

        # Should have --disable-features arg with components
        disable_features_arg = [a for a in args if a.startswith("--disable-features=")]
        assert len(disable_features_arg) == 1
        assert "AutomationControlled" in disable_features_arg[0]

    def test_build_args_docker(self):
        """Test building args for Docker environment."""
        config = StealthConfig(in_docker=True)
        builder = StealthArgsBuilder(config)

        args = builder.build_args()

        # Should include Docker-specific args
        assert "--no-sandbox" in args
        assert "--disable-gpu-sandbox" in args

    def test_build_args_disable_security(self):
        """Test building args with security disabled."""
        config = StealthConfig(disable_security=True)
        builder = StealthArgsBuilder(config)

        args = builder.build_args()

        # Should include security-disabling args
        assert "--disable-web-security" in args
        assert "--ignore-certificate-errors" in args

    def test_build_extension_args_headless(self):
        """Test that extension args are empty in headless mode."""
        config = StealthConfig(enable_extensions=True)
        builder = StealthArgsBuilder(config)

        args = builder.build_extension_args(headless=True)

        assert args == []

    def test_get_ignore_default_args(self):
        """Test getting ignore default args."""
        config = StealthConfig()
        builder = StealthArgsBuilder(config)

        ignore_args = builder.get_ignore_default_args()

        assert "--enable-automation" in ignore_args
        assert "--disable-extensions" in ignore_args

    def test_get_ignore_default_args_disabled(self):
        """Test ignore args are empty when stealth disabled."""
        config = StealthConfig(enabled=False)
        builder = StealthArgsBuilder(config)

        ignore_args = builder.get_ignore_default_args()

        assert ignore_args == []

    def test_get_context_options(self):
        """Test getting context options."""
        config = StealthConfig()
        builder = StealthArgsBuilder(config)

        options = builder.get_context_options()

        assert "permissions" in options
        assert "accept_downloads" in options
        assert options["accept_downloads"] is True

    def test_get_context_options_disabled(self):
        """Test context options are empty when stealth disabled."""
        config = StealthConfig(enabled=False)
        builder = StealthArgsBuilder(config)

        options = builder.get_context_options()

        assert options == {}


class TestStealthConstants:
    """Tests for stealth constant values."""

    def test_stealth_args_not_empty(self):
        """Test that stealth args list is not empty."""
        assert len(CHROME_STEALTH_ARGS) > 40

    def test_disabled_components_not_empty(self):
        """Test that disabled components list is not empty."""
        assert len(CHROME_DISABLED_COMPONENTS) > 20

    def test_docker_args_not_empty(self):
        """Test that Docker args list is not empty."""
        assert len(CHROME_DOCKER_ARGS) > 5

    def test_security_args_not_empty(self):
        """Test that security args list is not empty."""
        assert len(CHROME_DISABLE_SECURITY_ARGS) > 5

    def test_ignore_default_args_not_empty(self):
        """Test that ignore default args list is not empty."""
        assert len(CHROME_IGNORE_DEFAULT_ARGS) > 0
        assert "--enable-automation" in CHROME_IGNORE_DEFAULT_ARGS

    def test_extensions_defined(self):
        """Test that extensions are defined."""
        assert len(STEALTH_EXTENSIONS) >= 4
        assert "ublock_origin" in STEALTH_EXTENSIONS
        assert "cookie_consent" in STEALTH_EXTENSIONS

    def test_extension_structure(self):
        """Test extension info structure."""
        for key, ext_info in STEALTH_EXTENSIONS.items():
            assert "name" in ext_info
            assert "id" in ext_info
            assert "url" in ext_info
            assert ext_info["url"].startswith("https://")


class TestCreateStealthConfig:
    """Tests for create_stealth_config helper function."""

    def test_create_default(self):
        """Test creating default config."""
        config = create_stealth_config()

        assert config.enabled is True
        assert config.enable_extensions is True

    def test_create_custom(self):
        """Test creating custom config."""
        config = create_stealth_config(
            enabled=True,
            enable_extensions=False,
            disable_security=True,
        )

        assert config.enabled is True
        assert config.enable_extensions is False
        assert config.disable_security is True

    def test_create_with_kwargs(self):
        """Test creating config with additional kwargs."""
        config = create_stealth_config(
            cookie_whitelist_domains=["custom.com"],
        )

        assert "custom.com" in config.cookie_whitelist_domains


