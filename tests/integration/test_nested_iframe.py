"""
Integration tests for nested iframe ref resolution.

Fixture page structure:
  main frame
    - button "Main Button"           (data-testid: main-btn)
    - textbox "Main Input"
    - button "Shared Button"         [same name appears in all 3 frames → nth test]
    - iframe (level 1)
        - button "Level 1 Button"
        - textbox "Level 1 Input"
        - button "Shared Button"     [nth=1]
        - checkbox "Level 1 Checkbox"
        - combobox "Level 1 Select"
        - iframe (level 2)
            - button "Level 2 Button"
            - textbox "Level 2 Input"
            - button "Shared Button" [nth=2]

Test categories:
  1. Snapshot structure  — elements from all frames appear; frame_path depths correct
  2. playwright_ref      — f-prefix for iframe elements; no prefix for main frame
  3. Ref resolution      — get_element_by_ref → count=1, is_visible for all levels
  4. Click verification  — click actually fires onclick (reads data-click-count back)
  5. Input interaction   — input_text_by_ref writes correct value, survives re-snapshot
  6. Nth disambiguation  — "Shared Button" appears 3×; clicking nth=0/1/2 hits correct frame
  7. Interactive mode    — interactive snapshot includes iframe buttons/inputs
  8. State persistence   — interact → re-snapshot → element still reachable by ref
"""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from bridgic.browser.session import Browser

pytestmark = pytest.mark.integration

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
MAIN_PAGE = FIXTURES_DIR / "nested_iframe_test.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_ref(snap, name: str, nth: int = 0):
    """Return the bridgic ref for the Nth element with the given name."""
    matches = [(k, d) for k, d in snap.refs.items() if d.name == name]
    if nth < len(matches):
        # sort by frame_path depth then by nth field so ordering is deterministic
        matches.sort(key=lambda x: (len(x[1].frame_path or []), x[1].nth or 0))
        return matches[nth][0]
    return None


async def _get_click_count(browser: "Browser", ref: str) -> str:
    """Read data-click-count from the element directly via its locator.

    Using locator.get_attribute() avoids the frame-traversal complexity and
    the covered-element check that can misroute clicks on iframe-resident elements.
    """
    loc = await browser.get_element_by_ref(ref)
    if loc is None:
        return "0"
    val = await loc.get_attribute("data-click-count")
    return val if val is not None else "0"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def browser():
    b = Browser(headless=True, stealth=False, viewport={"width": 1280, "height": 900})
    await b.navigate_to(f"file://{MAIN_PAGE}")
    await asyncio.sleep(0.6)  # wait for nested iframes to load
    yield b
    await b.close()


async def _snap(browser: Browser):
    return await browser.get_snapshot(full_page=True)


# ---------------------------------------------------------------------------
# 1. Snapshot structure
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedIframeSnapshot:
    """Verify the snapshot tree includes all elements across all three frames."""

    @pytest.mark.asyncio
    async def test_all_levels_appear_in_snapshot(self, browser):
        snap = await _snap(browser)
        names = {d.name for d in snap.refs.values() if d.name}
        for expected in ("Main Button", "Level 1 Button", "Level 2 Button",
                         "Main Input", "Level 1 Input", "Level 2 Input"):
            assert expected in names, f"'{expected}' missing from snapshot"

    @pytest.mark.asyncio
    async def test_main_frame_frame_path_is_none(self, browser):
        snap = await _snap(browser)
        d = next((d for d in snap.refs.values() if d.name == "Main Button"), None)
        assert d is not None
        assert d.frame_path is None

    @pytest.mark.asyncio
    async def test_level1_frame_path_depth_is_1(self, browser):
        snap = await _snap(browser)
        d = next((d for d in snap.refs.values() if d.name == "Level 1 Button"), None)
        assert d is not None
        assert d.frame_path is not None and len(d.frame_path) == 1, (
            f"Expected frame_path length 1, got {d.frame_path}"
        )

    @pytest.mark.asyncio
    async def test_level2_frame_path_depth_is_2(self, browser):
        snap = await _snap(browser)
        d = next((d for d in snap.refs.values() if d.name == "Level 2 Button"), None)
        assert d is not None
        assert d.frame_path is not None and len(d.frame_path) == 2, (
            f"Expected frame_path length 2, got {d.frame_path}"
        )

    @pytest.mark.asyncio
    async def test_shared_button_appears_in_all_three_frames(self, browser):
        """'Shared Button' exists in main, l1, l2 — snapshot should see 3 refs."""
        snap = await _snap(browser)
        shared = [d for d in snap.refs.values() if d.name == "Shared Button"]
        assert len(shared) == 3, (
            f"Expected 3 'Shared Button' refs across frames, got {len(shared)}: "
            f"{[(d.frame_path, d.nth) for d in shared]}"
        )

    @pytest.mark.asyncio
    async def test_level1_checkbox_and_select_in_snapshot(self, browser):
        snap = await _snap(browser)
        names = {d.name for d in snap.refs.values() if d.name}
        assert "Level 1 Checkbox" in names
        assert "Level 1 Select" in names


