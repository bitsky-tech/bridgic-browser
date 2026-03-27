"""
Integration tests for nth key-space mismatch bug.

Uses tests/fixtures/nth_keyspace_test.html which contains elements where the
same text appears across different roles (div, td, span, button, fieldset, etc.).
These tests verify that clicking a ref targets the correct DOM element, not a
same-text element of a different role.

Fixture page scenarios (1-8):
  1. "Pending" across roles: <td>, <div>, <span>, <div role="button">, <p>, <fieldset>/<div>
  2. Unnamed generics with child text "Item Alpha" / "Item Beta"
  3. Group role: <fieldset> "Billing" + standalone <div> "Billing"
  4. Text pseudo-role: "Ready" in <span>, <button>, <h4>
  5. Table cells with duplicate "$50.00" text + a <div> with same text
  6. Unnamed elements with text content "Save"
  7. Explicit role="generic"
  8. Span as implicit generic: "SpanText" in <span> and <div>

Select/options tests are in test_select_options.py with their own fixture.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from bridgic.browser.session import Browser


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "nth_keyspace_test.html"


@pytest_asyncio.fixture
async def browser():
    """Create a headless browser for nth key-space tests."""
    b = Browser(headless=True, stealth=False, viewport={"width": 1280, "height": 720})
    yield b
    await b.close()


async def _open_and_snapshot(browser: Browser):
    """Navigate to the fixture page and return a fresh snapshot."""
    await browser.navigate_to(f"file://{FIXTURE_PATH}")
    return await browser.get_snapshot()


def _find_ref(snapshot, role: str, name: str, nth: int = 0) -> str | None:
    """Find a ref in the snapshot matching role, name, and nth.

    For duplicate elements, nth starts at 0 for the first occurrence.
    For unique elements (only one with this role:name), nth is None in RefData
    but we match both None and 0 when nth=0 is requested.
    """
    for ref_id, data in snapshot.refs.items():
        if data.role == role and data.name == name:
            actual_nth = data.nth if data.nth is not None else 0
            if actual_nth == nth:
                return ref_id
    return None


async def _click_and_read_log(browser: Browser, ref: str) -> str:
    """Click a ref and return the data-testid from the onclick log."""
    await browser.click_element_by_ref(ref)
    # The onclick handler writes to #log; read its first entry
    page = browser._context.pages[0]
    log_text = await page.evaluate(
        "document.querySelector('#log .entry').textContent"
    )
    # Format: "HH:MM:SS PM → div[data-testid=div-pending-0]"
    if "→" in log_text:
        return log_text.split("→", 1)[1].strip()
    return log_text


# ---------------------------------------------------------------------------
# Scenario 1: generic "Pending" must NOT click <td>, <span>, <button>, <p>
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGenericPendingNth:
    """Verify generic "Pending" [nth=N] clicks the correct <div>."""

    @pytest.mark.asyncio
    async def test_generic_pending_nth0(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "generic", "Pending", nth=0)
        assert ref, "generic 'Pending' (first occurrence) not found"
        result = await _click_and_read_log(browser, ref)
        assert "div-pending-0" in result, f"Expected div-pending-0, got: {result}"

    @pytest.mark.asyncio
    async def test_generic_pending_nth1(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "generic", "Pending", nth=1)
        assert ref, "generic 'Pending' nth=1 not found"
        result = await _click_and_read_log(browser, ref)
        assert "div-pending-1" in result, f"Expected div-pending-1, got: {result}"

    @pytest.mark.asyncio
    async def test_generic_pending_nth2_in_fieldset(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "generic", "Pending", nth=2)
        assert ref, "generic 'Pending' nth=2 not found"
        result = await _click_and_read_log(browser, ref)
        assert "div-pending-2-in-fieldset" in result, f"Expected div-pending-2-in-fieldset, got: {result}"

    @pytest.mark.asyncio
    async def test_button_pending(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "button", "Pending", nth=0)
        assert ref, "button 'Pending' not found"
        result = await _click_and_read_log(browser, ref)
        assert "btn-pending" in result, f"Expected btn-pending, got: {result}"


# ---------------------------------------------------------------------------
# Scenario 2: Unnamed generics — child text anchoring
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestUnnamedGenericChildText:
    """Verify unnamed generics with child text click the correct container."""

    @pytest.mark.asyncio
    async def test_item_alpha_nth0(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "generic", "Item Alpha", nth=0)
        assert ref, "generic 'Item Alpha' nth=0 not found"
        result = await _click_and_read_log(browser, ref)
        assert "unnamed-div-0" in result, f"Expected unnamed-div-0, got: {result}"

    @pytest.mark.asyncio
    async def test_item_alpha_nth1(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "generic", "Item Alpha", nth=1)
        assert ref, "generic 'Item Alpha' nth=1 not found"
        result = await _click_and_read_log(browser, ref)
        assert "unnamed-div-2" in result, f"Expected unnamed-div-2, got: {result}"


# ---------------------------------------------------------------------------
# Scenario 3: Group role — <legend> counted as generic in a11y tree
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGroupRoleBilling:
    """Verify generic "Billing" [nth=1] clicks the standalone <div>, not the <legend>."""

    @pytest.mark.asyncio
    async def test_generic_billing_nth1(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "generic", "Billing", nth=1)
        assert ref, "generic 'Billing' nth=1 not found"
        result = await _click_and_read_log(browser, ref)
        assert "div-billing" in result, f"Expected div-billing, got: {result}"


# ---------------------------------------------------------------------------
# Scenario 5: Table cells — cell "Pending" must not click <div> "Pending"
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCellNth:
    """Verify cell refs target the correct <td>, not other elements with same text."""

    @pytest.mark.asyncio
    async def test_cell_pending_nth0(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "cell", "Pending", nth=0)
        assert ref, "cell 'Pending' nth=0 not found"
        result = await _click_and_read_log(browser, ref)
        assert "td-pending-0" in result, f"Expected td-pending-0, got: {result}"

    @pytest.mark.asyncio
    async def test_cell_pending_nth1(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "cell", "Pending", nth=1)
        assert ref, "cell 'Pending' nth=1 not found"
        result = await _click_and_read_log(browser, ref)
        assert "td-pending-1" in result, f"Expected td-pending-1, got: {result}"

    @pytest.mark.asyncio
    async def test_cell_fifty_nth0(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "cell", "$50.00", nth=0)
        assert ref, "cell '$50.00' nth=0 not found"
        result = await _click_and_read_log(browser, ref)
        assert "cell-amount-0" in result, f"Expected cell-amount-0, got: {result}"

    @pytest.mark.asyncio
    async def test_cell_fifty_nth1(self, browser):
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "cell", "$50.00", nth=1)
        assert ref, "cell '$50.00' nth=1 not found"
        result = await _click_and_read_log(browser, ref)
        assert "cell-amount-1" in result, f"Expected cell-amount-1, got: {result}"

    @pytest.mark.asyncio
    async def test_generic_fifty_not_confused_with_cell(self, browser):
        """generic "$50.00" must click the <div>, not a <td>."""
        snapshot = await _open_and_snapshot(browser)
        ref = _find_ref(snapshot, "generic", "$50.00", nth=0)
        assert ref, "generic '$50.00' not found"
        result = await _click_and_read_log(browser, ref)
        assert "div-fifty" in result, f"Expected div-fifty, got: {result}"


# ---------------------------------------------------------------------------
# Snapshot structure sanity checks
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSnapshotStructure:
    """Verify the snapshot assigns correct roles and nth values."""

    @pytest.mark.asyncio
    async def test_generic_pending_count(self, browser):
        """There should be exactly 3 generic 'Pending' refs (nth 0, 1, 2)."""
        snapshot = await _open_and_snapshot(browser)
        pending_generics = [
            (ref, data) for ref, data in snapshot.refs.items()
            if data.role == "generic" and data.name == "Pending"
        ]
        assert len(pending_generics) == 3, (
            f"Expected 3 generic 'Pending', got {len(pending_generics)}: "
            f"{[(r, d.nth) for r, d in pending_generics]}"
        )
        nths = sorted(d.nth or 0 for _, d in pending_generics)
        assert nths == [0, 1, 2], f"Expected nth [0,1,2], got {nths}"

    @pytest.mark.asyncio
    async def test_cell_pending_count(self, browser):
        """Scenario 1 table has 2 cell 'Pending' (nth 0, 1).
        Scenario 5 table has 2 more cell 'Pending' (nth 2, 3)."""
        snapshot = await _open_and_snapshot(browser)
        pending_cells = [
            (ref, data) for ref, data in snapshot.refs.items()
            if data.role == "cell" and data.name == "Pending"
        ]
        assert len(pending_cells) == 4, (
            f"Expected 4 cell 'Pending', got {len(pending_cells)}: "
            f"{[(r, d.nth) for r, d in pending_cells]}"
        )

    @pytest.mark.asyncio
    async def test_text_pending_is_span(self, browser):
        """The <span>Pending</span> should get text role, NOT generic."""
        snapshot = await _open_and_snapshot(browser)
        text_pending = [
            (ref, data) for ref, data in snapshot.refs.items()
            if data.role == "text" and data.name == "Pending"
        ]
        assert len(text_pending) >= 1, "Expected at least 1 text 'Pending' (the <span>)"

    @pytest.mark.asyncio
    async def test_generic_billing_count(self, browser):
        """There should be 2 generic 'Billing': one <legend> (nth=0) and one <div> (nth=1)."""
        snapshot = await _open_and_snapshot(browser)
        billing_generics = [
            (ref, data) for ref, data in snapshot.refs.items()
            if data.role == "generic" and data.name == "Billing"
        ]
        assert len(billing_generics) == 2, (
            f"Expected 2 generic 'Billing', got {len(billing_generics)}: "
            f"{[(r, d.nth) for r, d in billing_generics]}"
        )
