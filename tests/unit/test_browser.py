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
        """In headed mode the main stealth init script is skipped, but the
        anti-devtools-detector script is still injected (safe for headed)."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(headless=False)  # headed mode, stealth enabled by default
            await browser._start()

            ctx = mock_playwright.chromium.launch_persistent_context.return_value
            # Only the anti-devtools-detector script should be injected (1 call),
            # NOT the main stealth init script that patches navigator/window.chrome.
            assert ctx.add_init_script.call_count == 1
            # Verify it's the anti-devtools-detector script, not the main one
            script_arg = ctx.add_init_script.call_args_list[0][0][0]
            assert "console.table" in script_arg
            assert "navigator.webdriver" not in script_arg

    @pytest.mark.asyncio
    async def test_headless_mode_injects_init_script(self, mock_playwright):
        """In headless mode both the main stealth script and the
        anti-devtools-detector script are injected."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(headless=True)  # headless mode, stealth enabled by default
            await browser._start()

            ctx = mock_playwright.chromium.launch_persistent_context.return_value
            # Both the main stealth script and anti-devtools-detector script
            assert ctx.add_init_script.call_count == 2

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
            context.remove_listener = MagicMock()

            # Create a mock CDP screencast recorder
            import tempfile
            _tmp_video_fd, _tmp_video_path = tempfile.mkstemp(suffix=".webm")
            os.close(_tmp_video_fd)
            mock_recorder = MagicMock()
            mock_recorder.prepare_stop = AsyncMock()
            mock_recorder.finalize = AsyncMock(return_value=_tmp_video_path)
            mock_recorder.stop = AsyncMock(return_value=_tmp_video_path)
            browser._video_recorder = mock_recorder
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }

            context_key = browser_module._get_context_key(context)
            browser._tracing_state[context_key] = True
            browser._video_state[context_key] = True

            await browser.close()

            # Trace should be saved into the auto-created session dir
            trace_call = context.tracing.stop.call_args
            trace_path = trace_call.kwargs.get("path") or trace_call.args[0]
            assert "close-" in trace_path
            assert trace_path.endswith("trace.zip")

            mock_recorder.prepare_stop.assert_awaited_once()
            mock_recorder.finalize.assert_awaited_once()
            assert browser._last_shutdown_artifacts["trace"] == [os.path.abspath(trace_path)]
            assert len(browser._last_shutdown_artifacts["video"]) == 1
            video_path = browser._last_shutdown_artifacts["video"][0]
            assert "video" in video_path
            assert context_key not in browser._tracing_state
            assert context_key not in browser._video_state

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
            context.remove_listener = MagicMock()

            # Create a mock CDP screencast recorder
            import tempfile
            _tmp_video_fd, _tmp_video_path = tempfile.mkstemp(suffix=".webm")
            os.close(_tmp_video_fd)
            mock_recorder = MagicMock()
            mock_recorder.prepare_stop = AsyncMock()
            mock_recorder.finalize = AsyncMock(return_value=_tmp_video_path)
            mock_recorder.stop = AsyncMock(return_value=_tmp_video_path)
            browser._video_recorder = mock_recorder
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }

            context_key = browser_module._get_context_key(context)
            browser._tracing_state[context_key] = True
            browser._video_state[context_key] = True

            result = await browser.close()

            assert "Browser closed successfully" in result
            assert "trace.zip" in result
            assert "video" in result

    @pytest.mark.asyncio
    async def test_close_auto_stops_cdp_recorder(self, mock_playwright):
        """close() should auto-stop the CDP screencast recorder and save the video."""
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
            context.remove_listener = MagicMock()

            # Create a mock VideoRecorder
            import tempfile
            _tmp_fd, _tmp_path = tempfile.mkstemp(suffix=".webm")
            os.close(_tmp_fd)
            mock_recorder = MagicMock()
            mock_recorder.prepare_stop = AsyncMock()
            mock_recorder.finalize = AsyncMock(return_value=_tmp_path)
            mock_recorder.stop = AsyncMock(return_value=_tmp_path)
            browser._video_recorder = mock_recorder
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }

            context_key = browser_module._get_context_key(context)
            browser._video_state[context_key] = True

            await browser.close()

            mock_recorder.prepare_stop.assert_awaited_once()
            mock_recorder.finalize.assert_awaited_once()
            assert browser._video_recorder is None
            assert browser._video_session is None
            assert len(browser._last_shutdown_artifacts["video"]) == 1
            assert context_key not in browser._video_state

    @pytest.mark.asyncio
    async def test_close_page_switches_recorder_to_remaining_tab(self, mock_playwright):
        """_close_page() should switch the recorder to a remaining page, not stop it."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            # Mock a second page so _close_page has a tab to switch to
            second_page = MagicMock()
            second_page.url = "https://example.com/2"
            second_page.title = AsyncMock(return_value="Page 2")
            second_page.close = AsyncMock()
            second_page.is_closed = MagicMock(return_value=False)
            context.pages = [page, second_page]

            # Set up mock recorder recording the current page
            mock_recorder = MagicMock()
            mock_recorder.is_stopped = False
            mock_recorder.current_page = page
            mock_recorder.switch_page = AsyncMock()
            browser._video_recorder = mock_recorder
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }

            context_key = browser_module._get_context_key(context)
            browser._video_state[context_key] = True

            # Close the page that is being recorded
            success, msg = await browser._close_page(page)
            assert success

            # Recorder should have been switched to the remaining page, not stopped
            mock_recorder.switch_page.assert_awaited_once_with(second_page)
            # Recorder is still active
            assert browser._video_recorder is mock_recorder
            assert browser._video_session is not None

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

    def test_last_close_properties_default_empty_before_close(self):
        """Before any close() runs, both properties return empty defaults."""
        browser = Browser(stealth=False)
        assert browser.last_close_artifacts == {"trace": [], "video": []}
        assert browser.last_close_errors == []

    @pytest.mark.asyncio
    async def test_last_close_properties_after_clean_close(self, mock_playwright):
        """A clean close() with no tracing/video leaves both properties empty."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()
            await browser.close()

            assert browser.last_close_artifacts == {"trace": [], "video": []}
            assert browser.last_close_errors == []

    @pytest.mark.asyncio
    async def test_last_close_properties_populated_after_trace_video_close(self, mock_playwright):
        """close() with active trace+video populates the properties, and the
        returned objects are independent copies (mutating them does not affect
        the browser's internal state)."""
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
            context.remove_listener = MagicMock()

            import tempfile
            _tmp_video_fd, _tmp_video_path = tempfile.mkstemp(suffix=".webm")
            os.close(_tmp_video_fd)
            mock_recorder = MagicMock()
            mock_recorder.prepare_stop = AsyncMock()
            mock_recorder.finalize = AsyncMock(return_value=_tmp_video_path)
            mock_recorder.stop = AsyncMock(return_value=_tmp_video_path)
            browser._video_recorder = mock_recorder
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }

            context_key = browser_module._get_context_key(context)
            browser._tracing_state[context_key] = True
            browser._video_state[context_key] = True

            await browser.close()

            artifacts = browser.last_close_artifacts
            assert len(artifacts["trace"]) == 1
            assert artifacts["trace"][0].endswith("trace.zip")
            assert len(artifacts["video"]) == 1
            assert "video" in artifacts["video"][0]
            assert browser.last_close_errors == []

            # Defensive copy: mutating the returned dict and lists must
            # not affect the browser's stored state.
            artifacts["trace"].clear()
            artifacts["video"].clear()
            artifacts["trace"].append("hacked")
            errors = browser.last_close_errors
            errors.append("hacked")

            re_read = browser.last_close_artifacts
            assert len(re_read["trace"]) == 1
            assert re_read["trace"][0].endswith("trace.zip")
            assert len(re_read["video"]) == 1
            assert browser.last_close_errors == []

    @pytest.mark.asyncio
    async def test_inspect_close_artifacts_skips_dir_when_nothing_active(self, mock_playwright):
        """Regression: SDK close() with no tracing/video must not leak an
        empty close-session directory under BRIDGIC_TMP_DIR.

        Previously inspect_pending_close_artifacts() always created a
        ``close-<ts>-<rand>/`` directory, so every plain ``Browser.close()``
        accumulated an empty directory for the SDK user. The fix returns an
        empty ``session_dir`` when there is nothing to write, and close()
        propagates that — no directory should be created.
        """
        from bridgic.browser._constants import BRIDGIC_TMP_DIR

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            # Snapshot the existing close-* directories so we can verify
            # nothing new was created (the directory may already exist
            # from prior tests/sessions in the same temp root).
            tmp_root = Path(str(BRIDGIC_TMP_DIR))
            before = set()
            if tmp_root.exists():
                before = {p.name for p in tmp_root.iterdir() if p.name.startswith("close-")}

            artifacts = browser.inspect_pending_close_artifacts()
            assert artifacts["session_dir"] == ""
            assert artifacts["trace"] == []
            assert artifacts["video"] == []
            assert browser._close_session_dir is None

            await browser.close()

            after = set()
            if tmp_root.exists():
                after = {p.name for p in tmp_root.iterdir() if p.name.startswith("close-")}
            new_dirs = after - before
            assert new_dirs == set(), (
                f"close() leaked empty session dirs: {new_dirs}"
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

    @pytest.mark.asyncio
    async def test_launch_mode_close_records_page_close_failure(self, mock_playwright, mock_page):
        """Launch / persistent mode: page.close() failures must be recorded in
        _last_shutdown_errors, mirroring the borrowed-CDP branch (symmetry).

        Regression guard for H1: the non-borrowed branch in Browser.close() used
        to silently swallow regular Exception results from asyncio.gather().
        """
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            # Simulate page.close() raising a regular Exception (not BaseException).
            mock_page.close = AsyncMock(side_effect=RuntimeError("page-boom"))

            await browser.close()

            assert any("page-boom" in e for e in browser._last_shutdown_errors), (
                f"expected 'page-boom' in errors, got: {browser._last_shutdown_errors}"
            )
            # Downstream cleanup must still complete.
            assert browser._page is None
            assert browser._context is None


class TestSingleVideoRecorderClose:
    """Tests verifying single-stream video recorder lifecycle during close().

    close() uses a two-phase shutdown:
      Phase 1: prepare_stop() the single recorder (fast, while Chrome alive)
      Phase 2: finalize() the single recorder (slow, after Chrome exits)
    """

    @pytest.mark.asyncio
    async def test_close_finalize_success(self, mock_playwright):
        """close() must finalize the single recorder and move the video file."""
        import tempfile as _tempfile
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            assert context is not None
            context.remove_listener = MagicMock()

            _fd, _temp_path = _tempfile.mkstemp(suffix=".webm")
            os.close(_fd)

            page = MagicMock()
            page.close = AsyncMock()
            rec = MagicMock()
            rec.prepare_stop = AsyncMock()
            rec.finalize = AsyncMock(return_value=_temp_path)

            context.pages = [page]
            browser._video_recorder = rec
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }
            context_key = browser_module._get_context_key(context)
            browser._video_state[context_key] = True

            await browser.close()

            rec.prepare_stop.assert_awaited_once()
            rec.finalize.assert_awaited_once()
            assert len(browser._last_shutdown_artifacts["video"]) == 1

    @pytest.mark.asyncio
    async def test_close_collects_finalize_timeout_error(self, mock_playwright):
        """close() must collect the timeout error from finalize, not raise."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            assert context is not None
            context.remove_listener = MagicMock()

            async def timeout_finalize():
                raise asyncio.TimeoutError()

            page = MagicMock()
            page.close = AsyncMock()
            rec = MagicMock()
            rec.prepare_stop = AsyncMock()
            rec.finalize = timeout_finalize

            context.pages = [page]
            browser._video_recorder = rec
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }
            context_key = browser_module._get_context_key(context)
            browser._video_state[context_key] = True

            await browser.close()  # must not raise

            timeout_errors = [
                e for e in browser._last_shutdown_errors
                if "video_recorder.finalize: timeout" in e
            ]
            assert len(timeout_errors) == 1

    @pytest.mark.asyncio
    async def test_close_re_raises_cancelled_error_from_recorder(self, mock_playwright):
        """CancelledError from finalize is stored and re-raised after cleanup."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser._start()

            context = browser._context
            assert context is not None
            context.remove_listener = MagicMock()

            async def cancelling_finalize() -> str:
                raise asyncio.CancelledError("simulated task cancellation")

            page = MagicMock()
            page.close = AsyncMock()
            rec = MagicMock()
            rec.prepare_stop = AsyncMock()
            rec.finalize = cancelling_finalize

            context.pages = [page]
            browser._video_recorder = rec
            browser._video_session = {
                "width": 800, "height": 600, "context": context,
                "page_listener": lambda *_: None,
            }
            context_key = browser_module._get_context_key(context)
            browser._video_state[context_key] = True

            with pytest.raises(asyncio.CancelledError):
                await browser.close()

            assert any(
                "video_recorder.finalize" in e for e in browser._last_shutdown_errors
            )


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


