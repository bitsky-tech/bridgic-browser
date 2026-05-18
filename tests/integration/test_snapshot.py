"""
Integration tests for the snapshot pipeline.

Uses a real browser against test_page.html to verify that the snapshot
pipeline correctly:
  - Extracts element refs and names
  - Detects interactive elements (cursor:pointer, event handlers, ARIA roles)
  - Filters by viewport vs full-page mode
  - Handles disabled elements
  - Produces correct name deduplication
  - Supports locator resolution for all element types
"""

import asyncio
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

from bridgic.browser.session import Browser


# ==================== Constants ====================

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
TEST_PAGE_PATH = FIXTURES_DIR / "test_page.html"
ROLE_TEXT_MATCH_CASE_PATH = FIXTURES_DIR / "role_text_match_case.html"


# ==================== Helpers ====================

_REF_PATTERN = re.compile(
    r'- (\w+)\s+"([^"]*?)"\s+\[ref=([a-zA-Z0-9]+)\]'    # type "name" [ref=XXXX]
    r'|'
    r'- (\w+)\s+\[ref=([a-zA-Z0-9]+)\]'                   # type [ref=XXXX]  (unnamed)
)

_SUFFIX_PATTERN = re.compile(r'\[ref=[a-zA-Z0-9]+\]\s*(.*)')


def parse_snapshot(snapshot: str) -> Dict[str, dict]:
    """Parse snapshot text into {ref: {type, name, line, suffix}} dict."""
    refs: Dict[str, dict] = {}
    for line in snapshot.splitlines():
        m = _REF_PATTERN.search(line)
        if not m:
            continue
        if m.group(1):  # named element
            elem_type, name, ref = m.group(1), m.group(2), m.group(3)
        else:           # unnamed element
            elem_type, ref = m.group(4), m.group(5)
            name = ""
        suffix_m = _SUFFIX_PATTERN.search(line)
        suffix = suffix_m.group(1).strip() if suffix_m else ""
        refs[ref] = {
            "type": elem_type,
            "name": name,
            "suffix": suffix,
            "line": line.strip(),
        }
    return refs


def refs_by_type(refs: Dict[str, dict], elem_type: str) -> List[dict]:
    return [r for r in refs.values() if r["type"].lower() == elem_type.lower()]


def find_ref(refs: Dict[str, dict], elem_type: str, name_contains: str = "") -> Optional[str]:
    """Find a ref by accessible role and (optionally) a name substring.

    ``name_contains`` does case-insensitive substring matching. Callers that
    need precise matching (e.g. to distinguish ``"Item 1"`` from
    ``"Drag Item 1"``) should use :func:`find_ref_exact`.
    """
    for ref, info in refs.items():
        if info["type"].lower() == elem_type.lower():
            if not name_contains or name_contains.lower() in info["name"].lower():
                return ref
    return None


def find_ref_exact(refs: Dict[str, dict], elem_type: str, name: str) -> Optional[str]:
    """Find a ref by accessible role and exact (case-insensitive) name."""
    target = name.lower()
    for ref, info in refs.items():
        if info["type"].lower() == elem_type.lower() and info["name"].lower() == target:
            return ref
    return None


def all_names(refs: Dict[str, dict]) -> Set[str]:
    return {r["name"] for r in refs.values() if r["name"]}


# ==================== Fixtures ====================

@pytest_asyncio.fixture
async def browser():
    """Real browser on the test page."""
    b = Browser(headless=True, stealth=False, viewport={"width": 1280, "height": 720})
    await b.navigate_to(f"file://{TEST_PAGE_PATH.absolute()}")
    await asyncio.sleep(0.3)
    yield b
    await b.close()


@pytest_asyncio.fixture
async def role_text_match_browser():
    """Real browser on role_text_match_case.html."""
    b = Browser(headless=True, stealth=False, viewport={"width": 1280, "height": 720})
    await b.navigate_to(f"file://{ROLE_TEXT_MATCH_CASE_PATH.absolute()}")
    await asyncio.sleep(0.3)
    yield b
    await b.close()


# ==================== Snapshot Mode Tests ====================

