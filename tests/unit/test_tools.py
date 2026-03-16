"""
Unit tests for Browser Tools.

This file tests all browser tools including:
- Navigation tools
- Page control tools
- Tab management tools
- Element interaction tools
- Mouse tools (coordinate-based)
- Keyboard tools
- Screenshot tools
- Network tools
- Dialog tools
- Storage tools
- Verification tools
- DevTools (tracing/video)
- State tools
- BrowserToolSetBuilder
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from bridgic.browser._cli_catalog import (
    build_tool_categories_from_help_sections,
    build_tool_presets_from_cli_preset_commands,
    CLI_COMMAND_TO_TOOL_METHOD,
    CLI_PRESET_COMMANDS,
)
from bridgic.browser.session import Browser

# ==================== Fixtures ====================

@pytest.fixture
def mock_browser():
    """Create a comprehensive mock Browser instance."""
    browser = MagicMock()
    browser.navigate_to = AsyncMock()
    browser.get_current_page = AsyncMock()
    browser.get_pages = MagicMock(return_value=[])
    browser.get_all_page_descs = AsyncMock(return_value=[])
    browser.switch_to_page = AsyncMock(return_value=(True, "Switched"))
    browser.close_page = AsyncMock(return_value=(True, "Closed"))
    browser.new_page = AsyncMock()
    browser.get_current_page_info = AsyncMock()
    browser.get_snapshot = AsyncMock()
    browser.get_element_by_ref = AsyncMock()

    # Browser tool methods (all async)
    browser.search = AsyncMock(return_value="Searched on Duckduckgo for 'test query'")
    browser.navigate_to_url = AsyncMock(return_value="Navigated to https://example.com")
    browser.go_back = AsyncMock(return_value="Navigated back")
    browser.go_forward = AsyncMock(return_value="Navigated forward")
    browser.reload_page = AsyncMock(return_value="Page reloaded")
    browser.scroll_to_text = AsyncMock(return_value="Scrolled to text")
    browser.press_key = AsyncMock(return_value="Pressed key")
    browser.evaluate_javascript = AsyncMock(return_value="result")
    browser.get_current_page_info_str = AsyncMock(return_value="URL: https://example.com/test\nTitle: Test Page")
    browser.new_tab = AsyncMock(return_value="Created new blank tab")
    browser.get_tabs = AsyncMock(return_value="Tab 1")
    browser.switch_tab = AsyncMock(return_value="Switched to tab")
    browser.close_tab = AsyncMock(return_value="Closed tab")
    browser.browser_close = AsyncMock(return_value="Browser closed")
    browser.browser_resize = AsyncMock(return_value="Resized browser to 800x600")
    browser.wait_for = AsyncMock(return_value="Waited")
    browser.get_snapshot_text = AsyncMock(return_value="- button 'Submit' [ref=e1]")
    browser.input_text_by_ref = AsyncMock(return_value="Input text")
    browser.click_element_by_ref = AsyncMock(return_value="Clicked e1")
    browser.get_dropdown_options_by_ref = AsyncMock(return_value="Option 1\nOption 2")
    browser.select_dropdown_option_by_ref = AsyncMock(return_value="Selected")
    browser.hover_element_by_ref = AsyncMock(return_value="Hovered")
    browser.focus_element_by_ref = AsyncMock(return_value="Focused")
    browser.evaluate_javascript_on_ref = AsyncMock(return_value="result")
    browser.upload_file_by_ref = AsyncMock(return_value="Uploaded")
    browser.drag_element_by_ref = AsyncMock(return_value="Dragged")
    browser.check_checkbox_by_ref = AsyncMock(return_value="Checked")
    browser.uncheck_checkbox_by_ref = AsyncMock(return_value="Unchecked")
    browser.double_click_element_by_ref = AsyncMock(return_value="Double-clicked")
    browser.scroll_element_into_view_by_ref = AsyncMock(return_value="Scrolled into view")
    browser.mouse_move = AsyncMock(return_value="Moved mouse")
    browser.mouse_click = AsyncMock(return_value="Clicked")
    browser.mouse_drag = AsyncMock(return_value="Dragged")
    browser.mouse_down = AsyncMock(return_value="Mouse down")
    browser.mouse_up = AsyncMock(return_value="Mouse up")
    browser.mouse_wheel = AsyncMock(return_value="Scrolled")
    browser.type_text = AsyncMock(return_value="Typed")
    browser.key_down = AsyncMock(return_value="Key down")
    browser.key_up = AsyncMock(return_value="Key up")
    browser.fill_form = AsyncMock(return_value="Filled form")
    browser.insert_text = AsyncMock(return_value="Inserted text")
    browser.take_screenshot = AsyncMock(return_value=b"fake_screenshot_data")
    browser.save_pdf = AsyncMock(return_value=b"fake_pdf_data")
    browser.start_console_capture = AsyncMock(return_value="Console capture started")
    browser.stop_console_capture = AsyncMock(return_value="Console capture stopped")
    browser.get_console_messages = AsyncMock(return_value="No messages")
    browser.start_network_capture = AsyncMock(return_value="Network capture started")
    browser.stop_network_capture = AsyncMock(return_value="Network capture stopped")
    browser.get_network_requests = AsyncMock(return_value="No requests")
    browser.wait_for_network_idle = AsyncMock(return_value="Network idle")
    browser.setup_dialog_handler = AsyncMock(return_value="Dialog handler set")
    browser.handle_dialog = AsyncMock(return_value="Dialog handled")
    browser.remove_dialog_handler = AsyncMock(return_value="Dialog handler removed")
    browser.save_storage_state = AsyncMock(return_value="State saved")
    browser.restore_storage_state = AsyncMock(return_value="State restored")
    browser.clear_cookies = AsyncMock(return_value="Cookies cleared")
    browser.get_cookies = AsyncMock(return_value="[]")
    browser.set_cookie = AsyncMock(return_value="Cookie set")
    browser.verify_element_visible = AsyncMock(return_value="PASS: Element is visible")
    browser.verify_text_visible = AsyncMock(return_value="PASS: Text is visible")
    browser.verify_value = AsyncMock(return_value="PASS: Value matches")
    browser.verify_element_state = AsyncMock(return_value="PASS: Element state matches")
    browser.verify_url = AsyncMock(return_value="PASS: URL matches")
    browser.verify_title = AsyncMock(return_value="PASS: Title matches")
    browser.start_tracing = AsyncMock(return_value="Tracing started")
    browser.stop_tracing = AsyncMock(return_value="Tracing stopped")
    browser.start_video = AsyncMock(return_value="Video started")
    browser.stop_video = AsyncMock(return_value="Video stopped")
    browser.add_trace_chunk = AsyncMock(return_value="Chunk added")

    # Mock page
    mock_page = MagicMock()
    mock_page.url = "https://example.com/test"
    mock_page.title = AsyncMock(return_value="Test Page")

    # Mock mouse
    mock_page.mouse = MagicMock()
    mock_page.mouse.move = AsyncMock()
    mock_page.mouse.click = AsyncMock()
    mock_page.mouse.down = AsyncMock()
    mock_page.mouse.up = AsyncMock()
    mock_page.mouse.wheel = AsyncMock()

    # Mock keyboard
    mock_page.keyboard = MagicMock()
    mock_page.keyboard.press = AsyncMock()
    mock_page.keyboard.down = AsyncMock()
    mock_page.keyboard.up = AsyncMock()
    mock_page.keyboard.insert_text = AsyncMock()

    # Mock other page methods
    mock_page.go_back = AsyncMock()
    mock_page.go_forward = AsyncMock()
    mock_page.reload = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"fake_screenshot_data")
    mock_page.pdf = AsyncMock(return_value=b"fake_pdf_data")
    mock_page.evaluate = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.set_viewport_size = AsyncMock()
    mock_page.get_by_text = MagicMock()
    mock_page.get_by_role = MagicMock()

    # Mock context
    mock_context = MagicMock()
    mock_context.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})
    mock_context.add_cookies = AsyncMock()
    mock_context.clear_cookies = AsyncMock()
    mock_context.cookies = AsyncMock(return_value=[])
    mock_context.tracing = MagicMock()
    mock_context.tracing.start = AsyncMock()
    mock_context.tracing.stop = AsyncMock()
    mock_context.new_page = AsyncMock()
    mock_page.context = mock_context

    browser.get_current_page.return_value = mock_page
    browser._page = mock_page
    browser._context = mock_context

    return browser

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)

# ==================== Navigation Tools Tests ====================

class TestNavigationTools:
    """Tests for navigation tools."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("engine,expected_domain", [
        ("duckduckgo", "duckduckgo.com"),
        ("google", "google.com"),
        ("bing", "bing.com"),
    ])
    async def test_search_engines(self, mock_browser, engine, expected_domain):
        """Test search with different engines."""

        result = await Browser.search(mock_browser, "test query", engine)

        mock_browser.navigate_to.assert_called_once()
        call_url = mock_browser.navigate_to.call_args[0][0]
        assert expected_domain in call_url

    @pytest.mark.asyncio
    async def test_search_invalid_engine(self, mock_browser):
        """Test search with invalid engine returns error."""

        result = await Browser.search(mock_browser, "test query", "invalid")

        assert "unsupported" in result.lower() or "error" in result.lower()
        mock_browser.navigate_to.assert_not_called()

    @pytest.mark.asyncio
    async def test_navigate_to_url(self, mock_browser):
        """Test navigate_to_url."""

        result = await Browser.navigate_to_url(mock_browser, "https://example.com")

        mock_browser.navigate_to.assert_called_once_with(
            "https://example.com", wait_until="domcontentloaded", timeout=None
        )
        assert "Navigated to" in result

    @pytest.mark.asyncio
    async def test_navigate_to_url_adds_protocol(self, mock_browser):
        """Test navigate_to_url adds http:// if missing."""

        result = await Browser.navigate_to_url(mock_browser, "example.com")

        mock_browser.navigate_to.assert_called_once_with(
            "http://example.com", wait_until="domcontentloaded", timeout=None
        )

    @pytest.mark.asyncio
    async def test_navigate_to_url_empty(self, mock_browser):
        """Test navigate_to_url with empty URL."""

        result = await Browser.navigate_to_url(mock_browser, "")

        assert "empty" in result.lower()
        mock_browser.navigate_to.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dangerous_url", [
        "javascript:alert(1)",
        "data:text/html,<h1>test</h1>",
    ])
    async def test_navigate_to_url_blocks_dangerous(self, mock_browser, dangerous_url):
        """Test navigate_to_url blocks dangerous URLs."""

        result = await Browser.navigate_to_url(mock_browser, dangerous_url)

        assert "not allowed" in result.lower()
        mock_browser.navigate_to.assert_not_called()

    @pytest.mark.asyncio
    async def test_go_back(self, mock_browser):
        """Test go_back."""

        result = await Browser.go_back(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.go_back.assert_called_once()
        assert "back" in result.lower()

    @pytest.mark.asyncio
    async def test_go_forward(self, mock_browser):
        """Test go_forward."""

        result = await Browser.go_forward(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.go_forward.assert_called_once()
        assert "forward" in result.lower()

    @pytest.mark.asyncio
    async def test_reload_page(self, mock_browser):
        """Test reload_page."""

        result = await Browser.reload_page(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.reload.assert_called_once()

# ==================== Page Control Tools Tests ====================

class TestPageControlTools:
    """Tests for page control tools."""

    @pytest.mark.asyncio
    async def test_scroll_to_text(self, mock_browser):
        """Test scroll_to_text."""

        mock_page = mock_browser.get_current_page.return_value
        mock_first_locator = MagicMock()
        mock_first_locator.bounding_box = AsyncMock(return_value={"x": 0, "y": 100})
        mock_first_locator.scroll_into_view_if_needed = AsyncMock()

        mock_locator = MagicMock()
        mock_locator.first = mock_first_locator
        mock_page.get_by_text = MagicMock(return_value=mock_locator)

        result = await Browser.scroll_to_text(mock_browser, "Some text")

        mock_page.get_by_text.assert_called_with("Some text", exact=False)

    @pytest.mark.asyncio
    async def test_evaluate_javascript(self, mock_browser):
        """Test evaluate_javascript."""

        mock_page = mock_browser.get_current_page.return_value
        mock_page.evaluate.return_value = {"result": "test"}

        result = await Browser.evaluate_javascript(mock_browser, "return {result: 'test'}")

        mock_page.evaluate.assert_called()
        assert "result" in result

    @pytest.mark.asyncio
    async def test_wait_for_time(self, mock_browser):
        """Test wait_for function with time parameter."""
        import time

        start = time.time()
        result = await Browser.wait_for(mock_browser, time_seconds=0.5)
        elapsed = time.time() - start

        assert elapsed >= 0.5
        assert "wait" in result.lower() or "0.5" in result

    @pytest.mark.asyncio
    async def test_wait_for_text(self, mock_browser):
        """Test wait_for function with text parameter."""

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_locator.first = MagicMock()
        mock_locator.first.wait_for = AsyncMock()
        mock_page.get_by_text = MagicMock(return_value=mock_locator)

        result = await Browser.wait_for(mock_browser, text="Loading complete")

        mock_page.get_by_text.assert_called_once_with("Loading complete", exact=False)
        mock_locator.first.wait_for.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_current_page_info(self, mock_browser):
        """Test get_current_page_info_str."""

        mock_browser._get_page_info = AsyncMock(return_value=MagicMock(
            url="https://example.com",
            title="Example",
            viewport_width=1920,
            viewport_height=1080,
            page_width=1920,
            page_height=3000,
            scroll_x=0,
            scroll_y=0,
        ))

        result = await Browser.get_current_page_info_str(mock_browser)

        mock_browser._get_page_info.assert_called_once()
        assert "example.com" in result

    @pytest.mark.asyncio
    async def test_browser_resize(self, mock_browser):
        """Test resizing browser viewport."""

        result = await Browser.browser_resize(mock_browser, width=1280, height=720)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.set_viewport_size.assert_called_once_with({"width": 1280, "height": 720})
        assert "1280" in result and "720" in result

# ==================== Tab Management Tools Tests ====================

class TestTabManagementTools:
    """Tests for tab management tools."""

    @pytest.mark.asyncio
    async def test_new_tab(self, mock_browser):
        """Test new_tab."""

        mock_browser.new_page.return_value = MagicMock()

        result = await Browser.new_tab(mock_browser)

        mock_browser.new_page.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_tab_with_url(self, mock_browser):
        """Test new_tab with URL."""

        mock_browser.new_page.return_value = MagicMock()

        result = await Browser.new_tab(mock_browser, "https://example.com")

        mock_browser.new_page.assert_called_once_with(
            "https://example.com", wait_until="domcontentloaded", timeout=None
        )

    @pytest.mark.asyncio
    async def test_get_tabs(self, mock_browser):
        """Test get_tabs."""

        mock_browser.get_all_page_descs.return_value = [
            MagicMock(url="https://example.com", title="Example", page_id="1"),
            MagicMock(url="https://test.com", title="Test", page_id="2"),
        ]

        result = await Browser.get_tabs(mock_browser)

        mock_browser.get_all_page_descs.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_tab(self, mock_browser):
        """Test switch_tab."""

        result = await Browser.switch_tab(mock_browser, "page_123")

        mock_browser.switch_to_page.assert_called_once_with("page_123")

    @pytest.mark.asyncio
    async def test_close_tab(self, mock_browser):
        """Test close_tab."""

        result = await Browser.close_tab(mock_browser, "page_123")

        mock_browser.close_page.assert_called_once_with("page_123")

# ==================== Element Interaction Tools Tests ====================

class TestElementInteractionTools:
    """Tests for element interaction tools."""

    @pytest.mark.asyncio
    async def test_click_element_by_ref(self, mock_browser):
        """Test click_element_by_ref — element not covered (bounding_box returns None → direct click)."""

        mock_locator = MagicMock()
        mock_locator.click = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.click_element_by_ref(mock_browser, "e1")

        mock_browser.get_element_by_ref.assert_called_once_with("e1")
        mock_locator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_click_element_by_ref_not_covered(self, mock_browser):
        """Test click_element_by_ref — element visible and not covered → direct click."""

        mock_locator = MagicMock()
        mock_locator.click = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value={"x": 10, "y": 20, "width": 100, "height": 40})
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(return_value=False)  # not covered
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.click_element_by_ref(mock_browser, "e1")

        mock_locator.click.assert_called_once()
        assert "Clicked" in result

    @pytest.mark.asyncio
    async def test_click_element_by_ref_covered_uses_elementFromPoint(self, mock_browser):
        """Test click_element_by_ref — element covered by overlay → elementFromPoint click."""

        mock_page = AsyncMock()
        mock_browser.get_current_page = AsyncMock(return_value=mock_page)

        mock_locator = MagicMock()
        mock_locator.click = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value={"x": 10, "y": 20, "width": 100, "height": 40})
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(return_value=True)  # covered by overlay
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.click_element_by_ref(mock_browser, "e1")

        mock_locator.click.assert_not_called()
        mock_page.evaluate.assert_called_once()
        assert "Clicked" in result

    @pytest.mark.asyncio
    async def test_click_element_by_ref_not_found(self, mock_browser):
        """Test click_element_by_ref when element not found."""

        mock_browser.get_element_by_ref.return_value = None

        result = await Browser.click_element_by_ref(mock_browser, "e999")

        assert "not found" in result.lower() or "failed" in result.lower() or "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_input_text_by_ref(self, mock_browser):
        """Test input_text_by_ref."""

        mock_locator = MagicMock()
        mock_locator.clear = AsyncMock()
        mock_locator.fill = AsyncMock()
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.input_text_by_ref(mock_browser, "e1", "test text")

        mock_locator.clear.assert_called_once()
        mock_locator.fill.assert_called_once_with("test text")

    @pytest.mark.asyncio
    async def test_input_text_by_ref_secret(self, mock_browser):
        """Test input_text_by_ref with secret flag doesn't log value."""

        mock_locator = MagicMock()
        mock_locator.clear = AsyncMock()
        mock_locator.fill = AsyncMock()
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.input_text_by_ref(
            mock_browser, "e1", "secret_password", is_secret=True
        )

        mock_locator.fill.assert_called_once_with("secret_password")
        assert "secret_password" not in result

    @pytest.mark.asyncio
    async def test_hover_element_by_ref(self, mock_browser):
        """Test hover_element_by_ref — not covered (bounding_box None → direct hover)."""

        mock_locator = MagicMock()
        mock_locator.hover = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.hover_element_by_ref(mock_browser, "e1")

        mock_locator.hover.assert_called_once()

    @pytest.mark.asyncio
    async def test_focus_element_by_ref(self, mock_browser):
        """Test focus_element_by_ref."""

        mock_locator = MagicMock()
        mock_locator.focus = AsyncMock()
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.focus_element_by_ref(mock_browser, "e1")

        mock_locator.focus.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_dropdown_options_by_ref(self, mock_browser):
        """Test get_dropdown_options_by_ref."""

        mock_locator = MagicMock()
        mock_option1 = MagicMock()
        mock_option1.text_content = AsyncMock(return_value="Option 1")
        mock_option1.get_attribute = AsyncMock(return_value="value1")
        mock_option2 = MagicMock()
        mock_option2.text_content = AsyncMock(return_value="Option 2")
        mock_option2.get_attribute = AsyncMock(return_value="value2")

        mock_locator.locator = MagicMock()
        mock_locator.locator.return_value.all = AsyncMock(
            return_value=[mock_option1, mock_option2]
        )

        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.get_dropdown_options_by_ref(mock_browser, "e1")

        assert "Option 1" in result or "value1" in result

    @pytest.mark.asyncio
    async def test_get_dropdown_options_by_ref_avoids_ambiguous_global_options(self, mock_browser):
        """When multiple visible listboxes exist, avoid global fallback option matching."""

        mock_locator = MagicMock()
        mock_locator.get_attribute = AsyncMock(return_value=None)

        mock_empty = MagicMock()
        mock_empty.all = AsyncMock(return_value=[])
        mock_locator.locator = MagicMock(return_value=mock_empty)

        listbox_1 = MagicMock()
        listbox_1.is_visible = AsyncMock(return_value=True)
        listbox_2 = MagicMock()
        listbox_2.is_visible = AsyncMock(return_value=True)

        listbox_locator = MagicMock()
        listbox_locator.all = AsyncMock(return_value=[listbox_1, listbox_2])

        mock_page = MagicMock()
        mock_page.locator = MagicMock(return_value=listbox_locator)
        mock_browser.get_current_page = AsyncMock(return_value=mock_page)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.get_dropdown_options_by_ref(mock_browser, "e1")

        assert result == "This dropdown has no options"

    @pytest.mark.asyncio
    async def test_select_dropdown_option_by_ref(self, mock_browser):
        """Test select_dropdown_option_by_ref."""

        mock_locator = MagicMock()
        mock_locator.evaluate = AsyncMock(return_value="select")
        mock_locator.select_option = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.select_dropdown_option_by_ref(mock_browser, "e1", "Option 1")

        mock_locator.select_option.assert_called()

    @pytest.mark.asyncio
    async def test_upload_file_by_ref(self, mock_browser, temp_dir):
        """Test upload_file_by_ref."""

        test_file = temp_dir / "test.txt"
        test_file.write_text("test content")

        mock_locator = MagicMock()
        mock_locator.evaluate = AsyncMock(return_value="input")
        mock_locator.get_attribute = AsyncMock(return_value="file")
        mock_locator.set_input_files = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.upload_file_by_ref(mock_browser, "e1", str(test_file))

        mock_locator.set_input_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_drag_element_by_ref(self, mock_browser):
        """Test dragging element to another element."""

        mock_source = MagicMock()
        mock_source.bounding_box = AsyncMock(return_value={"x": 100, "y": 100, "width": 50, "height": 50})
        mock_source.drag_to = AsyncMock()

        mock_target = MagicMock()
        mock_target.bounding_box = AsyncMock(return_value={"x": 300, "y": 300, "width": 50, "height": 50})

        async def get_element(ref):
            return mock_source if ref == "e1" else mock_target

        mock_browser.get_element_by_ref.side_effect = get_element

        result = await Browser.drag_element_by_ref(mock_browser, start_ref="e1", end_ref="e2")

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_check_checkbox_by_ref(self, mock_browser):
        """Test checking a checkbox — element not covered (bounding_box None → direct check)."""

        mock_locator = MagicMock()
        mock_locator.check = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(side_effect=["input", False, True])
        mock_locator.get_attribute = AsyncMock(return_value="checkbox")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.check_checkbox_by_ref(mock_browser, ref="e1")

        mock_locator.check.assert_called_once()
        assert "check" in result.lower()

    @pytest.mark.asyncio
    async def test_uncheck_checkbox_by_ref(self, mock_browser):
        """Test unchecking a checkbox — not covered (bounding_box None → direct uncheck)."""

        mock_locator = MagicMock()
        mock_locator.uncheck = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(side_effect=["input", True, False])
        mock_locator.get_attribute = AsyncMock(return_value="checkbox")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.uncheck_checkbox_by_ref(mock_browser, ref="e1")

        mock_locator.uncheck.assert_called_once()
        assert "uncheck" in result.lower()

    @pytest.mark.asyncio
    async def test_uncheck_checkbox_by_ref_covered_uses_elementFromPoint(self, mock_browser):
        """Test uncheck — element covered by overlay → elementFromPoint click."""

        mock_page = AsyncMock()
        mock_browser.get_current_page = AsyncMock(return_value=mock_page)

        mock_locator = MagicMock()
        mock_locator.uncheck = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value={"x": 10, "y": 20, "width": 100, "height": 40})
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(side_effect=["input", True, True, False])
        mock_locator.get_attribute = AsyncMock(return_value="checkbox")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.uncheck_checkbox_by_ref(mock_browser, ref="e1")

        mock_locator.uncheck.assert_not_called()
        mock_page.evaluate.assert_called_once()
        assert "Unchecked" in result

    @pytest.mark.asyncio
    async def test_check_custom_checkbox_uses_click_instead_of_check(self, mock_browser):
        """Custom role checkbox/radio should use click path, not locator.check()."""

        mock_locator = MagicMock()
        mock_locator.check = AsyncMock()
        mock_locator.click = AsyncMock()
        mock_locator.dispatch_event = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(side_effect=["div", False, True])
        mock_locator.get_attribute = AsyncMock(return_value=None)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.check_checkbox_by_ref(mock_browser, ref="e1")

        mock_locator.check.assert_not_called()
        mock_locator.click.assert_called_once()
        assert "Checked" in result

    @pytest.mark.asyncio
    async def test_uncheck_custom_checkbox_uses_click_instead_of_uncheck(self, mock_browser):
        """Custom role checkbox/radio should use click path, not locator.uncheck()."""

        mock_locator = MagicMock()
        mock_locator.uncheck = AsyncMock()
        mock_locator.click = AsyncMock()
        mock_locator.dispatch_event = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(side_effect=["div", True, False])
        mock_locator.get_attribute = AsyncMock(return_value=None)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.uncheck_checkbox_by_ref(mock_browser, ref="e1")

        mock_locator.uncheck.assert_not_called()
        mock_locator.click.assert_called_once()
        assert "Unchecked" in result

    @pytest.mark.asyncio
    async def test_check_custom_checkbox_reports_failure_when_state_not_changed(self, mock_browser):
        """Custom checkbox: click path must fail if checked state does not change."""

        mock_locator = MagicMock()
        mock_locator.click = AsyncMock()
        mock_locator.dispatch_event = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        # tag=input? no, custom div; initially unchecked -> still unchecked after click
        mock_locator.evaluate = AsyncMock(side_effect=["div", False, False])
        mock_locator.get_attribute = AsyncMock(return_value=None)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.check_checkbox_by_ref(mock_browser, ref="e1")

        mock_locator.click.assert_called_once()
        assert result.startswith("Failed to check element e1")

    @pytest.mark.asyncio
    async def test_uncheck_custom_checkbox_reports_failure_when_state_not_changed(self, mock_browser):
        """Custom checkbox: click path must fail if checked state does not change."""

        mock_locator = MagicMock()
        mock_locator.click = AsyncMock()
        mock_locator.dispatch_event = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        # custom div; initially checked -> still checked after click
        mock_locator.evaluate = AsyncMock(side_effect=["div", True, True])
        mock_locator.get_attribute = AsyncMock(return_value=None)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.uncheck_checkbox_by_ref(mock_browser, ref="e1")

        mock_locator.click.assert_called_once()
        assert result.startswith("Failed to uncheck element e1")

    @pytest.mark.asyncio
    async def test_uncheck_native_radio_skips_post_condition_check(self, mock_browser):
        """Native radio inputs cannot be unchecked by clicking; post-condition must be skipped."""

        mock_locator = MagicMock()
        mock_locator.uncheck = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        # evaluate: [tagName="input", already_checked=True]
        mock_locator.evaluate = AsyncMock(side_effect=["input", True])
        # get_attribute: [type="radio" for is_native check, type="radio" for post-condition]
        mock_locator.get_attribute = AsyncMock(return_value="radio")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.uncheck_checkbox_by_ref(mock_browser, ref="e1")

        # Radio remains checked after click — must NOT report failure
        assert "Unchecked" in result
        assert "Failed" not in result

    @pytest.mark.asyncio
    async def test_hover_element_by_ref_covered_uses_mouse_move(self, mock_browser):
        """Test hover — element covered → mouse.move to coordinates instead of locator.hover."""

        mock_page = AsyncMock()
        mock_browser.get_current_page = AsyncMock(return_value=mock_page)

        mock_locator = MagicMock()
        mock_locator.hover = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value={"x": 10, "y": 20, "width": 100, "height": 40})
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.evaluate = AsyncMock(return_value=True)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.hover_element_by_ref(mock_browser, ref="e1")

        mock_locator.hover.assert_not_called()
        mock_page.mouse.move.assert_called_once_with(60.0, 40.0)
        assert "Hovered" in result

    @pytest.mark.asyncio
    async def test_double_click_element_by_ref(self, mock_browser):
        """Test double-clicking an element — element not covered (bounding_box None → direct dblclick)."""

        mock_locator = MagicMock()
        mock_locator.dblclick = AsyncMock()
        mock_locator.bounding_box = AsyncMock(return_value=None)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.double_click_element_by_ref(mock_browser, ref="e1")

        mock_locator.dblclick.assert_called_once()
        assert "double" in result.lower() or "click" in result.lower()

    @pytest.mark.asyncio
    async def test_scroll_element_into_view_by_ref(self, mock_browser):
        """Test scrolling element into view."""

        mock_locator = MagicMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.scroll_element_into_view_by_ref(mock_browser, ref="e1")

        mock_locator.scroll_into_view_if_needed.assert_called_once()
        assert "scroll" in result.lower()