# ─────────────────────────────────────────────────────────────────────────────
# Browser._start() CDP mode
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserStartCdp:
    """Tests for Browser._start() in CDP connect mode (connect_over_cdp)."""

    def _make_cdp_mocks(self, pages=None, contexts_count=1):
        """Return (mock_pw, mock_cdp_browser, mock_ctx, mock_page) tuple."""
        mock_pg = MagicMock()
        mock_pg.bring_to_front = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.add_init_script = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_pg)
        mock_ctx.pages = pages if pages is not None else [mock_pg]

        mock_cdp_browser = MagicMock()
        mock_cdp_browser.contexts = [mock_ctx] * contexts_count
        mock_cdp_browser.new_context = AsyncMock(return_value=mock_ctx)

        mock_pw = MagicMock()
        mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_cdp_browser)
        mock_pw.stop = AsyncMock()

        return mock_pw, mock_cdp_browser, mock_ctx, mock_pg

    @pytest.mark.asyncio
    async def test_cdp_url_calls_connect_over_cdp(self):
        mock_pw, mock_cdp_brow, mock_ctx, _ = self._make_cdp_mocks()
        cdp_url = "ws://localhost:9222/devtools/browser/abc"
        browser = Browser(cdp_url=cdp_url, stealth=False)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        mock_pw.chromium.connect_over_cdp.assert_awaited_once_with(cdp_url)
        mock_pw.chromium.launch.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_contexts_reused(self):
        mock_pw, mock_cdp_brow, mock_ctx, _ = self._make_cdp_mocks()
        browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc", stealth=False)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        assert browser._context is mock_ctx
        mock_cdp_brow.new_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_contexts_calls_new_context(self):
        mock_pw, mock_cdp_brow, mock_ctx, _ = self._make_cdp_mocks(contexts_count=0)
        mock_cdp_brow.contexts = []
        mock_cdp_brow.new_context = AsyncMock(return_value=mock_ctx)
        browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc", stealth=False)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        mock_cdp_brow.new_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stealth_true_headless_calls_add_init_script(self):
        mock_pw, _, mock_ctx, _ = self._make_cdp_mocks()
        browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc", stealth=True, headless=True)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        mock_ctx.add_init_script.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stealth_true_headed_skips_init_script(self):
        """Headed CDP mode must skip init script (same as non-CDP headed mode)."""
        mock_pw, _, mock_ctx, _ = self._make_cdp_mocks()
        browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc", stealth=True, headless=False)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        mock_ctx.add_init_script.assert_not_called()

    @pytest.mark.asyncio
    async def test_stealth_false_no_add_init_script(self):
        mock_pw, _, mock_ctx, _ = self._make_cdp_mocks()
        browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc", stealth=False)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        mock_ctx.add_init_script.assert_not_called()

    @pytest.mark.asyncio
    async def test_cdp_always_creates_new_page_in_borrowed_context(self):
        """CDP mode must NEVER reuse a borrowed user tab. Always create a new
        bridgic-owned page so the user's existing tabs stay untouched."""
        page1, page2 = MagicMock(), MagicMock()
        page1.bring_to_front = AsyncMock()
        page2.bring_to_front = AsyncMock()
        # mock_pg is the page returned by mock_ctx.new_page() — this is the
        # page bridgic should adopt as self._page, NOT page2.
        mock_pw, _, mock_ctx, mock_pg = self._make_cdp_mocks(pages=[page1, page2])
        browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc", stealth=False)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        mock_ctx.new_page.assert_awaited_once()
        assert browser._page is mock_pg
        assert browser._page is not page2  # CRITICAL: never hijack user's tab

    @pytest.mark.asyncio
    async def test_cdp_new_page_called_unconditionally(self):
        """Even when the borrowed context has no pages, _start() still calls
        new_page() to create a tab for bridgic to drive."""
        mock_pw, _, mock_ctx, mock_pg = self._make_cdp_mocks(pages=[])
        browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc", stealth=False)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        mock_ctx.new_page.assert_awaited_once()
        assert browser._page is mock_pg

    @pytest.mark.asyncio
    async def test_download_manager_attached(self, tmp_path):
        mock_pw, _, mock_ctx, _ = self._make_cdp_mocks()
        downloads_dir = tmp_path / "dl"
        downloads_dir.mkdir()
        browser = Browser(
            cdp_url="ws://localhost:9222/devtools/browser/abc",
            stealth=False,
            downloads_path=str(downloads_dir),
        )
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            with patch.object(browser._download_manager, "attach_to_context") as mock_attach:
                await browser._start()
        mock_attach.assert_called_once_with(mock_ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Browser.use_persistent_context — CDP mode
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserUsePersistentContextCdp:
    """Tests for use_persistent_context property in CDP vs normal mode."""

    def test_cdp_url_returns_false(self):
        browser = Browser(
            cdp_url="ws://localhost:9222/devtools/browser/abc",
            user_data_dir="/tmp/profile",
        )
        assert browser.use_persistent_context is False

    def test_no_cdp_with_user_data_dir_returns_true(self):
        browser = Browser(user_data_dir="/tmp/profile")
        assert browser.use_persistent_context is True


# ─────────────────────────────────────────────────────────────────────────────
# Browser.close() — CDP mode
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserCloseCdp:
    """Tests for Browser.close() in CDP mode — must disconnect without
    destroying pages/context in the remote browser."""

    def _make_cdp_mocks(self, pages=None, contexts_count=1):
        """Return (mock_pw, mock_cdp_browser, mock_ctx, mock_page) tuple.

        ``mock_page`` is the page returned by ``mock_ctx.new_page()`` — i.e. the
        bridgic-owned page in CDP mode."""
        mock_pg = MagicMock()
        mock_pg.bring_to_front = AsyncMock()
        mock_pg.close = AsyncMock()
        mock_pg.goto = AsyncMock()
        mock_pg.video = None
        mock_pg.is_closed = MagicMock(return_value=False)

        mock_ctx = MagicMock()
        mock_ctx.add_init_script = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_pg)
        mock_ctx.pages = pages if pages is not None else [mock_pg]
        mock_ctx.close = AsyncMock()
        mock_ctx.tracing = MagicMock()
        mock_ctx.tracing.stop = AsyncMock()

        mock_cdp_browser = MagicMock()
        mock_cdp_browser.contexts = [mock_ctx] * contexts_count
        mock_cdp_browser.new_context = AsyncMock(return_value=mock_ctx)
        mock_cdp_browser.close = AsyncMock()

        mock_pw = MagicMock()
        mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_cdp_browser)
        mock_pw.stop = AsyncMock()

        return mock_pw, mock_cdp_browser, mock_ctx, mock_pg

    async def _start_cdp_browser(self, mock_pw, *, cdp_url="ws://localhost:9222/devtools/browser/abc", **kwargs):
        """Create and start a Browser in CDP mode."""
        browser = Browser(cdp_url=cdp_url, stealth=False, **kwargs)
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)
            await browser._start()
        return browser

    @pytest.mark.asyncio
    async def test_cdp_close_does_not_close_borrowed_pages(self):
        """close() in CDP borrowed context must NOT close any pages — bridgic
        just disconnects and leaves the remote browser intact."""
        borrowed_pg = MagicMock()
        borrowed_pg.close = AsyncMock()
        borrowed_pg.goto = AsyncMock()
        borrowed_pg.bring_to_front = AsyncMock()
        borrowed_pg.video = None
        borrowed_pg.is_closed = MagicMock(return_value=False)

        mock_pw, _, mock_ctx, bridgic_pg = self._make_cdp_mocks(pages=[borrowed_pg])
        bridgic_pg.is_closed = MagicMock(return_value=False)
        browser = await self._start_cdp_browser(mock_pw)

        assert browser._page is bridgic_pg

        await browser.close()

        # No page is closed — bridgic only disconnects.
        borrowed_pg.close.assert_not_called()
        bridgic_pg.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_cdp_close_does_not_close_borrowed_context(self):
        """close() in CDP mode must NOT call context.close() on borrowed context."""
        mock_pw, _, mock_ctx, _ = self._make_cdp_mocks()
        browser = await self._start_cdp_browser(mock_pw)

        assert browser._cdp_context_owned is False
        await browser.close()

        mock_ctx.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_cdp_close_does_not_close_owned_context(self):
        """close() in CDP mode must NOT call context.close() even when bridgic
        created the context — the remote browser manages its own lifecycle."""
        mock_pw, mock_cdp_browser, mock_ctx, _ = self._make_cdp_mocks(contexts_count=0)
        mock_cdp_browser.contexts = []
        browser = await self._start_cdp_browser(mock_pw)

        assert browser._cdp_context_owned is True
        await browser.close()

        mock_ctx.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_cdp_close_does_not_navigate_about_blank(self):
        """close() in CDP mode must NOT navigate pages to about:blank."""
        mock_pw, _, mock_ctx, mock_pg = self._make_cdp_mocks()
        browser = await self._start_cdp_browser(mock_pw)

        await browser.close()

        mock_pg.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_cdp_close_disconnects_browser(self):
        """close() in CDP mode must call _browser.close() to disconnect."""
        mock_pw, mock_cdp_browser, _, _ = self._make_cdp_mocks()
        browser = await self._start_cdp_browser(mock_pw)

        await browser.close()

        mock_cdp_browser.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cdp_close_stops_playwright(self):
        """close() in CDP mode must stop the Playwright driver."""
        mock_pw, _, _, _ = self._make_cdp_mocks()
        browser = await self._start_cdp_browser(mock_pw)

        await browser.close()

        mock_pw.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cdp_close_clears_internal_references(self):
        """close() in CDP mode must clear all internal references."""
        mock_pw, _, _, _ = self._make_cdp_mocks()
        browser = await self._start_cdp_browser(mock_pw)

        await browser.close()

        assert browser._playwright is None
        assert browser._browser is None
        assert browser._context is None
        assert browser._page is None

    @pytest.mark.asyncio
    async def test_cdp_close_multiple_borrowed_pages_not_closed(self):
        """close() in CDP borrowed mode must not close or navigate any page."""
        page1 = MagicMock()
        page1.close = AsyncMock()
        page1.goto = AsyncMock()
        page1.bring_to_front = AsyncMock()
        page1.video = None
        page1.is_closed = MagicMock(return_value=False)
        page2 = MagicMock()
        page2.close = AsyncMock()
        page2.goto = AsyncMock()
        page2.bring_to_front = AsyncMock()
        page2.video = None
        page2.is_closed = MagicMock(return_value=False)
        mock_pw, _, mock_ctx, bridgic_pg = self._make_cdp_mocks(pages=[page1, page2])
        bridgic_pg.is_closed = MagicMock(return_value=False)
        browser = await self._start_cdp_browser(mock_pw)

        await browser.close()

        # No page is closed or navigated — bridgic only disconnects.
        page1.close.assert_not_called()
        page2.close.assert_not_called()
        page1.goto.assert_not_called()
        page2.goto.assert_not_called()
        bridgic_pg.close.assert_not_called()

    # --- Owned CDP context: still just disconnect, no page/context cleanup ---

    @pytest.mark.asyncio
    async def test_cdp_owned_context_does_not_close_pages(self):
        """Owned CDP context: page.close() is NOT called — bridgic only disconnects."""
        mock_pw, mock_cdp_browser, mock_ctx, mock_pg = self._make_cdp_mocks(contexts_count=0)
        mock_cdp_browser.contexts = []
        browser = await self._start_cdp_browser(mock_pw)

        assert browser._cdp_context_owned is True
        await browser.close()

        mock_pg.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_cdp_owned_context_does_not_close_context(self):
        """Owned CDP context: context.close() is NOT called — bridgic only disconnects."""
        mock_pw, mock_cdp_browser, mock_ctx, mock_pg = self._make_cdp_mocks(contexts_count=0)
        mock_cdp_browser.contexts = []
        browser = await self._start_cdp_browser(mock_pw)

        await browser.close()

        mock_ctx.close.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# find_cdp_url() — system proxy bypass for loopback hosts