class TestSnapshotModes:
    """Test the 4 snapshot parameter combinations."""

    @pytest.mark.asyncio
    async def test_default_mode_contains_viewport_elements(self, browser):
        """Default mode (interactive=False, full_page=False) shows all
        element types in the viewport — headings, links, textboxes, labels."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=False)
        refs = parse_snapshot(snap)

        # Viewport should contain the top navigation links
        assert find_ref(refs, "link", "Go to Form Section")
        assert find_ref(refs, "link", "External Link")

        # Viewport should contain the form textboxes
        assert find_ref(refs, "textbox", "Username")
        assert find_ref(refs, "textbox", "Email")
        assert find_ref(refs, "textbox", "Password")

        # Headings in viewport
        assert find_ref(refs, "heading", "Navigation")
        assert find_ref(refs, "heading", "Form Elements")

    @pytest.mark.asyncio
    async def test_default_mode_excludes_below_viewport(self, browser):
        """Default mode should NOT contain interactive controls far below the
        viewport.

        Non-interactive ``generic`` leaves (grid items, scroll markers, etc.)
        are intentionally "assumed in-viewport" for performance — only refs
        with roles in ``INTERACTIVE_ROLES`` or ``VIEWPORT_CONTAINER_ROLES``
        get precise getBoundingClientRect checks. See ``_pre_filter_raw_snapshot``
        design notes in ``_snapshot.py`` for rationale.
        """
        snap = await browser.get_snapshot_text(interactive=False, full_page=False)
        refs = parse_snapshot(snap)

        # Interactive controls below viewport must be excluded — they go
        # through the control_leaf visibility check.
        assert not find_ref_exact(refs, "button", "Show Alert"), \
            "Dialog buttons should be excluded from viewport-only mode"
        assert not find_ref_exact(refs, "button", "Toggle Element"), \
            "Visibility toggle button should be excluded from viewport-only mode"
        assert not find_ref_exact(refs, "button", "Disabled Button"), \
            "Disabled section button should be excluded from viewport-only mode"

    @pytest.mark.asyncio
    async def test_interactive_mode_only_actionable(self, browser):
        """Interactive mode (interactive=True, full_page=False) shows only
        interactive elements in viewport — no headings, no paragraphs."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=False)
        refs = parse_snapshot(snap)

        # Interactive elements in viewport
        assert find_ref(refs, "link", "Go to Form Section")
        assert find_ref(refs, "textbox", "Username")

        # Non-interactive elements should be excluded
        assert not find_ref(refs, "heading"), \
            "Headings should be excluded in interactive mode"
        assert not find_ref(refs, "paragraph"), \
            "Paragraphs should be excluded in interactive mode"

    @pytest.mark.asyncio
    async def test_full_page_mode_includes_all_sections(self, browser):
        """Full page mode (interactive=False, full_page=True) includes
        elements from ALL sections of the page."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        refs = parse_snapshot(snap)

        # Elements from every section
        assert find_ref(refs, "heading", "Navigation")
        assert find_ref(refs, "heading", "Form Elements")
        assert find_ref(refs, "heading", "Checkboxes")
        assert find_ref(refs, "heading", "Buttons")
        assert find_ref(refs, "heading", "Hover")
        assert find_ref(refs, "heading", "Keyboard")
        assert find_ref(refs, "heading", "Drag")
        assert find_ref(refs, "heading", "Scroll")
        assert find_ref(refs, "heading", "Visibility")
        assert find_ref(refs, "heading", "Dialogs")
        assert find_ref(refs, "heading", "Grid")
        assert find_ref(refs, "heading", "Disabled")
        assert find_ref(refs, "heading", "Verification")

        # Grid items and scroll markers present in full-page mode
        assert find_ref(refs, "generic", "Item 1")
        assert find_ref(refs, "generic", "Marker 1")

    @pytest.mark.asyncio
    async def test_interactive_full_page_comprehensive(self, browser):
        """Interactive + full_page mode is the most comprehensive — includes
        all interactive elements from the entire page."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        # All links
        links = refs_by_type(refs, "link")
        assert len(links) == 4, f"Expected 4 links, got {len(links)}"

        # All textboxes (Username, Email, Password, Message, Disabled, Verify)
        textboxes = refs_by_type(refs, "textbox")
        assert len(textboxes) >= 5, f"Expected >=5 textboxes, got {len(textboxes)}"

        # All checkboxes (Technology, Music, Sports, Art)
        checkboxes = refs_by_type(refs, "checkbox")
        assert len(checkboxes) == 4, f"Expected 4 checkboxes, got {len(checkboxes)}"

        # All radios (Male, Female, Other)
        radios = refs_by_type(refs, "radio")
        assert len(radios) == 3, f"Expected 3 radios, got {len(radios)}"

        # All options (6: Select a country + 5 countries)
        options = refs_by_type(refs, "option")
        assert len(options) == 6, f"Expected 6 options, got {len(options)}"

        # Buttons: Primary, Success, Danger, Warning, +1, -1, Reset(x2),
        #          File Upload, Submit, Toggle Element, Show Alert/Confirm/Prompt,
        #          Disabled, Toggle Disabled
        buttons = refs_by_type(refs, "button")
        assert len(buttons) >= 14, f"Expected >=14 buttons, got {len(buttons)}"

        # No headings or paragraphs in interactive mode
        assert not refs_by_type(refs, "heading")
        assert not refs_by_type(refs, "paragraph")


