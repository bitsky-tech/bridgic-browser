"""
Integration tests for select & options workflow.

Uses tests/fixtures/select_options_test.html which contains:
  - Section 1: Native <select> dropdowns (Status, Priority)
  - Section 2: Portalized dropdown (vanilla aria-controls)
  - Section 3: React + antd Select components (fruit, color, tags)

Tests cover the two-step options → select workflow for both native and
non-native (portalized / antd) dropdowns.
"""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from bridgic.browser.session import Browser


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "select_options_test.html"


@pytest_asyncio.fixture
async def browser():
    """Create a headless browser for select/options tests."""
    b = Browser(headless=True, stealth=False, viewport={"width": 1280, "height": 720})
    yield b
    await b.close()


async def _open_and_snapshot(browser: Browser):
    """Navigate to the fixture page and return a fresh snapshot."""
    await browser.navigate_to(f"file://{FIXTURE_PATH}")
    # Wait a bit for React + antd to render via Babel standalone
    await asyncio.sleep(2)
    return await browser.get_snapshot()


def _find_ref(snapshot, role: str, name: str, nth: int = 0) -> str | None:
    """Find a ref in the snapshot matching role, name, and nth."""
    for ref_id, data in snapshot.refs.items():
        if data.role == role and data.name == name:
            actual_nth = data.nth if data.nth is not None else 0
            if actual_nth == nth:
                return ref_id
    return None



# ---------------------------------------------------------------------------
# Section 1: Native <select> dropdowns
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNativeSelectWorkflow:
    """Verify the options → select two-step pattern for native <select> dropdowns."""

    @pytest.mark.asyncio
    async def test_options_lists_all_choices(self, browser):
        """options(ref) should return all <option> texts for a <select>."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_ref(snapshot, "combobox", "Status:")
        assert combo_ref, "combobox 'Status:' not found"

        result = await browser.get_dropdown_options_by_ref(combo_ref)
        assert "All" in result
        assert "Pending" in result
        assert "Active" in result
        assert "Resolved" in result

    @pytest.mark.asyncio
    async def test_select_changes_value(self, browser):
        """select(ref, text) should change the <select> value."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_ref(snapshot, "combobox", "Status:")
        assert combo_ref, "combobox 'Status:' not found"

        result = await browser.select_dropdown_option_by_ref(combo_ref, "Active")
        assert "error" not in result.lower(), f"select failed: {result}"

        page = browser._context.pages[0]
        value = await page.evaluate(
            "document.getElementById('status-filter').value"
        )
        assert value == "active", f"Expected 'active', got '{value}'"

    @pytest.mark.asyncio
    async def test_select_by_value(self, browser):
        """select(ref, value) should also work when text doesn't match label."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_ref(snapshot, "combobox", "Status:")
        assert combo_ref

        result = await browser.select_dropdown_option_by_ref(combo_ref, "resolved")
        assert "error" not in result.lower(), f"select by value failed: {result}"

        page = browser._context.pages[0]
        value = await page.evaluate(
            "document.getElementById('status-filter').value"
        )
        assert value == "resolved", f"Expected 'resolved', got '{value}'"

    @pytest.mark.asyncio
    async def test_snapshot_reflects_selection(self, browser):
        """After select, snapshot should show [selected] on the chosen option."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_ref(snapshot, "combobox", "Status:")
        assert combo_ref

        await browser.select_dropdown_option_by_ref(combo_ref, "Active")

        new_snapshot = await browser.get_snapshot()
        found_selected = False
        for line in new_snapshot.tree.split("\n"):
            if 'option "Active"' in line and "[selected]" in line:
                found_selected = True
                break
        assert found_selected, "option 'Active' should have [selected] after selection"

    @pytest.mark.asyncio
    async def test_second_dropdown_independent(self, browser):
        """Selecting in one dropdown must not affect the other."""
        snapshot = await _open_and_snapshot(browser)
        status_ref = _find_ref(snapshot, "combobox", "Status:")
        priority_ref = _find_ref(snapshot, "combobox", "Priority:")
        assert status_ref and priority_ref

        await browser.select_dropdown_option_by_ref(status_ref, "Active")
        await browser.select_dropdown_option_by_ref(priority_ref, "High")

        page = browser._context.pages[0]
        status_val = await page.evaluate(
            "document.getElementById('status-filter').value"
        )
        priority_val = await page.evaluate(
            "document.getElementById('priority-filter').value"
        )
        assert status_val == "active", f"Status should be 'active', got '{status_val}'"
        assert priority_val == "high", f"Priority should be 'high', got '{priority_val}'"

    @pytest.mark.asyncio
    async def test_option_all_count(self, browser):
        """Both dropdowns have an 'All' option — should be 2 option 'All' refs."""
        snapshot = await _open_and_snapshot(browser)
        all_options = [
            (ref, data) for ref, data in snapshot.refs.items()
            if data.role == "option" and data.name == "All"
        ]
        assert len(all_options) == 2, (
            f"Expected 2 option 'All' (one per dropdown), got {len(all_options)}"
        )


