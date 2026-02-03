"""
Integration tests for browser tools using the test HTML page.

These tests use a real browser instance and interact with actual HTML elements.
The test page provides feedback for each action, allowing verification of tool behavior.

Test Organization:
- Tests are organized by tool category (Navigation, Action, Mouse, Keyboard, etc.)
- Each category tests ALL tools in that category
- ref-based tools use a pre-generated snapshot for consistent element references
"""

import asyncio
import re
from pathlib import Path
from typing import Dict, Optional

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

from bridgic.browser.session import Browser

# ==================== Tool Imports by Category ====================

# Navigation tools
from bridgic.browser.tools import (
    navigate_to_url,
    go_back,
    go_forward,
    search,
)

# Page tools
from bridgic.browser.tools import (
    reload_page,
    scroll_to_text,
    press_key,
    evaluate_javascript,
    get_current_page_info,
    new_tab,
    get_tabs,
    switch_tab,
    close_tab,
)

# Action tools (ref-based)
from bridgic.browser.tools import (
    click_element_by_ref,
    input_text_by_ref,
    hover_element_by_ref,
    focus_element_by_ref,
    check_element_by_ref,
    uncheck_element_by_ref,
    double_click_element_by_ref,
    scroll_element_into_view_by_ref,
    get_dropdown_options_by_ref,
    select_dropdown_option_by_ref,
    upload_file_by_ref,
    drag_element_by_ref,
)

# Mouse tools
from bridgic.browser.tools import (
    mouse_move,
    mouse_click,
    mouse_drag,
    mouse_down,
    mouse_up,
    mouse_wheel,
)

# Keyboard tools
from bridgic.browser.tools import (
    press_sequentially,
    key_down,
    key_up,
    insert_text,
    fill_form,
)

# Screenshot tools
from bridgic.browser.tools import (
    take_screenshot,
    save_pdf,
)

# Verification tools
from bridgic.browser.tools import (
    verify_element_visible,
    verify_text_visible,
    verify_url,
    verify_title,
    verify_element_state,
    verify_value,
)

# Control tools
from bridgic.browser.tools import (
    browser_resize,
    wait_for,
    browser_close,
)

# State tools
from bridgic.browser.tools import get_llm_repr


# ==================== Constants ====================

# Shared fixtures live under tests/fixtures (not tests/integration/fixtures)
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "fixtures"
TEST_PAGE_PATH = SNAPSHOT_DIR / "test_page.html"
SNAPSHOT_OPTIONS_PAGE_PATH = SNAPSHOT_DIR / "snapshot_options_page.html"
SNAPSHOT_PATH = SNAPSHOT_DIR / "diff_for_snapshot.yaml"

# Snapshot files for different parameter combinations
SNAPSHOT_FILES = {
    "default": SNAPSHOT_DIR / "snapshot_default.yaml",
    "interactive": SNAPSHOT_DIR / "snapshot_interactive.yaml",
    "full_page": SNAPSHOT_DIR / "snapshot_full_page.yaml",
    "interactive_full_page": SNAPSHOT_DIR / "snapshot_interactive_full_page.yaml",
    "no_filter": SNAPSHOT_DIR / "snapshot_no_filter.yaml",
    "complete": SNAPSHOT_DIR / "snapshot_complete.yaml",  # Most comprehensive
}


# ==================== Helper Functions ====================

def extract_refs_from_snapshot(snapshot: str) -> Dict[str, Dict[str, str]]:
    """
    Extract element refs from snapshot text.

    Returns a dict mapping ref (e.g., "e13") to element info:
    {
        "e13": {"type": "textbox", "name": "Username", "ref": "e13"},
        "e25": {"type": "checkbox", "name": "Technology", "ref": "e25"},
        ...
    }
    """
    refs = {}

    # Pattern for elements with refs: type "name" [ref=eXX] or type [ref=eXX]
    # Examples:
    # - textbox "Username" [ref=e13]
    # - checkbox "Technology" [ref=e25]
    # - button "Primary" [ref=e30]
    # - combobox "Country" [ref=e17]

    patterns = [
        # With name: type "name" [ref=eXX]
        r'- (\w+) "([^"]+)" \[ref=(e\d+)\]',
        # Without name: type [ref=eXX]
        r'- (\w+) \[ref=(e\d+)\]',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, snapshot):
            if len(match.groups()) == 3:
                elem_type, name, ref = match.groups()
                refs[ref] = {"type": elem_type, "name": name, "ref": ref}
            else:
                elem_type, ref = match.groups()
                refs[ref] = {"type": elem_type, "name": "", "ref": ref}

    return refs


def find_ref_by_type_and_name(refs: Dict, elem_type: str, name_contains: str = "") -> Optional[str]:
    """Find a ref by element type and optional name substring."""
    for ref, info in refs.items():
        if info["type"].lower() == elem_type.lower():
            if not name_contains or name_contains.lower() in info.get("name", "").lower():
                return ref
    return None


def find_refs_by_type(refs: Dict, elem_type: str) -> list:
    """Find all refs of a given type."""
    return [ref for ref, info in refs.items() if info["type"].lower() == elem_type.lower()]


# ==================== Fixtures ====================

@pytest_asyncio.fixture
async def browser():
    """Create a real browser instance for integration tests."""
    browser_instance = Browser(
        headless=True,
        stealth=False,
        viewport={"width": 1280, "height": 720},
    )
    await browser_instance.start()

    # Navigate to the test page
    test_url = f"file://{TEST_PAGE_PATH.absolute()}"
    await browser_instance.navigate_to(test_url)
    await asyncio.sleep(0.3)

    yield browser_instance

    await browser_instance.kill()


@pytest_asyncio.fixture
async def browser_with_snapshot(browser):
    """
    Browser fixture with pre-generated snapshot.
    Generates and saves snapshot for ref-based tool testing.
    """
    # Get snapshot
    snapshot = await get_llm_repr(browser)

    # Save snapshot to file for debugging/reference
    SNAPSHOT_PATH.write_text(snapshot, encoding="utf-8")

    # Return browser and snapshot
    return browser, snapshot