# ==================== Interactive Element Detection ====================

class TestInteractiveDetection:
    """Verify that elements with various interactivity signals are correctly
    detected and included in interactive snapshots."""

    @pytest.mark.asyncio
    async def test_cursor_pointer_elements_detected(self, browser):
        """Elements with cursor:pointer CSS should be detected as interactive."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        # Grid items have cursor:pointer via CSS class .grid-item
        for i in range(1, 7):
            ref = find_ref(refs, "generic", f"Item {i}")
            assert ref is not None, f"Grid Item {i} with cursor:pointer not detected"

        # Double-click area has cursor:pointer via CSS class .double-click-area
        assert find_ref(refs, "generic", "Double-click me!"), \
            "Double-click area with cursor:pointer not detected"

    @pytest.mark.asyncio
    async def test_event_handler_elements_detected(self, browser):
        """Elements with inline event handlers (onclick, onmouseenter, etc.)
        should be detected as interactive."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        # Hover target has onmouseenter/onmouseleave
        assert find_ref(refs, "generic", "Hover over me!"), \
            "Element with onmouseenter handler not detected as interactive"

        # Double-click area has ondblclick
        assert find_ref(refs, "generic", "Double-click me!"), \
            "Element with ondblclick handler not detected as interactive"

    @pytest.mark.asyncio
    async def test_focusable_generic_with_keyboard_handlers_detected(self, browser):
        """Focusable generics should survive interactive pre-filter fallback."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        assert find_ref(refs, "generic", "Press a key..."), \
            "Focusable key-display generic should be in interactive snapshot"

    @pytest.mark.asyncio
    async def test_disabled_elements_included_with_flag(self, browser):
        """Disabled elements should be included in interactive snapshot
        with [disabled] attribute."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        # Disabled button
        disabled_btn = find_ref(refs, "button", "Disabled Button")
        assert disabled_btn is not None, "Disabled button should be in interactive snapshot"
        assert "[disabled]" in refs[disabled_btn]["suffix"], \
            "Disabled button should have [disabled] attribute"

        # Disabled input
        disabled_input = find_ref(refs, "textbox", "Disabled Input")
        assert disabled_input is not None, "Disabled textbox should be in interactive snapshot"
        assert "[disabled]" in refs[disabled_input]["suffix"], \
            "Disabled textbox should have [disabled] attribute"

    @pytest.mark.asyncio
    async def test_file_upload_button_detected(self, browser):
        """input[type=file] should appear as a button with 'File Upload' name."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        file_btn = find_ref(refs, "button", "File Upload")
        assert file_btn is not None, "File upload button not found in interactive snapshot"


# ==================== Name Deduplication ====================

class TestNameDedup:
    """Verify that name deduplication works — elements should NOT have their
    name repeated in the suffix text."""

    @pytest.mark.asyncio
    async def test_no_name_duplication_on_cursor_pointer_generics(self, browser):
        """generic "Item 1" [ref=eXX] [cursor=pointer] should NOT end with
        ': Item 1' or ': "Item 1"'."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        refs = parse_snapshot(snap)

        for i in range(1, 7):
            ref = find_ref(refs, "generic", f"Item {i}")
            if ref:
                line = refs[ref]["line"]
                # Check no ': Item N' after the ref
                assert f': Item {i}' not in line.split(']')[-1], \
                    f"Name duplicated in suffix for Item {i}: {line}"
                assert f': "Item {i}"' not in line.split(']')[-1], \
                    f"Quoted name duplicated in suffix for Item {i}: {line}"

    @pytest.mark.asyncio
    async def test_no_name_duplication_on_double_click_area(self, browser):
        """generic "Double-click me! (Count: 0)" should not have duplicated text."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        refs = parse_snapshot(snap)

        ref = find_ref(refs, "generic", "Double-click me!")
        assert ref is not None
        line = refs[ref]["line"]
        # The suffix should NOT contain a second copy of the name
        parts = line.split(']')
        last_part = parts[-1] if parts else ""
        assert 'Double-click me!' not in last_part, \
            f"Name duplicated in suffix: {line}"

    @pytest.mark.asyncio
    async def test_no_name_duplication_on_escaped_quotes(self, browser):
        """Elements with escaped quotes in name should also be deduped."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)

        # The element: textbox "Type \"hello\" to verify:"
        # Check the line containing this ref doesn't have duplicated text
        for line in snap.splitlines():
            if 'Type \\"hello\\"' in line and '[ref=' in line:
                parts = line.split(']')
                last_part = parts[-1] if parts else ""
                assert 'Type \\"hello\\"' not in last_part or '/placeholder' in last_part, \
                    f"Escaped-quote name duplicated: {line}"