# ─────────────────────────────────────────────────────────────────────────────

class TestFindCdpUrlProxyBypass:
    """find_cdp_url(mode="port") must bypass the system HTTP proxy when probing
    loopback hosts (localhost / 127.0.0.1 / ::1) so a misconfigured proxy cannot
    return misleading 502 errors for ports that are simply not listening.

    Remote hosts (cloud browser services, SSH-tunneled CDP, etc.) MUST keep
    proxy support."""

    def _make_fake_response(self, payload: dict):
        """Return an object with a .read() method returning JSON bytes."""
        import json as _json
        fake = MagicMock()
        fake.read = MagicMock(return_value=_json.dumps(payload).encode("utf-8"))
        return fake

    def test_find_cdp_url_localhost_bypasses_system_proxy(self, monkeypatch):
        """Localhost probes must build an opener with empty ProxyHandler({})."""
        import urllib.request
        from bridgic.browser.session import find_cdp_url

        # Set a system proxy that would obviously break the probe if used.
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")

        captured_handlers = []
        real_build_opener = urllib.request.build_opener

        def _spy_build_opener(*handlers):
            captured_handlers.append(handlers)
            opener = MagicMock()
            opener.open = MagicMock(
                return_value=self._make_fake_response(
                    {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}
                )
            )
            return opener

        # Track whether default urlopen was used (it must NOT be).
        urlopen_calls = []
        real_urlopen = urllib.request.urlopen

        def _spy_urlopen(*args, **kwargs):
            urlopen_calls.append((args, kwargs))
            return self._make_fake_response(
                {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}
            )

        monkeypatch.setattr(urllib.request, "build_opener", _spy_build_opener)
        monkeypatch.setattr(urllib.request, "urlopen", _spy_urlopen)

        result = find_cdp_url(mode="port", host="localhost", port=9222)

        assert result == "ws://localhost:9222/devtools/browser/abc"
        # build_opener was called once for the loopback bypass path.
        assert len(captured_handlers) == 1, (
            f"Expected 1 build_opener call, got {len(captured_handlers)}"
        )
        # The handler list must contain a ProxyHandler with empty proxies dict.
        handler_types = [type(h).__name__ for h in captured_handlers[0]]
        assert "ProxyHandler" in handler_types, (
            f"Expected ProxyHandler in handlers, got: {handler_types}"
        )
        for h in captured_handlers[0]:
            if isinstance(h, urllib.request.ProxyHandler):
                # Empty dict means: no proxies, bypass system config entirely.
                assert h.proxies == {}, (
                    f"ProxyHandler must be constructed with empty dict, got: {h.proxies}"
                )
        # Default urlopen must not be used for loopback hosts.
        assert urlopen_calls == [], (
            f"Default urlopen must not be used for localhost, got: {urlopen_calls}"
        )

    def test_find_cdp_url_127_0_0_1_bypasses_system_proxy(self, monkeypatch):
        """127.0.0.1 must also trigger the loopback bypass path."""
        import urllib.request
        from bridgic.browser.session import find_cdp_url

        captured_handlers = []

        def _spy_build_opener(*handlers):
            captured_handlers.append(handlers)
            opener = MagicMock()
            opener.open = MagicMock(
                return_value=self._make_fake_response(
                    {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}
                )
            )
            return opener

        monkeypatch.setattr(urllib.request, "build_opener", _spy_build_opener)

        result = find_cdp_url(mode="port", host="127.0.0.1", port=9222)

        assert "ws://127.0.0.1:9222/devtools/browser/abc" == result
        assert len(captured_handlers) == 1
        assert any(
            isinstance(h, urllib.request.ProxyHandler) and h.proxies == {}
            for h in captured_handlers[0]
        )

    def test_find_cdp_url_remote_uses_default_opener(self, monkeypatch):
        """Remote hosts must keep proxy support and use the default urlopen."""
        import urllib.request
        from bridgic.browser.session import find_cdp_url

        build_opener_calls = []

        def _spy_build_opener(*handlers):
            build_opener_calls.append(handlers)
            return MagicMock()

        urlopen_calls = []

        def _spy_urlopen(*args, **kwargs):
            urlopen_calls.append((args, kwargs))
            return self._make_fake_response(
                {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}
            )

        monkeypatch.setattr(urllib.request, "build_opener", _spy_build_opener)
        monkeypatch.setattr(urllib.request, "urlopen", _spy_urlopen)

        result = find_cdp_url(mode="port", host="example.com", port=9222)

        # Remote host: replace localhost in the returned URL with the actual host.
        assert result == "ws://example.com:9222/devtools/browser/abc"
        # Loopback bypass branch must NOT have been taken.
        assert build_opener_calls == [], (
            f"Remote host must not call build_opener, got {build_opener_calls}"
        )
        # Default urlopen must have been used exactly once.
        assert len(urlopen_calls) == 1, (
            f"Expected 1 urlopen call for remote host, got {len(urlopen_calls)}"
        )

    def test_find_cdp_url_localhost_returns_connection_error_when_port_dead(self):
        """End-to-end check: probing a dead local port surfaces a clean
        ConnectionError that mentions the port number, not a proxy-shaped
        message like '502 Bad Gateway'.

        Note: the original macOS system-proxy bug cannot be reproduced via
        env-var proxies in unit tests because urllib auto-bypasses 127.0.0.1
        for env-var proxies (proxy_bypass_environment). The two preceding tests
        cover the bypass mechanism directly via build_opener spying. This test
        guards against regressions in the basic localhost path."""
        import socket
        from bridgic.browser.session import find_cdp_url

        # Find a free port by binding then releasing it.
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
        finally:
            s.close()

        with pytest.raises(ConnectionError) as exc_info:
            find_cdp_url(mode="port", host="127.0.0.1", port=dead_port)

        # Error message must mention the port and not look like a proxy error.
        msg = str(exc_info.value)
        assert str(dead_port) in msg, f"Expected port {dead_port} in error: {msg}"
        assert "Bad Gateway" not in msg, f"Error must not mention Bad Gateway: {msg}"