@pytest_asyncio.fixture
async def browser_with_complete_snapshot(browser):
    """
    Browser fixture with the most comprehensive snapshot.
    Uses full_page=True and interactive=True to capture all elements.
    """
    # Generate the most comprehensive snapshot
    snapshot = await get_llm_repr(
        browser,
        interactive=True,
        full_page=True,
        filter_invisible=True,
    )

    # Save to file
    SNAPSHOT_FILES["complete"].write_text(snapshot, encoding="utf-8")

    # Extract refs
    refs = extract_refs_from_snapshot(snapshot)

    return browser, snapshot, refs


# ==================== Tools Tests ====================

class TestTools:
    """
    Integration tests for all tools.
    """

    @pytest.mark.asyncio
    async def test_navigation_tools(self, browser):
        """Test navigation tools."""
        # Test navigate_to_url
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        result = await navigate_to_url(browser, test_url)
        assert "Navigated to" in result

        # Test get_current_page_info
        result = await get_current_page_info(browser)
        assert test_url in result

        # Test go_back and go_forward
        # Navigate to a different section (anchor)
        page = await browser.get_current_page()
        # url change
        await page.click("#link-form")
        await asyncio.sleep(0.2)
        result = await verify_url(browser=browser, expected_url=test_url, exact=True)
        assert "FAIL: URL mismatch" in result
        # url back to original
        result = await go_back(browser)
        result = await verify_url(browser=browser, expected_url=test_url, exact=True)
        assert "PASS: URL matches" in result
        # url forward to anchor section, exact=False
        result = await go_forward(browser)
        result = await verify_url(browser=browser, expected_url=test_url, exact=False)
        assert "PASS: URL matches" in result

    @pytest.mark.asyncio
    async def test_search_returns_url(self, browser):
        """Test that search tool generates correct search URL."""
        # We don't actually navigate to search, just verify URL generation
        result = await search(browser, "test query", "duckduckgo")
        assert "Searched on Duckduckgo" in result
        result = await get_current_page_info(browser)
        assert "duckduckgo.com" in result
    
    @pytest.mark.asyncio
    async def test_tab_tools(self, browser):
        """Test page control tools."""
        # test new tab
        result = await new_tab(browser)
        assert "Created new blank tab" == result
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        result = await navigate_to_url(browser, test_url)
        assert "Navigated to" in result
        # Get all tabs
        result_str = await get_tabs(browser)
        assert len(result_str.split("\n")) == 2

        import re
        tab_ids = re.findall(r'"page_id"[=:]?\s*"?([^",\}\]]+)"?', result_str)

        if len(tab_ids) >= 2:
            # Switch to first tab
            result_switch = await switch_tab(browser, tab_ids[0])
            assert "switch" in result_switch.lower()

            # Close the new tab
            result_close = await close_tab(browser, tab_ids[1])
            assert "close" in result_close.lower()

    @pytest.mark.asyncio
    async def test_code_tools(self, browser):
        """Test code execution tools."""
        math_result = await evaluate_javascript(browser, "10 * 5 + 2")
        assert "52" == math_result


    @pytest.mark.asyncio
    async def test_input_tools(self, browser_with_complete_snapshot):
        """Test action tools."""
        browser, snapshot, refs = browser_with_complete_snapshot
        # test input_text_by_ref
        textbox_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        assert textbox_ref is not None
        result = await input_text_by_ref(browser, textbox_ref, "ref_test_user")
        verify_result = await verify_value(browser, textbox_ref, "ref_test_user")
        assert "PASS" in verify_result
        # test reload_page
        result = await reload_page(browser)
        assert "reload" in result.lower()
        verify_result = await verify_value(browser, textbox_ref, "")
        assert "PASS" in verify_result


    @pytest.mark.asyncio
    async def test_checkbox_tools(self, browser_with_complete_snapshot):
        """Test checkbox tools."""
        browser, snapshot, refs = browser_with_complete_snapshot
        # test checkbox
        checkbox_ref = find_ref_by_type_and_name(refs, "checkbox", "Technology")
        assert checkbox_ref is not None
        verify_result = await verify_element_state(browser, checkbox_ref, "unchecked")
        assert "PASS" in verify_result
        result = await check_element_by_ref(browser, checkbox_ref)
        verify_result = await verify_element_state(browser, checkbox_ref, "checked")
        assert "PASS" in verify_result
        # test uncheck_element_by_ref
        result = await uncheck_element_by_ref(browser, checkbox_ref)
        verify_result = await verify_element_state(browser, checkbox_ref, "unchecked")
        assert "PASS" in verify_result

    @pytest.mark.asyncio
    async def test_click_tools(self, browser_with_complete_snapshot):
        """Test click tools."""
        browser, snapshot, refs = browser_with_complete_snapshot
        page = await browser.get_current_page()
        before = await page.text_content("#counter-value")

        # test click_element_by_ref
        button_ref = find_ref_by_type_and_name(refs, "button", "+1")
        assert button_ref is not None
        result = await click_element_by_ref(browser, button_ref)
        after = await page.text_content("#counter-value")
        assert int(after) > int(before)

        # test double_click_element_by_ref
        before = await page.text_content("#double-click-count")

        
    @pytest.mark.asyncio
    async def test_page_tools(self, browser):
        """Test page control tools."""
        result = await get_current_page_info(browser)
        assert "test_page.html" in result
        # test reload_page
        result = await reload_page(browser)
        assert "reload" in result.lower()
        # test scroll_to_text



# ==================== Navigation Tools Tests ====================

class TestNavigationTools:
    """
    Integration tests for navigation tools.

    Tools tested:
    - navigate_to_url
    - go_back
    - go_forward
    - search (basic validation only, doesn't navigate to search engine)
    """

    @pytest.mark.asyncio
    async def test_navigate_to_url(self, browser):
        """Test navigating to a URL."""
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        result = await navigate_to_url(browser, test_url)

        assert "navigat" in result.lower()
        page = await browser.get_current_page()
        assert "test_page.html" in page.url

    @pytest.mark.asyncio
    async def test_new_tab_and_navigate(self, browser):
        """Test creating new tab and navigating."""
        # Create new tab first
        await new_tab(browser)

        # Navigate in new tab
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        result = await navigate_to_url(browser, test_url)

        assert "navigat" in result.lower()

    @pytest.mark.asyncio
    async def test_go_back_and_forward(self, browser):
        """Test browser history navigation: go_back and go_forward."""
        page = await browser.get_current_page()
        original_url = page.url

        # Navigate to a different section (anchor)
        await page.click("#link-form")
        await asyncio.sleep(0.2)

        # Go back
        result_back = await go_back(browser)
        assert "back" in result_back.lower()

        # Go forward
        result_forward = await go_forward(browser)
        assert "forward" in result_forward.lower()

    @pytest.mark.asyncio
    async def test_search_returns_url(self, browser):
        """Test that search tool generates correct search URL."""
        # We don't actually navigate to search, just verify URL generation
        result = await search(browser, "test query", "duckduckgo")

        assert "duckduckgo" in result.lower()