# ==================== Ref Structure & Metadata ====================

class TestRefStructure:
    """Verify structural properties of the snapshot output."""

    @pytest.mark.asyncio
    async def test_all_refs_are_unique(self, browser):
        """Every [ref=eXX] should appear exactly once in the snapshot."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        ref_ids = re.findall(r'\[ref=(e\d+)\]', snap)
        assert len(ref_ids) == len(set(ref_ids)), \
            f"Duplicate refs found: {[r for r in ref_ids if ref_ids.count(r) > 1]}"

    @pytest.mark.asyncio
    async def test_combobox_has_nested_options(self, browser):
        """Combobox should contain nested options with proper indentation."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        lines = snap.splitlines()

        combobox_idx = None
        for i, line in enumerate(lines):
            if 'combobox "Country"' in line:
                combobox_idx = i
                break
        assert combobox_idx is not None, "Country combobox not found"

        # Next lines should be indented options
        for offset in range(1, 7):
            next_line = lines[combobox_idx + offset]
            assert 'option' in next_line, \
                f"Expected option at line {combobox_idx + offset}, got: {next_line}"

    @pytest.mark.asyncio
    async def test_links_have_url_metadata(self, browser):
        """Links should include /url metadata."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        lines = snap.splitlines()

        for i, line in enumerate(lines):
            if 'link "Go to Form Section"' in line:
                # Next line should contain /url
                next_line = lines[i + 1]
                assert '/url' in next_line, f"Link missing /url metadata: {next_line}"
                assert '#form-section' in next_line
                break
        else:
            pytest.fail("Link 'Go to Form Section' not found")

    @pytest.mark.asyncio
    async def test_textbox_has_placeholder_metadata(self, browser):
        """Textboxes should include /placeholder metadata."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        lines = snap.splitlines()

        for i, line in enumerate(lines):
            if 'textbox "Username"' in line:
                next_line = lines[i + 1]
                assert '/placeholder' in next_line
                assert 'Enter username' in next_line
                break
        else:
            pytest.fail("Textbox 'Username' not found")

    @pytest.mark.asyncio
    async def test_nth_attribute_for_duplicate_names(self, browser):
        """Elements with duplicate role+name should get [nth=N] attribute."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)

        # There are two "Reset" buttons: form reset and counter reset
        reset_lines = [
            l for l in snap.splitlines()
            if 'button "Reset"' in l and '[ref=' in l
        ]
        assert len(reset_lines) == 2, f"Expected 2 Reset buttons, got {len(reset_lines)}"

        # First one has no [nth], second one has [nth=1]
        assert '[nth=' not in reset_lines[0], \
            f"First Reset button should not have [nth]: {reset_lines[0]}"
        assert '[nth=1]' in reset_lines[1], \
            f"Second Reset button should have [nth=1]: {reset_lines[1]}"

    @pytest.mark.asyncio
    async def test_selected_option_marked(self, browser):
        """Default selected option should have [selected] attribute."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)

        for line in snap.splitlines():
            if 'option "Select a country"' in line:
                assert '[selected]' in line, \
                    f"Default option should be marked [selected]: {line}"
                break
        else:
            pytest.fail("Default option 'Select a country' not found")


