"""
Regression tests for ``take_screenshot`` option plumbing.

Before the fix, ``screenshot_options`` always carried a ``full_page`` key (set
to ``False`` in the ref branch).  But Playwright's ``Locator.screenshot()``
rejects ``full_page`` — only ``Page.screenshot()`` accepts it — so every
``take_screenshot(ref=...)`` call raised
``TypeError: ... got an unexpected keyword argument 'full_page'``.

The fix omits the key entirely in the ref branch.  These tests lock that
invariant by inspecting what ``page.screenshot`` / ``locator.screenshot`` is
called with.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridgic.browser.session._browser import Browser


@pytest.fixture
def browser_with_mocks(monkeypatch):
    """A ``Browser`` whose ``get_current_page`` / ``get_element_by_ref`` are
    replaced with mocks returning inspectable ``screenshot`` coroutines."""
    b = Browser(headless=True, stealth=False)

    page_screenshot = AsyncMock(return_value=b"\x89PNG")
    page = MagicMock()
    page.screenshot = page_screenshot

    locator_screenshot = AsyncMock(return_value=b"\x89PNG")
    locator = MagicMock()
    locator.screenshot = locator_screenshot

    async def _get_page():
        return page

    async def _get_el(ref: str):
        return locator

    monkeypatch.setattr(b, "get_current_page", _get_page)
    monkeypatch.setattr(b, "get_element_by_ref", _get_el)

    return b, page_screenshot, locator_screenshot


class TestScreenshotOptions:
    async def test_page_path_includes_full_page(self, browser_with_mocks) -> None:
        b, page_screenshot, _ = browser_with_mocks
        await b.take_screenshot(full_page=True)
        assert page_screenshot.await_count == 1
        kwargs = page_screenshot.await_args.kwargs
        assert kwargs["full_page"] is True
        assert kwargs["type"] == "png"

    async def test_ref_path_omits_full_page(self, browser_with_mocks) -> None:
        """The ref branch must not pass ``full_page`` to ``Locator.screenshot``."""
        b, _, locator_screenshot = browser_with_mocks
        await b.take_screenshot(ref="abcd1234")
        assert locator_screenshot.await_count == 1
        kwargs = locator_screenshot.await_args.kwargs
        assert "full_page" not in kwargs, (
            f"Locator.screenshot() does not accept full_page; got kwargs={kwargs!r}"
        )
        assert kwargs["type"] == "png"

    async def test_ref_path_omits_full_page_even_when_true(
        self, browser_with_mocks
    ) -> None:
        """Caller-supplied ``full_page=True`` must still be stripped in ref branch."""
        b, _, locator_screenshot = browser_with_mocks
        await b.take_screenshot(ref="abcd1234", full_page=True)
        kwargs = locator_screenshot.await_args.kwargs
        assert "full_page" not in kwargs

    async def test_jpeg_quality_forwarded(self, browser_with_mocks) -> None:
        b, page_screenshot, _ = browser_with_mocks
        await b.take_screenshot(type="jpeg", quality=80)
        kwargs = page_screenshot.await_args.kwargs
        assert kwargs["type"] == "jpeg"
        assert kwargs["quality"] == 80

    async def test_jpeg_quality_forwarded_in_ref_path(
        self, browser_with_mocks
    ) -> None:
        b, _, locator_screenshot = browser_with_mocks
        await b.take_screenshot(ref="abcd1234", type="jpeg", quality=60)
        kwargs = locator_screenshot.await_args.kwargs
        assert kwargs["type"] == "jpeg"
        assert kwargs["quality"] == 60
        assert "full_page" not in kwargs