# ─────────────────────────────────────────────────────────────────────────────
# Public API exposure
# ─────────────────────────────────────────────────────────────────────────────

class TestApiExposure:
    """Smoke tests verifying find_cdp_url and resolve_cdp_input are callable
    and present in the public API (bridgic.browser and bridgic.browser.session)."""

    def test_importable_from_bridgic_browser(self):
        from bridgic.browser import find_cdp_url, resolve_cdp_input
        assert callable(find_cdp_url)
        assert callable(resolve_cdp_input)

    def test_importable_from_bridgic_browser_session(self):
        from bridgic.browser.session import find_cdp_url, resolve_cdp_input
        assert callable(find_cdp_url)
        assert callable(resolve_cdp_input)

    def test_in_all(self):
        import bridgic.browser as pkg
        assert "find_cdp_url" in pkg.__all__
        assert "resolve_cdp_input" in pkg.__all__


# ─────────────────────────────────────────────────────────────────────────────
# get_page_size_info
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPageSizeInfo:
    """Tests for Browser.get_page_size_info (CDP Page.getLayoutMetrics path)."""

    @pytest.mark.asyncio
    async def test_returns_page_size_info_from_cdp(self, mock_playwright, mock_page, mock_context, mock_cdp_session):
        """Successful CDP Page.getLayoutMetrics returns a populated PageSizeInfo."""
        from bridgic.browser.session._browser_model import PageSizeInfo

        mock_cdp_session.send = AsyncMock(return_value={
            "cssLayoutViewport": {"clientWidth": 1280, "clientHeight": 800, "pageX": 0, "pageY": 200},
            "cssContentSize": {"width": 1280, "height": 4000},
            "cssVisualViewport": {"clientWidth": 1280, "clientHeight": 800},
        })

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            browser = Browser(stealth=False)
            await browser._start()

            result = await browser.get_page_size_info()

        assert isinstance(result, PageSizeInfo)
        assert result.viewport_width == 1280
        assert result.viewport_height == 800
        assert result.page_height == 4000
        assert result.scroll_y == 200
        assert result.pixels_above == 200
        assert result.pixels_below == 4000 - 800 - 200

    @pytest.mark.asyncio
    async def test_returns_none_when_no_page(self):
        """Returns None immediately when no page is open."""
        browser = Browser(stealth=False)
        assert browser._page is None
        result = await browser.get_page_size_info()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_evaluate_raises(self, mock_playwright, mock_page, mock_context, mock_cdp_session):
        """Returns None gracefully when CDP session send fails."""
        mock_cdp_session.send = AsyncMock(side_effect=RuntimeError("cdp failed"))

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            browser = Browser(stealth=False)
            await browser._start()

            result = await browser.get_page_size_info()

        assert result is None

    @pytest.mark.asyncio
    async def test_cdp_session_created_and_detached(self, mock_playwright, mock_page, mock_context, mock_cdp_session):
        """Verify CDP session is opened for Page.getLayoutMetrics and detached afterwards."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            browser = Browser(stealth=False)
            await browser._start()

            await browser.get_page_size_info()

        mock_context.new_cdp_session.assert_called_once_with(mock_page)
        mock_cdp_session.send.assert_called_once_with("Page.getLayoutMetrics")
        mock_cdp_session.detach.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# get_full_page_info
# ─────────────────────────────────────────────────────────────────────────────

class TestGetFullPageInfo:
    """Tests for Browser.get_full_page_info concurrent fetch behavior."""

    @pytest.mark.asyncio
    async def test_returns_full_page_info_on_success(self, mock_playwright, mock_page, mock_context):
        """Returns FullPageInfo combining snapshot tree and page size data."""
        from bridgic.browser.session._browser_model import FullPageInfo

        fake_snapshot = MagicMock()
        fake_snapshot.tree = "- button \"Go\" [ref=abc]"

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            browser = Browser(stealth=False)
            await browser._start()
            browser.get_snapshot = AsyncMock(return_value=fake_snapshot)

            result = await browser.get_full_page_info()

        assert isinstance(result, FullPageInfo)
        assert result.tree == fake_snapshot.tree

    @pytest.mark.asyncio
    async def test_returns_none_when_no_page(self):
        """Returns None immediately when no page is open."""
        browser = Browser(stealth=False)
        assert browser._page is None
        result = await browser.get_full_page_info()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_snapshot_raises(self, mock_playwright, mock_page):
        """Returns None when get_snapshot raises."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            browser = Browser(stealth=False)
            await browser._start()
            browser.get_snapshot = AsyncMock(side_effect=RuntimeError("snap failed"))

            result = await browser.get_full_page_info()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_page_info_fails(self, mock_playwright, mock_page, mock_context, mock_cdp_session):
        """Returns None when get_page_size_info returns None (CDP send failed)."""
        fake_snapshot = MagicMock()
        fake_snapshot.tree = "- heading \"Hi\""

        mock_cdp_session.send = AsyncMock(side_effect=RuntimeError("cdp error"))

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            browser = Browser(stealth=False)
            await browser._start()
            browser.get_snapshot = AsyncMock(return_value=fake_snapshot)

            result = await browser.get_full_page_info()

        assert result is None

    @pytest.mark.asyncio
    async def test_snapshot_and_page_info_run_concurrently(self, mock_playwright, mock_page, mock_context, mock_cdp_session):
        """get_snapshot and get_page_size_info must overlap in time (asyncio.gather)."""
        call_log: list[str] = []
        snapshot_started = asyncio.Event()
        page_info_started = asyncio.Event()

        async def _slow_snapshot(*args, **kwargs):
            call_log.append("snapshot:start")
            snapshot_started.set()
            await asyncio.sleep(0)  # yield to let get_page_size_info start
            await page_info_started.wait()
            call_log.append("snapshot:end")
            snap = MagicMock()
            snap.tree = "- button"
            return snap

        async def _slow_cdp_send(*args, **kwargs):
            call_log.append("page_info:start")
            page_info_started.set()
            await snapshot_started.wait()
            return {
                "cssLayoutViewport": {"clientWidth": 1280, "clientHeight": 800, "pageX": 0, "pageY": 0},
                "cssContentSize": {"width": 1280, "height": 2000},
                "cssVisualViewport": {"clientWidth": 1280, "clientHeight": 800},
            }

        mock_cdp_session.send = AsyncMock(side_effect=_slow_cdp_send)

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            browser = Browser(stealth=False)
            await browser._start()
            browser.get_snapshot = AsyncMock(side_effect=_slow_snapshot)

            result = await browser.get_full_page_info()

        assert result is not None
        # Both must have started before either finished — proving concurrency.
        assert "snapshot:start" in call_log
        assert "page_info:start" in call_log
        snapshot_end_idx = call_log.index("snapshot:end")
        page_info_start_idx = call_log.index("page_info:start")
        # page_info started before snapshot finished → they overlapped
        assert page_info_start_idx < snapshot_end_idx, (
            "page_info should have started before snapshot finished (concurrent)"
        )


