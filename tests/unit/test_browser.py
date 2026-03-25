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


class TestBrowserInitialization:
    """Tests for Browser initialization and configuration."""

    def test_default_initialization(self):
        """Test Browser with default parameters."""
        browser = Browser()

        assert browser.headless is True
        assert browser.viewport == {"width": 1600, "height": 900}
        assert browser.user_data_dir is None
        assert browser.stealth_enabled is True  # Stealth is enabled by default
        assert browser.use_persistent_context is False

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
        config = StealthConfig(enable_extensions=False, disable_security=True)
        browser = Browser(stealth=config)

        assert browser.stealth_enabled is True
        assert browser.stealth_config.enable_extensions is False
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

    def test_headless_false_with_stealth_extensions(self):
        """Test that stealth with extensions forces persistent context."""
        browser = Browser(headless=False, stealth=True)

        # Stealth with extensions needs persistent context
        assert browser.use_persistent_context is True

    def test_headless_true_no_extensions(self):
        """Test that headless mode disables stealth extensions."""
        browser = Browser(headless=True, stealth=True)

        # Extensions can't run in headless, so no persistent context needed
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

    def test_context_options_with_stealth(self):
        """Test context options include stealth settings."""
        browser = Browser(stealth=True)
        options = browser._get_context_options()

        assert "permissions" in options
        assert "accept_downloads" in options
        assert "screen" in options  # Stealth adds screen to match viewport

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
        """Test starting browser in normal mode."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            assert browser._playwright is not None
            assert browser._context is not None
            mock_playwright.chromium.launch.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_persistent_mode(self, mock_playwright):
        """Test starting browser in persistent context mode."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            with tempfile.TemporaryDirectory() as tmpdir:
                browser = Browser(user_data_dir=tmpdir, stealth=False)
                await browser.start()

                mock_playwright.chromium.launch_persistent_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_already_started(self, mock_playwright):
        """Test that starting an already started browser logs warning."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            # Second start should just return
            await browser.start()

            # launch should only be called once
            assert mock_playwright.chromium.launch.call_count == 1

    @pytest.mark.asyncio
    async def test_start_rolls_back_on_launch_failure(self, mock_playwright):
        """If launch fails after playwright starts, partial state should be cleaned up."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_playwright.chromium.launch = AsyncMock(side_effect=RuntimeError("launch failed"))

            browser = Browser(stealth=False)
            with pytest.raises(RuntimeError, match="launch failed"):
                await browser.start()

            assert browser._playwright is None
            assert browser._browser is None
            assert browser._context is None
            assert browser._page is None
            mock_playwright.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_cleanup(self, mock_playwright, mock_context, mock_page):
        """Test that stop cleans up all resources."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()
            await browser.stop()

            assert browser._playwright is None
            assert browser._browser is None
            assert browser._context is None
            assert browser._page is None

    @pytest.mark.asyncio
    async def test_kill_cleans_temp_dir(self, mock_playwright):
        """Test that stop cleans up temporary user data directory."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(headless=False, stealth=True)
            await browser.start()

            # Stealth with extensions creates temp dir
            temp_dir = browser._temp_user_data_dir

            await browser.stop()

            assert browser._temp_user_data_dir is None

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
        """stop() auto-finalizes active tracing/video before teardown."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            context.tracing = MagicMock()
            context.tracing.stop = AsyncMock()
            context.pages = [page]

            page.video = MagicMock()
            page.video.path = AsyncMock(return_value="/tmp/playwright-video.webm")

            context_key = browser_module._get_context_key(context)
            browser._tracing_state[context_key] = True
            browser._video_state[context_key] = True

            with patch.object(browser_module.tempfile, "mkstemp", return_value=(99, "/tmp/auto_trace.zip")):
                with patch.object(browser_module.os, "close"):
                    await browser.stop()

            context.tracing.stop.assert_awaited_once_with(path="/tmp/auto_trace.zip")
            page.close.assert_awaited()
            assert browser._last_shutdown_artifacts["trace"] == [os.path.abspath("/tmp/auto_trace.zip")]
            assert browser._last_shutdown_artifacts["video"] == [
                os.path.abspath("/tmp/playwright-video.webm")
            ]
            assert context_key not in browser._tracing_state
            assert context_key not in browser._video_state

    @pytest.mark.asyncio
    async def test_stop_reports_auto_saved_paths(self, mock_playwright):
        """stop() should include auto-saved artifact paths in the result."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            context.tracing = MagicMock()
            context.tracing.stop = AsyncMock()
            context.pages = [page]

            page.video = MagicMock()
            page.video.path = AsyncMock(return_value="/tmp/auto_video.webm")

            context_key = browser_module._get_context_key(context)
            browser._tracing_state[context_key] = True
            browser._video_state[context_key] = True

            with patch.object(browser_module.tempfile, "mkstemp", return_value=(88, "/tmp/auto_trace_2.zip")):
                with patch.object(browser_module.os, "close"):
                    result = await browser.stop()

            assert "Browser closed successfully" in result
            assert os.path.abspath("/tmp/auto_trace_2.zip") in result
            assert os.path.abspath("/tmp/auto_video.webm") in result

    @pytest.mark.asyncio
    async def test_stop_warns_on_trace_finalize_failure(self, mock_playwright):
        """stop() should report warnings when trace auto-save fails."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            context = browser._context
            page = browser._page
            assert context is not None
            assert page is not None

            context.tracing = MagicMock()
            context.tracing.stop = AsyncMock(side_effect=RuntimeError("disk full"))
            context.pages = [page]

            context_key = browser_module._get_context_key(context)
            browser._tracing_state[context_key] = True

            temp_trace_path = "/tmp/auto_trace_failed.zip"

            def _exists(path: str) -> bool:
                return path == temp_trace_path

            with patch.object(browser_module.tempfile, "mkstemp", return_value=(77, temp_trace_path)):
                with patch.object(browser_module.os, "close"):
                    with patch.object(browser_module.os.path, "exists", side_effect=_exists):
                        with patch.object(browser_module.os, "remove") as mock_remove:
                            result = await browser.stop()

            mock_remove.assert_called_once_with(temp_trace_path)
            assert "Browser closed with warnings" in result
            assert "tracing.stop: disk full" in result

    @pytest.mark.asyncio
    async def test_stop_clears_page_scoped_handlers_before_auto_video_finalize(self, mock_playwright):
        """stop() should remove page listeners/caches even when auto-video closes the page first."""
        from bridgic.browser.session import _browser as browser_module

        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

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

            await browser.stop()

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
            await browser.start()

            async def _slow_close():
                await asyncio.sleep(1.0)

            assert browser._context is not None
            browser._context.close = AsyncMock(side_effect=_slow_close)
            browser._CONTEXT_CLOSE_TIMEOUT = 0.01

            await browser.stop()

            assert any(
                warning.startswith("context.close: timeout after")
                for warning in browser._last_shutdown_errors
            )


class TestBrowserNavigation:
    """Tests for Browser navigation methods."""

    @pytest.mark.asyncio
    async def test_navigate_to(self, mock_playwright, mock_page):
        """Test navigate_to method."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()
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
            await browser.start()

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
            await browser.start()

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
    async def test_navigate_to_without_context_raises_state_error(self):
        browser = Browser(stealth=False)

        with pytest.raises(StateError) as exc_info:
            await browser.navigate_to("https://example.com")
        assert exc_info.value.code == "NO_BROWSER_CONTEXT"