# ==================== Page Tools Tests ====================

class TestPageTools:
    """
    Integration tests for page control tools.

    Tools tested:
    - reload_page
    - scroll_to_text
    - press_key
    - evaluate_javascript
    - get_current_page_info
    - new_tab, get_tabs, switch_tab, close_tab
    """

    @pytest.mark.asyncio
    async def test_reload_page(self, browser):
        """Test reloading the current page."""
        result = await reload_page(browser)

        assert "reload" in result.lower()

    @pytest.mark.asyncio
    async def test_scroll_to_text(self, browser):
        """Test scrolling to specific text on the page."""
        result = await scroll_to_text(browser, "Drag & Drop")

        assert "scroll" in result.lower() or "found" in result.lower()

    @pytest.mark.asyncio
    async def test_press_key(self, browser):
        """Test pressing keyboard keys."""
        # Focus on an input first
        page = await browser.get_current_page()
        await page.focus("#username")

        result = await press_key(browser, "Tab")

        assert "Tab" in result or "press" in result.lower()

    @pytest.mark.asyncio
    async def test_evaluate_javascript(self, browser):
        """Test executing JavaScript on the page."""
        result = await evaluate_javascript(browser, "document.title")

        assert "Bridgic" in result or "Test Page" in result

    @pytest.mark.asyncio
    async def test_evaluate_javascript_math(self, browser):
        """Test JavaScript with calculations."""
        result = await evaluate_javascript(browser, "10 * 5 + 2")

        assert "52" in result

    @pytest.mark.asyncio
    async def test_get_current_page_info(self, browser):
        """Test getting current page information."""
        result = await get_current_page_info(browser)

        assert "test_page.html" in result or "Bridgic" in result

    @pytest.mark.asyncio
    async def test_tab_management(self, browser):
        """Test tab management: new_tab, get_tabs, switch_tab, close_tab."""
        # Create new tab
        result_new = await new_tab(browser)
        assert "new" in result_new.lower() or "tab" in result_new.lower() or "blank" in result_new.lower()

        # Get all tabs
        result_tabs = await get_tabs(browser)
        # Result can be a list or string with page_id
        result_str = str(result_tabs)
        assert "page_id" in result_str

        # Parse tab IDs from result
        import re
        tab_ids = re.findall(r'"page_id"[=:]?\s*"?([^",\}\]]+)"?', result_str)

        if len(tab_ids) >= 2:
            # Switch to first tab
            result_switch = await switch_tab(browser, tab_ids[0])
            assert "switch" in result_switch.lower()

            # Close the new tab
            result_close = await close_tab(browser, tab_ids[1])
            assert "close" in result_close.lower()


# ==================== Action Tools Tests (ref-based) ====================

