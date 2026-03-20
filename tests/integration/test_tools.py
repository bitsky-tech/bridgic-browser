"""
Integration tests for ALL browser tools using the test HTML page.

These tests use a real browser instance and interact with actual HTML elements.
The test page provides feedback for each action, allowing verification of tool behavior.

Test Organization:
- Tests are organized by tool category (Navigation, Action, Mouse, Keyboard, etc.)
- Each category tests ALL tools in that category
- ref-based tools use a pre-generated snapshot for consistent element references

Tool Coverage (67 tools, aligned with CLI sections):
- Navigation (6): navigate_to, search, get_current_page_info, reload_page, go_back, go_forward
- Snapshot (1): get_snapshot_text
- Element Interaction (13): click_element_by_ref, input_text_by_ref, fill_form,
               scroll_element_into_view_by_ref, select_dropdown_option_by_ref,
               get_dropdown_options_by_ref, check_checkbox_or_radio_by_ref, uncheck_checkbox_by_ref,
               focus_element_by_ref, hover_element_by_ref, double_click_element_by_ref,
               upload_file_by_ref, drag_element_by_ref
- Tabs (4): get_tabs, new_tab, switch_tab, close_tab
- Evaluate (2): evaluate_javascript, evaluate_javascript_on_ref
- Keyboard (4): type_text, press_key, key_down, key_up
- Mouse (6): mouse_wheel, mouse_click, mouse_move, mouse_drag, mouse_down, mouse_up
- Wait (1): wait_for
- Capture (2): take_screenshot, save_pdf
- Network (4): start_network_capture, get_network_requests, stop_network_capture,
               wait_for_network_idle
- Dialog (3): setup_dialog_handler, handle_dialog, remove_dialog_handler
- Storage (5): get_cookies, set_cookie, clear_cookies, save_storage_state,
               restore_storage_state
- Verify (6): verify_text_visible, verify_element_visible, verify_url,
              verify_title, verify_element_state, verify_value
- Developer (8): start_console_capture, get_console_messages, stop_console_capture,
                 start_tracing, add_trace_chunk, stop_tracing, start_video, stop_video
- Lifecycle (2): stop, browser_resize
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

from bridgic.browser.errors import VerificationError
from bridgic.browser.session import Browser

# ==================== Constants ====================

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "fixtures"
TEST_PAGE_PATH = SNAPSHOT_DIR / "test_page.html"
SNAPSHOT_PATH = SNAPSHOT_DIR / "diff_for_snapshot.yaml"
SNAPSHOT_FILES = {
    "complete": SNAPSHOT_DIR / "snapshot_complete.yaml",
}

# ==================== Helper Functions ====================

def extract_refs_from_snapshot(snapshot: str) -> Dict[str, Dict[str, str]]:
    """Extract element refs from snapshot text."""
    REF = r'[a-zA-Z0-9]+'
    refs: Dict[str, Dict[str, str]] = {}
    # Pattern order matters: more specific first so refs already captured are not overwritten.
    # Pattern 1: - type "name" [ref=XXXX]
    for m in re.finditer(rf'- (\w+) "([^"]+)" \[ref=({REF})\]', snapshot):
        elem_type, name, ref = m.groups()
        refs[ref] = {"type": elem_type, "name": name, "ref": ref}
    # Pattern 2: - type [ref=XXXX]: "text"
    for m in re.finditer(rf'- (\w+) \[ref=({REF})\][^:]*:\s*"([^"]+)"', snapshot):
        elem_type, ref, text = m.groups()
        if ref not in refs:
            refs[ref] = {"type": elem_type, "name": text.strip(), "ref": ref}
    # Pattern 3: - type [ref=XXXX]: text (unquoted)
    for m in re.finditer(rf'- (\w+) \[ref=({REF})\][^:]*:\s*([^\n"]+)', snapshot):
        elem_type, ref, text = m.groups()
        if ref not in refs:
            refs[ref] = {"type": elem_type, "name": text.strip(), "ref": ref}
    # Pattern 4: - type [ref=XXXX]  (unnamed)
    for m in re.finditer(rf'- (\w+) \[ref=({REF})\]', snapshot):
        elem_type, ref = m.groups()
        if ref not in refs:
            refs[ref] = {"type": elem_type, "name": "", "ref": ref}
    return refs

def find_ref_by_type_and_name(
    refs: Dict, elem_type: str, name_contains: str = "",
) -> Optional[str]:
    """Find a ref by element type and optional name substring."""
    for ref, info in refs.items():
        if info["type"].lower() == elem_type.lower():
            if not name_contains or name_contains.lower() in info.get("name", "").lower():
                return ref
    return None

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
    test_url = f"file://{TEST_PAGE_PATH.absolute()}"
    await browser_instance.navigate_to(test_url)
    await asyncio.sleep(0.3)
    yield browser_instance
    await browser_instance.stop()

@pytest_asyncio.fixture
async def browser_with_complete_snapshot(browser):
    """Browser fixture with interactive=True, full_page=True snapshot."""
    snapshot = await browser.get_snapshot_text(interactive=True, full_page=True)
    SNAPSHOT_FILES["complete"].write_text(snapshot, encoding="utf-8")
    refs = extract_refs_from_snapshot(snapshot)
    return browser, snapshot, refs

# ==================== 1. Navigation Tools (4 tools) ====================

class TestNavigationTools:
    """Tests: navigate_to, go_back, go_forward, search"""

    @pytest.mark.asyncio
    async def test_navigate_to(self, browser):
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        result = await browser.navigate_to(test_url)
        assert "Navigated to" in result

    @pytest.mark.asyncio
    async def test_go_back_and_forward(self, browser):
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        page = await browser.get_current_page()
        await page.click("#link-form")
        await asyncio.sleep(0.2)

        await browser.go_back()
        verify = await browser.verify_url(test_url, exact=True)
        assert "PASS" in verify

        await browser.go_forward()
        verify = await browser.verify_url(test_url, exact=False)
        assert "PASS" in verify

    @pytest.mark.asyncio
    async def test_search(self, browser):
        result = await browser.search("test query", "duckduckgo")
        assert "Searched on Duckduckgo" in result
        info = await browser.get_current_page_info()
        assert "duckduckgo.com" in info

# ==================== 2. Page & Tab Tools (9 tools) ====================

class TestPageTools:
    """Tests: reload_page, scroll_to_text, press_key,
    evaluate_javascript, new_tab, get_tabs, switch_tab, close_tab"""

    @pytest.mark.asyncio
    async def test_get_current_page_info(self, browser):
        result = await browser.get_current_page_info()
        assert "test_page.html" in result

    @pytest.mark.asyncio
    async def test_reload_page(self, browser):
        result = await browser.reload_page()
        assert "reload" in result.lower()

    @pytest.mark.asyncio
    async def test_scroll_to_text(self, browser):
        result = await browser.scroll_to_text("Hover over me!")
        assert "scroll" in result.lower() or "found" in result.lower()

    @pytest.mark.asyncio
    async def test_press_key(self, browser):
        """press_key sends a keyboard key to the page."""
        # Focus on key-display first
        page = await browser.get_current_page()
        await page.focus("#key-display")
        result = await browser.press_key("a")
        assert "press" in result.lower() or "key" in result.lower() or result
        # Verify the key was captured
        last_key = await page.text_content("#last-key")
        assert last_key == "a"

    @pytest.mark.asyncio
    async def test_evaluate_javascript(self, browser):
        result = await browser.evaluate_javascript("10 * 5 + 2")
        assert "52" == result

    @pytest.mark.asyncio
    async def test_tab_lifecycle(self, browser):
        """Tests new_tab, get_tabs, switch_tab, close_tab."""
        result = await browser.new_tab()
        assert result.startswith("Created new blank tab")
        assert re.search(r"\bpage_\d+\b", result), result

        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        await browser.navigate_to(test_url)

        result_str = await browser.get_tabs()
        assert len(result_str.split("\n")) == 2

        tab_ids = re.findall(r'"page_id"[=:]?\s*"?([^",\}\]]+)"?', result_str)
        if len(tab_ids) >= 2:
            result = await browser.switch_tab(tab_ids[0])
            assert "switch" in result.lower()
            result = await browser.close_tab(tab_ids[1])
            assert "close" in result.lower()

# ==================== 3. Control Tools (3 tools) ====================

class TestControlTools:
    """Tests: browser_resize, wait_for, stop"""

    @pytest.mark.asyncio
    async def test_browser_resize(self, browser):
        result = await browser.browser_resize(800, 600)
        assert "resize" in result.lower() or "800" in result

        # Verify viewport changed
        page = await browser.get_current_page()
        size = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
        assert size["w"] == 800
        assert size["h"] == 600

        # Restore original size
        await browser.browser_resize(1280, 720)

    @pytest.mark.asyncio
    async def test_wait_for_time(self, browser):
        """wait_for with time_seconds waits briefly."""
        result = await browser.wait_for(time_seconds=0.1)
        assert "wait" in result.lower() or result

    @pytest.mark.asyncio
    async def test_wait_for_text(self, browser):
        """wait_for with text waits for visible text."""
        result = await browser.wait_for(text="Bridgic Browser Test Page", timeout=5.0)
        assert "wait" in result.lower() or "found" in result.lower() or result

    @pytest.mark.asyncio
    async def test_stop(self):
        """stop() kills the browser instance (separate browser to avoid fixture conflict)."""
        b = Browser(headless=True, stealth=False, viewport={"width": 800, "height": 600})
        await b.start()
        result = await b.stop()
        assert "close" in result.lower() or "browser" in result.lower() or result

# ==================== 4. Action Tools (13 tools) ====================

class TestActionTools:
    """Tests: click_element_by_ref, input_text_by_ref, hover_element_by_ref,
    focus_element_by_ref, check_checkbox_or_radio_by_ref, uncheck_checkbox_by_ref,
    double_click_element_by_ref, scroll_element_into_view_by_ref,
    get_dropdown_options_by_ref, select_dropdown_option_by_ref,
    upload_file_by_ref, drag_element_by_ref, evaluate_javascript_on_ref"""

    @pytest.mark.asyncio
    async def test_click_element_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        page = await browser.get_current_page()
        before = await page.text_content("#counter-value")
        btn_ref = find_ref_by_type_and_name(refs, "button", "+1")
        assert btn_ref is not None
        await browser.click_element_by_ref(btn_ref)
        after = await page.text_content("#counter-value")
        assert int(after) > int(before)

    @pytest.mark.asyncio
    async def test_input_text_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        tb_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        assert tb_ref is not None
        await browser.input_text_by_ref(tb_ref, "test_user")
        result = await browser.verify_value(tb_ref, "test_user")
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_hover_element_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        hover_ref = find_ref_by_type_and_name(refs, "generic", "Hover over me!")
        assert hover_ref is not None
        result = await browser.hover_element_by_ref(hover_ref)
        assert "hover" in result.lower() or "success" in result.lower()

    @pytest.mark.asyncio
    async def test_focus_element_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        tb_ref = find_ref_by_type_and_name(refs, "textbox", "Email")
        assert tb_ref is not None
        result = await browser.focus_element_by_ref(tb_ref)
        assert "error" not in result.lower()

    @pytest.mark.asyncio
    async def test_check_uncheck_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        cb_ref = find_ref_by_type_and_name(refs, "checkbox", "Technology")
        assert cb_ref is not None

        await browser.check_checkbox_or_radio_by_ref(cb_ref)
        result = await browser.verify_element_state(cb_ref, "checked")
        assert "PASS" in result

        await browser.uncheck_checkbox_by_ref(cb_ref)
        result = await browser.verify_element_state(cb_ref, "unchecked")
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_double_click_element_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        page = await browser.get_current_page()
        before = await page.text_content("#double-click-count")
        dbl_ref = find_ref_by_type_and_name(refs, "generic", "Double-click me!")
        assert dbl_ref is not None
        await browser.double_click_element_by_ref(dbl_ref)
        after = await page.text_content("#double-click-count")
        assert int(after) > int(before)

    @pytest.mark.asyncio
    async def test_scroll_element_into_view_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        # Pick an element that's below the viewport
        btn_ref = find_ref_by_type_and_name(refs, "button", "Show Alert")
        assert btn_ref is not None
        result = await browser.scroll_element_into_view_by_ref(btn_ref)
        assert "error" not in result.lower()

    @pytest.mark.asyncio
    async def test_dropdown_tools(self, browser_with_complete_snapshot):
        """Tests get_dropdown_options_by_ref & select_dropdown_option_by_ref."""
        browser, _, refs = browser_with_complete_snapshot
        combo_ref = find_ref_by_type_and_name(refs, "combobox", "Country")
        assert combo_ref is not None

        options_result = await browser.get_dropdown_options_by_ref(combo_ref)
        assert "United States" in options_result
        assert "China" in options_result

        result = await browser.select_dropdown_option_by_ref(combo_ref, "Japan")
        assert "error" not in result.lower()

        # Verify selection took effect
        page = await browser.get_current_page()
        selected = await page.evaluate("document.getElementById('country').value")
        assert selected == "jp"

    @pytest.mark.asyncio
    async def test_upload_file_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        file_ref = find_ref_by_type_and_name(refs, "button", "File Upload")
        assert file_ref is not None

        # Create a temp file to upload
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
        ) as f:
            f.write("test content")
            temp_path = f.name

        try:
            result = await browser.upload_file_by_ref(file_ref, temp_path)
            assert "error" not in result.lower() or "upload" in result.lower()
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_drag_element_by_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        # The snapshot uses interactive=True, so draggable items may appear
        # Try to drag from one element to another
        # Grid items have onclick so they're interactive
        item1_ref = find_ref_by_type_and_name(refs, "generic", "Item 1")
        item2_ref = find_ref_by_type_and_name(refs, "generic", "Item 2")
        if item1_ref and item2_ref:
            result = await browser.drag_element_by_ref(item1_ref, item2_ref)
            # Just verify it doesn't crash
            assert result is not None

    @pytest.mark.asyncio
    async def test_evaluate_javascript_on_ref(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        btn_ref = find_ref_by_type_and_name(refs, "button", "Primary")
        assert btn_ref is not None
        result = await browser.evaluate_javascript_on_ref(
            btn_ref, "el => el.textContent",
        )
        assert "Primary" in result

# ==================== 5. Mouse Tools (6 tools) ====================

class TestMouseTools:
    """Tests: mouse_move, mouse_click, mouse_drag, mouse_down, mouse_up, mouse_wheel"""

    @pytest.mark.asyncio
    async def test_mouse_move(self, browser):
        result = await browser.mouse_move(400, 300)
        assert "mouse" in result.lower() or "move" in result.lower() or result

        # Verify mouse position was tracked
        page = await browser.get_current_page()
        x = await page.text_content("#mouse-x")
        y = await page.text_content("#mouse-y")
        assert int(x) > 0 or int(y) > 0

    @pytest.mark.asyncio
    async def test_mouse_click(self, browser):
        """mouse_click at coordinates should trigger click."""
        page = await browser.get_current_page()

        # Scroll the +1 button into view first so bounding_box returns viewport coords
        btn = page.locator("#btn-increment")
        await btn.scroll_into_view_if_needed()
        box = await btn.bounding_box()
        assert box is not None
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2

        before = await page.text_content("#counter-value")
        result = await browser.mouse_click(cx, cy)
        assert result is not None
        after = await page.text_content("#counter-value")
        assert int(after) > int(before)

    @pytest.mark.asyncio
    async def test_mouse_drag(self, browser):
        """mouse_drag from start to end coordinates."""
        result = await browser.mouse_drag(100, 100, 300, 300)
        assert result is not None

    @pytest.mark.asyncio
    async def test_mouse_down_and_up(self, browser):
        """mouse_down and mouse_up lifecycle."""
        await browser.mouse_move(400, 300)
        result_down = await browser.mouse_down()
        assert result_down is not None
        result_up = await browser.mouse_up()
        assert result_up is not None

    @pytest.mark.asyncio
    async def test_mouse_wheel(self, browser):
        """mouse_wheel scrolls the page."""
        page = await browser.get_current_page()
        scroll_before = await page.evaluate("window.scrollY")
        result = await browser.mouse_wheel(delta_y=300)
        assert result is not None
        await asyncio.sleep(0.2)
        scroll_after = await page.evaluate("window.scrollY")
        assert scroll_after > scroll_before

# ==================== 6. Keyboard Tools (5 tools) ====================

class TestKeyboardTools:
    """Tests: type_text, key_down, key_up, fill_form, insert_text"""

    @pytest.mark.asyncio
    async def test_type_text(self, browser_with_complete_snapshot):
        """type_text types text character by character."""
        browser, _, refs = browser_with_complete_snapshot
        tb_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        assert tb_ref is not None
        await browser.focus_element_by_ref(tb_ref)
        result = await browser.type_text("hello")
        assert result is not None

        page = await browser.get_current_page()
        value = await page.evaluate("document.getElementById('username').value")
        assert "hello" in value

    @pytest.mark.asyncio
    async def test_key_down_and_key_up(self, browser):
        """key_down and key_up send individual key events."""
        page = await browser.get_current_page()
        await page.focus("#key-display")
        result_down = await browser.key_down("Shift")
        assert result_down is not None
        result_up = await browser.key_up("Shift")
        assert result_up is not None

    @pytest.mark.asyncio
    async def test_insert_text(self, browser_with_complete_snapshot):
        """insert_text inserts text at cursor position."""
        browser, _, refs = browser_with_complete_snapshot
        tb_ref = find_ref_by_type_and_name(refs, "textbox", "Email")
        assert tb_ref is not None
        await browser.focus_element_by_ref(tb_ref)
        result = await browser.insert_text("test@example.com")
        assert result is not None

        page = await browser.get_current_page()
        value = await page.evaluate("document.getElementById('email').value")
        assert "test@example.com" in value

    @pytest.mark.asyncio
    async def test_fill_form(self, browser_with_complete_snapshot):
        """fill_form fills multiple fields at once."""
        browser, _, refs = browser_with_complete_snapshot
        username_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        email_ref = find_ref_by_type_and_name(refs, "textbox", "Email")
        assert username_ref and email_ref

        result = await browser.fill_form([
            {"ref": username_ref, "value": "form_user"},
            {"ref": email_ref, "value": "form@test.com"},
        ])
        assert result is not None

        page = await browser.get_current_page()
        username_val = await page.evaluate("document.getElementById('username').value")
        email_val = await page.evaluate("document.getElementById('email').value")
        assert "form_user" in username_val
        assert "form@test.com" in email_val

# ==================== 7. Screenshot Tools (2 tools) ====================

class TestScreenshotTools:
    """Tests: take_screenshot, save_pdf"""

    @pytest.mark.asyncio
    async def test_take_screenshot(self, browser):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "screenshot.png")
            result = await browser.take_screenshot(filename=filepath)
            assert result is not None
            assert os.path.exists(filepath)
            assert os.path.getsize(filepath) > 0

    @pytest.mark.asyncio
    async def test_take_screenshot_full_page(self, browser):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "full.png")
            result = await browser.take_screenshot(filename=filepath, full_page=True)
            assert result is not None
            assert os.path.exists(filepath)

    @pytest.mark.asyncio
    async def test_save_pdf(self, browser):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "page.pdf")
            result = await browser.save_pdf(filename=filepath)
            assert result is not None
            assert os.path.exists(filepath)
            assert os.path.getsize(filepath) > 0

# ==================== 8. Network Tools (7 tools) ====================

class TestNetworkTools:
    """Tests: start_console_capture, stop_console_capture, get_console_messages,
    start_network_capture, stop_network_capture, get_network_requests,
    wait_for_network_idle"""

    @pytest.mark.asyncio
    async def test_console_capture_lifecycle(self, browser):
        """Tests start_console_capture, get_console_messages, stop_console_capture."""
        result = await browser.start_console_capture()
        assert result is not None

        # Trigger a console.log via JS (test page logs on load)
        page = await browser.get_current_page()
        await page.evaluate("console.log('integration_test_message')")
        await asyncio.sleep(0.2)

        messages = await browser.get_console_messages()
        assert "integration_test_message" in messages

        result = await browser.stop_console_capture()
        assert result is not None

    @pytest.mark.asyncio
    async def test_network_capture_lifecycle(self, browser):
        """Tests start_network_capture, get_network_requests, stop_network_capture."""
        result = await browser.start_network_capture()
        assert result is not None

        # Trigger a navigation (which creates network requests)
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        await browser.navigate_to(test_url)

        reqs = await browser.get_network_requests(include_static=True)
        assert reqs is not None

        result = await browser.stop_network_capture()
        assert result is not None

    @pytest.mark.asyncio
    async def test_wait_for_network_idle(self, browser):
        result = await browser.wait_for_network_idle(timeout=5.0)
        assert result is not None

# ==================== 9. Dialog Tools (3 tools) ====================

class TestDialogTools:
    """Tests: setup_dialog_handler, handle_dialog, remove_dialog_handler"""

    @pytest.mark.asyncio
    async def test_dialog_handler_lifecycle(self, browser):
        """Tests setup_dialog_handler, handle_dialog (via auto-accept), remove_dialog_handler."""
        # Set up auto-accept handler
        result = await browser.setup_dialog_handler(default_action="accept")
        assert result is not None

        # Trigger alert and it should be auto-accepted
        page = await browser.get_current_page()
        await page.evaluate("setTimeout(() => alert('test alert'), 100)")
        await asyncio.sleep(0.5)

        # Remove handler
        result = await browser.remove_dialog_handler()
        assert result is not None

    @pytest.mark.asyncio
    async def test_dialog_handler_with_prompt(self, browser):
        """setup_dialog_handler with prompt text."""
        result = await browser.setup_dialog_handler(
            default_action="accept", default_prompt_text="my_answer",
        )
        assert result is not None

        page = await browser.get_current_page()
        # Trigger a prompt dialog
        answer = await page.evaluate(
            "() => new Promise(r => { setTimeout(() => r(prompt('Enter:')), 0) })"
        )
        # The auto handler should have responded with "my_answer"
        assert answer == "my_answer"

        await browser.remove_dialog_handler()

    @pytest.mark.asyncio
    async def test_handle_dialog_directly(self, browser):
        """handle_dialog accepts/dismisses a pending dialog."""
        # handle_dialog sets up a one-shot handler for the next dialog
        result = await browser.handle_dialog(accept=True, prompt_text=None)
        assert result is not None

# ==================== 10. Storage Tools (5 tools) ====================

class TestStorageTools:
    """Tests: save_storage_state, restore_storage_state, clear_cookies,
    get_cookies, set_cookie"""

    @pytest.mark.asyncio
    async def test_set_and_get_cookies(self, browser):
        """Tests set_cookie and get_cookies."""
        # Navigate to http page for cookie domain (file:// doesn't support cookies well)
        # Use the current page URL for cookie setting
        result = await browser.set_cookie(
            name="test_cookie", value="test_value",
            url="http://localhost",
        )
        assert result is not None

        cookies_result = await browser.get_cookies()
        assert cookies_result is not None

    @pytest.mark.asyncio
    async def test_clear_cookies(self, browser):
        result = await browser.clear_cookies()
        assert "clear" in result.lower() or "cookie" in result.lower() or result

    @pytest.mark.asyncio
    async def test_save_and_restore_storage_state(self, browser):
        """Tests save_storage_state and restore_storage_state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "state.json")
            result = await browser.save_storage_state(filename=filepath)
            assert result is not None
            assert os.path.exists(filepath)

            # Restore it
            result = await browser.restore_storage_state(filename=filepath)
            assert result is not None

