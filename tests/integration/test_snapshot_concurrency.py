"""
Integration tests for concurrent snapshot pipeline isolation (S-1).

Regression guard: ``window.__bridgicRoleIndex`` is a page-global symbol.
Two ``get_snapshot()`` calls arriving in parallel (daemon with multiple
clients) would race on this key — one task cleans up the key while the
other still relies on it, leading to missing refs or phantom elements.
The fix keys the cache per-generation (``__bridgicRoleIndex_<hex>``) so
each call has its own isolated namespace.

Tests:
  1. Two concurrent snapshots both succeed and return usable refs.
  2. After concurrent calls finish, no ``__bridgicRoleIndex_*`` keys are
     left on ``window`` (each generation cleans up its own key).
"""

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from bridgic.browser.session import Browser


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
TEST_PAGE_PATH = FIXTURES_DIR / "test_page.html"


@pytest.mark.asyncio
async def test_two_concurrent_snapshots_both_return_usable_refs() -> None:
    """S-1: running two snapshots in parallel must not poison each other."""
    assert TEST_PAGE_PATH.exists(), f"missing fixture: {TEST_PAGE_PATH}"

    async with Browser(headless=True, stealth=False) as browser:
        await browser.navigate_to(f"file://{TEST_PAGE_PATH}")

        # Kick off both snapshots simultaneously — this is the scenario that
        # used to corrupt ``window.__bridgicRoleIndex`` when the key was
        # shared across calls.
        snap1, snap2 = await asyncio.gather(
            browser.get_snapshot(),
            browser.get_snapshot(),
        )

        assert snap1.refs, "first snapshot returned no refs"
        assert snap2.refs, "second snapshot returned no refs"

        # Pick a ref from each snapshot and confirm it still resolves.
        ref1 = next(iter(snap1.refs))
        ref2 = next(iter(snap2.refs))
        loc1 = await browser.get_element_by_ref(ref1)
        loc2 = await browser.get_element_by_ref(ref2)
        # Both locators must resolve — count > 0 means the DOM node exists.
        assert await loc1.count() > 0
        assert await loc2.count() > 0


@pytest.mark.asyncio
async def test_concurrent_snapshot_cleans_up_all_generation_keys() -> None:
    """S-1 cleanup: no ``__bridgicRoleIndex_*`` left behind on window.

    Each snapshot call owns a per-generation cache key; the cleanup phase
    of ``_batch_get_elements_info`` must remove its own key so the page
    doesn't leak arbitrary object references.  After two concurrent calls
    finish, zero ``__bridgicRoleIndex_*`` keys should remain.
    """
    async with Browser(headless=True, stealth=False) as browser:
        await browser.navigate_to(f"file://{TEST_PAGE_PATH}")

        await asyncio.gather(
            browser.get_snapshot(),
            browser.get_snapshot(),
            browser.get_snapshot(),
        )

        # Use the current page to inspect window.
        page = browser._page
        leftover = await page.evaluate(
            "() => Object.keys(window).filter(k => k.startsWith('__bridgicRoleIndex'))"
        )
        assert leftover == [], (
            f"generation keys not cleaned up after concurrent snapshots: {leftover}"
        )