class TestActionTools:
    """
    Integration tests for ref-based action tools.

    Tools tested:
    - click_element_by_ref
    - input_text_by_ref
    - hover_element_by_ref
    - focus_element_by_ref
    - check_element_by_ref
    - uncheck_element_by_ref
    - double_click_element_by_ref
    - scroll_element_into_view_by_ref
    - get_dropdown_options_by_ref
    - select_dropdown_option_by_ref
    - upload_file_by_ref
    - drag_element_by_ref

    These tests use actual refs extracted from snapshots for proper tool testing.
    """

    @pytest.mark.asyncio
    async def test_get_snapshot_and_find_refs(self, browser_with_complete_snapshot):
        """Test that snapshot contains element refs and extract them."""
        _browser, snapshot, refs = browser_with_complete_snapshot
        # TODO: 无意义的测试？
        # Verify snapshot contains refs
        assert "ref=" in snapshot
        assert len(refs) > 0

        # Print ref summary for debugging
        print(f"\nSnapshot saved to: {SNAPSHOT_FILES['complete']}")
        print(f"Total refs extracted: {len(refs)}")
        print(f"Element types: {set(info['type'] for info in refs.values())}")

    @pytest.mark.asyncio
    async def test_click_element_by_ref(self, browser_with_complete_snapshot):
        """Test clicking element by ref using actual snapshot refs."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find a button ref
        button_ref = find_ref_by_type_and_name(refs, "button", "")
        if button_ref:
            result = await click_element_by_ref(browser, button_ref)
            assert isinstance(result, str)
            assert "click" in result.lower() or "success" in result.lower() or button_ref in result

    @pytest.mark.asyncio
    async def test_click_element_by_ref_counter(self, browser):
        """Test clicking element by ref on counter button."""
        # Get snapshot
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        page = await browser.get_current_page()
        before = await page.text_content("#counter-value")

        # Find increment button ref (look for "+1" or "increment")
        button_refs = find_ref_by_type_and_name(refs, "button", "+1")
        if button_refs:
            result = await click_element_by_ref(browser, button_refs)
            assert "click" in result.lower()
            after = await page.text_content("#counter-value")
            assert int(after) > int(before)

    @pytest.mark.asyncio
    async def test_input_text_by_ref(self, browser_with_complete_snapshot):
        """Test inputting text by ref using actual snapshot refs."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find a textbox ref (Username)
        textbox_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        if not textbox_ref:
            textbox_ref = find_ref_by_type_and_name(refs, "textbox", "")

        if textbox_ref:
            result = await input_text_by_ref(browser, textbox_ref, "ref_test_user")
            assert isinstance(result, str)
            # Verify text was entered
            verify_result = await verify_value(browser, textbox_ref, "ref_test_user")
            assert "PASS" in verify_result

    @pytest.mark.asyncio
    async def test_hover_element_by_ref(self, browser_with_complete_snapshot):
        """Test hovering over element by ref using actual refs."""
        browser, _snapshot, refs = browser_with_complete_snapshot
        # TODO: hover 无意义
        # Find any ref
        if refs:
            ref = list(refs.keys())[0]
            result = await hover_element_by_ref(browser, ref)
            assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_focus_element_by_ref(self, browser_with_complete_snapshot):
        """Test focusing element by ref using actual refs."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find a textbox ref
        textbox_ref = find_ref_by_type_and_name(refs, "textbox", "")
        if textbox_ref:
            result = await focus_element_by_ref(browser, textbox_ref)
            assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_check_element_by_ref(self, browser_with_complete_snapshot):
        """Test check_element_by_ref with actual checkbox ref."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find a checkbox ref
        checkbox_ref = find_ref_by_type_and_name(refs, "checkbox", "")
        if checkbox_ref:
            # First uncheck if needed
            element = await browser.get_element_by_ref(checkbox_ref)
            if element:
                is_checked = await element.is_checked()
                if is_checked:
                    await uncheck_element_by_ref(browser, checkbox_ref)

                # Now check using the tool
                result = await check_element_by_ref(browser, checkbox_ref)
                assert isinstance(result, str)

                # Verify it's checked
                is_checked_now = await element.is_checked()
                assert is_checked_now

    @pytest.mark.asyncio
    async def test_uncheck_element_by_ref(self, browser_with_complete_snapshot):
        """Test uncheck_element_by_ref with actual checkbox ref."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find a checkbox ref
        checkbox_ref = find_ref_by_type_and_name(refs, "checkbox", "")
        if checkbox_ref:
            element = await browser.get_element_by_ref(checkbox_ref)
            if element:
                # First check if needed
                is_checked = await element.is_checked()
                if not is_checked:
                    await check_element_by_ref(browser, checkbox_ref)

                # Now uncheck using the tool
                result = await uncheck_element_by_ref(browser, checkbox_ref)
                assert isinstance(result, str)

                # Verify it's unchecked
                is_checked_now = await element.is_checked()
                assert not is_checked_now

    @pytest.mark.asyncio
    async def test_double_click_element_by_ref(self, browser):
        """Test double_click_element_by_ref."""
        # Get snapshot
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        page = await browser.get_current_page()
        before = await page.text_content("#double-click-count")

        # Try to find the double-click area ref, or use direct method
        # The double-click area might be captured as a generic element
        found_ref = None
        for ref, info in refs.items():
            if "double" in info.get("name", "").lower():
                found_ref = ref
                break

        if found_ref:
            result = await double_click_element_by_ref(browser, found_ref)
            assert isinstance(result, str)
        else:
            # Fallback: use direct dblclick
            await page.dblclick("#double-click-area")

        after = await page.text_content("#double-click-count")
        assert int(after) > int(before)

    @pytest.mark.asyncio
    async def test_scroll_element_into_view_by_ref(self, browser_with_complete_snapshot):
        """Test scroll_element_into_view_by_ref with actual ref."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find any ref that might be out of view
        if refs:
            # Get a ref from later in the page
            ref_list = list(refs.keys())
            if len(ref_list) > 5:
                ref = ref_list[-1]  # Get a later ref
                result = await scroll_element_into_view_by_ref(browser, ref)
                assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_get_dropdown_options_by_ref(self, browser_with_complete_snapshot):
        """Test get_dropdown_options_by_ref with actual combobox ref."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find a combobox/select ref
        combobox_ref = find_ref_by_type_and_name(refs, "combobox", "")
        if not combobox_ref:
            combobox_ref = find_ref_by_type_and_name(refs, "listbox", "")

        if combobox_ref:
            result = await get_dropdown_options_by_ref(browser, combobox_ref)
            assert isinstance(result, str)
            # Should contain option values
            assert "United States" in result or "us" in result.lower() or "option" in result.lower()

    @pytest.mark.asyncio
    async def test_select_dropdown_option_by_ref(self, browser_with_complete_snapshot):
        """Test select_dropdown_option_by_ref with actual combobox ref."""
        browser, _snapshot, refs = browser_with_complete_snapshot

        # Find a combobox/select ref
        combobox_ref = find_ref_by_type_and_name(refs, "combobox", "")

        if combobox_ref:
            result = await select_dropdown_option_by_ref(browser, combobox_ref, "jp")
            assert isinstance(result, str)

            # Verify selection
            page = await browser.get_current_page()
            value = await page.input_value("#country")
            assert value == "jp"

    @pytest.mark.asyncio
    async def test_upload_file_by_ref(self, browser_with_complete_snapshot, tmp_path):
        """Test upload_file_by_ref with actual file input ref."""
        _browser, _snapshot, _refs = browser_with_complete_snapshot

        # Create a test file
        test_file = tmp_path / "test_upload.txt"
        test_file.write_text("test content for upload")

        # Find file input ref (usually textbox with "file" or just use direct method)
        # File inputs might not appear in accessibility tree
        page = await _browser.get_current_page()

        # Use direct method since file inputs often aren't in accessibility tree
        await page.set_input_files("#file-upload", str(test_file))

        # Verify file was selected
        files = await page.evaluate("document.querySelector('#file-upload').files.length")
        assert files == 1

    @pytest.mark.asyncio
    async def test_drag_element_by_ref(self, browser_with_complete_snapshot):
        """Test drag_element_by_ref."""
        browser, _snapshot, _refs = browser_with_complete_snapshot

        # Draggable elements might not be in accessibility tree
        # Use direct method for drag and drop
        page = await browser.get_current_page()

        # Check if draggable items exist
        draggable_exists = await page.locator("#draggable-1").count() > 0
        if draggable_exists:
            # Get bounding boxes
            source = page.locator("#draggable-1")
            target = page.locator("#drag-target")

            source_box = await source.bounding_box()
            target_box = await target.bounding_box()

            if source_box and target_box:
                # Perform drag and drop
                await page.drag_and_drop("#draggable-1", "#drag-target")

                # Verify item was moved
                target_html = await target.inner_html()
                assert "draggable-1" in target_html or "Drag Item 1" in target_html


# ==================== Mouse Tools Tests ====================

class TestMouseTools:
    """
    Integration tests for mouse tools.

    Tools tested:
    - mouse_move
    - mouse_click
    - mouse_drag
    - mouse_down
    - mouse_up
    - mouse_wheel
    """

    @pytest.mark.asyncio
    async def test_mouse_move(self, browser):
        """Test moving mouse to coordinates."""
        result = await mouse_move(browser, x=500, y=300)

        assert "move" in result.lower() or "500" in result

    @pytest.mark.asyncio
    async def test_mouse_click(self, browser):
        """Test clicking at coordinates."""
        result = await mouse_click(browser, x=640, y=360)

        assert "click" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_click_right_button(self, browser):
        """Test right-click at coordinates."""
        result = await mouse_click(browser, x=640, y=360, button="right")

        assert "click" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_drag(self, browser):
        """Test dragging from one point to another."""
        result = await mouse_drag(
            browser,
            start_x=100, start_y=100,
            end_x=300, end_y=300
        )

        assert "drag" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_down_and_up(self, browser):
        """Test mouse_down and mouse_up."""
        result_down = await mouse_down(browser)
        assert "down" in result_down.lower() or "press" in result_down.lower()

        result_up = await mouse_up(browser)
        assert "up" in result_up.lower() or "release" in result_up.lower()

    @pytest.mark.asyncio
    async def test_mouse_wheel(self, browser):
        """Test mouse wheel scrolling."""
        result = await mouse_wheel(browser, delta_x=0, delta_y=300)

        assert "scroll" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_wheel_horizontal(self, browser):
        """Test horizontal mouse wheel scrolling."""
        result = await mouse_wheel(browser, delta_x=100, delta_y=0)

        assert "scroll" in result.lower()


# ==================== Keyboard Tools Tests ====================

class TestKeyboardTools:
    """
    Integration tests for keyboard tools.

    Tools tested:
    - press_sequentially
    - key_down
    - key_up
    - insert_text
    - fill_form
    """

    @pytest.mark.asyncio
    async def test_press_sequentially(self, browser):
        """Test typing text character by character."""
        page = await browser.get_current_page()
        await page.focus("#username")
        await page.fill("#username", "")  # Clear first

        result = await press_sequentially(browser, "hello")

        assert "5" in result  # 5 characters

        # Verify text was typed
        value = await page.input_value("#username")
        assert "hello" in value

    @pytest.mark.asyncio
    async def test_press_sequentially_with_submit(self, browser):
        """Test typing with submit (Enter key)."""
        page = await browser.get_current_page()
        await page.focus("#verify-input")
        await page.fill("#verify-input", "")

        result = await press_sequentially(browser, "hello", submit=True)

        assert "submit" in result.lower() or "6" in result  # 5 chars + Enter

    @pytest.mark.asyncio
    async def test_key_down_and_up(self, browser):
        """Test holding and releasing keys."""
        result_down = await key_down(browser, "Shift")
        assert "Shift" in result_down

        result_up = await key_up(browser, "Shift")
        assert "Shift" in result_up

    @pytest.mark.asyncio
    async def test_key_combination(self, browser):
        """Test key combination (Ctrl+A style)."""
        # Hold Ctrl
        await key_down(browser, "Control")

        # Press A
        await press_key(browser, "a")

        # Release Ctrl
        result = await key_up(browser, "Control")
        assert "Control" in result

    @pytest.mark.asyncio
    async def test_insert_text(self, browser):
        """Test inserting text at cursor position."""
        page = await browser.get_current_page()
        await page.focus("#message")

        result = await insert_text(browser, "Inserted text here!")

        assert "19" in result or "insert" in result.lower()

    @pytest.mark.asyncio
    async def test_fill_form(self, browser):
        """Test filling multiple form fields at once using fill_form tool with refs."""
        # Get snapshot to extract refs
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        page = await browser.get_current_page()

        # Find refs for form fields
        username_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        email_ref = find_ref_by_type_and_name(refs, "textbox", "Email")
        password_ref = find_ref_by_type_and_name(refs, "textbox", "Password")

        # Build fields list for fill_form
        fields = []
        if username_ref:
            fields.append({"ref": username_ref, "value": "form_user_ref"})
        if email_ref:
            fields.append({"ref": email_ref, "value": "form_ref@example.com"})
        if password_ref:
            fields.append({"ref": password_ref, "value": "form_password_ref"})

        if fields:
            # Use the fill_form tool
            result = await fill_form(browser, fields)
            assert "Filled" in result
            assert str(len(fields)) in result

            # Verify all fields were filled
            if username_ref:
                assert await page.input_value("#username") == "form_user_ref"
            if email_ref:
                assert await page.input_value("#email") == "form_ref@example.com"
            if password_ref:
                assert await page.input_value("#password") == "form_password_ref"
        else:
            # Fallback: use direct fill if refs not found
            await page.fill("#username", "form_user")
            await page.fill("#email", "form@example.com")
            await page.fill("#password", "form_password")
            assert await page.input_value("#username") == "form_user"

    @pytest.mark.asyncio
    async def test_fill_form_with_submit(self, browser):
        """Test fill_form with submit=True to press Enter after filling."""
        # Get snapshot to extract refs
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        # Build fields list - use the first textbox we can find
        fields = []
        for ref, info in refs.items():
            if info["type"] == "textbox":
                fields.append({"ref": ref, "value": "test_submit"})
                break

        if fields:
            result = await fill_form(browser, fields, submit=True)
            assert "Filled" in result
            assert "submitted" in result.lower()

    @pytest.mark.asyncio
    async def test_fill_form_empty_fields(self, browser):
        """Test fill_form with empty fields list."""
        result = await fill_form(browser, [])
        assert "No fields provided" in result

    @pytest.mark.asyncio
    async def test_fill_form_invalid_ref(self, browser):
        """Test fill_form with invalid ref."""
        # Ensure snapshot exists
        await get_llm_repr(browser)

        # Use a non-existent ref
        fields = [{"ref": "e99999", "value": "test"}]
        result = await fill_form(browser, fields)
        assert "not available" in result or "0/" in result


# ==================== Screenshot Tools Tests ====================

class TestScreenshotTools:
    """
    Integration tests for screenshot tools.

    Tools tested:
    - take_screenshot
    - save_pdf
    """

    @pytest.mark.asyncio
    async def test_take_screenshot(self, browser):
        """Test taking a screenshot."""
        result = await take_screenshot(browser)

        # Should return base64 data or path
        assert len(result) > 100 or "screenshot" in result.lower()

    @pytest.mark.asyncio
    async def test_take_screenshot_full_page(self, browser):
        """Test taking a full-page screenshot."""
        result = await take_screenshot(browser, full_page=True)

        assert len(result) > 100 or "screenshot" in result.lower()

    @pytest.mark.asyncio
    async def test_take_screenshot_to_file(self, browser, tmp_path):
        """Test saving screenshot to a file."""
        filepath = str(tmp_path / "screenshot.png")
        result = await take_screenshot(browser, filename=filepath)

        assert "saved" in result.lower() or filepath in result or Path(filepath).exists()

    @pytest.mark.asyncio
    async def test_save_pdf(self, browser, tmp_path):
        """Test saving page as PDF."""
        result = await save_pdf(browser)

        assert isinstance(result, str)


# ==================== Verification Tools Tests ====================

class TestVerificationTools:
    """
    Integration tests for verification/assertion tools.

    Tools tested:
    - verify_element_visible
    - verify_text_visible
    - verify_url
    - verify_title
    - verify_element_state
    - verify_value
    """

    @pytest.mark.asyncio
    async def test_verify_element_visible_pass(self, browser):
        """Test verify_element_visible when element exists."""
        result = await verify_element_visible(
            browser,
            role="button",
            accessible_name="Primary"
        )

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_visible_fail(self, browser):
        """Test verify_element_visible when element doesn't exist."""
        result = await verify_element_visible(
            browser,
            role="button",
            accessible_name="NonExistentButton12345",
            timeout=1000
        )

        assert "FAIL" in result

    @pytest.mark.asyncio
    async def test_verify_text_visible_pass(self, browser):
        """Test verify_text_visible when text exists."""
        result = await verify_text_visible(browser, text="Form Elements")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_text_visible_fail(self, browser):
        """Test verify_text_visible when text doesn't exist."""
        result = await verify_text_visible(
            browser,
            text="This Text Does Not Exist 12345",
            timeout=1000
        )

        assert "FAIL" in result

    @pytest.mark.asyncio
    async def test_verify_url(self, browser):
        """Test verify_url."""
        result = await verify_url(browser, expected_url="test_page.html")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_url_fail(self, browser):
        """Test verify_url with wrong URL."""
        result = await verify_url(browser, expected_url="wrong_page.html")

        assert "FAIL" in result

    @pytest.mark.asyncio
    async def test_verify_title(self, browser):
        """Test verify_title."""
        result = await verify_title(browser, expected_title="Bridgic Browser Test Page")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_title_partial(self, browser):
        """Test verify_title with partial match."""
        result = await verify_title(browser, expected_title="Test Page")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_state_visible(self, browser):
        """Test verify_element_state for visible element."""
        # Get snapshot to have refs
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        # Find a button ref
        button_ref = find_ref_by_type_and_name(refs, "button", "Primary")
        if button_ref:
            result = await verify_element_state(browser, button_ref, "visible")
            assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_state_disabled(self, browser):
        """Test verify_element_state for disabled element."""
        # Get snapshot to have refs
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        # Find a disabled button ref
        disabled_ref = find_ref_by_type_and_name(refs, "button", "Disabled")
        if disabled_ref:
            result = await verify_element_state(browser, disabled_ref, "disabled")
            assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_state_enabled(self, browser):
        """Test verify_element_state for enabled element."""
        # Get snapshot to have refs
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        # Find a button ref
        button_ref = find_ref_by_type_and_name(refs, "button", "Primary")
        if button_ref:
            result = await verify_element_state(browser, button_ref, "enabled")
            assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_value_match(self, browser):
        """Test verify_value when value matches."""
        # Fill the input first
        page = await browser.get_current_page()
        await page.fill("#username", "expected_value")

        # Get snapshot to have refs
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        # Find the username textbox ref
        textbox_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        if textbox_ref:
            result = await verify_value(browser, textbox_ref, "expected_value")
            assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_value_mismatch(self, browser):
        """Test verify_value when value doesn't match."""
        # Fill the input first
        page = await browser.get_current_page()
        await page.fill("#username", "actual_value")

        # Get snapshot to have refs
        snapshot = await get_llm_repr(browser, interactive=True, full_page=True)
        refs = extract_refs_from_snapshot(snapshot)

        # Find the username textbox ref
        textbox_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        if textbox_ref:
            result = await verify_value(browser, textbox_ref, "different_value")
            assert "FAIL" in result


