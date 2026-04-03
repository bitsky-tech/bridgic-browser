"""
Unit tests for the Browser class.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from bridgic.browser.errors import InvalidInputError, OperationError, StateError
from bridgic.browser.session import Browser, StealthConfig
import bridgic.browser.session._browser as _browser_module


@pytest.fixture(autouse=True)
def _isolate_config():
    """Prevent real config files and real filesystem side-effects from affecting unit tests."""
    from bridgic.browser._constants import BRIDGIC_USER_DATA_DIR as _REAL_UDD
    _mock_udd = MagicMock(spec=Path)
    _mock_udd.__str__ = MagicMock(return_value=str(_REAL_UDD))
    _mock_udd.mkdir = MagicMock()
    with patch("bridgic.browser._config._load_config_sources", return_value={}), \
         patch("bridgic.browser.session._browser.BRIDGIC_USER_DATA_DIR", _mock_udd):
        yield


class TestBrowserInitialization:
    """Tests for Browser initialization and configuration."""

    def test_default_initialization(self):
        """Test Browser with default parameters."""
        browser = Browser()

        assert browser.headless is True
        assert browser.viewport == {"width": 1600, "height": 900}
        assert browser.user_data_dir is None
        assert browser.stealth_enabled is True  # Stealth is enabled by default
        assert browser.clear_user_data is False
        assert browser.use_persistent_context is True  # default: persistent

    def test_custom_viewport(self):
        """Test Browser with custom viewport."""
        browser = Browser(viewport={"width": 1280, "height": 720})

        assert browser.viewport == {"width": 1280, "height": 720}

    def test_user_data_dir_enables_persistent_context(self):
        """Test that providing user_data_dir enables persistent context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            browser = Browser(user_data_dir=tmpdir)

            assert browser.user_data_dir == Path(tmpdir)
            assert browser.use_persistent_context is True

    def test_user_data_dir_expansion(self):
        """Test that ~ in user_data_dir is expanded."""
        browser = Browser(user_data_dir="~/test_browser_data")

        assert browser.user_data_dir == Path.home() / "test_browser_data"

    def test_stealth_enabled_by_default(self):
        """Test that stealth mode is enabled by default."""
        browser = Browser()

        assert browser.stealth_enabled is True
        assert browser.stealth_config is not None

    def test_stealth_disabled(self):
        """Test disabling stealth mode."""
        browser = Browser(stealth=False)

        assert browser.stealth_enabled is False
        assert browser.stealth_config is None

    def test_stealth_custom_config(self):
        """Test custom stealth configuration."""
        config = StealthConfig(disable_security=True)
        browser = Browser(stealth=config)

        assert browser.stealth_enabled is True
        assert browser.stealth_config.disable_security is True

    def test_strip_playwright_call_log(self):
        message = (
            "Wait condition not met: Locator.wait_for: Timeout 30000ms exceeded.\n"
            "Call Log:\n- waiting for get_by_text(\"Golden3\").first to be visible"
        )
        stripped = _browser_module._strip_playwright_call_log(message)
        assert "Call Log" not in stripped
        assert stripped.endswith("Timeout 30000ms exceeded.")

    def test_devtools_forces_headless_false(self):
        """Test that devtools forces headless=False."""
        browser = Browser(headless=True, devtools=True)

        assert browser.headless is False

    def test_no_viewport_conflict_raises(self):
        """Test that viewport and no_viewport cannot be used together."""
        with pytest.raises(InvalidInputError) as exc_info:
            Browser(viewport={"width": 800, "height": 600}, no_viewport=True)
        assert exc_info.value.code == "VIEWPORT_CONFLICT"

    def test_headless_false_uses_persistent_context(self):
        """Headed mode uses persistent context (clear_user_data=False default)."""
        browser = Browser(headless=False, stealth=True)

        assert browser.use_persistent_context is True

    def test_headless_true_uses_persistent_context_by_default(self):
        """Headless mode also uses persistent context by default (clear_user_data=False)."""
        browser = Browser(headless=True, stealth=True)

        assert browser.use_persistent_context is True

    def test_clear_user_data_disables_persistent_context(self):
        """clear_user_data=True disables persistent context regardless of headless mode."""
        browser_headless = Browser(headless=True, clear_user_data=True)
        browser_headed = Browser(headless=False, clear_user_data=True)

        assert browser_headless.use_persistent_context is False
        assert browser_headed.use_persistent_context is False

    def test_config_file_clear_user_data_activates_ephemeral_mode(self):
        """clear_user_data=True from config file activates ephemeral mode."""
        with patch("bridgic.browser._config._load_config_sources", return_value={"clear_user_data": True}):
            browser = Browser()
            assert browser.clear_user_data is True
            assert browser.use_persistent_context is False

    def test_explicit_false_overrides_config_clear_user_data(self):
        """Explicit clear_user_data=False in constructor wins over config file True."""
        with patch("bridgic.browser._config._load_config_sources", return_value={"clear_user_data": True}):
            browser = Browser(clear_user_data=False)
            assert browser.clear_user_data is False
            assert browser.use_persistent_context is True

    def test_explicit_true_overrides_config_false_clear_user_data(self):
        """Explicit clear_user_data=True in constructor wins over config file False."""
        with patch("bridgic.browser._config._load_config_sources", return_value={"clear_user_data": False}):
            browser = Browser(clear_user_data=True)
            assert browser.clear_user_data is True
            assert browser.use_persistent_context is False

    def test_downloads_path_creates_manager(self):
        """Test that downloads_path creates a download manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            browser = Browser(downloads_path=tmpdir)

            assert browser.download_manager is not None
            assert browser.download_manager.downloads_path == Path(tmpdir)

    def test_no_downloads_path_no_manager(self):
        """Test that no downloads_path means no download manager."""
        browser = Browser()

        assert browser.download_manager is None
        assert browser.downloaded_files == []

    def test_get_config(self):
        """Test get_config returns all configuration."""
        browser = Browser(
            headless=False,
            viewport={"width": 1280, "height": 720},
            channel="chrome",
            slow_mo=100,
        )

        config = browser.get_config()

        assert config["headless"] is False
        assert config["viewport"] == {"width": 1280, "height": 720}
        assert config["channel"] == "chrome"
        assert config["slow_mo"] == 100
        assert config["stealth_enabled"] is True
        # clear_user_data=False is not None, so it must appear in the returned dict
        assert config["clear_user_data"] is False
        assert config["use_persistent_context"] is True


class TestBrowserLaunchOptions:
    """Tests for Browser launch options generation."""

    def test_launch_options_basic(self):
        """Test basic launch options generation."""
        browser = Browser(headless=True, stealth=False)
        options = browser._get_launch_options()

        assert options["headless"] is True
        assert "args" not in options or options["args"] == []

    def test_launch_options_with_stealth(self):
        """Test launch options include stealth args."""
        browser = Browser(headless=True, stealth=True)
        options = browser._get_launch_options()

        assert "args" in options
        assert len(options["args"]) > 40  # Stealth adds 50+ args
        assert "ignore_default_args" in options

    def test_launch_options_with_proxy(self):
        """Test launch options with proxy."""
        browser = Browser(
            stealth=False,
            proxy={"server": "http://proxy:8080"},
        )
        options = browser._get_launch_options()

        assert options["proxy"] == {"server": "http://proxy:8080"}

    def test_launch_options_custom_args(self):
        """Test custom args are merged with stealth args."""
        browser = Browser(
            stealth=True,
            args=["--custom-arg", "--another-arg"],
        )
        options = browser._get_launch_options()

        assert "--custom-arg" in options["args"]
        assert "--another-arg" in options["args"]

    def test_launch_options_no_downloads_path(self):
        """Test that downloads_path is NOT passed to Playwright."""
        with tempfile.TemporaryDirectory() as tmpdir:
            browser = Browser(downloads_path=tmpdir, stealth=False)
            options = browser._get_launch_options()

            # downloads_path should NOT be in launch options
            # (DownloadManager handles it instead)
            assert "downloads_path" not in options

    def test_launch_options_new_headless_active(self):
        """stealth+headless=True redirects to full Chromium binary via headless=False + --headless=new."""
        browser = Browser(headless=True, stealth=True)
        options = browser._get_launch_options()

        # Playwright must receive headless=False to use the full chromium binary
        assert options["headless"] is False
        # The actual headless behaviour comes from --headless=new in args
        assert "--headless=new" in options["args"]
        assert "--hide-scrollbars" in options["args"]
        assert "--mute-audio" in options["args"]

    def test_launch_options_new_headless_disabled_by_config(self):
        """use_new_headless=False restores chromium-headless-shell behaviour."""
        from bridgic.browser.session import StealthConfig
        browser = Browser(headless=True, stealth=StealthConfig(use_new_headless=False))
        options = browser._get_launch_options()

        assert options["headless"] is True
        assert "--headless=new" not in options.get("args", [])

    def test_launch_options_system_chrome_not_redirected(self):
        """System Chrome (channel set) is NOT redirected to new headless mode."""
        browser = Browser(headless=True, stealth=True, channel="chrome")
        options = browser._get_launch_options()

        assert options["headless"] is True
        assert "--headless=new" not in options.get("args", [])

    def test_launch_options_headless_false_unchanged(self):
        """headless=False with stealth does NOT add --headless=new."""
        browser = Browser(headless=False, stealth=True)
        options = browser._get_launch_options()

        assert options["headless"] is False
        assert "--headless=new" not in options.get("args", [])

    def test_launch_options_stealth_disabled_headless_unchanged(self):
        """stealth=False leaves headless setting unchanged."""
        browser = Browser(headless=True, stealth=False)
        options = browser._get_launch_options()

        assert options["headless"] is True


class TestBrowserContextOptions:
    """Tests for Browser context options generation."""

    def test_context_options_basic(self):
        """Test basic context options generation."""
        browser = Browser(stealth=False)
        options = browser._get_context_options()

        assert options["viewport"] == {"width": 1600, "height": 900}

    def test_context_options_no_viewport(self):
        """Test no_viewport disables viewport and passes through flag."""
        browser = Browser(stealth=False, no_viewport=True)
        options = browser._get_context_options()

        assert "viewport" not in options
        assert options["no_viewport"] is True

    def test_context_options_stealth_no_viewport_has_screen_fallback(self):
        """stealth=True + no_viewport=True: screen falls back to 1600×900 (window.screen spoof)."""
        browser = Browser(stealth=True, no_viewport=True)
        options = browser._get_context_options()

        assert "viewport" not in options
        assert options["no_viewport"] is True
        # Even with no_viewport, stealth must set a screen size for window.screen spoofing
        assert options["screen"] == {"width": 1600, "height": 900}

    def test_context_options_with_stealth(self):
        """Test context options include stealth settings."""
        browser = Browser(stealth=True)
        options = browser._get_context_options()

        assert "permissions" in options
        assert "accept_downloads" in options
        assert "screen" in options  # Stealth adds screen to match viewport
        assert options["screen"] == {"width": 1600, "height": 900}  # default viewport

    def test_context_options_stealth_screen_matches_custom_viewport(self):
        """stealth=True with custom viewport: screen must mirror viewport dimensions."""
        browser = Browser(stealth=True, viewport={"width": 1280, "height": 720})
        options = browser._get_context_options()

        assert options["screen"] == {"width": 1280, "height": 720}

    def test_context_options_user_agent(self):
        """Test context options with custom user agent."""
        browser = Browser(
            stealth=False,
            user_agent="Custom User Agent",
        )
        options = browser._get_context_options()

        assert options["user_agent"] == "Custom User Agent"

    def test_context_options_locale(self):
        """Test context options with locale."""
        browser = Browser(
            stealth=False,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        options = browser._get_context_options()

        assert options["locale"] == "zh-CN"
        assert options["timezone_id"] == "Asia/Shanghai"

    def test_context_options_accept_downloads_auto(self):
        """Test accept_downloads is auto-enabled with downloads_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            browser = Browser(downloads_path=tmpdir, stealth=False)
            options = browser._get_context_options()

            assert options["accept_downloads"] is True