# ---------------------------------------------------------------------------
# _locator_action_with_fallback — click timeout + dispatch_event fallback
# ---------------------------------------------------------------------------

class TestLocatorActionWithFallback:
    """Tests for :func:`_browser_module._locator_action_with_fallback`.

    This helper caps Playwright's default 30s locator timeout at 10s and
    dispatches a DOM event as a fallback. It's the core defence against the
    "click hangs for 30s on SPA elements" pathology observed in prod logs.
    """

    @pytest.mark.asyncio
    async def test_primary_action_success_uses_timeout(self):
        """Happy path: action succeeds; no fallback event dispatched."""
        locator = MagicMock()
        locator.click = AsyncMock(return_value=None)
        locator.dispatch_event = AsyncMock()

        await _browser_module._locator_action_with_fallback(locator, action="click")

        locator.click.assert_awaited_once()
        # Timeout must be explicitly passed (lower than Playwright's default).
        assert locator.click.await_args.kwargs.get("timeout") == _browser_module._DEFAULT_CLICK_TIMEOUT_MS
        locator.dispatch_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_triggers_dispatch_event_fallback(self):
        """On Playwright TimeoutError, dispatch_event is called with the configured event."""
        locator = MagicMock()
        locator.click = AsyncMock(
            side_effect=_browser_module.PlaywrightTimeoutError("locator.click: Timeout 10000ms")
        )
        locator.dispatch_event = AsyncMock()

        await _browser_module._locator_action_with_fallback(
            locator, action="click", fallback_event="click"
        )

        locator.click.assert_awaited_once()
        locator.dispatch_event.assert_awaited_once_with("click")

    @pytest.mark.asyncio
    async def test_dblclick_fallback_event(self):
        """dblclick action pairs with a 'dblclick' DOM fallback by convention."""
        locator = MagicMock()
        locator.dblclick = AsyncMock(
            side_effect=_browser_module.PlaywrightTimeoutError("timeout")
        )
        locator.dispatch_event = AsyncMock()

        await _browser_module._locator_action_with_fallback(
            locator, action="dblclick", fallback_event="dblclick"
        )

        locator.dispatch_event.assert_awaited_once_with("dblclick")

    @pytest.mark.asyncio
    async def test_non_timeout_error_not_swallowed(self):
        """Errors that aren't PlaywrightTimeoutError bubble up unchanged."""
        locator = MagicMock()
        locator.click = AsyncMock(side_effect=RuntimeError("not a timeout"))
        locator.dispatch_event = AsyncMock()

        with pytest.raises(RuntimeError, match="not a timeout"):
            await _browser_module._locator_action_with_fallback(locator, action="click")

        locator.dispatch_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_custom_timeout_respected(self):
        """Caller-supplied timeout overrides the module default."""
        locator = MagicMock()
        locator.click = AsyncMock(return_value=None)

        await _browser_module._locator_action_with_fallback(
            locator, action="click", timeout_ms=5000
        )

        assert locator.click.await_args.kwargs.get("timeout") == 5000

    @pytest.mark.asyncio
    async def test_check_action_dispatches_click_on_timeout(self):
        """`check` action falls back to a 'click' DOM event (same activation semantics)."""
        locator = MagicMock()
        locator.check = AsyncMock(
            side_effect=_browser_module.PlaywrightTimeoutError("timeout")
        )
        locator.dispatch_event = AsyncMock()

        await _browser_module._locator_action_with_fallback(
            locator, action="check", fallback_event="click"
        )

        locator.dispatch_event.assert_awaited_once_with("click")