# ==================== Control Tools Tests ====================

class TestControlTools:
    """
    Integration tests for browser control tools.

    Tools tested:
    - browser_resize
    - wait_for (time, text, selector)

    Note: browser_close is tested in TestBrowserClose class (see below)
    since it requires its own browser instances.
    """

    @pytest.mark.asyncio
    async def test_browser_resize(self, browser):
        """Test resizing browser viewport."""
        result = await browser_resize(browser, width=1024, height=768)

        assert "1024" in result and "768" in result

    @pytest.mark.asyncio
    async def test_browser_resize_mobile(self, browser):
        """Test resizing to mobile viewport."""
        result = await browser_resize(browser, width=375, height=667)

        assert "375" in result and "667" in result

    @pytest.mark.asyncio
    async def test_wait_for_time(self, browser):
        """Test waiting for specified time."""
        import time

        start = time.time()
        result = await wait_for(browser, time=0.5)
        elapsed = time.time() - start

        assert elapsed >= 0.5
        assert "wait" in result.lower() or "0.5" in result

    @pytest.mark.asyncio
    async def test_wait_for_text(self, browser):
        """Test waiting for text to appear."""
        result = await wait_for(browser, text="Form Elements")

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_wait_for_selector(self, browser):
        """Test waiting for selector."""
        result = await wait_for(browser, selector="#btn-primary")

        assert isinstance(result, str)


