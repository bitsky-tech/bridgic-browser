"""
Unit tests for the Stealth module.
"""

from pathlib import Path

from bridgic.browser.session import StealthConfig, StealthArgsBuilder, create_stealth_config
from bridgic.browser.session._stealth import (
    CHROME_STEALTH_ARGS,
    CHROME_STEALTH_ARGS_HEADED,
    CHROME_DISABLED_COMPONENTS,
    CHROME_DISABLED_COMPONENTS_HEADED,
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
        """Test that extensions can't be used in headless mode (chromium-headless-shell limitation)."""
        config = StealthConfig(enable_extensions=True)

        assert config.can_use_extensions(headless=True) is False
        assert config.can_use_extensions(headless=False) is True

    def test_can_use_extensions_disabled(self):
        """Test that extensions can't be used when disabled."""
        config = StealthConfig(enable_extensions=False)

        assert config.can_use_extensions(headless=True) is False
        assert config.can_use_extensions(headless=False) is False

    def test_docker_detection_reflects_cgroup(self):
        """Test that in_docker=True activates Docker-specific Chrome args."""
        config_docker = StealthConfig(in_docker=True)
        config_normal = StealthConfig(in_docker=False)
        assert config_docker.in_docker is True
        assert config_normal.in_docker is False
        # Docker config should produce --no-sandbox in build_args
        from bridgic.browser.session import StealthArgsBuilder
        docker_args = StealthArgsBuilder(config_docker).build_args()
        normal_args = StealthArgsBuilder(config_normal).build_args()
        assert "--no-sandbox" in docker_args
        assert "--no-sandbox" not in normal_args


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

    def test_get_init_script_returns_none_when_disabled(self):
        """get_init_script() returns None when stealth is disabled."""
        config = StealthConfig(enabled=False)
        builder = StealthArgsBuilder(config)

        assert builder.get_init_script() is None

    def test_get_init_script_default_locale(self):
        """get_init_script() defaults navigator.languages to ['en-US', 'en']."""
        builder = StealthArgsBuilder(StealthConfig())

        script = builder.get_init_script()

        assert script is not None
        assert '["en-US", "en"]' in script
        assert "__BRIDGIC_LANGS__" not in script

    def test_get_init_script_en_us_locale(self):
        """Explicit en-US locale produces ['en-US', 'en']."""
        builder = StealthArgsBuilder(StealthConfig())

        script = builder.get_init_script(locale="en-US")

        assert '["en-US", "en"]' in script

    def test_get_init_script_non_english_locale(self):
        """Non-English locale produces [locale, base, 'en'] for consistency with navigator.language."""
        builder = StealthArgsBuilder(StealthConfig())

        for locale, expected in [
            ("zh-CN", '["zh-CN", "zh", "en"]'),
            ("fr-FR", '["fr-FR", "fr", "en"]'),
            ("de-DE", '["de-DE", "de", "en"]'),
        ]:
            script = builder.get_init_script(locale=locale)
            assert expected in script, f"locale={locale!r}: expected {expected!r} in script"

    def test_get_init_script_no_placeholder_remains(self):
        """The __BRIDGIC_LANGS__ placeholder must always be substituted."""
        builder = StealthArgsBuilder(StealthConfig())

        for locale in [None, "en-US", "zh-CN", "fr-FR"]:
            script = builder.get_init_script(locale=locale)
            assert "__BRIDGIC_LANGS__" not in script, (
                f"Placeholder not substituted for locale={locale!r}"
            )

    def test_get_init_script_empty_locale(self):
        """Empty string locale falls through to default ['en-US', 'en']."""
        builder = StealthArgsBuilder(StealthConfig())

        script = builder.get_init_script(locale="")

        assert '["en-US", "en"]' in script
        assert "__BRIDGIC_LANGS__" not in script

    def test_get_init_script_bare_language_locale(self):
        """Bare language locale (no region) produces [locale, 'en'] without duplication."""
        builder = StealthArgsBuilder(StealthConfig())

        script = builder.get_init_script(locale="zh")

        # base == normalized so no base appended; English fallback added
        assert '["zh", "en"]' in script
        assert "__BRIDGIC_LANGS__" not in script

    def test_get_init_script_three_part_locale(self):
        """Three-part locale (e.g. zh-Hans-CN) produces [locale, base, 'en']."""
        builder = StealthArgsBuilder(StealthConfig())

        script = builder.get_init_script(locale="zh-Hans-CN")

        assert '["zh-Hans-CN", "zh", "en"]' in script
        assert "__BRIDGIC_LANGS__" not in script

    def test_get_init_script_has_languages_try_catch(self):
        """navigator.languages defineProperty is wrapped in try/catch."""
        builder = StealthArgsBuilder(StealthConfig())

        script = builder.get_init_script()

        # The try/catch guard must be present around the languages defineProperty
        assert "try {" in script
        assert "Object.defineProperty(navigator, 'languages'" in script

    def test_get_init_script_chrome_guard_checks_csi_and_loadtimes(self):
        """window.chrome guard checks csi and loadTimes, not just runtime."""
        builder = StealthArgsBuilder(StealthConfig())

        script = builder.get_init_script()

        # Must check for csi and loadTimes so partial chrome objects are also patched
        assert "!window.chrome.csi" in script
        assert "!window.chrome.loadTimes" in script


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
        assert len(STEALTH_EXTENSIONS) >= 3
        assert "ublock_origin" in STEALTH_EXTENSIONS
        assert "cookie_consent" in STEALTH_EXTENSIONS

    def test_extension_structure(self):
        """Test extension info structure."""
        for ext_info in STEALTH_EXTENSIONS.values():
            assert "name" in ext_info
            assert "id" in ext_info
            assert "url" in ext_info
            assert ext_info["url"].startswith("https://")


class TestNewHeadlessMode:
    """Tests for new headless mode (--headless=new with full Chromium binary)."""

    def test_use_new_headless_default_is_true(self):
        config = StealthConfig()
        assert config.use_new_headless is True

    def test_use_new_headless_can_be_disabled(self):
        config = StealthConfig(use_new_headless=False)
        assert config.use_new_headless is False

    def test_build_args_headless_new_in_headless_mode(self):
        """--headless=new and companion args appear when headless_intent=True and use_new_headless=True."""
        builder = StealthArgsBuilder(StealthConfig(use_new_headless=True))
        args = builder.build_args(headless_intent=True)
        assert "--headless=new" in args
        assert "--hide-scrollbars" in args
        assert "--mute-audio" in args
        assert any("blink-settings" in a for a in args)

    def test_build_args_no_headless_new_when_headed(self):
        """--headless=new must NOT appear when headless_intent=False."""
        builder = StealthArgsBuilder(StealthConfig(use_new_headless=True))
        args = builder.build_args(headless_intent=False)
        assert "--headless=new" not in args

    def test_build_args_no_headless_new_when_disabled(self):
        """--headless=new must NOT appear when use_new_headless=False."""
        builder = StealthArgsBuilder(StealthConfig(use_new_headless=False))
        args = builder.build_args(headless_intent=True)
        assert "--headless=new" not in args

    def test_build_args_default_headless_intent_is_true(self):
        """Default headless_intent=True so existing callers get --headless=new."""
        builder = StealthArgsBuilder(StealthConfig(use_new_headless=True))
        args_default = builder.build_args()
        args_explicit = builder.build_args(headless_intent=True)
        assert args_default == args_explicit


class TestRemovedFingerprintArgs:
    """Confirm high-risk fingerprint args are absent from CHROME_STEALTH_ARGS."""

    def test_simulate_outdated_not_in_stealth_args(self):
        assert not any("simulate-outdated-no-au" in a for a in CHROME_STEALTH_ARGS)

    def test_enable_network_information_downlink_max_removed(self):
        assert "--enable-network-information-downlink-max" not in CHROME_STEALTH_ARGS

    def test_enable_features_network_service_removed(self):
        assert not any("NetworkService,NetworkServiceInProcess" in a for a in CHROME_STEALTH_ARGS)


class TestInitScriptPatches:
    """Verify new navigator patches in the JS init script."""

    def test_init_script_device_memory_patch(self):
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "navigator.deviceMemory" in script

    def test_init_script_hardware_concurrency_patch(self):
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "navigator.hardwareConcurrency" in script

    def test_init_script_connection_patch(self):
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "navigator.connection" in script
        assert "effectiveType" in script

    def test_init_script_outer_height_no_plus_85(self):
        """The old +85 browser-chrome offset must not appear in new headless mode."""
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "innerHeight + 85" not in script

    def test_init_script_webgl_vendor_patch(self):
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "WebGLRenderingContext" in script
        assert "WebGL2RenderingContext" in script
        assert "37445" in script  # UNMASKED_VENDOR_WEBGL
        assert "37446" in script  # UNMASKED_RENDERER_WEBGL
        assert "Intel Inc." in script
        assert "Intel Iris OpenGL Engine" in script

    def test_init_script_webgl_conditional_spoof(self):
        """WebGL spoof must be conditional — only fires for SwiftShader/Google GPU."""
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        # Conditional check must be present
        assert "_val.includes('Google')" in script
        assert "_val.includes('SwiftShader')" in script
        # Unconditional return must NOT be present (would break headed Apple Silicon)
        assert "if (parameter === 37445) return 'Intel Inc.';" not in script
        assert "if (parameter === 37446) return 'Intel Iris OpenGL Engine';" not in script

    def test_init_script_fn_tostring_spoofing(self):
        """_mkNative / Function.prototype.toString spoofing must be present and first."""
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        # Framework must be present
        assert "_mkNative" in script
        assert "_nativeFns" in script
        assert "Function.prototype.toString" in script
        assert "[native code]" in script
        # getParameter must be registered as native
        assert "_mkNative(function getParameter" in script
        # permissions.query must be registered as native
        assert "_mkNative(function query" in script
        # _mkNative setup must appear before any usage (i.e. before navigator.webdriver)
        mk_pos = script.index("const _mkNative")
        wd_pos = script.index("navigator.webdriver")
        assert mk_pos < wd_pos, "_mkNative must be defined before first usage"

    def test_init_script_document_hasfocus(self):
        """document.hasFocus must be patched to return true in headless."""
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "document.hasFocus" in script
        assert "_mkNative(function hasFocus" in script

    def test_init_script_document_visibility(self):
        """document.hidden and visibilityState must be patched."""
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "defineProperty(document, 'hidden'" in script
        assert "defineProperty(document, 'visibilityState'" in script
        assert "'visible'" in script

    def test_init_script_notification_permission(self):
        """Notification.permission guard must be present."""
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        assert "Notification.permission" in script
        assert "'default'" in script

    def test_init_script_webdriver_conditional(self):
        """navigator.webdriver must check the prototype first, not override unconditionally."""
        script = StealthArgsBuilder(StealthConfig()).get_init_script()
        # Must check the prototype descriptor before patching — avoids creating
        # a detectable own-property on navigator where real Chrome has none
        assert "Navigator.prototype, 'webdriver'" in script
        assert "getOwnPropertyDescriptor" in script
        # The unconditional one-liner form must not be present (that's the old broken form)
        assert "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });" not in script


class TestHeadedModeArgs:
    """Tests for headed mode (headless_intent=False) Chrome args."""

    def test_headed_excludes_background_networking(self):
        """--disable-background-networking must NOT appear in headed mode."""
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--disable-background-networking" not in args

    def test_headed_excludes_renderer_backgrounding(self):
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--disable-renderer-backgrounding" not in args

    def test_headed_excludes_component_update(self):
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--disable-component-update" not in args

    def test_headed_excludes_sync(self):
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--disable-sync" not in args

    def test_headed_excludes_domain_reliability(self):
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--disable-domain-reliability" not in args

    def test_headed_excludes_field_trial_config(self):
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--disable-field-trial-config" not in args

    def test_headed_excludes_metrics_recording(self):
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--metrics-recording-only" not in args

    def test_headed_includes_automation_controlled(self):
        """AutomationControlled must remain in headed mode."""
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        assert "--disable-blink-features=AutomationControlled" in args

    def test_headed_no_headless_new(self):
        """headed mode must never add --headless=new even with use_new_headless=True."""
        builder = StealthArgsBuilder(StealthConfig(use_new_headless=True))
        args = builder.build_args(headless_intent=False)
        assert "--headless=new" not in args

    def test_headed_disable_features_minimal(self):
        """Headed mode --disable-features= must not contain heavy user-facing features."""
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False)
        df_args = [a for a in args if a.startswith("--disable-features=")]
        assert len(df_args) == 1
        assert "AutomationControlled" in df_args[0]
        # These are real user features; disabling them is detectable
        assert "HttpsUpgrades" not in df_args[0]
        assert "MediaRouter" not in df_args[0]
        assert "Translate" not in df_args[0]

    def test_headed_lang_from_locale(self):
        """--lang arg must reflect the passed locale in headed mode."""
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False, locale="zh-CN")
        assert "--lang=zh-CN" in args

    def test_headed_lang_normalises_underscore(self):
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False, locale="zh_CN")
        assert "--lang=zh-CN" in args

    def test_headed_lang_default_en_us(self):
        """--lang defaults to en-US when no locale passed in headed mode."""
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=False, locale=None)
        assert "--lang=en-US" in args

    def test_headless_still_has_full_flags(self):
        """headless_intent=True (default) should still contain all original flags."""
        builder = StealthArgsBuilder(StealthConfig())
        args = builder.build_args(headless_intent=True)
        assert "--disable-background-networking" in args
        assert "--disable-component-update" in args
        assert "--disable-sync" in args

    def test_default_headless_intent_backward_compat(self):
        """build_args() with no headless_intent arg defaults to headless=True behaviour."""
        builder = StealthArgsBuilder(StealthConfig())
        args_default = builder.build_args()
        args_explicit = builder.build_args(headless_intent=True)
        assert args_default == args_explicit

    def test_headed_fewer_args_than_headless(self):
        """Headed mode should produce significantly fewer args than headless mode."""
        builder = StealthArgsBuilder(StealthConfig())
        headed_args = builder.build_args(headless_intent=False)
        headless_args = builder.build_args(headless_intent=True)
        assert len(headed_args) < len(headless_args)

    def test_headed_constants_exported(self):
        """New headed constants are importable and contain expected values."""
        assert len(CHROME_STEALTH_ARGS_HEADED) >= 5
        assert "--disable-blink-features=AutomationControlled" in CHROME_STEALTH_ARGS_HEADED
        assert len(CHROME_DISABLED_COMPONENTS_HEADED) >= 1
        assert "AutomationControlled" in CHROME_DISABLED_COMPONENTS_HEADED


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