# ---------------------------------------------------------------------------
# _retriable_launch — exponential back-off for transient launch failures
# ---------------------------------------------------------------------------

class TestRetriableLaunch:
    """Tests for :func:`_browser_module._retriable_launch`.

    Playwright's :meth:`launch_persistent_context` can fail with
    ``TargetClosedError`` when the prior Chromium process hasn't released the
    user-data-dir singleton lock. Without back-off, a user repeatedly running
    ``navigate_to`` gets 8 rapid-fire failures (per prod log). With the
    helper, we get 3 attempts max with 0s → 1s → 2.5s spacing.
    """

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """Successful launch returns immediately; no retries, no sleeps."""
        result_obj = object()
        call_count = 0

        async def launch():
            nonlocal call_count
            call_count += 1
            return result_obj

        with patch("bridgic.browser.session._browser.asyncio.sleep") as mock_sleep:
            result = await _browser_module._retriable_launch(launch, mode="persistent_context")

        assert result is result_obj
        assert call_count == 1
        # First-attempt delay is 0.0 → sleep not called (helper guards >0).
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_target_closed_error(self):
        """Transient 'target ... has been closed' error retries until success."""
        attempts = []

        async def launch():
            attempts.append("tried")
            if len(attempts) < 2:
                raise Exception("Target page, context or browser has been closed")
            return "ok"

        with patch("bridgic.browser.session._browser.asyncio.sleep", new=AsyncMock()):
            result = await _browser_module._retriable_launch(launch, mode="persistent_context")

        assert result == "ok"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_retries_on_singleton_lock(self):
        """'SingletonLock' error (profile still held) is retriable."""
        attempts = []

        async def launch():
            attempts.append("tried")
            if len(attempts) < 3:
                raise Exception("SingletonLock still held by previous process")
            return "ok"

        with patch("bridgic.browser.session._browser.asyncio.sleep", new=AsyncMock()):
            result = await _browser_module._retriable_launch(launch, mode="persistent_context")

        assert result == "ok"
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_non_retriable_fails_fast(self):
        """Errors not in _RETRIABLE_LAUNCH_TOKENS raise after the first attempt."""
        attempts = []

        async def launch():
            attempts.append("tried")
            raise Exception("Executable not found at /bad/path")

        with patch("bridgic.browser.session._browser.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="Executable not found"):
                await _browser_module._retriable_launch(launch, mode="launch")

        assert len(attempts) == 1

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        """Persistent transient errors exhaust all delays and re-raise the last error."""
        attempts = []

        async def launch():
            attempts.append("tried")
            raise Exception("Target closed still")

        with patch("bridgic.browser.session._browser.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="Target closed"):
                await _browser_module._retriable_launch(launch, mode="persistent_context")

        assert len(attempts) == len(_browser_module._LAUNCH_RETRY_DELAYS)

    @pytest.mark.asyncio
    async def test_backoff_delays_applied(self):
        """Each retry waits the corresponding delay before calling the launch callable."""
        sleep_calls: list[float] = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)

        async def launch():
            raise Exception("has been closed")

        with patch("bridgic.browser.session._browser.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(Exception):
                await _browser_module._retriable_launch(launch, mode="persistent_context")

        # Only non-zero delays get a real sleep call; attempt 1 has delay=0.0.
        expected = [d for d in _browser_module._LAUNCH_RETRY_DELAYS if d > 0]
        assert sleep_calls == expected