# ==================== Mouse Tools Tests ====================

class TestMouseTools:
    """Tests for coordinate-based mouse tools."""

    @pytest.mark.asyncio
    async def test_mouse_move(self, mock_browser):
        """Test mouse_move to specific coordinates."""

        result = await Browser.mouse_move(mock_browser, x=100, y=200)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.move.assert_called_once_with(100.0, 200.0)
        assert "100" in result and "200" in result

    @pytest.mark.asyncio
    async def test_mouse_click(self, mock_browser):
        """Test mouse_click at coordinates."""

        result = await Browser.mouse_click(mock_browser, x=150, y=250)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.click.assert_called_once()
        assert "click" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_click_with_button(self, mock_browser):
        """Test mouse_click with specific button."""

        result = await Browser.mouse_click(mock_browser, x=150, y=250, button="right")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.click.assert_called_once()
        call_kwargs = mock_page.mouse.click.call_args
        assert call_kwargs[1]["button"] == "right"

    @pytest.mark.asyncio
    async def test_mouse_drag(self, mock_browser):
        """Test mouse_drag from one point to another."""

        result = await Browser.mouse_drag(mock_browser, 
            start_x=100, start_y=100,
            end_x=300, end_y=300
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_mouse_down(self, mock_browser):
        """Test mouse_down (press button)."""

        result = await Browser.mouse_down(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.down.assert_called_once()
        assert "down" in result.lower() or "pressed" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_up(self, mock_browser):
        """Test mouse_up (release button)."""

        result = await Browser.mouse_up(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.up.assert_called_once()
        assert "up" in result.lower() or "released" in result.lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("delta_x,delta_y,direction", [
        (0, 500, "down"),
        (0, -500, "up"),
        (0, 0, "none"),
    ])
    async def test_mouse_wheel(self, mock_browser, delta_x, delta_y, direction):
        """Test mouse_wheel scrolling with various deltas."""

        result = await Browser.mouse_wheel(mock_browser, delta_x=delta_x, delta_y=delta_y)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.wheel.assert_called_once_with(delta_x=float(delta_x), delta_y=float(delta_y))
        assert "scroll" in result.lower()

# ==================== Keyboard Tools Tests ====================

class TestKeyboardTools:
    """Tests for keyboard tools."""

    @pytest.mark.asyncio
    async def test_press_key(self, mock_browser):
        """Test press_key."""

        mock_page = mock_browser.get_current_page.return_value

        result = await Browser.press_key(mock_browser, "Enter")

        mock_page.keyboard.press.assert_called_once_with("Enter")
        assert "Enter" in result

    @pytest.mark.asyncio
    async def test_type_text(self, mock_browser):
        """Test typing text character by character."""

        result = await Browser.type_text(mock_browser, "hello")

        mock_page = mock_browser.get_current_page.return_value
        assert mock_page.keyboard.press.call_count == 5
        assert "5" in result

    @pytest.mark.asyncio
    async def test_type_text_with_submit(self, mock_browser):
        """Test typing with submit (Enter key)."""

        result = await Browser.type_text(mock_browser, "test", submit=True)

        mock_page = mock_browser.get_current_page.return_value
        assert mock_page.keyboard.press.call_count == 5  # 4 chars + 1 Enter
        assert "submit" in result.lower()

    @pytest.mark.asyncio
    async def test_key_down(self, mock_browser):
        """Test holding a key down."""

        result = await Browser.key_down(mock_browser, "Shift")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.keyboard.down.assert_called_once_with("Shift")
        assert "Shift" in result

    @pytest.mark.asyncio
    async def test_key_up(self, mock_browser):
        """Test releasing a key."""

        result = await Browser.key_up(mock_browser, "Shift")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.keyboard.up.assert_called_once_with("Shift")
        assert "Shift" in result

    @pytest.mark.asyncio
    async def test_fill_form(self, mock_browser):
        """Test filling multiple form fields."""

        mock_locator = MagicMock()
        mock_locator.fill = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        fields = [
            {"ref": "e1", "value": "john@example.com"},
            {"ref": "e2", "value": "password123"},
        ]

        result = await Browser.fill_form(mock_browser, fields)

        assert mock_locator.fill.call_count == 2
        assert "2" in result

    @pytest.mark.asyncio
    async def test_fill_form_with_errors(self, mock_browser):
        """Test fill_form with some invalid refs."""

        mock_locator = MagicMock()
        mock_locator.fill = AsyncMock()

        async def get_element(ref):
            return mock_locator if ref == "e1" else None

        mock_browser.get_element_by_ref.side_effect = get_element

        fields = [
            {"ref": "e1", "value": "valid"},
            {"ref": "e999", "value": "invalid"},
        ]

        result = await Browser.fill_form(mock_browser, fields)

        assert "1/2" in result
        assert "Failed" in result

    @pytest.mark.asyncio
    async def test_insert_text(self, mock_browser):
        """Test inserting text at cursor position."""

        result = await Browser.insert_text(mock_browser, "Hello World")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.keyboard.insert_text.assert_called_once_with("Hello World")
        assert "11" in result

# ==================== Screenshot Tools Tests ====================

class TestScreenshotTools:
    """Tests for screenshot and PDF tools."""

    @pytest.mark.asyncio
    async def test_take_screenshot(self, mock_browser, temp_dir):
        """Test taking a screenshot."""

        result = await Browser.take_screenshot(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.screenshot.assert_called_once()
        assert "screenshot" in result.lower() or "base64" in result.lower()

    @pytest.mark.asyncio
    async def test_take_screenshot_full_page(self, mock_browser):
        """Test taking a full-page screenshot."""

        result = await Browser.take_screenshot(mock_browser, full_page=True)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.screenshot.assert_called_once()
        call_kwargs = mock_page.screenshot.call_args[1]
        assert call_kwargs.get("full_page") is True

    @pytest.mark.asyncio
    async def test_take_screenshot_to_file(self, mock_browser, temp_dir):
        """Test saving screenshot to file."""

        filepath = str(temp_dir / "test.png")
        result = await Browser.take_screenshot(mock_browser, filename=filepath)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.screenshot.assert_called_once()
        assert "saved" in result.lower() or filepath in result

    @pytest.mark.asyncio
    async def test_save_pdf_headless_can_save(self, mock_browser):
        """Headless mode should allow PDF export."""
        mock_browser._headless = True

        result = await Browser.save_pdf(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.pdf.assert_called_once()
        assert result.startswith("PDF saved to: ")

        output_path = result.split("PDF saved to: ", 1)[1]
        assert output_path.endswith(".pdf")
        assert os.path.isabs(output_path)
        assert mock_page.pdf.call_args.kwargs["path"] == output_path
        assert os.path.exists(output_path)
        os.remove(output_path)

    @pytest.mark.asyncio
    async def test_save_pdf_headful_can_attempt_export(self, mock_browser, temp_dir):
        """Headful mode should still attempt PDF export."""
        mock_browser._headless = False
        output_path = str(temp_dir / "page.pdf")

        result = await Browser.save_pdf(mock_browser, filename=output_path)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.pdf.assert_called_once()
        assert result == f"PDF saved to: {output_path}"
        assert mock_page.pdf.call_args.kwargs["path"] == output_path

    @pytest.mark.asyncio
    async def test_save_pdf_removes_temp_file_on_export_failure(self, mock_browser):
        """Temporary output file should be removed if PDF export fails."""
        mock_page = mock_browser.get_current_page.return_value
        mock_page.pdf = AsyncMock(side_effect=RuntimeError("pdf failure"))

        with patch("bridgic.browser.session._browser.tempfile.mkstemp", return_value=(9, "/tmp/pdf_fail.pdf")):
            with patch("bridgic.browser.session._browser.os.close"):
                with patch("bridgic.browser.session._browser.os.path.exists", return_value=True):
                    with patch("bridgic.browser.session._browser.os.remove") as mock_remove:
                        result = await Browser.save_pdf(mock_browser)

        mock_remove.assert_called_once_with("/tmp/pdf_fail.pdf")
        assert "Failed to save PDF" in result
        assert "pdf failure" in result

# ==================== Network Tools Tests ====================

class TestNetworkTools:
    """Tests for network and console monitoring tools."""

    @pytest.mark.asyncio
    async def test_start_console_capture(self, mock_browser):
        """Test starting console message capture."""

        result = await Browser.start_console_capture(mock_browser)

        assert "started" in result.lower() or "capture" in result.lower()

    @pytest.mark.asyncio
    async def test_get_console_messages(self, mock_browser):
        """Test getting captured console messages."""

        result = await Browser.get_console_messages(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_start_network_capture(self, mock_browser):
        """Test starting network request capture."""

        result = await Browser.start_network_capture(mock_browser)

        assert "started" in result.lower() or "capture" in result.lower() or "network" in result.lower()

    @pytest.mark.asyncio
    async def test_get_network_requests(self, mock_browser):
        """Test getting captured network requests."""

        result = await Browser.get_network_requests(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_stop_console_capture(self, mock_browser):
        """Test stopping console message capture."""
        # Start then stop to exercise cleanup path
        await Browser.start_console_capture(mock_browser)
        result = await Browser.stop_console_capture(mock_browser)

        assert isinstance(result, str)
        assert "stop" in result.lower() or "console" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_network_capture(self, mock_browser):
        """Test stopping network request capture."""
        await Browser.start_network_capture(mock_browser)
        result = await Browser.stop_network_capture(mock_browser)

        assert isinstance(result, str)
        assert "stop" in result.lower() or "network" in result.lower()

    @pytest.mark.asyncio
    async def test_wait_for_network_idle(self, mock_browser):
        """Test waiting for network to become idle."""

        result = await Browser.wait_for_network_idle(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.wait_for_load_state.assert_called()
        assert "idle" in result.lower() or "network" in result.lower()

# ==================== Dialog Tools Tests ====================

class TestDialogTools:
    """Tests for dialog handling tools."""

    @pytest.mark.asyncio
    async def test_setup_dialog_handler(self, mock_browser):
        """Test setting up a dialog handler."""

        result = await Browser.setup_dialog_handler(mock_browser, default_action="accept")

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_handle_dialog_accept(self, mock_browser):
        """Test accepting a dialog."""

        result = await Browser.handle_dialog(mock_browser, accept=True)

        assert "accept" in result.lower() or "dialog" in result.lower() or "no" in result.lower()

    @pytest.mark.asyncio
    async def test_handle_dialog_dismiss(self, mock_browser):
        """Test dismissing a dialog."""

        result = await Browser.handle_dialog(mock_browser, accept=False)

        assert "dismiss" in result.lower() or "dialog" in result.lower() or "no" in result.lower()

    @pytest.mark.asyncio
    async def test_remove_dialog_handler(self, mock_browser):
        """Test removing a dialog handler."""

        result = await Browser.remove_dialog_handler(mock_browser)

        assert "removed" in result.lower() or "handler" in result.lower()

# ==================== Storage Tools Tests ====================

class TestStorageTools:
    """Tests for storage state tools."""

    @pytest.mark.asyncio
    async def test_save_storage_state(self, mock_browser, temp_dir):
        """Test saving storage state."""

        filepath = str(temp_dir / "storage.json")
        result = await Browser.save_storage_state(mock_browser, filename=filepath)

        mock_context = mock_browser._context
        mock_context.storage_state.assert_called()
        assert "saved" in result.lower() or "storage" in result.lower()

    @pytest.mark.asyncio
    async def test_restore_storage_state(self, mock_browser, temp_dir):
        """Test restoring storage state."""

        filepath = temp_dir / "storage.json"
        filepath.write_text('{"cookies": [], "origins": []}')

        result = await Browser.restore_storage_state(mock_browser, filename=str(filepath))

        assert "restored" in result.lower() or "storage" in result.lower() or "loaded" in result.lower()

    @pytest.mark.asyncio
    async def test_clear_cookies(self, mock_browser):
        """Test clearing cookies."""

        result = await Browser.clear_cookies(mock_browser)

        mock_context = mock_browser._context
        mock_context.clear_cookies.assert_called_once()
        assert "cleared" in result.lower() or "cookies" in result.lower()

    @pytest.mark.asyncio
    async def test_get_cookies(self, mock_browser):
        """Test getting cookies."""

        mock_browser._context.cookies.return_value = [
            {"name": "session", "value": "abc123", "domain": "example.com"}
        ]

        result = await Browser.get_cookies(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_set_cookie(self, mock_browser):
        """Test setting a cookie."""

        result = await Browser.set_cookie(mock_browser, 
            name="test_cookie",
            value="test_value",
            domain="example.com"
        )

        assert result is not None

# ==================== Verification Tools Tests ====================

class TestVerifyTools:
    """Tests for verification/assertion tools."""

    @pytest.mark.asyncio
    async def test_verify_element_visible_pass(self, mock_browser):
        """Test verify_element_visible when element is visible."""

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_locator.wait_for = AsyncMock()
        mock_page.get_by_role.return_value = mock_locator

        result = await Browser.verify_element_visible(mock_browser, role="button", accessible_name="Submit")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_visible_fail(self, mock_browser):
        """Test verify_element_visible when element is not visible."""

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_locator.wait_for = AsyncMock(side_effect=Exception("Timeout"))
        mock_page.get_by_role.return_value = mock_locator

        result = await Browser.verify_element_visible(mock_browser, role="button", accessible_name="NonExistent")

        assert "FAIL" in result

    @pytest.mark.asyncio
    async def test_verify_text_visible(self, mock_browser):
        """Test verify_text_visible."""

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock()
        mock_locator.first = mock_first
        mock_page.get_by_text.return_value = mock_locator

        result = await Browser.verify_text_visible(mock_browser, text="Welcome")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_value(self, mock_browser):
        """Test verify_value for input element."""

        mock_locator = MagicMock()
        mock_locator.input_value = AsyncMock(return_value="expected_value")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.verify_value(mock_browser, ref="e1", value="expected_value")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_value_mismatch(self, mock_browser):
        """Test verify_value when values don't match."""

        mock_locator = MagicMock()
        mock_locator.input_value = AsyncMock(return_value="actual_value")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.verify_value(mock_browser, ref="e1", value="expected_value")

        assert "FAIL" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state,method,return_value", [
        ("visible", "is_visible", True),
        ("disabled", "is_disabled", True),
    ])
    async def test_verify_element_state(self, mock_browser, state, method, return_value):
        """Test verify_element_state for various states."""

        mock_locator = MagicMock()
        setattr(mock_locator, method, AsyncMock(return_value=return_value))
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.verify_element_state(mock_browser, ref="e1", state=state)

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_url(self, mock_browser):
        """Test verify_url."""

        result = await Browser.verify_url(mock_browser, expected_url="example.com")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_title(self, mock_browser):
        """Test verify_title."""

        result = await Browser.verify_title(mock_browser, expected_title="Test")

        assert "PASS" in result

# ==================== DevTools Tests ====================

class TestDevTools:
    """Tests for DevTools (tracing/video) tools."""

    @pytest.mark.asyncio
    async def test_start_tracing(self, mock_browser):
        """Test starting trace recording."""

        result = await Browser.start_tracing(mock_browser)

        mock_context = mock_browser._context
        mock_context.tracing.start.assert_called_once()
        assert "started" in result.lower() or "tracing" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_tracing(self, mock_browser, temp_dir):
        """Test stopping trace recording."""

        mock_browser._tracing_active = True

        result = await Browser.stop_tracing(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_start_video(self, mock_browser):
        """Test starting video recording."""

        result = await Browser.start_video(mock_browser)

        assert "video" in result.lower() or "recording" in result.lower() or "not" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_video(self, mock_browser):
        """Test stopping video recording."""

        result = await Browser.stop_video(mock_browser)

        assert "video" in result.lower() or "stopped" in result.lower() or "not" in result.lower()

# ==================== State Tools Tests ====================

class TestStateTools:
    """Tests for state retrieval tools."""

    @pytest.mark.asyncio
    async def test_get_snapshot_text(self, mock_browser):
        """Test get_snapshot_text."""

        mock_snapshot = MagicMock()
        mock_snapshot.tree = "- button 'Click me' [ref=e1]\n- link 'Home' [ref=e2]"
        mock_browser.get_snapshot.return_value = mock_snapshot

        result = await Browser.get_snapshot_text(mock_browser)

        mock_browser.get_snapshot.assert_called_once()
        assert "button" in result or "Click me" in result

    @pytest.mark.asyncio
    async def test_get_snapshot_text_pagination(self, mock_browser):
        """Test get_snapshot_text with pagination."""

        long_text = "x" * 50000
        mock_snapshot = MagicMock()
        mock_snapshot.tree = long_text
        mock_browser.get_snapshot.return_value = mock_snapshot

        result = await Browser.get_snapshot_text(mock_browser, start_from_char=0)

        assert len(result) < len(long_text) + 500
        assert "start_from_char" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("interactive,full_page", [
        (True, False),
        (False, True),
        (True, True),
    ])
    async def test_get_snapshot_text_options(self, mock_browser, interactive, full_page):
        """Test get_snapshot_text with various options."""

        mock_snapshot = MagicMock()
        mock_snapshot.tree = "- button 'Click me' [ref=e1]"
        mock_browser.get_snapshot.return_value = mock_snapshot

        result = await Browser.get_snapshot_text(mock_browser,
            interactive=interactive,
            full_page=full_page,
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_snapshot_text_snapshot_failed(self, mock_browser):
        """Test get_snapshot_text when snapshot returns None."""

        mock_browser.get_snapshot.return_value = None

        result = await Browser.get_snapshot_text(mock_browser)

        assert "failed" in result.lower()

# ==================== BrowserToolSetBuilder Tests ====================

class TestBrowserToolSetBuilder:
    """Tests for the BrowserToolSetBuilder API."""

    def test_list_presets(self):
        """Test listing available presets."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        presets = BrowserToolSetBuilder.list_presets()

        assert "MINIMAL" in presets
        assert "FORM_FILLING" in presets
        assert "TESTING" in presets
        assert "COMPLETE" in presets

    def test_list_categories(self):
        """Test listing available categories."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        categories = BrowserToolSetBuilder.list_categories()

        assert "navigation" in categories
        assert "element_interaction" in categories
        assert "snapshot" in categories
        assert "mouse" in categories
        assert "keyboard" in categories

    def test_for_categories_adds_tools(self):
        """Test that for_categories adds tools from that category."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock()
        for name in ("take_screenshot", "save_pdf"):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_categories(browser, "capture")
        names = {spec.to_tool().name for spec in builder.build()["tool_specs"]}
        assert names == {"take_screenshot", "save_pdf"}

    def test_for_tool_names_strict_raises_on_unknown(self):
        """Strict mode raises when unknown tool names are provided."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock(spec=["search"])
        search_method = MagicMock()
        search_method.__name__ = "search"
        search_method.__doc__ = "Mock search method."
        setattr(browser, "search", search_method)

        with pytest.raises(ValueError, match="Unknown tool name"):
            BrowserToolSetBuilder.for_tool_names(
                browser, "search", "not_a_real_tool", strict=True
            )

    def test_for_tool_names_strict_raises_when_method_missing_on_browser(self):
        """Strict mode should raise when known tool is unavailable on browser."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock(spec=["search"])
        search_method = MagicMock()
        search_method.__name__ = "search"
        search_method.__doc__ = "Mock search method."
        setattr(browser, "search", search_method)

        with pytest.raises(ValueError, match="not available on browser instance"):
            BrowserToolSetBuilder.for_tool_names(
                browser, "search", "navigate_to_url", strict=True
            )

    def test_for_preset_returns_builder(self):
        """Test for_preset returns a configured builder."""
        from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

        browser = MagicMock()
        for name in ("navigate_to_url", "go_back", "go_forward"):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.NAVIGATION)
        assert isinstance(builder, BrowserToolSetBuilder)

        names = {spec.to_tool().name for spec in builder.build()["tool_specs"]}
        assert names == {"navigate_to_url", "go_back", "go_forward"}

    def test_for_funcs_returns_builder(self):
        """Test for_funcs returns a configured builder."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock()
        search_method = MagicMock()
        search_method.__name__ = "search"
        search_method.__doc__ = "Mock search method."
        setattr(browser, "search", search_method)

        builder = BrowserToolSetBuilder.for_funcs(browser, search_method)
        assert isinstance(builder, BrowserToolSetBuilder)

        names = {spec.to_tool().name for spec in builder.build()["tool_specs"]}
        assert names == {"search"}

    def test_for_funcs_raises_on_non_callable(self):
        """for_funcs should fail fast when passed non-callable entries."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock()
        with pytest.raises(TypeError, match="expects callable tool methods"):
            BrowserToolSetBuilder.for_funcs(browser, "search")  # type: ignore[arg-type]

    def test_for_tool_names_builds_specs(self):
        """Test for_tool_names returns a configured builder and builds specs."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        # Create a mock with proper __name__ on returned attributes
        browser = MagicMock()
        for name in ("search", "navigate_to_url"):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_tool_names(
            browser,
            "search",
            "navigate_to_url",
        )
        assert isinstance(builder, BrowserToolSetBuilder)
        specs = builder.build()["tool_specs"]

        names = {spec.to_tool().name for spec in specs}
        assert "search" in names
        assert "navigate_to_url" in names

    def test_build_has_deterministic_tool_order(self):
        """Build output should be deterministic for known and custom tools."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock()
        for name in (
            "go_forward",
            "search",
            "navigate_to_url",
            "custom_b",
            "custom_a",
        ):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_tool_names(
            browser,
            "go_forward",
            "custom_b",
            "search",
            "custom_a",
            "navigate_to_url",
        )

        names = [spec.to_tool().name for spec in builder.build()["tool_specs"]]
        assert names == [
            "navigate_to_url",
            "search",
            "go_forward",
            "custom_a",
            "custom_b",
        ]

    def test_preset_tool_names_are_valid(self):
        """Test that all preset tool names exist in the category inventory."""
        from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

        all_tools = set()
        for names in BrowserToolSetBuilder._CATEGORIES.values():
            all_tools.update(names)
        for preset in ToolPreset:
            tools = BrowserToolSetBuilder._PRESET_TOOL_NAMES.get(preset, [])
            for tool_name in tools:
                assert tool_name in all_tools, f"Tool {tool_name} not found for preset {preset}"

    def test_categories_match_cli_sections(self):
        """Tool categories should stay aligned with CLI command sections."""
        from bridgic.browser.cli._commands import SectionedGroup
        from bridgic.browser.tools import BrowserToolSetBuilder

        expected = build_tool_categories_from_help_sections(SectionedGroup.SECTIONS)

        assert expected == BrowserToolSetBuilder._CATEGORIES

    def test_presets_match_cli_presets(self):
        """Tool preset method lists should match CLI preset command mappings."""
        from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

        expected_by_preset = build_tool_presets_from_cli_preset_commands(CLI_PRESET_COMMANDS)

        for preset in ToolPreset:
            expected = expected_by_preset[preset]
            actual = BrowserToolSetBuilder._PRESET_TOOL_NAMES[preset]
            assert set(expected) == set(actual)
            assert len(expected) == len(actual)

    def test_cli_section_commands_are_mapped(self):
        """Every CLI section command should have a tool mapping."""
        from bridgic.browser.cli._commands import SectionedGroup

        for section_title, commands in SectionedGroup.SECTIONS:
            for command in commands:
                assert command in CLI_COMMAND_TO_TOOL_METHOD

    def test_cli_preset_commands_are_mapped(self):
        """Every CLI preset command should have a tool mapping."""
        for commands in CLI_PRESET_COMMANDS.values():
            for command in commands:
                assert command in CLI_COMMAND_TO_TOOL_METHOD
