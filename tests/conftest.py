"""
Pytest configuration and shared fixtures for bridgic-browser tests.
"""

# Prefer installed bridgic namespace (browser + core + llms) over project root
# so that "from bridgic.browser.session import Browser" sees bridgic.core / bridgic.llms
import sys
from pathlib import Path

_site_packages = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
_project_root = Path(__file__).resolve().parent.parent
_project_root_str = str(_project_root)
if _site_packages.exists():
    _sp = str(_site_packages)
    # Remove project root so bridgic is loaded from site-packages (full namespace)
    while _project_root_str in sys.path:
        sys.path.remove(_project_root_str)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)
    else:
        sys.path.remove(_sp)
        sys.path.insert(0, _sp)

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Skip all browser tests if SKIP_BROWSER_TESTS is set (for CI without browser)
SKIP_BROWSER_TESTS = os.environ.get("SKIP_BROWSER_TESTS", "").lower() in ("1", "true", "yes")


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory(prefix="bridgic-test-") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_downloads_dir(temp_dir: Path) -> Path:
    """Create a temporary downloads directory."""
    downloads = temp_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads



@pytest.fixture
def mock_page() -> MagicMock:
    """Create a mock Playwright Page object."""
    page = MagicMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example Page")
    page.goto = AsyncMock()
    page.close = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"fake_screenshot_data")
    page.evaluate = AsyncMock(return_value={
        "viewport_width": 1920,
        "viewport_height": 1080,
        "page_width": 1920,
        "page_height": 3000,
        "scroll_x": 0,
        "scroll_y": 0,
    })
    page.bring_to_front = AsyncMock()
    page.locator = MagicMock()
    page.context = MagicMock()
    return page


@pytest.fixture
def mock_context(mock_page: MagicMock) -> MagicMock:
    """Create a mock Playwright BrowserContext object."""
    context = MagicMock()
    context.pages = [mock_page]
    context.new_page = AsyncMock(return_value=mock_page)
    context.close = AsyncMock()
    context.browser = None  # Playwright persistent contexts return None for .browser
    context.new_cdp_session = AsyncMock()
    context.add_init_script = AsyncMock()
    return context


@pytest.fixture
def mock_browser(mock_context: MagicMock) -> MagicMock:
    """Create a mock Playwright Browser object."""
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=mock_context)
    browser.close = AsyncMock()
    return browser


@pytest.fixture
def mock_playwright(mock_browser: MagicMock, mock_context: MagicMock) -> MagicMock:
    """Create a mock Playwright instance."""
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=mock_browser)
    playwright.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)
    playwright.stop = AsyncMock()
    return playwright


@pytest.fixture
def mock_download() -> MagicMock:
    """Create a mock Playwright Download object."""
    download = MagicMock()
    download.url = "https://example.com/file.pdf"
    download.suggested_filename = "document.pdf"
    download.path = AsyncMock(return_value="/tmp/download-uuid")
    download.save_as = AsyncMock()
    download.failure = AsyncMock(return_value=None)
    return download


# Browser fixtures that require actual Playwright
@pytest_asyncio.fixture
async def browser_instance():
    """Create an actual Browser instance for integration tests.

    This fixture is skipped if SKIP_BROWSER_TESTS is set.
    """
    if SKIP_BROWSER_TESTS:
        pytest.skip("Browser tests skipped (SKIP_BROWSER_TESTS=1)")

    from bridgic.browser.session import Browser

    browser = Browser(
        headless=True,
        stealth=False,  # Disable stealth for faster tests
        clear_user_data=True,  # Ephemeral: no cross-test profile state
        viewport={"width": 1280, "height": 720},
    )

    try:
        yield browser
    finally:
        await browser.close()


@pytest_asyncio.fixture
async def browser_with_stealth():
    """Create a Browser instance with stealth mode for integration tests."""
    if SKIP_BROWSER_TESTS:
        pytest.skip("Browser tests skipped (SKIP_BROWSER_TESTS=1)")

    from bridgic.browser.session import Browser

    browser = Browser(
        headless=True,
        stealth=True,
        clear_user_data=True,  # Ephemeral: no cross-test profile state
        viewport={"width": 1280, "height": 720},
    )

    try:
        await browser._start()
        yield browser
    finally:
        await browser.close()