# ==================== Locator Resolution ====================

class TestLocatorResolution:
    """Verify that refs from the snapshot can actually be resolved back to
    locators and used for actions on the page."""

    @pytest.mark.asyncio
    async def test_click_button_by_ref(self, browser):
        """Button ref from interactive snapshot should resolve to a clickable locator."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        page = await browser.get_current_page()
        before = await page.text_content("#counter-value")

        btn_ref = find_ref(refs, "button", "+1")
        assert btn_ref is not None
        result = await browser.click_element_by_ref(btn_ref)
        assert "error" not in result.lower(), f"Click failed: {result}"

        after = await page.text_content("#counter-value")
        assert int(after) == int(before) + 1

    @pytest.mark.asyncio
    async def test_input_text_by_ref(self, browser):
        """Textbox ref should resolve and accept input."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        tb_ref = find_ref(refs, "textbox", "Username")
        assert tb_ref is not None
        await browser.input_text_by_ref(tb_ref, "integration_test")
        result = await browser.verify_value(tb_ref, "integration_test")
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_check_uncheck_by_ref(self, browser):
        """Checkbox ref should support check/uncheck operations."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        cb_ref = find_ref(refs, "checkbox", "Technology")
        assert cb_ref is not None

        # Initially unchecked
        result = await browser.verify_element_state(cb_ref, "unchecked")
        assert "PASS" in result

        # Check
        await browser.check_checkbox_or_radio_by_ref(cb_ref)
        result = await browser.verify_element_state(cb_ref, "checked")
        assert "PASS" in result

        # Uncheck
        await browser.uncheck_checkbox_by_ref(cb_ref)
        result = await browser.verify_element_state(cb_ref, "unchecked")
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_double_click_generic_by_ref(self, browser):
        """generic element ref (e.g., Double-click area) should resolve
        to a valid locator for double-click."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        page = await browser.get_current_page()
        before = await page.text_content("#double-click-count")

        dbl_ref = find_ref(refs, "generic", "Double-click me!")
        assert dbl_ref is not None, "Double-click area not found in interactive snapshot"
        result = await browser.double_click_element_by_ref(dbl_ref)
        assert "error" not in result.lower(), f"Double-click failed: {result}"

        after = await page.text_content("#double-click-count")
        assert int(after) > int(before)

    @pytest.mark.asyncio
    async def test_hover_generic_by_ref(self, browser):
        """generic element ref with onmouseenter should resolve for hover."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        hover_ref = find_ref(refs, "generic", "Hover over me!")
        assert hover_ref is not None, \
            "Hover target with onmouseenter not found in interactive snapshot"

        await browser.scroll_to_text("Hover over me!")
        result = await browser.hover_element_by_ref(hover_ref)
        assert "error" not in result.lower(), f"Hover failed: {result}"

    @pytest.mark.asyncio
    async def test_select_dropdown_by_ref(self, browser):
        """Combobox ref should support option selection."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        combo_ref = find_ref(refs, "combobox", "Country")
        assert combo_ref is not None

        # Get options
        options_result = await browser.get_dropdown_options_by_ref(combo_ref)
        assert "United States" in options_result

        # Select an option
        result = await browser.select_dropdown_option_by_ref(
            combo_ref, "United States"
        )
        assert "error" not in result.lower(), f"Select failed: {result}"

    @pytest.mark.asyncio
    async def test_focus_element_by_ref(self, browser):
        """Textbox ref should support focus operation."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        tb_ref = find_ref(refs, "textbox", "Email")
        assert tb_ref is not None
        result = await browser.focus_element_by_ref(tb_ref)
        assert "error" not in result.lower(), f"Focus failed: {result}"

    @pytest.mark.asyncio
    async def test_listitem_refs_resolve_to_elements(self, browser):
        """Listitem refs from snapshot should resolve to real locators."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        refs = parse_snapshot(snap)

        listitem_refs = [
            ref for ref, info in refs.items() if info["type"].lower() == "listitem"
        ]
        assert listitem_refs, "Expected at least one listitem ref in snapshot"

        # Validate several list items so nth disambiguation path is also exercised.
        for ref in listitem_refs[:4]:
            locator = await browser.get_element_by_ref(ref)
            assert locator is not None, f"Failed to resolve listitem ref: {ref}"
            count = await locator.count()
            assert count > 0, f"Resolved locator for {ref} has count=0"

    @pytest.mark.asyncio
    async def test_option_refs_resolve_to_elements(self, browser):
        """Option refs should keep role+name path and resolve correctly."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        option_refs = [
            ref for ref, info in refs.items() if info["type"].lower() == "option"
        ]
        assert option_refs, "Expected option refs in interactive snapshot"

        for ref in option_refs:
            locator = await browser.get_element_by_ref(ref)
            assert locator is not None, f"Failed to resolve option ref: {ref}"
            count = await locator.count()
            assert count > 0, f"Resolved locator for {ref} has count=0"

    @pytest.mark.asyncio
    async def test_role_text_match_refs_resolve_from_dedicated_fixture(
        self, role_text_match_browser
    ):
        """Text-matched structural roles should resolve when ref has a name."""
        snapshot = await role_text_match_browser.get_snapshot(
            interactive=False, full_page=True
        )
        assert snapshot is not None

        target_roles = {
            "listitem",
            "row",
            "cell",
            "gridcell",
            "columnheader",
            "rowheader",
        }
        refs_by_role = {role: [] for role in target_roles}

        for ref, ref_data in snapshot.refs.items():
            if ref_data.role in target_roles and ref_data.name:
                refs_by_role[ref_data.role].append(ref)

        missing = [role for role, refs in refs_by_role.items() if not refs]
        assert not missing, f"Fixture missing named refs for roles: {missing}"

        for role, refs in refs_by_role.items():
            for ref in refs:
                locator = await role_text_match_browser.get_element_by_ref(ref)
                assert locator is not None, f"Failed to resolve {role} ref: {ref}"
                count = await locator.count()
                assert count > 0, f"Resolved locator for {role} ref {ref} has count=0"

    @pytest.mark.asyncio
    async def test_role_text_match_refs_survive_prefilter_in_viewport_mode(
        self, role_text_match_browser
    ):
        """Pre-filter path should keep refs resolvable for role-text-match roles."""
        snapshot = await role_text_match_browser.get_snapshot(
            interactive=False, full_page=False
        )
        assert snapshot is not None

        target_roles = {"row", "listitem", "cell"}
        refs = [
            ref for ref, data in snapshot.refs.items()
            if data.role in target_roles and data.name
        ]
        assert refs, "Expected named refs for row/listitem/cell in viewport snapshot"

        for ref in refs:
            locator = await role_text_match_browser.get_element_by_ref(ref)
            assert locator is not None, f"Pre-filter lost resolvability for ref: {ref}"
            count = await locator.count()
            assert count > 0, f"Pre-filtered ref {ref} resolves with count=0"


# ==================== Snapshot After State Changes ====================

class TestSnapshotAfterStateChange:
    """Verify that snapshots reflect page state changes."""

    @pytest.mark.asyncio
    async def test_snapshot_reflects_input_value(self, browser):
        """After typing into a field, the snapshot should reflect the value
        or at least still contain the field."""
        snap1 = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs1 = parse_snapshot(snap1)

        tb_ref = find_ref(refs1, "textbox", "Username")
        assert tb_ref is not None
        await browser.input_text_by_ref(tb_ref, "changed_value")

        # Get fresh snapshot
        snap2 = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs2 = parse_snapshot(snap2)
        # Textbox should still be present with same or updated name
        assert find_ref(refs2, "textbox", "Username") or find_ref(refs2, "textbox", "changed")

    @pytest.mark.asyncio
    async def test_snapshot_reflects_checkbox_state(self, browser):
        """After checking a checkbox, the snapshot should show [checked]."""
        snap1 = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs1 = parse_snapshot(snap1)

        cb_ref = find_ref(refs1, "checkbox", "Technology")
        assert cb_ref is not None

        # Initially should NOT have [checked]
        assert "[checked]" not in refs1[cb_ref]["suffix"]

        await browser.check_checkbox_or_radio_by_ref(cb_ref)

        # Fresh snapshot should show [checked]
        snap2 = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs2 = parse_snapshot(snap2)
        cb_ref2 = find_ref(refs2, "checkbox", "Technology")
        assert cb_ref2 is not None
        assert "[checked]" in refs2[cb_ref2]["suffix"], \
            "Checked checkbox should show [checked] in snapshot"

    @pytest.mark.asyncio
    async def test_snapshot_reflects_selected_option(self, browser):
        """After selecting a dropdown option, snapshot should update [selected]."""
        snap1 = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs1 = parse_snapshot(snap1)

        combo_ref = find_ref(refs1, "combobox", "Country")
        assert combo_ref is not None
        await browser.select_dropdown_option_by_ref(combo_ref, "China")

        snap2 = await browser.get_snapshot_text(interactive=True, full_page=True)
        # "China" option should now have [selected]
        for line in snap2.splitlines():
            if 'option "China"' in line:
                assert '[selected]' in line, \
                    f"China option should be [selected] after selection: {line}"
                break
        else:
            pytest.fail("China option not found in post-selection snapshot")


# ==================== Edge Cases ====================

class TestEdgeCases:
    """Edge cases and regression tests."""

    @pytest.mark.asyncio
    async def test_list_structure_preserved(self, browser):
        """Navigation list should maintain list > listitem > link hierarchy."""
        snap = await browser.get_snapshot_text(interactive=False, full_page=True)
        lines = snap.splitlines()

        # Find the list line
        list_idx = None
        for i, line in enumerate(lines):
            if line.strip() == '- list:':
                list_idx = i
                break
        assert list_idx is not None, "Navigation list not found"

        # Next 4 groups should be listitem > link
        listitem_count = 0
        for line in lines[list_idx + 1:]:
            if 'listitem' in line:
                listitem_count += 1
            elif 'link' in line and listitem_count > 0:
                continue  # link inside listitem
            elif line.strip().startswith('- /url:'):
                continue  # url metadata inside link
            elif not line.strip() or not line.startswith('  '):
                break  # exited list structure
        assert listitem_count == 4, f"Expected 4 listitems in nav list, got {listitem_count}"

    @pytest.mark.asyncio
    async def test_interactive_snapshot_element_count_reasonable(self, browser):
        """Interactive + full_page snapshot should have a reasonable count
        of elements — not too few (missing elements) nor too many (noise)."""
        snap = await browser.get_snapshot_text(interactive=True, full_page=True)
        refs = parse_snapshot(snap)

        # The test page has roughly:
        # 4 links + 6 textboxes + 1 combobox + 6 options + 4 checkboxes
        # + 3 radios + ~16 buttons + ~8 generic interactive + ...
        total = len(refs)
        assert total >= 40, f"Too few elements in interactive full-page: {total}"
        assert total <= 80, f"Too many elements in interactive full-page: {total}"

    @pytest.mark.asyncio
    async def test_full_page_snapshot_more_elements_than_viewport(self, browser):
        """Full page mode should always return more elements than viewport mode."""
        snap_viewport = await browser.get_snapshot_text(interactive=False, full_page=False)
        snap_full = await browser.get_snapshot_text(interactive=False, full_page=True)

        refs_vp = parse_snapshot(snap_viewport)
        refs_full = parse_snapshot(snap_full)

        assert len(refs_full) > len(refs_vp), \
            f"Full page ({len(refs_full)}) should have more elements than viewport ({len(refs_vp)})"

    @pytest.mark.asyncio
    async def test_interactive_subset_of_full(self, browser):
        """Interactive elements should be a subset of the full element set
        (by role+name, not by ref number since those differ)."""
        snap_all = await browser.get_snapshot_text(interactive=False, full_page=True)
        snap_interactive = await browser.get_snapshot_text(interactive=True, full_page=True)

        refs_all = parse_snapshot(snap_all)
        refs_int = parse_snapshot(snap_interactive)

        # Every element in interactive should appear in full (by type+name)
        all_type_names = {(r["type"], r["name"]) for r in refs_all.values()}
        for _, info in refs_int.items():
            key = (info["type"], info["name"])
            assert key in all_type_names, \
                f"Interactive element {info['type']} \"{info['name']}\" not in full snapshot"