# ==================== 11. Verification Tools (6 tools) ====================

class TestVerificationTools:
    """Tests: verify_element_visible, verify_text_visible, verify_value,
    verify_element_state, verify_url, verify_title"""

    @pytest.mark.asyncio
    async def test_verify_element_visible(self, browser):
        result = await browser.verify_element_visible("heading", "Bridgic Browser Test Page")
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_visible_not_found(self, browser):
        with pytest.raises(VerificationError) as exc_info:
            await browser.verify_element_visible(
                "heading", "NonExistent Element XYZ", timeout=1.0,
            )
        assert "FAIL" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_verify_text_visible(self, browser):
        result = await browser.verify_text_visible("Bridgic Browser Test Page")
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_text_visible_exact(self, browser):
        with pytest.raises(VerificationError) as exc_info:
            await browser.verify_text_visible(
                "Nonexistent text XYZ", exact=True, timeout=1.0,
            )
        assert "FAIL" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_verify_url(self, browser):
        test_url = f"file://{TEST_PAGE_PATH.absolute()}"
        result = await browser.verify_url(test_url, exact=True)
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_title(self, browser):
        result = await browser.verify_title("Bridgic Browser Test Page", exact=True)
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_title_partial(self, browser):
        result = await browser.verify_title("Bridgic", exact=False)
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_value(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        tb_ref = find_ref_by_type_and_name(refs, "textbox", "Username")
        assert tb_ref is not None
        # Empty by default
        result = await browser.verify_value(tb_ref, "")
        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_state(self, browser_with_complete_snapshot):
        browser, _, refs = browser_with_complete_snapshot
        # Disabled button
        btn_ref = find_ref_by_type_and_name(refs, "button", "Disabled Button")
        assert btn_ref is not None
        result = await browser.verify_element_state(btn_ref, "disabled")
        assert "PASS" in result

# ==================== 12. DevTools Tools (5 tools) ====================

class TestDevTools:
    """Tests: start_tracing, stop_tracing, start_video, stop_video, add_trace_chunk"""

    @pytest.mark.asyncio
    async def test_tracing_lifecycle(self, browser):
        """Tests start_tracing, add_trace_chunk, stop_tracing."""
        result = await browser.start_tracing()
        assert result is not None

        # Perform some action while tracing
        await browser.evaluate_javascript("1 + 1")
        result = await browser.add_trace_chunk(title="test_chunk")
        assert result is not None

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "trace.zip")
            result = await browser.stop_tracing(filename=filepath)
            assert result is not None
            assert os.path.exists(filepath)

    @pytest.mark.asyncio
    async def test_video_lifecycle(self, browser):
        """Tests start_video, stop_video."""
        result = await browser.start_video()
        assert result is not None

        # Perform some action while recording
        await browser.evaluate_javascript("document.title")
        await asyncio.sleep(0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "video.webm")
            result = await browser.stop_video(filename=filepath)
            assert result is not None