# ---------------------------------------------------------------------------
# 2. playwright_ref prefix
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedIframePlaywrightRef:
    """Verify playwright_ref carries the correct frame prefix."""

    @pytest.mark.asyncio
    async def test_main_frame_ref_has_no_f_prefix(self, browser):
        snap = await _snap(browser)
        d = next((d for d in snap.refs.values() if d.name == "Main Button"), None)
        assert d is not None and d.playwright_ref is not None
        assert not d.playwright_ref.startswith("f"), (
            f"Main-frame ref should not start with 'f', got '{d.playwright_ref}'"
        )

    @pytest.mark.asyncio
    async def test_level1_ref_has_f_prefix(self, browser):
        snap = await _snap(browser)
        d = next((d for d in snap.refs.values() if d.name == "Level 1 Button"), None)
        assert d is not None and d.playwright_ref is not None
        assert d.playwright_ref.startswith("f"), (
            f"Level-1 ref should start with 'f', got '{d.playwright_ref}'"
        )

    @pytest.mark.asyncio
    async def test_level2_ref_has_f_prefix(self, browser):
        snap = await _snap(browser)
        d = next((d for d in snap.refs.values() if d.name == "Level 2 Button"), None)
        assert d is not None and d.playwright_ref is not None
        assert d.playwright_ref.startswith("f"), (
            f"Level-2 ref should start with 'f', got '{d.playwright_ref}'"
        )

    @pytest.mark.asyncio
    async def test_l1_and_l2_use_different_f_prefixes(self, browser):
        """Level 1 and Level 2 are in different frames → different f<seq> prefixes."""
        snap = await _snap(browser)
        l1 = next((d for d in snap.refs.values() if d.name == "Level 1 Button"), None)
        l2 = next((d for d in snap.refs.values() if d.name == "Level 2 Button"), None)
        assert l1 and l2
        # Extract f-prefix number: "f1e5" → "f1", "f2e3" → "f2"
        import re
        l1_prefix = re.match(r'^(f\d+)', l1.playwright_ref).group(1)
        l2_prefix = re.match(r'^(f\d+)', l2.playwright_ref).group(1)
        assert l1_prefix != l2_prefix, (
            f"Level 1 and Level 2 should have different frame prefixes, "
            f"got {l1_prefix} and {l2_prefix}"
        )


