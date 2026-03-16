"""
Integration tests for Browser core functionality.

These tests require an actual browser and are marked with @pytest.mark.integration.
"""

import pytest
import pytest_asyncio

from bridgic.browser.session import Browser


@pytest_asyncio.fixture
async def browser_instance():
    """Create a real browser instance for integration tests."""
    browser = Browser(headless=True, stealth=False)
    await browser.start()
    yield browser
    await browser.stop()


@pytest.mark.integration
class TestBrowserIntegration:
    """Integration tests with actual browser."""

    @pytest.mark.asyncio
    async def test_real_browser_navigation(self, browser_instance):
        """Test real browser navigation."""
        await browser_instance.navigate_to("https://example.com")

        url = browser_instance.get_current_page_url()
        assert "example.com" in url

    @pytest.mark.asyncio
    async def test_real_browser_screenshot(self, browser_instance):
        """Test real browser screenshot."""
        await browser_instance.navigate_to("https://example.com")

        screenshot = await browser_instance.take_screenshot()
        assert screenshot is not None
        assert len(screenshot) > 0

    @pytest.mark.asyncio
    async def test_real_browser_page_info(self, browser_instance):
        """Test real browser page info."""
        await browser_instance.navigate_to("https://example.com")

        page_info = await browser_instance.get_current_page_info()
        assert page_info is not None
        assert page_info.url is not None
        assert page_info.viewport_width > 0
        assert page_info.viewport_height > 0

    @pytest.mark.asyncio
    async def test_real_browser_snapshot(self, browser_instance):
        """Test real browser snapshot."""
        await browser_instance.navigate_to("https://example.com")

        snapshot = await browser_instance.get_snapshot()
        assert snapshot is not None
        assert snapshot.tree is not None
        assert len(snapshot.tree) > 0