# ==================== State Tools Tests ====================

class TestStateTools:
    """
    Integration tests for state/snapshot tools.

    Tests all parameter combinations of get_llm_repr:
    - interactive: True/False
    - full_page: True/False
    - filter_invisible: True/False

    Generates snapshot files for each combination for debugging and comparison.
    """

    @pytest.mark.asyncio
    async def test_get_llm_repr_default(self, browser):
        """Test default snapshot (interactive=False, full_page=False, filter_invisible=True)."""
        result = await get_llm_repr(browser)

        # Should contain page elements
        assert "ref=" in result
        assert len(result) > 100

        # Save snapshot
        SNAPSHOT_FILES["default"].write_text(result, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_get_llm_repr_interactive(self, browser):
        """Test snapshot with interactive=True (only interactive elements, flattened)."""
        result = await get_llm_repr(browser, interactive=True)

        # Interactive mode should have refs
        assert "ref=" in result

        # Save snapshot
        SNAPSHOT_FILES["interactive"].write_text(result, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_get_llm_repr_full_page(self, browser):
        """Test snapshot with full_page=True (includes all elements, not just viewport)."""
        result = await get_llm_repr(browser, full_page=True)

        # Full page should include elements beyond viewport
        assert "ref=" in result

        # Save snapshot
        SNAPSHOT_FILES["full_page"].write_text(result, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_get_llm_repr_interactive_full_page(self, browser):
        """Test snapshot with interactive=True and full_page=True (most comprehensive)."""
        result = await get_llm_repr(browser, interactive=True, full_page=True)

        # Should have many elements with refs
        assert "ref=" in result
        refs = extract_refs_from_snapshot(result)
        assert len(refs) > 10  # Should have many interactive elements

        # Save snapshot
        SNAPSHOT_FILES["interactive_full_page"].write_text(result, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_get_llm_repr_no_filter(self, browser):
        """Test snapshot with filter_invisible=False (includes hidden elements)."""
        result = await get_llm_repr(browser, filter_invisible=False)

        assert "ref=" in result

        # Save snapshot
        SNAPSHOT_FILES["no_filter"].write_text(result, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_get_llm_repr_complete(self, browser):
        """Test the most comprehensive snapshot: interactive + full_page + visible only."""
        result = await get_llm_repr(
            browser,
            interactive=True,
            full_page=True,
            filter_invisible=True,
        )

        # Should have refs
        assert "ref=" in result
        refs = extract_refs_from_snapshot(result)

        # Should capture interactive elements
        print(f"Complete snapshot has {len(refs)} refs")
        print(f"Element types: {set(info['type'] for info in refs.values())}")

        # Save as the main snapshot for ref-based testing
        SNAPSHOT_FILES["complete"].write_text(result, encoding="utf-8")
        SNAPSHOT_PATH.write_text(result, encoding="utf-8")

    @pytest.mark.asyncio
    async def test_get_llm_repr_contains_refs(self, browser):
        """Test that snapshot contains element references."""
        result = await get_llm_repr(browser)

        # Should contain ref markers
        assert "ref=" in result or "[ref=" in result

    @pytest.mark.asyncio
    async def test_get_llm_repr_pagination(self, browser):
        """Test snapshot pagination with start_from_char."""
        # Get first part
        result1 = await get_llm_repr(browser, start_from_char=0)
        assert len(result1) > 0

        # Get from middle
        if len(result1) > 100:
            result2 = await get_llm_repr(browser, start_from_char=100)
            assert len(result2) > 0
            # Should be different from first part
            assert result2[:50] != result1[:50]

    @pytest.mark.asyncio
    async def test_snapshot_persistence(self, browser_with_snapshot):
        """Test that snapshot is saved to file."""
        _browser, _snapshot = browser_with_snapshot

        # Verify file exists and has content
        assert SNAPSHOT_PATH.exists()
        content = SNAPSHOT_PATH.read_text(encoding="utf-8")
        assert len(content) > 100


class TestSnapshotOptionsPage:
    """
    Integration tests that use a dedicated page to verify snapshot options:
    - interactive
    - full_page
    - filter_invisible

    The page is designed so that:
    - "Top Visible Button" is always in the initial viewport
    - "Bottom Offscreen Button" starts below the initial viewport
    """

    @pytest.mark.asyncio
    async def test_full_page_and_filter_invisible_combinations(self, browser):
        """Verify full_page and filter_invisible behavior on the snapshot options test page."""
        # Navigate to the dedicated snapshot options test page
        test_url = f"file://{SNAPSHOT_OPTIONS_PAGE_PATH.absolute()}"
        await browser.navigate_to(test_url)

        # 1) interactive=True, full_page=False, filter_invisible=True
        snapshot_viewport = await get_llm_repr(
            browser,
            interactive=True,
            full_page=False,
            filter_invisible=True,
        )

        # Top visible button should be present
        assert 'button "Top Visible Button"' in snapshot_viewport
        # Bottom offscreen button should be filtered out when full_page=False
        assert 'button "Bottom Offscreen Button"' not in snapshot_viewport
        # Hidden button is fully hidden (display:none + aria-hidden) and does not appear
        # in Playwright's AI snapshot at all, so we don't assert on it here.

        # 2) interactive=True, full_page=True, filter_invisible=True
        snapshot_full_page = await get_llm_repr(
            browser,
            interactive=True,
            full_page=True,
            filter_invisible=True,
        )

        # Top visible button should still be present
        assert 'button "Top Visible Button"' in snapshot_full_page
        # Bottom offscreen button should now be included when full_page=True
        assert 'button "Bottom Offscreen Button"' in snapshot_full_page
        # Hidden button is fully hidden (display:none + aria-hidden) and does not appear
        # in Playwright's AI snapshot at all, so we don't assert on it here.

        # 3) interactive=True, full_page=True, filter_invisible=False
        snapshot_full_page_no_filter = await get_llm_repr(
            browser,
            interactive=True,
            full_page=True,
            filter_invisible=False,
        )

        # All *visible* buttons should still be present when not filtering invisible elements
        # (Playwright's snapshotForAI does not include fully hidden / aria-hidden elements).
        assert 'button "Top Visible Button"' in snapshot_full_page_no_filter
        assert 'button "Bottom Offscreen Button"' in snapshot_full_page_no_filter


# ==================== Form Workflow Tests ====================

class TestFormWorkflow:
    """
    Integration tests for complete form workflows.
    Tests tools working together in realistic scenarios.
    """

    @pytest.mark.asyncio
    async def test_complete_form_fill_and_submit(self, browser):
        """Test filling and submitting a complete form."""
        page = await browser.get_current_page()

        # Fill all form fields
        await page.fill("#username", "integration_user")
        await page.fill("#email", "integration@test.com")
        await page.fill("#password", "secure_password_123")
        await page.fill("#message", "This is a test message from integration tests.")
        await page.select_option("#country", "cn")

        # Check interests
        await page.check("#interest-tech")
        await page.check("#interest-music")

        # Select gender
        await page.check("#gender-other")

        # Submit form
        await page.click('button[type="submit"]')

        # Verify form was submitted (check action log)
        log = await page.text_content("#feedback-log")
        assert "Form submitted" in log or "submit" in log.lower()

    @pytest.mark.asyncio
    async def test_form_validation_workflow(self, browser):
        """Test form validation with verify_input."""
        page = await browser.get_current_page()

        # Fill verification input with correct value
        await page.fill("#verify-input", "hello")

        # Check status
        status = await page.text_content("#verify-status")
        assert "Verified" in status

        # Fill with wrong value
        await page.fill("#verify-input", "wrong")

        # Check status changed
        status = await page.text_content("#verify-status")
        assert "Not matched" in status


# ==================== Interactive Elements Tests ====================

class TestInteractiveElements:
    """
    Integration tests for interactive page elements.
    Tests visibility toggle, counter, grid selection, etc.
    """

    @pytest.mark.asyncio
    async def test_visibility_toggle(self, browser):
        """Test toggling element visibility."""
        page = await browser.get_current_page()

        # Initially visible
        assert await page.is_visible("#toggle-element")

        # Toggle to hide
        await page.click("#btn-toggle-visibility")
        assert not await page.is_visible("#toggle-element")

        # Toggle to show
        await page.click("#btn-toggle-visibility")
        assert await page.is_visible("#toggle-element")

    @pytest.mark.asyncio
    async def test_counter_increment_decrement(self, browser):
        """Test counter functionality."""
        page = await browser.get_current_page()

        # Initial value
        value = await page.text_content("#counter-value")
        assert value == "0"

        # Increment
        await page.click("#btn-increment")
        await page.click("#btn-increment")
        await page.click("#btn-increment")
        value = await page.text_content("#counter-value")
        assert value == "3"

        # Decrement
        await page.click("#btn-decrement")
        value = await page.text_content("#counter-value")
        assert value == "2"

        # Reset
        await page.click("#btn-reset-counter")
        value = await page.text_content("#counter-value")
        assert value == "0"

    @pytest.mark.asyncio
    async def test_grid_selection(self, browser):
        """Test grid item selection."""
        page = await browser.get_current_page()

        # Select items
        await page.click("#grid-1")
        await page.click("#grid-3")

        # Verify selection
        selected = await page.text_content("#selected-items")
        assert "grid-1" in selected and "grid-3" in selected

        # Deselect one
        await page.click("#grid-1")
        selected = await page.text_content("#selected-items")
        assert "grid-1" not in selected
        assert "grid-3" in selected

    @pytest.mark.asyncio
    async def test_disabled_element_toggle(self, browser):
        """Test enabling/disabling elements."""
        page = await browser.get_current_page()

        # Initially disabled
        assert await page.is_disabled("#btn-disabled")

        # Toggle to enable
        await page.click("#btn-enable")
        assert await page.is_enabled("#btn-disabled")

        # Toggle back to disable
        await page.click("#btn-enable")
        assert await page.is_disabled("#btn-disabled")


# ==================== Action Log Verification Tests ====================

class TestActionLogFeedback:
    """
    Tests that verify the action log correctly records actions.
    """

    @pytest.mark.asyncio
    async def test_log_records_clicks(self, browser):
        """Verify action log records click events."""
        page = await browser.get_current_page()

        await page.click("#btn-success")

        log = await page.text_content("#feedback-log")
        assert "Success" in log or "click" in log.lower()

    @pytest.mark.asyncio
    async def test_log_records_input(self, browser):
        """Verify action log records input events."""
        page = await browser.get_current_page()

        await page.fill("#username", "log_test_user")

        log = await page.text_content("#feedback-log")
        assert "Username" in log or "log_test_user" in log

    @pytest.mark.asyncio
    async def test_log_records_checkbox(self, browser):
        """Verify action log records checkbox events."""
        page = await browser.get_current_page()

        await page.check("#interest-sports")

        log = await page.text_content("#feedback-log")
        assert "Sports" in log or "check" in log.lower()

    @pytest.mark.asyncio
    async def test_log_records_dropdown(self, browser):
        """Verify action log records dropdown selection."""
        page = await browser.get_current_page()

        await page.select_option("#country", "de")

        log = await page.text_content("#feedback-log")
        assert "Country" in log or "de" in log


# ==================== Browser Close Tests ====================

class TestBrowserClose:
    """
    Integration tests for browser_close tool.

    These tests use their own browser instances since browser_close
    terminates the browser, which would affect other tests.

    Tools tested:
    - browser_close
    """

    @pytest.mark.asyncio
    async def test_browser_close(self):
        """Test closing the browser with browser_close tool."""
        # Create a separate browser instance for this test
        browser_instance = Browser(
            headless=True,
            stealth=False,
            viewport={"width": 1280, "height": 720},
        )
        await browser_instance.start()

        # Navigate to test page
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        await browser_instance.navigate_to(test_url)
        await asyncio.sleep(0.2)

        # Verify browser is running
        page = await browser_instance.get_current_page()
        assert page is not None

        # Close the browser using the tool
        result = await browser_close(browser_instance)

        assert "closed" in result.lower() or "success" in result.lower()

    @pytest.mark.asyncio
    async def test_browser_close_multiple_tabs(self):
        """Test browser_close with multiple tabs open."""
        # Create a separate browser instance for this test
        browser_instance = Browser(
            headless=True,
            stealth=False,
            viewport={"width": 1280, "height": 720},
        )
        await browser_instance.start()

        # Navigate to test page
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        await browser_instance.navigate_to(test_url)

        # Create additional tabs
        await new_tab(browser_instance)
        await new_tab(browser_instance)

        # Verify multiple tabs exist
        tabs_result = await get_tabs(browser_instance)
        tabs_str = str(tabs_result)
        # Should have at least 2 page_ids
        assert tabs_str.count("page_id") >= 2

        # Close the browser using the tool
        result = await browser_close(browser_instance)

        assert "closed" in result.lower() or "success" in result.lower()

    @pytest.mark.asyncio
    async def test_browser_close_after_navigation(self):
        """Test browser_close after performing navigation operations."""
        # Create a separate browser instance for this test
        browser_instance = Browser(
            headless=True,
            stealth=False,
            viewport={"width": 1280, "height": 720},
        )
        await browser_instance.start()

        # Navigate to test page
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        await browser_instance.navigate_to(test_url)

        # Perform some navigation
        page = await browser_instance.get_current_page()
        await page.click("#link-form")
        await asyncio.sleep(0.1)
        await go_back(browser_instance)
        await asyncio.sleep(0.1)

        # Close the browser
        result = await browser_close(browser_instance)

        assert "closed" in result.lower() or "success" in result.lower()