# ---------------------------------------------------------------------------
# 3. Ref resolution — locator validity
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedIframeRefResolution:
    """Verify get_element_by_ref resolves to count=1, visible locators for all depths."""

    @pytest.mark.asyncio
    async def test_main_button_resolves(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Main Button")
        assert ref
        loc = await browser.get_element_by_ref(ref)
        assert loc is not None
        assert await loc.count() == 1
        assert await loc.is_visible()

    @pytest.mark.asyncio
    async def test_level1_button_resolves(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 1 Button")
        assert ref
        loc = await browser.get_element_by_ref(ref)
        assert loc is not None
        assert await loc.count() == 1
        assert await loc.is_visible()

    @pytest.mark.asyncio
    async def test_level2_button_resolves(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 2 Button")
        assert ref
        loc = await browser.get_element_by_ref(ref)
        assert loc is not None
        assert await loc.count() == 1
        assert await loc.is_visible()

    @pytest.mark.asyncio
    async def test_level1_checkbox_resolves(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 1 Checkbox")
        assert ref
        loc = await browser.get_element_by_ref(ref)
        assert loc is not None
        assert await loc.count() == 1

    @pytest.mark.asyncio
    async def test_level2_input_resolves(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 2 Input")
        assert ref
        loc = await browser.get_element_by_ref(ref)
        assert loc is not None
        assert await loc.count() == 1


# ---------------------------------------------------------------------------
# 4. Click verification — onclick handler sets data-click-count
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedIframeClickVerification:
    """Verify that click_element_by_ref actually fires the onclick in each frame."""

    @pytest.mark.asyncio
    async def test_click_main_button_fires_onclick(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Main Button")
        assert ref
        await browser.click_element_by_ref(ref)
        count = await _get_click_count(browser, ref)
        assert count == "1", f"Expected click-count=1, got '{count}'"

    @pytest.mark.asyncio
    async def test_click_main_button_twice_increments(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Main Button")
        await browser.click_element_by_ref(ref)
        await browser.click_element_by_ref(ref)
        count = await _get_click_count(browser, ref)
        assert count == "2", f"Expected click-count=2, got '{count}'"

    @pytest.mark.asyncio
    async def test_click_level1_button_fires_onclick(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 1 Button")
        assert ref
        await browser.click_element_by_ref(ref)
        count = await _get_click_count(browser, ref)
        assert count == "1", f"Expected click-count=1 in l1, got '{count}'"

    @pytest.mark.asyncio
    async def test_click_level2_button_fires_onclick(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 2 Button")
        assert ref
        await browser.click_element_by_ref(ref)
        count = await _get_click_count(browser, ref)
        assert count == "1", f"Expected click-count=1 in l2, got '{count}'"

    @pytest.mark.asyncio
    async def test_clicks_in_different_frames_are_independent(self, browser):
        """Clicking l1 button should not increment l2 button counter."""
        snap = await _snap(browser)
        l1_ref = _find_ref(snap, "Level 1 Button")
        l2_ref = _find_ref(snap, "Level 2 Button")

        await browser.click_element_by_ref(l1_ref)

        l1_count = await _get_click_count(browser, l1_ref)
        l2_count = await _get_click_count(browser, l2_ref)
        assert l1_count == "1"
        assert l2_count == "0", "l2 button should not be affected by l1 click"


# ---------------------------------------------------------------------------
# 5. Input interaction + state persistence after re-snapshot
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedIframeInputInteraction:
    """Verify input_text_by_ref works in all frames and state survives re-snapshot."""

    @pytest.mark.asyncio
    async def test_type_in_level1_input_and_verify(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 1 Input")
        assert ref
        await browser.input_text_by_ref(ref, "typed in l1")
        loc = await browser.get_element_by_ref(ref)
        assert await loc.input_value() == "typed in l1"

    @pytest.mark.asyncio
    async def test_type_in_level2_input_and_verify(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 2 Input")
        assert ref
        await browser.input_text_by_ref(ref, "typed in l2")
        loc = await browser.get_element_by_ref(ref)
        assert await loc.input_value() == "typed in l2"

    @pytest.mark.asyncio
    async def test_typed_value_survives_re_snapshot(self, browser):
        """Type in l2 input → re-snapshot → resolve ref again → value still present."""
        snap1 = await _snap(browser)
        ref = _find_ref(snap1, "Level 2 Input")
        await browser.input_text_by_ref(ref, "persistent value")

        # Re-snapshot: new bridgic ref will be assigned (stable hash same input)
        snap2 = await _snap(browser)
        ref2 = _find_ref(snap2, "Level 2 Input")
        assert ref2 is not None, "Level 2 Input should still appear after re-snapshot"

        loc = await browser.get_element_by_ref(ref2)
        assert loc is not None
        assert await loc.input_value() == "persistent value"

    @pytest.mark.asyncio
    async def test_check_level1_checkbox(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 1 Checkbox")
        assert ref
        await browser.check_checkbox_or_radio_by_ref(ref)
        loc = await browser.get_element_by_ref(ref)
        assert await loc.is_checked(), "Level 1 Checkbox should be checked"

    @pytest.mark.asyncio
    async def test_uncheck_level1_checkbox_after_check(self, browser):
        snap = await _snap(browser)
        ref = _find_ref(snap, "Level 1 Checkbox")
        await browser.check_checkbox_or_radio_by_ref(ref)
        await browser.uncheck_checkbox_by_ref(ref)
        loc = await browser.get_element_by_ref(ref)
        assert not await loc.is_checked(), "Level 1 Checkbox should be unchecked"


# ---------------------------------------------------------------------------
# 6. Nth disambiguation — "Shared Button" in three frames
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedIframeNthDisambiguation:
    """'Shared Button' appears in main frame, l1, and l2.
    Verify that each ref correctly targets the element in its own frame."""

    async def _shared_click_counts(self, browser: Browser, snap):
        """Click all three Shared Buttons and return (main_count, l1_count, l2_count)."""
        shared = sorted(
            [(k, d) for k, d in snap.refs.items() if d.name == "Shared Button"],
            key=lambda x: (len(x[1].frame_path or []), x[1].nth or 0)
        )
        assert len(shared) == 3, f"Expected 3 Shared Buttons, got {len(shared)}"
        for ref, _ in shared:
            await browser.click_element_by_ref(ref)
        counts = []
        for ref, _ in shared:
            c = await _get_click_count(browser, ref)
            counts.append(c)
        return counts

    @pytest.mark.asyncio
    async def test_each_shared_button_clicked_exactly_once(self, browser):
        snap = await _snap(browser)
        main_c, l1_c, l2_c = await self._shared_click_counts(browser, snap)
        assert main_c == "1", f"main Shared Button count={main_c}"
        assert l1_c == "1",   f"l1 Shared Button count={l1_c}"
        assert l2_c == "1",   f"l2 Shared Button count={l2_c}"

    @pytest.mark.asyncio
    async def test_shared_button_refs_have_different_frame_paths(self, browser):
        snap = await _snap(browser)
        shared = [d for d in snap.refs.values() if d.name == "Shared Button"]
        paths = [repr(d.frame_path) for d in shared]
        assert len(set(paths)) == 3, (
            f"All 3 Shared Buttons should have distinct frame_paths, got: {paths}"
        )


# ---------------------------------------------------------------------------
# 7. Interactive-mode snapshot includes iframe elements
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNestedIframeInteractiveMode:
    """Interactive snapshot should surface clickable/editable elements from iframes."""

    @pytest.mark.asyncio
    async def test_interactive_snapshot_includes_l1_button(self, browser):
        snap = await browser.get_snapshot(interactive=True, full_page=True)
        names = {d.name for d in snap.refs.values() if d.name}
        assert "Level 1 Button" in names, (
            f"Level 1 Button missing from interactive snapshot: {names}"
        )

    @pytest.mark.asyncio
    async def test_interactive_snapshot_includes_l2_input(self, browser):
        snap = await browser.get_snapshot(interactive=True, full_page=True)
        names = {d.name for d in snap.refs.values() if d.name}
        assert "Level 2 Input" in names, (
            f"Level 2 Input missing from interactive snapshot: {names}"
        )

    @pytest.mark.asyncio
    async def test_interactive_refs_resolve_and_are_clickable(self, browser):
        """Refs from an interactive snapshot should still be resolvable."""
        snap = await browser.get_snapshot(interactive=True, full_page=True)
        ref = _find_ref(snap, "Level 1 Button")
        assert ref
        loc = await browser.get_element_by_ref(ref)
        assert loc is not None
        assert await loc.count() == 1
        assert await loc.is_visible()