class TestBrowserPageManagement:
    """Tests for Browser page management methods."""

    @pytest.mark.asyncio
    async def test_new_page(self, mock_playwright, mock_context, mock_page):
        """Test creating a new page."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            new_page = await browser.new_page()

            mock_context.new_page.assert_called_once()
            # new_page() returns the Playwright page from context.new_page()
            assert new_page is mock_page

    @pytest.mark.asyncio
    async def test_new_page_without_context_raises_state_error(self):
        browser = Browser(stealth=False)

        with pytest.raises(StateError) as exc_info:
            await browser.new_page()
        assert exc_info.value.code == "NO_BROWSER_CONTEXT"

    @pytest.mark.asyncio
    async def test_get_pages(self, mock_playwright, mock_context, mock_page):
        """Test getting all pages."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            pages = browser.get_pages()

            assert len(pages) == 1
            assert pages[0] == mock_page

    @pytest.mark.asyncio
    async def test_get_current_page_url(self, mock_playwright, mock_page):
        """Test getting current page URL."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            url = browser.get_current_page_url()

            assert url == "https://example.com"

    @pytest.mark.asyncio
    async def test_get_current_page_title(self, mock_playwright, mock_page):
        """Test getting current page title."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()

            title = await browser.get_current_page_title()

            assert title == "Example Page"


class TestBrowserRefResolution:
    """Tests for ref -> locator resolution behavior."""

    @pytest.mark.asyncio
    async def test_get_element_by_ref_prefers_only_visible_match(self):
        """When multiple matches exist, pick the unique visible candidate."""
        browser = Browser(stealth=False)
        browser._page = MagicMock()
        browser._last_snapshot = MagicMock(
            refs={"e7": SimpleNamespace(role="generic", name=None, nth=None)}
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
            refs={"e7": SimpleNamespace(role="generic", name=None, nth=None)}
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
            refs={"e7": SimpleNamespace(role="button", name="Automatic detection", nth=None)}
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
            refs={"e7": SimpleNamespace(role="generic", name="Automatic detection", nth=None)}
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
            refs={"e7": SimpleNamespace(role="button", name=None, nth=1)}
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
