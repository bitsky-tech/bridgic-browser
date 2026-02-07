"""
Unit tests for the Browser class.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from bridgic.browser.session import Browser, StealthConfig


class TestBrowserInitialization:
    """Tests for Browser initialization and configuration."""

    def test_default_initialization(self):
        """Test Browser with default parameters."""
        browser = Browser()

        assert browser.headless is True
        assert browser.viewport == {"width": 1920, "height": 1080}
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

        assert options["viewport"] == {"width": 1920, "height": 1080}

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
    async def test_kill_cleanup(self, mock_playwright, mock_context, mock_page):
        """Test that kill cleans up all resources."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(stealth=False)
            await browser.start()
            await browser.kill()

            assert browser._playwright is None
            assert browser._browser is None
            assert browser._context is None
            assert browser._page is None

    @pytest.mark.asyncio
    async def test_kill_cleans_temp_dir(self, mock_playwright):
        """Test that kill cleans up temporary user data directory."""
        with patch("bridgic.browser.session._browser.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

            browser = Browser(headless=False, stealth=True)
            await browser.start()

            # Stealth with extensions creates temp dir
            temp_dir = browser._temp_user_data_dir

            await browser.kill()

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

            assert new_page is not None
            mock_context.new_page.assert_called()

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