class TestBrowserStartStop:
    """Tests for Browser start and stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_normal_mode(self, mock_playwright):
        """Test starting browser with clear_user_data=True uses launch() not persistent."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False, clear_user_data=True)
            await browser._start()

            assert browser._playwright is not None
            assert browser._context is not None
            mock_playwright.chromium.launch.assert_called_once()
            mock_playwright.chromium.launch_persistent_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_persistent_mode(self, mock_playwright):
        """Test starting browser in persistent context mode."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            with tempfile.TemporaryDirectory() as tmpdir:
                browser = Browser(user_data_dir=tmpdir, stealth=False)
                await browser._start()

                mock_playwright.chromium.launch_persistent_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_already_started(self, mock_playwright):
        """Test that starting an already started browser logs warning."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False, clear_user_data=True)
            await browser._start()

            # Second start should just return
            await browser._start()

            # launch should only be called once
            assert mock_playwright.chromium.launch.call_count == 1

    @pytest.mark.asyncio
    async def test_start_rolls_back_on_launch_failure(self, mock_playwright):
        """If launch fails after playwright starts, partial state should be cleaned up."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_playwright.chromium.launch = AsyncMock(side_effect=RuntimeError("launch failed"))

            browser = Browser(stealth=False, clear_user_data=True)
            with pytest.raises(RuntimeError, match="launch failed"):
                await browser._start()

            assert browser._playwright is None
            assert browser._browser is None
            assert browser._context is None
            assert browser._page is None
            mock_playwright.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_headed_mode_skips_init_script(self, mock_playwright):
        """In headed mode (headless=False) add_init_script must NOT be called."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(headless=False)  # headed mode, stealth enabled by default
            await browser._start()

            # Persistent context returns context directly (no new_context call)
            mock_playwright.chromium.launch_persistent_context.return_value.add_init_script.assert_not_called()

    @pytest.mark.asyncio
    async def test_headless_mode_injects_init_script(self, mock_playwright):
        """In headless mode (headless=True) add_init_script must be called."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(headless=True)  # headless mode, stealth enabled by default
            await browser._start()

            # Persistent context returns context directly; stealth init script is injected
            mock_playwright.chromium.launch_persistent_context.return_value.add_init_script.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_cleanup(self, mock_playwright, mock_context, mock_page):
        """Test that stop cleans up all resources."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()
            await browser.close()

            assert browser._playwright is None
            assert browser._browser is None
            assert browser._context is None
            assert browser._page is None

    @pytest.mark.asyncio
    async def test_clear_user_data_uses_launch_not_persistent(self, mock_playwright):
        """clear_user_data=True uses launch()+new_context() not launch_persistent_context."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(clear_user_data=True, stealth=False)
            assert browser.use_persistent_context is False
            assert browser.clear_user_data is True

            await browser._start()

            # Should have called launch() not launch_persistent_context()
            mock_playwright.chromium.launch.assert_called_once()
            mock_playwright.chromium.launch_persistent_context.assert_not_called()

            await browser.close()

    @pytest.mark.asyncio
    async def test_clear_user_data_true_ignores_user_data_dir(self, mock_playwright):
        """clear_user_data=True causes launch()+new_context() even when user_data_dir is set."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(clear_user_data=True, user_data_dir="/tmp/myprofile", stealth=False)
            assert browser.use_persistent_context is False

            await browser._start()

            mock_playwright.chromium.launch.assert_called_once()
            mock_playwright.chromium.launch_persistent_context.assert_not_called()

            await browser.close()

    @pytest.mark.asyncio
    async def test_default_persistent_uses_bridgic_user_data_dir(self, mock_playwright):
        """Default (clear_user_data=False, no user_data_dir) passes BRIDGIC_USER_DATA_DIR to launch_persistent_context."""
        from bridgic.browser._constants import BRIDGIC_USER_DATA_DIR

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            assert browser.use_persistent_context is True
            assert browser.clear_user_data is False

            await browser._start()

            mock_playwright.chromium.launch_persistent_context.assert_called_once()
            call_kwargs = mock_playwright.chromium.launch_persistent_context.call_args
            # _isolate_config fixture patches BRIDGIC_USER_DATA_DIR with a MagicMock whose
            # str() returns str(BRIDGIC_USER_DATA_DIR), so this check remains meaningful.
            assert call_kwargs.kwargs.get("user_data_dir") == str(BRIDGIC_USER_DATA_DIR)

            await browser.close()

    @pytest.mark.asyncio
    async def test_async_context_manager_uses_start_and_kill(self, mock_playwright, mock_context, mock_page):
        """Test that async context manager starts and kills browser."""
        from bridgic.browser.session import Browser
        from bridgic.browser.session import _browser as browser_module

        with patch.object(browser_module, "async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            # Use async context manager
            async with Browser(stealth=False) as browser:
                # Inside context, browser should have active page
                assert browser._page is not None

            # After context exit, resources should be cleaned up
            assert browser._playwright is None
            assert browser._browser is None
            assert browser._context is None
            assert browser._page is None

    @pytest.mark.asyncio
    async def test_stop_auto_saves_active_trace_and_video(self, mock_playwright):
        """stop() auto-finalizes active tracing/video before teardown.

        close() auto-calls inspect_pending_close_artifacts() which creates a
        session directory and pre-allocates a trace path inside it, so trace
        and video end up grouped in the same session dir.
        """
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            context.tracing = MagicMock()
            context.tracing.stop = AsyncMock()
            context.pages = [page]

            # Create a real temp file so shutil.copy2 works in Phase 2
            _tmp_video = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
            _tmp_video.write(b"fake video data")
            _tmp_video.close()
            try:
                page.video = MagicMock()
                page.video.path = AsyncMock(return_value=_tmp_video.name)
                page.video.save_as = AsyncMock()

                context_key = browser_module._get_context_key(context)
                browser._tracing_state[context_key] = True
                browser._video_state[context_key] = True

                await browser.close()

                # Trace should be saved into the auto-created session dir
                trace_call = context.tracing.stop.call_args
                trace_path = trace_call.kwargs.get("path") or trace_call.args[0]
                assert "close-" in trace_path
                assert trace_path.endswith("trace.zip")

                page.close.assert_awaited()
                assert browser._last_shutdown_artifacts["trace"] == [os.path.abspath(trace_path)]
                # Video copied via shutil.copy2 into session dir
                assert len(browser._last_shutdown_artifacts["video"]) == 1
                video_path = browser._last_shutdown_artifacts["video"][0]
                assert "close-" in video_path
                assert "video" in video_path
                assert context_key not in browser._tracing_state
                assert context_key not in browser._video_state
            finally:
                os.unlink(_tmp_video.name)

    @pytest.mark.asyncio
    async def test_stop_reports_auto_saved_paths(self, mock_playwright):
        """stop() should include auto-saved artifact paths in the result."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            context.tracing = MagicMock()
            context.tracing.stop = AsyncMock()
            context.pages = [page]

            # Create a real temp file so shutil.copy2 works in Phase 2
            _tmp_video = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
            _tmp_video.write(b"fake video data")
            _tmp_video.close()
            try:
                page.video = MagicMock()
                page.video.path = AsyncMock(return_value=_tmp_video.name)
                page.video.save_as = AsyncMock()

                context_key = browser_module._get_context_key(context)
                browser._tracing_state[context_key] = True
                browser._video_state[context_key] = True

                result = await browser.close()

                assert "Browser closed successfully" in result
                assert "trace.zip" in result
                assert "video" in result
            finally:
                if os.path.exists(_tmp_video.name):
                    os.unlink(_tmp_video.name)

    @pytest.mark.asyncio
    async def test_stop_warns_on_trace_finalize_failure(self, mock_playwright):
        """stop() should report warnings when trace auto-save fails."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            context.tracing = MagicMock()
            context.tracing.stop = AsyncMock(side_effect=RuntimeError("disk full"))
            context.pages = [page]

            context_key = browser_module._get_context_key(context)
            browser._tracing_state[context_key] = True

            # close() auto-calls inspect_pending_close_artifacts() which
            # pre-allocates a trace path.  When tracing.stop() fails, close()
            # attempts to clean up the pre-allocated file.
            result = await browser.close()

            assert "Browser closed with warnings" in result
            assert "tracing.stop: disk full" in result

    @pytest.mark.asyncio
    async def test_stop_clears_page_scoped_handlers_before_auto_video_finalize(self, mock_playwright):
        """stop() should remove page listeners/caches even when auto-video closes the page first."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            context.pages = [page]
            page.video = MagicMock()
            page.video.path = AsyncMock(return_value="/tmp/auto_listener_video.webm")

            context_key = browser_module._get_context_key(context)
            browser._video_state[context_key] = True

            page_key = browser_module._get_page_key(page)
            console_handler = MagicMock()
            network_handler = MagicMock()
            dialog_handler = MagicMock()
            browser._console_handlers[page_key] = console_handler
            browser._network_handlers[page_key] = network_handler
            browser._dialog_handlers[page_key] = dialog_handler
            browser._console_messages[page_key] = [{"type": "log", "text": "x"}]
            browser._network_requests[page_key] = [{"url": "https://example.com"}]

            await browser.close()

            page.remove_listener.assert_any_call("console", console_handler)
            page.remove_listener.assert_any_call("request", network_handler)
            page.remove_listener.assert_any_call("dialog", dialog_handler)
            assert page_key not in browser._console_handlers
            assert page_key not in browser._network_handlers
            assert page_key not in browser._dialog_handlers
            assert page_key not in browser._console_messages
            assert page_key not in browser._network_requests

    @pytest.mark.asyncio
    async def test_stop_adds_warning_when_context_close_times_out(self, mock_playwright):
        """stop() should not hang forever if context.close blocks."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            async def _slow_close():
                await asyncio.sleep(1.0)

            assert browser._context is not None
            browser._context.close = AsyncMock(side_effect=_slow_close)
            browser._CONTEXT_CLOSE_TIMEOUT = 0.01

            await browser.close()

            assert any(
                warning.startswith("context.close: timeout after")
                for warning in browser._last_shutdown_errors
            )

    @pytest.mark.asyncio
    async def test_ensure_started_recovers_from_inconsistent_state(self, mock_playwright, mock_context, mock_page):
        """_ensure_started() resets cleanly when _playwright is set but _context is None."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            # Simulate inconsistent state: playwright alive, context lost
            browser._context = None

            # _ensure_started should detect the inconsistency, close, and restart
            await browser._ensure_started()

            assert browser._playwright is not None
            assert browser._context is not None


class TestBrowserNavigation:
    """Tests for Browser navigation methods."""

    @pytest.mark.asyncio
    async def test_navigate_to(self, mock_playwright, mock_page):
        """Test navigate_to method."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.navigate_to("https://example.com")

            mock_page.goto.assert_called_once()
            call_args = mock_page.goto.call_args
            assert call_args[0][0] == "https://example.com"

    @pytest.mark.asyncio
    async def test_navigate_to_clears_snapshot_cache(self, mock_playwright, mock_page):
        """Test that navigation clears snapshot cache."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)

            # Set some cache
            browser._last_snapshot = MagicMock()
            browser._last_snapshot_url = "https://old.com"

            await browser.navigate_to("https://example.com")

            assert browser._last_snapshot is None
            assert browser._last_snapshot_url is None

    @pytest.mark.asyncio
    async def test_navigate_to_empty_url_raises_invalid_input(self):
        browser = Browser(stealth=False)

        with pytest.raises(InvalidInputError) as exc_info:
            await browser.navigate_to("   ")
        assert exc_info.value.code == "URL_EMPTY"

    @pytest.mark.asyncio
    async def test_navigate_to_wraps_playwright_errors(self, mock_playwright, mock_page):
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_page.goto = AsyncMock(side_effect=RuntimeError("boom"))

            browser = Browser(stealth=False)

            with pytest.raises(OperationError):
                await browser.navigate_to("https://example.com")


class TestBrowserSnapshot:
    """Tests for Browser snapshot methods."""

    @pytest.mark.asyncio
    async def test_get_snapshot_without_page_raises_state_error(self):
        browser = Browser(stealth=False)

        with pytest.raises(StateError) as exc_info:
            await browser.get_snapshot()
        assert exc_info.value.code == "NO_ACTIVE_PAGE"

    @pytest.mark.asyncio
    async def test_navigate_to_auto_starts_browser(self, mock_playwright, mock_page):
        """navigate_to lazily starts the browser without an explicit _start() call."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            assert browser._playwright is None

            await browser.navigate_to("https://example.com")

            assert browser._playwright is not None
            mock_page.goto.assert_called_once()


class TestBrowserPageManagement:
    """Tests for Browser page management methods."""


    @pytest.mark.asyncio
    async def test_get_pages(self, mock_playwright, mock_context, mock_page):
        """Test getting all pages."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            pages = browser.get_pages()

            assert len(pages) == 1
            assert pages[0] == mock_page

    @pytest.mark.asyncio
    async def test_get_current_page_url(self, mock_playwright, mock_page):
        """Test getting current page URL."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            url = browser.get_current_page_url()

            assert url == "https://example.com"

    @pytest.mark.asyncio
    async def test_get_current_page_title(self, mock_playwright, mock_page):
        """Test getting current page title."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            title = await browser.get_current_page_title()

            assert title == "Example Page"

    @pytest.mark.asyncio
    async def test_new_tab_raises_when_browser_not_started(self):
        """new_tab() raises StateError(BROWSER_NOT_STARTED) when called before navigate_to()."""
        from bridgic.browser.errors import StateError

        browser = Browser(stealth=False)
        assert browser._playwright is None

        with pytest.raises(StateError) as exc_info:
            await browser.new_tab()

        assert exc_info.value.code == "BROWSER_NOT_STARTED"


class TestBrowserRefResolution:
    """Tests for ref -> locator resolution behavior."""

    @pytest.mark.asyncio
    async def test_get_element_by_ref_prefers_only_visible_match(self):
        """When multiple matches exist, pick the unique visible candidate."""
        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._last_snapshot = MagicMock(
            refs={"e7": SimpleNamespace(role="generic", name=None, nth=None, playwright_ref=None, frame_path=None)}
        )
        browser._snapshot_generator = MagicMock()

        locator = MagicMock()
        locator.count = AsyncMock(return_value=2)
        locator.first = MagicMock()

        hidden_match = MagicMock()
        hidden_match.is_visible = AsyncMock(return_value=False)
        visible_match = MagicMock()
        visible_match.is_visible = AsyncMock(return_value=True)

        locator.nth.side_effect = [hidden_match, visible_match]
        browser._snapshot_generator.get_locator_from_ref_async.return_value = locator

        result = await browser.get_element_by_ref("e7")

        assert result is visible_match

    @pytest.mark.asyncio
    async def test_get_element_by_ref_falls_back_to_first_when_no_visible_match(self):
        """When ambiguity remains, fall back to the first locator match."""
        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._last_snapshot = MagicMock(
            refs={"e7": SimpleNamespace(role="generic", name=None, nth=None, playwright_ref=None, frame_path=None)}
        )
        browser._snapshot_generator = MagicMock()

        locator = MagicMock()
        locator.count = AsyncMock(return_value=2)
        fallback_first = MagicMock()
        locator.first = fallback_first

        match_1 = MagicMock()
        match_1.is_visible = AsyncMock(return_value=False)
        match_2 = MagicMock()
        match_2.is_visible = AsyncMock(return_value=False)

        locator.nth.side_effect = [match_1, match_2]
        browser._snapshot_generator.get_locator_from_ref_async.return_value = locator

        result = await browser.get_element_by_ref("e7")

        assert result is fallback_first

    @pytest.mark.asyncio
    async def test_get_element_by_ref_recovers_with_role_name_when_ambiguous(self):
        """Prefer role+name re-resolution before visibility-based fallback."""
        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        browser._last_snapshot = MagicMock(
            refs={"e7": SimpleNamespace(role="button", name="Automatic detection", nth=None, playwright_ref=None, frame_path=None)}
        )

        ambiguous_locator = MagicMock()
        ambiguous_locator.count = AsyncMock(return_value=2)
        browser._snapshot_generator.get_locator_from_ref_async.return_value = ambiguous_locator

        role_name_locator = MagicMock()
        role_name_locator.count = AsyncMock(return_value=1)
        browser._page.get_by_role.return_value = role_name_locator

        result = await browser.get_element_by_ref("e7")

        assert result is role_name_locator
        browser._page.get_by_role.assert_called_once_with(
            "button",
            name="Automatic detection",
            exact=True,
        )

    @pytest.mark.asyncio
    async def test_get_element_by_ref_structural_role_skips_role_name_recovery(self):
        """Structural noise roles should not use role+name recovery path."""
        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        browser._last_snapshot = MagicMock(
            refs={"e7": SimpleNamespace(role="generic", name="Automatic detection", nth=None, playwright_ref=None, frame_path=None)}
        )

        ambiguous_locator = MagicMock()
        ambiguous_locator.count = AsyncMock(return_value=2)
        first_visible = MagicMock()
        first_visible.is_visible = AsyncMock(return_value=True)
        second_visible = MagicMock()
        second_visible.is_visible = AsyncMock(return_value=True)
        ambiguous_locator.nth.side_effect = [first_visible, second_visible]
        browser._snapshot_generator.get_locator_from_ref_async.return_value = ambiguous_locator

        result = await browser.get_element_by_ref("e7")

        assert result is first_visible
        browser._page.get_by_role.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_element_by_ref_prefers_snapshot_nth_when_available(self):
        """Use snapshot nth to keep deterministic selection for ambiguous refs."""
        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        browser._last_snapshot = MagicMock(
            refs={"e7": SimpleNamespace(role="button", name=None, nth=1, playwright_ref=None, frame_path=None)}
        )

        ambiguous_locator = MagicMock()
        ambiguous_locator.count = AsyncMock(return_value=3)
        nth_locator = MagicMock()
        ambiguous_locator.nth.return_value = nth_locator
        browser._snapshot_generator.get_locator_from_ref_async.return_value = ambiguous_locator

        result = await browser.get_element_by_ref("e7")

        assert result is nth_locator
        ambiguous_locator.nth.assert_called_once_with(1)


class TestBrowserChildFallback:
    """Tests for count=0 child-ref fallback behavior."""

    @pytest.mark.asyncio
    async def test_fallback_picks_best_child_when_container_fails(self):
        """When unnamed container ref fails (count=0), fall back to best child."""
        from bridgic.browser.session._snapshot import RefData

        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        browser._last_snapshot = MagicMock(refs={
            "e6": RefData(
                selector="get_by_role('generic')",
                role="generic",
                name=None,
                nth=0,
                text_content=None,
                parent_ref=None,
            ),
            "e7": RefData(
                selector='get_by_text("Automatic detection", exact=True)',
                role="generic",
                name="Automatic detection",
                nth=None,
                text_content=None,
                parent_ref="e6",
            ),
            "e8": RefData(
                selector="get_by_role('generic')",
                role="generic",
                name=None,
                nth=1,
                text_content=None,
                parent_ref="e6",
            ),
        })

        failed_locator = MagicMock()
        failed_locator.count = AsyncMock(return_value=0)

        child_locator = MagicMock()
        child_locator.count = AsyncMock(return_value=1)

        def mock_get_locator(page, ref_arg, refs):
            if ref_arg == "e6":
                return failed_locator
            if ref_arg == "e7":
                return child_locator
            return None

        browser._snapshot_generator.get_locator_from_ref_async.side_effect = mock_get_locator

        result = await browser.get_element_by_ref("e6")

        assert result is child_locator

    @pytest.mark.asyncio
    async def test_no_fallback_for_named_container(self):
        """Named containers should NOT trigger child fallback."""
        from bridgic.browser.session._snapshot import RefData

        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        browser._last_snapshot = MagicMock(refs={
            "e6": RefData(
                selector='get_by_text("Menu", exact=True)',
                role="generic",
                name="Menu",
                nth=None,
                text_content=None,
                parent_ref=None,
            ),
        })

        failed_locator = MagicMock()
        failed_locator.count = AsyncMock(return_value=0)
        browser._snapshot_generator.get_locator_from_ref_async.return_value = failed_locator

        result = await browser.get_element_by_ref("e6")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_fallback_for_non_noise_role(self):
        """Non-noise roles (e.g. button) should NOT trigger child fallback."""
        from bridgic.browser.session._snapshot import RefData

        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        browser._last_snapshot = MagicMock(refs={
            "e1": RefData(
                selector="get_by_role('button')",
                role="button",
                name=None,
                nth=None,
                text_content=None,
                parent_ref=None,
            ),
        })

        failed_locator = MagicMock()
        failed_locator.count = AsyncMock(return_value=0)
        browser._snapshot_generator.get_locator_from_ref_async.return_value = failed_locator

        result = await browser.get_element_by_ref("e1")

        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_returns_none_when_no_scorable_children(self):
        """Fallback returns None when all children are unnamed noise roles."""
        from bridgic.browser.session._snapshot import RefData

        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        browser._last_snapshot = MagicMock(refs={
            "e6": RefData(
                selector="get_by_role('generic')",
                role="generic",
                name=None,
                nth=0,
                text_content=None,
                parent_ref=None,
            ),
            "e8": RefData(
                selector="get_by_role('generic')",
                role="generic",
                name=None,
                nth=1,
                text_content=None,
                parent_ref="e6",
            ),
        })

        failed_locator = MagicMock()
        failed_locator.count = AsyncMock(return_value=0)
        browser._snapshot_generator.get_locator_from_ref_async.return_value = failed_locator

        result = await browser.get_element_by_ref("e6")

        assert result is None


class TestGetElementByRefAriaRef:
    """Tests for the aria-ref O(1) fast-path in get_element_by_ref."""

    def _make_browser_with_ref(self, playwright_ref, frame_path=None):
        from bridgic.browser.session._snapshot import RefData
        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._snapshot_generator = MagicMock()
        ref_data = RefData(
            selector="get_by_role('button')",
            role="button",
            name="Submit",
            nth=None,
            playwright_ref=playwright_ref,
            frame_path=frame_path,
        )
        browser._last_snapshot = MagicMock(refs={"myref": ref_data})
        return browser

    @pytest.mark.asyncio
    async def test_aria_ref_fast_path_hit(self):
        """When aria-ref count=1, return immediately without calling CSS locator."""
        browser = self._make_browser_with_ref("e369")

        ar_locator = MagicMock()
        ar_locator.count = AsyncMock(return_value=1)
        browser._page.locator.return_value = ar_locator

        result = await browser.get_element_by_ref("myref")

        assert result is ar_locator
        browser._page.locator.assert_called_once_with("aria-ref=e369")
        browser._snapshot_generator.get_locator_from_ref_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_aria_ref_falls_through_on_stale(self):
        """When aria-ref count=0 (stale), fall through to CSS locator."""
        browser = self._make_browser_with_ref("e369")

        ar_locator = MagicMock()
        ar_locator.count = AsyncMock(return_value=0)
        browser._page.locator.return_value = ar_locator

        css_locator = MagicMock()
        css_locator.count = AsyncMock(return_value=1)
        browser._snapshot_generator.get_locator_from_ref_async.return_value = css_locator

        result = await browser.get_element_by_ref("myref")

        assert result is css_locator
        browser._snapshot_generator.get_locator_from_ref_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_aria_ref_falls_through_on_exception(self):
        """When aria-ref raises, fall through silently — no exception propagates."""
        browser = self._make_browser_with_ref("e369")

        browser._page.locator.side_effect = Exception("engine not available")

        css_locator = MagicMock()
        css_locator.count = AsyncMock(return_value=1)
        browser._snapshot_generator.get_locator_from_ref_async.return_value = css_locator

        result = await browser.get_element_by_ref("myref")

        assert result is css_locator

    @pytest.mark.asyncio
    async def test_aria_ref_skipped_when_playwright_ref_none(self):
        """When playwright_ref=None, skip fast-path entirely."""
        browser = self._make_browser_with_ref(playwright_ref=None)

        css_locator = MagicMock()
        css_locator.count = AsyncMock(return_value=1)
        browser._snapshot_generator.get_locator_from_ref_async.return_value = css_locator

        result = await browser.get_element_by_ref("myref")

        assert result is css_locator
        # page.locator should NOT have been called with aria-ref=...
        for call in browser._page.locator.call_args_list:
            assert "aria-ref=" not in str(call)

    @pytest.mark.asyncio
    async def test_aria_ref_iframe_uses_frame_locator_chain(self):
        """For iframe elements, aria-ref is scoped via frame_locator chain.

        Each frame stores its own _lastAriaSnapshotForQuery keyed by the full prefixed ref
        (e.g. L1 stores "f1e99" → element).  Scoping the locator to the correct frame
        ensures locator.evaluate() runs in the element's own frame context, not main frame.
        This is critical for the covered-element check: without scoping, evaluate() would
        run in the main frame where window.parent === window and the check mis-fires.
        """
        # Iframe element: playwright_ref has "f1" prefix, frame_path=[0]
        browser = self._make_browser_with_ref("f1e99", frame_path=[0])

        # Set up the frame_locator chain mock
        frame_locator_mock = MagicMock()
        nth_mock = MagicMock()
        ar_locator = MagicMock()
        ar_locator.count = AsyncMock(return_value=1)

        browser._page.frame_locator.return_value = frame_locator_mock
        frame_locator_mock.nth.return_value = nth_mock
        nth_mock.locator.return_value = ar_locator

        result = await browser.get_element_by_ref("myref")

        assert result is ar_locator
        # page.frame_locator("iframe") called once for the single frame_path level
        browser._page.frame_locator.assert_called_once_with("iframe")
        frame_locator_mock.nth.assert_called_once_with(0)
        nth_mock.locator.assert_called_once_with("aria-ref=f1e99")


class TestProfileLockHelpers:
    """Tests for SingletonLock detection and cleanup helpers."""

    def test_is_profile_lock_error_matches_known_patterns(self):
        assert Browser._is_profile_lock_error(
            Exception("user data directory is already in use")
        )
        assert Browser._is_profile_lock_error(
            Exception("Failed to create a ProcessSingleton for the profile")
        )
        assert Browser._is_profile_lock_error(
            Exception("Error: profile is already in use by another Chrome process")
        )
        assert Browser._is_profile_lock_error(
            Exception("Something about SingletonLock failed")
        )

    def test_is_profile_lock_error_rejects_unrelated(self):
        assert not Browser._is_profile_lock_error(Exception("timeout"))
        assert not Browser._is_profile_lock_error(Exception("connection refused"))
        assert not Browser._is_profile_lock_error(Exception(""))

    def test_try_clear_stale_lock_removes_dead_pid(self, tmp_path):
        """Stale lock (dead PID) should be removed."""
        lock = tmp_path / "SingletonLock"
        # Use a PID that almost certainly doesn't exist
        lock.symlink_to("localhost-999999999")
        Browser._try_clear_stale_lock(str(tmp_path))
        assert not lock.exists() and not lock.is_symlink()

    def test_try_clear_stale_lock_keeps_alive_pid(self, tmp_path):
        """Lock held by a living process should NOT be removed."""
        lock = tmp_path / "SingletonLock"
        lock.symlink_to(f"localhost-{os.getpid()}")  # our own PID — definitely alive
        Browser._try_clear_stale_lock(str(tmp_path))
        assert lock.is_symlink()  # still there

    def test_try_clear_stale_lock_no_lock_file(self, tmp_path):
        """No lock file — should return silently."""
        Browser._try_clear_stale_lock(str(tmp_path))  # no error

    def test_try_clear_stale_lock_unparseable_target(self, tmp_path):
        """Lock with unparseable target — should return silently."""
        lock = tmp_path / "SingletonLock"
        lock.symlink_to("garbage")
        Browser._try_clear_stale_lock(str(tmp_path))  # no error
        assert lock.is_symlink()  # left untouched