# ---------------------------------------------------------------------------
# Section 2: Portalized dropdown (vanilla aria-controls)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPortalizedDropdown:
    """Verify options/select for non-native dropdowns with portalized listbox."""

    @pytest.mark.asyncio
    async def test_options_via_aria_controls(self, browser):
        """options() should find choices in a portalized listbox via aria-controls."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_ref(snapshot, "combobox", "City")
        assert combo_ref, "combobox 'City' not found"

        # Expand the dropdown first (portalized options may be display:none)
        await browser.click_element_by_ref(combo_ref)

        result = await browser.get_dropdown_options_by_ref(combo_ref)
        assert "Shanghai" in result, f"Expected 'Shanghai' in options, got: {result}"
        assert "Beijing" in result
        assert "Shenzhen" in result

    @pytest.mark.asyncio
    async def test_select_portalized_option(self, browser):
        """select() should click the correct portalized option."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_ref(snapshot, "combobox", "City")
        assert combo_ref

        result = await browser.select_dropdown_option_by_ref(combo_ref, "Beijing")
        assert "error" not in result.lower(), f"select failed: {result}"

        page = browser._context.pages[0]
        display_text = await page.evaluate(
            "document.getElementById('portal-display').textContent"
        )
        assert display_text == "Beijing", f"Expected 'Beijing', got '{display_text}'"

    @pytest.mark.asyncio
    async def test_select_portalized_then_another(self, browser):
        """Selecting twice should work — dropdown re-opens and picks new option."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_ref(snapshot, "combobox", "City")
        assert combo_ref

        await browser.select_dropdown_option_by_ref(combo_ref, "Shanghai")
        result = await browser.select_dropdown_option_by_ref(combo_ref, "Guangzhou")
        assert "error" not in result.lower(), f"second select failed: {result}"

        page = browser._context.pages[0]
        display_text = await page.evaluate(
            "document.getElementById('portal-display').textContent"
        )
        assert display_text == "Guangzhou", f"Expected 'Guangzhou', got '{display_text}'"


# ---------------------------------------------------------------------------
# Section 3: React + antd Select components
# ---------------------------------------------------------------------------

def _find_unnamed_combobox(snapshot, nth: int) -> str | None:
    """Find an unnamed combobox by nth index.

    antd Select components render as combobox with name=None.
    On the fixture page: nth=0 → fruit, nth=1 → color, nth=2 → tags.
    """
    for ref_id, data in snapshot.refs.items():
        if data.role == "combobox" and not data.name:
            actual_nth = data.nth if data.nth is not None else 0
            if actual_nth == nth:
                return ref_id
    return None


@pytest.mark.integration
class TestAntdSelect:
    """Verify options/select for antd Select components.

    antd Select renders:
    - A combobox trigger with role="combobox" (name=None, placeholder as child)
    - A portalized dropdown with role="listbox" and role="option" children
    - The dropdown is rendered to <body> (getPopupContainer)

    On the fixture page, unnamed comboboxes map to:
      nth=0 → fruit (single select)
      nth=1 → color (single select with search)
      nth=2 → tags (multiple select)
    """

    @pytest.mark.asyncio
    async def test_antd_comboboxes_visible(self, browser):
        """The antd Select components should appear as unnamed comboboxes."""
        snapshot = await _open_and_snapshot(browser)
        unnamed = [
            (ref, data) for ref, data in snapshot.refs.items()
            if data.role == "combobox" and not data.name
        ]
        assert len(unnamed) >= 3, (
            f"Expected at least 3 unnamed comboboxes (fruit, color, tags), "
            f"got {len(unnamed)}: {[(r, d.nth) for r, d in unnamed]}"
        )

    @pytest.mark.asyncio
    async def test_antd_fruit_options(self, browser):
        """Clicking antd fruit Select should reveal fruit options."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_unnamed_combobox(snapshot, nth=0)
        assert combo_ref, "Unnamed combobox nth=0 (fruit) not found"

        # Click to open the dropdown
        await browser.click_element_by_ref(combo_ref)
        await asyncio.sleep(0.5)

        result = await browser.get_dropdown_options_by_ref(combo_ref)
        assert "Apple" in result, f"Expected 'Apple' in options, got: {result}"
        assert "Cherry" in result, f"Expected 'Cherry' in options, got: {result}"

    @pytest.mark.asyncio
    async def test_antd_fruit_select_option(self, browser):
        """Selecting a fruit option should update the display."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_unnamed_combobox(snapshot, nth=0)
        assert combo_ref, "Unnamed combobox nth=0 (fruit) not found"

        # Open dropdown first — antd renders options asynchronously via React
        await browser.click_element_by_ref(combo_ref)
        await asyncio.sleep(0.5)

        result = await browser.select_dropdown_option_by_ref(combo_ref, "Cherry")
        assert "error" not in result.lower(), f"select failed: {result}"

        page = browser._context.pages[0]
        display_text = await page.evaluate(
            "document.getElementById('fruit-display')?.textContent || ''"
        )
        assert "cherry" in display_text.lower(), (
            f"Expected 'cherry' in display, got: '{display_text}'. Select result: {result}"
        )

    @pytest.mark.asyncio
    async def test_antd_tags_multi_select(self, browser):
        """antd multi-select should allow selecting multiple tags."""
        snapshot = await _open_and_snapshot(browser)
        combo_ref = _find_unnamed_combobox(snapshot, nth=2)
        if not combo_ref:
            pytest.skip("Unnamed combobox nth=2 (tags) not found — antd may not have loaded")

        # Open dropdown first — antd renders options asynchronously
        await browser.click_element_by_ref(combo_ref)
        await asyncio.sleep(0.5)

        # Select first tag
        await browser.select_dropdown_option_by_ref(combo_ref, "Frontend")
        await asyncio.sleep(0.5)

        # Select second tag (dropdown may close after first selection,
        # click again to re-open for multi-select)
        await browser.click_element_by_ref(combo_ref)
        await asyncio.sleep(0.5)
        await browser.select_dropdown_option_by_ref(combo_ref, "Backend")
        await asyncio.sleep(0.3)

        page = browser._context.pages[0]
        display_text = await page.evaluate(
            "document.getElementById('tags-display')?.textContent || ''"
        )
        assert "frontend" in display_text.lower() and "backend" in display_text.lower(), (
            f"Expected 'frontend' and 'backend' in display, got: '{display_text}'"
        )
