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
    CLI_ALL_COMMANDS,
    CLI_COMMAND_TO_TOOL_METHOD,
    CLI_TOOL_CATEGORIES,
    map_cli_commands_to_tool_methods,
)
from bridgic.browser.errors import (
    InvalidInputError,
    OperationError,
    StateError,
    VerificationError,
)
from bridgic.browser.session import Browser

# ==================== Fixtures ====================

@pytest.fixture
def mock_browser():
    """Create a comprehensive mock Browser instance."""
    browser = MagicMock()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    browser._page = mock_page
    browser._context = MagicMock()
    browser.navigate_to = AsyncMock()
    browser.get_current_page = AsyncMock(return_value=mock_page)
    browser.get_pages = MagicMock(return_value=[])
    browser.get_all_page_descs = AsyncMock(return_value=[])
    browser.switch_to_page = AsyncMock(return_value=(True, "Switched"))
    browser._close_page = AsyncMock(return_value=(True, "Closed"))
    browser._ensure_started = AsyncMock()
    browser._new_page = AsyncMock()
    browser.get_snapshot = AsyncMock()
    browser.get_element_by_ref = AsyncMock()

    # Browser tool methods (all async)
    browser.search = AsyncMock(return_value="Searched on Duckduckgo for 'test query'")
    browser.navigate_to = AsyncMock(return_value="Navigated to https://example.com")
    browser.go_back = AsyncMock(return_value="Navigated back")
    browser.go_forward = AsyncMock(return_value="Navigated forward")
    browser.reload_page = AsyncMock(return_value="Page reloaded")
    browser.scroll_to_text = AsyncMock(return_value="Scrolled to text")
    browser.press_key = AsyncMock(return_value="Pressed key")
    browser.evaluate_javascript = AsyncMock(return_value="result")
    browser.get_current_page_info = AsyncMock(return_value="URL: https://example.com/test\nTitle: Test Page")
    browser.new_tab = AsyncMock(return_value="Created new blank tab")
    browser.get_tabs = AsyncMock(return_value="Tab 1")
    browser.switch_tab = AsyncMock(return_value="Switched to tab")
    browser.close_tab = AsyncMock(return_value="Closed tab")
    browser.close = AsyncMock(return_value="Browser closed")
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
    browser.check_checkbox_or_radio_by_ref = AsyncMock(return_value="Checked")
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
    browser._console_messages = {}
    browser._network_requests = {}
    browser._console_handlers = {}
    browser._network_handlers = {}
    browser._dialog_handlers = {}
    browser._tracing_state = {}
    browser._video_state = {}

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
        with pytest.raises(InvalidInputError) as exc_info:
            await Browser.search(mock_browser, "test query", "invalid")
        assert exc_info.value.code == "UNSUPPORTED_SEARCH_ENGINE"
        mock_browser.navigate_to.assert_not_called()

    @pytest.mark.asyncio
    async def test_navigate_to(self, mock_browser):
        """Test navigate_to."""
        mock_browser._page.goto = AsyncMock()

        result = await Browser.navigate_to(mock_browser, "https://example.com")

        mock_browser._page.goto.assert_called_once_with(
            "https://example.com", wait_until="domcontentloaded"
        )
        assert "Navigated to" in result

    @pytest.mark.asyncio
    async def test_navigate_to_adds_protocol(self, mock_browser):
        """Test navigate_to adds http:// if missing."""
        mock_browser._page.goto = AsyncMock()

        result = await Browser.navigate_to(mock_browser, "example.com")

        mock_browser._page.goto.assert_called_once_with(
            "http://example.com", wait_until="domcontentloaded"
        )

    @pytest.mark.asyncio
    async def test_navigate_to_empty(self, mock_browser):
        """Test navigate_to with empty URL."""
        with pytest.raises(InvalidInputError) as exc_info:
            await Browser.navigate_to(mock_browser, "")
        assert exc_info.value.code == "URL_EMPTY"
        mock_browser._page.goto.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("special_url", [
        "javascript:alert(1)",
        "data:text/html,<h1>test</h1>",
    ])
    async def test_navigate_to_allows_special_schemes(self, mock_browser, special_url):
        """Test navigate_to allows special schemes without auto-prefix."""
        mock_browser._page.goto = AsyncMock()

        result = await Browser.navigate_to(mock_browser, special_url)

        mock_browser._page.goto.assert_called_once_with(
            special_url, wait_until="domcontentloaded"
        )
        assert "Navigated to" in result

    @pytest.mark.asyncio
    async def test_go_back(self, mock_browser):
        """Test go_back."""

        result = await Browser.go_back(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.go_back.assert_called_once()
        assert result.startswith("Navigated back to:")

    @pytest.mark.asyncio
    async def test_go_forward(self, mock_browser):
        """Test go_forward."""

        result = await Browser.go_forward(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.go_forward.assert_called_once()
        assert result.startswith("Navigated forward to:")

    @pytest.mark.asyncio
    async def test_reload_page(self, mock_browser):
        """Test reload_page."""

        result = await Browser.reload_page(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.reload.assert_called_once()
        assert result.startswith("Page reloaded:")

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
        assert result.startswith("Scrolled to text:")

    @pytest.mark.asyncio
    async def test_evaluate_javascript(self, mock_browser):
        """Test evaluate_javascript."""

        mock_page = mock_browser.get_current_page.return_value
        mock_page.evaluate.return_value = {"result": "test"}

        result = await Browser.evaluate_javascript(mock_browser, "return {result: 'test'}")

        mock_page.evaluate.assert_called_once()
        assert "result" in result

    @pytest.mark.asyncio
    async def test_wait_for_time(self, mock_browser):
        """Test wait_for function with time parameter."""
        import time

        start = time.time()
        result = await Browser.wait_for(mock_browser, time_seconds=0.5)
        elapsed = time.time() - start

        assert elapsed >= 0.5
        assert result.startswith("Waited for ")

    @pytest.mark.asyncio
    async def test_wait_for_text(self, mock_browser):
        """Test wait_for function with text parameter."""

        mock_browser._wait_for_text_across_frames = AsyncMock()

        result = await Browser.wait_for(mock_browser, text="Loading complete")

        mock_browser._wait_for_text_across_frames.assert_called_once()
        args, kwargs = mock_browser._wait_for_text_across_frames.call_args
        assert "Loading complete" in args
        assert kwargs.get("gone") is False

    @pytest.mark.asyncio
    async def test_get_current_page_info(self, mock_browser):
        """Test get_current_page_info."""

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

        result = await Browser.get_current_page_info(mock_browser)

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
        """Test new_tab with no URL opens a blank page."""
        mock_browser._new_page.return_value = MagicMock()

        result = await Browser.new_tab(mock_browser)

        mock_browser._new_page.assert_called_once_with(None, wait_until="domcontentloaded", timeout=None)
        assert "new" in result.lower() or "tab" in result.lower() or "blank" in result.lower()

    @pytest.mark.asyncio
    async def test_new_tab_with_url(self, mock_browser):
        """Test new_tab with URL."""
        mock_browser._new_page.return_value = MagicMock()

        result = await Browser.new_tab(mock_browser, "https://example.com")

        mock_browser._new_page.assert_called_once_with(
            "https://example.com", wait_until="domcontentloaded", timeout=None
        )

    @pytest.mark.asyncio
    async def test_get_tabs(self, mock_browser):
        """Test get_tabs returns a string listing all open tabs."""
        from bridgic.browser.session._browser_model import PageDesc

        mock_browser.get_all_page_descs.return_value = [
            PageDesc(url="https://example.com", title="Example", page_id="page_1"),
            PageDesc(url="https://test.com", title="Test", page_id="page_2"),
        ]

        result = await Browser.get_tabs(mock_browser)

        mock_browser.get_all_page_descs.assert_called_once()
        assert "example.com" in result
        assert "test.com" in result

    @pytest.mark.asyncio
    async def test_switch_tab(self, mock_browser):
        """Test switch_tab switches to the given page and returns a confirmation string."""

        result = await Browser.switch_tab(mock_browser, "page_123")

        mock_browser.switch_to_page.assert_called_once_with("page_123")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_close_tab(self, mock_browser):
        """Test close_tab closes the given page and returns a confirmation string."""

        result = await Browser.close_tab(mock_browser, "page_123")

        mock_browser._close_page.assert_called_once_with("page_123")
        assert isinstance(result, str)
        assert len(result) > 0

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
        with pytest.raises(StateError) as exc_info:
            await Browser.click_element_by_ref(mock_browser, "e999")
        assert exc_info.value.code == "REF_NOT_AVAILABLE"

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

        with pytest.raises(StateError) as exc_info:
            await Browser.get_dropdown_options_by_ref(mock_browser, "e1")
        assert exc_info.value.code == "ELEMENT_STATE_ERROR"

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

        result = await Browser.check_checkbox_or_radio_by_ref(mock_browser, ref="e1")

        mock_locator.check.assert_called_once()
        assert result == "Checked element e1 (confirmed: checked=true)"

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
        assert result == "Unchecked element e1 (confirmed: checked=false)"

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

        result = await Browser.check_checkbox_or_radio_by_ref(mock_browser, ref="e1")

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

        with pytest.raises(OperationError) as exc_info:
            await Browser.check_checkbox_or_radio_by_ref(mock_browser, ref="e1")
        mock_locator.click.assert_called_once()
        assert "Failed to check element e1" in exc_info.value.message

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

        with pytest.raises(OperationError) as exc_info:
            await Browser.uncheck_checkbox_by_ref(mock_browser, ref="e1")
        mock_locator.click.assert_called_once()
        assert "Failed to uncheck element e1" in exc_info.value.message

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
        assert result == "Double-clicked element e1"

    @pytest.mark.asyncio
    async def test_scroll_element_into_view_by_ref(self, mock_browser):
        """Test scrolling element into view."""

        mock_locator = MagicMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await Browser.scroll_element_into_view_by_ref(mock_browser, ref="e1")

        mock_locator.scroll_into_view_if_needed.assert_called_once()
        assert result == "Scrolled element e1 into view"

# ==================== Mouse Tools Tests ====================

class TestMouseTools:
    """Tests for coordinate-based mouse tools."""

    @pytest.mark.asyncio
    async def test_mouse_move(self, mock_browser):
        """Test mouse_move to specific coordinates."""

        result = await Browser.mouse_move(mock_browser, x=100, y=200)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.move.assert_called_once_with(100.0, 200.0)
        assert result == "Moved mouse to coordinates (100, 200)"

    @pytest.mark.asyncio
    async def test_mouse_click(self, mock_browser):
        """Test mouse_click at coordinates."""

        result = await Browser.mouse_click(mock_browser, x=150, y=250)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.click.assert_called_once()
        assert result == "Mouse clicked at (150, 250) with left button"

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

        assert result.startswith("Dragged mouse from (")

    @pytest.mark.asyncio
    async def test_mouse_down(self, mock_browser):
        """Test mouse_down (press button)."""

        result = await Browser.mouse_down(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.down.assert_called_once()
        assert result == "Mouse left button pressed down"

    @pytest.mark.asyncio
    async def test_mouse_up(self, mock_browser):
        """Test mouse_up (release button)."""

        result = await Browser.mouse_up(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.up.assert_called_once()
        assert result == "Mouse left button released"

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
        assert result.startswith("Scrolled mouse wheel:")

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
        assert result == "Typed 4 characters sequentially and submitted"

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
        assert result.startswith("data:image/png;base64,")

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
        assert result == f"Screenshot saved to: {filepath}"

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
                        with pytest.raises(OperationError) as exc_info:
                            await Browser.save_pdf(mock_browser)

        mock_remove.assert_called_once_with("/tmp/pdf_fail.pdf")
        assert "Failed to save PDF" in exc_info.value.message
        assert "pdf failure" in exc_info.value.message

# ==================== Network Tools Tests ====================

class TestNetworkTools:
    """Tests for network and console monitoring tools."""

    @pytest.mark.asyncio
    async def test_start_console_capture(self, mock_browser):
        """Test starting console message capture."""

        result = await Browser.start_console_capture(mock_browser)

        assert result == "Console message capture started"

    @pytest.mark.asyncio
    async def test_get_console_messages(self, mock_browser):
        """Test getting captured console messages."""
        mock_page = mock_browser.get_current_page.return_value
        page_key = str(id(mock_page))
        mock_browser._console_messages[page_key] = [
            {"type": "log", "text": "hello", "location": None}
        ]
        result = await Browser.get_console_messages(mock_browser)

        assert isinstance(result, str)
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_start_network_capture(self, mock_browser):
        """Test starting network request capture."""

        result = await Browser.start_network_capture(mock_browser)

        assert result == "Network request capture started"

    @pytest.mark.asyncio
    async def test_get_network_requests(self, mock_browser):
        """Test get_network_requests returns a JSON-formatted string when no requests captured."""

        result = await Browser.get_network_requests(mock_browser)

        assert isinstance(result, str)
        # No requests captured → returns JSON empty array
        assert result == "[]"

    @pytest.mark.asyncio
    async def test_stop_console_capture(self, mock_browser):
        """Test stopping console message capture."""
        # Start then stop to exercise cleanup path
        await Browser.start_console_capture(mock_browser)
        result = await Browser.stop_console_capture(mock_browser)

        assert result == "Console capture stopped"

    @pytest.mark.asyncio
    async def test_stop_network_capture(self, mock_browser):
        """Test stopping network request capture."""
        await Browser.start_network_capture(mock_browser)
        result = await Browser.stop_network_capture(mock_browser)

        assert result == "Network capture stopped"

    @pytest.mark.asyncio
    async def test_wait_for_network_idle(self, mock_browser):
        """Test waiting for network to become idle."""

        result = await Browser.wait_for_network_idle(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=30000.0)
        assert result == "Network is idle"

# ==================== Dialog Tools Tests ====================

class TestDialogTools:
    """Tests for dialog handling tools."""

    @pytest.mark.asyncio
    async def test_setup_dialog_handler(self, mock_browser):
        """Test setting up a dialog handler."""

        result = await Browser.setup_dialog_handler(mock_browser, default_action="accept")

        assert result == "Dialog handler set up with default action: accept"

    @pytest.mark.asyncio
    async def test_handle_dialog_accept(self, mock_browser):
        """Test accepting a dialog."""

        result = await Browser.handle_dialog(mock_browser, accept=True)

        assert result == "Dialog handler ready to accept the next dialog"

    @pytest.mark.asyncio
    async def test_handle_dialog_dismiss(self, mock_browser):
        """Test dismissing a dialog."""

        result = await Browser.handle_dialog(mock_browser, accept=False)

        assert result == "Dialog handler ready to dismiss the next dialog"

    @pytest.mark.asyncio
    async def test_remove_dialog_handler(self, mock_browser):
        """Test removing a dialog handler."""

        result = await Browser.remove_dialog_handler(mock_browser)

        assert result in ("Dialog handler removed", "No dialog handler was set up")

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
        assert result.startswith("Storage state saved to:")

    @pytest.mark.asyncio
    async def test_restore_storage_state(self, mock_browser, temp_dir):
        """Test restoring storage state."""

        filepath = temp_dir / "storage.json"
        filepath.write_text('{"cookies": [], "origins": []}')

        result = await Browser.restore_storage_state(mock_browser, filename=str(filepath))

        assert result.startswith("Storage state restored from:")

    @pytest.mark.asyncio
    async def test_clear_cookies(self, mock_browser):
        """Test clearing cookies."""

        result = await Browser.clear_cookies(mock_browser)

        mock_context = mock_browser._context
        mock_context.clear_cookies.assert_called_once_with(name=None, domain=None, path=None)
        assert result == "All cookies cleared"

    @pytest.mark.asyncio
    async def test_clear_cookies_filtered(self, mock_browser):
        """Test clearing cookies with filters."""
        await Browser.clear_cookies(mock_browser, name="sid")
        mock_browser._context.clear_cookies.assert_called_once_with(name="sid", domain=None, path=None)

    @pytest.mark.asyncio
    async def test_get_cookies(self, mock_browser):
        """Test getting cookies."""

        mock_browser._context.cookies.return_value = [
            {
                "name": "session",
                "value": "abc123",
                "domain": "example.com",
                "path": "/",
                "expires": 1773809665.0,
                "httpOnly": False,
                "secure": True,
                "sameSite": "None",
            }
        ]

        result = await Browser.get_cookies(mock_browser)

        assert isinstance(result, str)
        payload = json.loads(result)
        assert isinstance(payload, list)
        assert payload and isinstance(payload[0], dict)
        for key in ("name", "value", "domain", "path"):
            assert key in payload[0]

    @pytest.mark.asyncio
    async def test_get_cookies_filters(self, mock_browser):
        """Test cookie filtering by name/domain/path."""
        mock_browser._context.cookies.return_value = [
            {
                "name": "sid",
                "value": "abc123",
                "domain": "sub.example.com",
                "path": "/app",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "None",
            },
            {
                "name": "other",
                "value": "zzz",
                "domain": "example.com",
                "path": "/other",
                "expires": -1,
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            },
        ]

        result = await Browser.get_cookies(
            mock_browser, name="sid", domain="example.com", path="/app"
        )
        payload = json.loads(result)
        assert len(payload) == 1
        assert payload[0]["name"] == "sid"
        assert "example.com" in payload[0]["domain"]
        assert payload[0]["path"].startswith("/app")

    @pytest.mark.asyncio
    async def test_set_cookie(self, mock_browser):
        """Test setting a cookie."""

        result = await Browser.set_cookie(mock_browser,
            name="test_cookie",
            value="test_value",
            domain="example.com"
        )

        assert result == "Cookie 'test_cookie' set successfully"

    @pytest.mark.asyncio
    async def test_set_cookie_expires_zero_is_preserved(self, mock_browser):
        """expires=0 should be passed through (epoch is a valid timestamp)."""
        await Browser.set_cookie(
            mock_browser,
            name="test_cookie",
            value="test_value",
            domain="example.com",
            expires=0,
        )

        mock_browser._context.add_cookies.assert_called_once()
        cookie = mock_browser._context.add_cookies.call_args[0][0][0]
        assert cookie["expires"] == 0

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

        with pytest.raises(VerificationError) as exc_info:
            await Browser.verify_element_visible(mock_browser, role="button", accessible_name="NonExistent")
        assert exc_info.value.code == "VERIFICATION_FAILED"

    @pytest.mark.asyncio
    async def test_verify_text_visible(self, mock_browser):
        """Test verify_text_visible."""

        mock_browser._wait_for_text_across_frames = AsyncMock()

        result = await Browser.verify_text_visible(mock_browser, text="Welcome")

        mock_browser._wait_for_text_across_frames.assert_called_once()
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

        with pytest.raises(VerificationError) as exc_info:
            await Browser.verify_value(mock_browser, ref="e1", value="expected_value")
        assert exc_info.value.code == "VERIFICATION_FAILED"

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
        assert result == "Tracing started"

    @pytest.mark.asyncio
    async def test_stop_tracing(self, mock_browser, temp_dir):
        """Test stopping trace recording."""
        with pytest.raises(StateError) as exc_info:
            await Browser.stop_tracing(mock_browser)
        assert exc_info.value.code == "NO_ACTIVE_TRACING"

    @pytest.mark.asyncio
    async def test_start_video(self, mock_browser):
        """Test starting video recording — multi-page: all existing
        pages in the context get a per-page recorder, and context.on('page')
        is subscribed so future pages auto-record too.
        """
        import types

        page = mock_browser._page
        page.viewport_size = {"width": 800, "height": 600}
        page.is_closed = MagicMock(return_value=False)
        mock_context = page.context
        mock_context.pages = [page]
        mock_context.on = MagicMock()
        mock_browser._video_state = {}
        mock_browser._video_recorders = {}
        mock_browser._video_session = None
        # Bind the real helper so start_video can drive _start_page_video_recorder.
        mock_browser._start_page_video_recorder = types.MethodType(
            Browser._start_page_video_recorder, mock_browser,
        )

        mock_recorder = MagicMock()
        mock_recorder.start = AsyncMock()
        with patch("bridgic.browser.session._browser._video_recorder_mod.VideoRecorder", return_value=mock_recorder):
            result = await Browser.start_video(mock_browser)

        assert "Video recording started" in result
        assert "1 page" in result
        assert mock_browser._video_recorders[page] is mock_recorder
        assert mock_browser._video_session is not None
        # context.on('page', handler) must be registered for auto-recording
        # of newly opened tabs.
        assert mock_context.on.called
        assert mock_context.on.call_args.args[0] == "page"

    @pytest.mark.asyncio
    async def test_stop_video(self, mock_browser):
        """Test stopping video recording when no session is active."""
        mock_browser._video_recorders = {}
        mock_browser._video_session = None
        mock_browser._video_state = {}
        with pytest.raises(StateError) as exc_info:
            await Browser.stop_video(mock_browser)
        assert exc_info.value.code == "NO_ACTIVE_RECORDING"

    @pytest.mark.asyncio
    async def test_start_video_records_all_pages(self, mock_browser):
        """start_video should record every existing page, not just current."""
        import types

        page1 = mock_browser._page
        page1.viewport_size = {"width": 800, "height": 600}
        page1.is_closed = MagicMock(return_value=False)

        page2 = MagicMock()
        page2.viewport_size = {"width": 800, "height": 600}
        page2.is_closed = MagicMock(return_value=False)
        page2.context = page1.context

        mock_context = page1.context
        mock_context.pages = [page1, page2]
        mock_context.on = MagicMock()
        mock_browser._video_state = {}
        mock_browser._video_recorders = {}
        mock_browser._video_session = None
        mock_browser._start_page_video_recorder = types.MethodType(
            Browser._start_page_video_recorder, mock_browser,
        )

        created_recorders = []

        def _factory(context, page, output_path, size):
            rec = MagicMock()
            rec.start = AsyncMock()
            rec.output_path = output_path
            created_recorders.append(rec)
            return rec

        with patch(
            "bridgic.browser.session._browser._video_recorder_mod.VideoRecorder",
            side_effect=_factory,
        ):
            result = await Browser.start_video(mock_browser)

        assert "2 pages" in result
        assert len(mock_browser._video_recorders) == 2
        assert page1 in mock_browser._video_recorders
        assert page2 in mock_browser._video_recorders
        assert len(created_recorders) == 2
        for rec in created_recorders:
            rec.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_video_returns_multiple_paths(self, mock_browser, tmp_path):
        """stop_video should stop all page recorders and return all paths."""
        from bridgic.browser.session import _browser as browser_module

        mock_browser._context.remove_listener = MagicMock()
        context_key = browser_module._get_context_key(mock_browser._context)
        mock_browser._video_state = {context_key: True}
        # Bind real static helpers — otherwise `self._resolve_multi_video_dests`
        # on a MagicMock returns another MagicMock (truthy) and the code
        # takes the wrong branch.
        mock_browser._resolve_multi_video_dests = Browser._resolve_multi_video_dests
        mock_browser._move_video_local = Browser._move_video_local

        page1 = MagicMock()
        page2 = MagicMock()
        rec1 = MagicMock()
        rec1.stop = AsyncMock(return_value=str(tmp_path / "a.webm"))
        rec2 = MagicMock()
        rec2.stop = AsyncMock(return_value=str(tmp_path / "b.webm"))
        (tmp_path / "a.webm").write_bytes(b"")
        (tmp_path / "b.webm").write_bytes(b"")

        mock_browser._video_recorders = {page1: rec1, page2: rec2}
        mock_browser._video_session = {
            "width": 800, "height": 600, "context": mock_browser._context,
            "page_listener": lambda *_: None,
        }

        result = await Browser.stop_video(mock_browser)

        rec1.stop.assert_awaited_once()
        rec2.stop.assert_awaited_once()
        assert "Video files saved" in result
        assert str(tmp_path / "a.webm") in result
        assert str(tmp_path / "b.webm") in result
        assert mock_browser._video_recorders == {}
        assert mock_browser._video_session is None

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
    async def test_get_snapshot_text_overflow(self, mock_browser, tmp_path):
        """Test get_snapshot_text writes file when content exceeds limit."""

        long_text = "x" * 50000
        mock_snapshot = MagicMock()
        mock_snapshot.tree = long_text
        mock_browser.get_snapshot.return_value = mock_snapshot
        mock_browser._write_snapshot_file = Browser._write_snapshot_file.__get__(mock_browser)

        file_path = str(tmp_path / "snap.txt")
        result = await Browser.get_snapshot_text(mock_browser, file=file_path)

        assert "[notice]" in result
        assert "saved to:" in result
        assert file_path in result
        written = (tmp_path / "snap.txt").read_text(encoding="utf-8")
        assert long_text in written
        assert written.startswith("[Page:")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("interactive,full_page", [
        (True, False),
        (False, True),
        (True, True),
    ])
    async def test_get_snapshot_text_options(self, mock_browser, interactive, full_page):
        """Test get_snapshot_text passes interactive and full_page to get_snapshot."""

        mock_snapshot = MagicMock()
        mock_snapshot.tree = "- button 'Click me' [ref=e1]"
        mock_browser.get_snapshot.return_value = mock_snapshot

        result = await Browser.get_snapshot_text(mock_browser,
            interactive=interactive,
            full_page=full_page,
        )

        mock_browser.get_snapshot.assert_called_once_with(
            interactive=interactive, full_page=full_page
        )
        assert "button" in result or "Click me" in result

    @pytest.mark.asyncio
    async def test_get_snapshot_text_invalid_limit(self, mock_browser):
        """limit < 1 should raise InvalidInputError."""
        mock_snapshot = MagicMock()
        mock_snapshot.tree = "- button 'Click me' [ref=e1]"
        mock_browser.get_snapshot.return_value = mock_snapshot

        with pytest.raises(InvalidInputError) as exc_info:
            await Browser.get_snapshot_text(mock_browser, limit=0)
        assert exc_info.value.code == "INVALID_LIMIT"

    @pytest.mark.asyncio
    async def test_get_snapshot_text_snapshot_failed(self, mock_browser):
        """Test get_snapshot_text when snapshot returns None."""

        mock_browser.get_snapshot.return_value = None
        with pytest.raises(OperationError) as exc_info:
            await Browser.get_snapshot_text(mock_browser)
        assert "Failed to get interface information" in exc_info.value.message

# ==================== BrowserToolSetBuilder Tests ====================

class TestBrowserToolSetBuilder:
    """Tests for the BrowserToolSetBuilder API."""

    def test_list_categories(self):
        """Test listing available categories."""
        from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

        categories = BrowserToolSetBuilder.list_categories()

        assert ToolCategory.NAVIGATION in categories
        assert ToolCategory.ELEMENT_INTERACTION in categories
        assert ToolCategory.SNAPSHOT in categories
        assert ToolCategory.MOUSE in categories
        assert ToolCategory.KEYBOARD in categories

    def test_for_categories_adds_tools(self):
        """Test that for_categories adds tools from that category."""
        from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

        browser = MagicMock()
        for name in ("take_screenshot", "save_pdf"):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.CAPTURE)
        names = {spec.to_tool().name for spec in builder.build()["tool_specs"]}
        assert names == {"take_screenshot", "save_pdf"}

    def test_for_categories_accepts_string_aliases(self):
        """String aliases should map to ToolCategory values."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock(spec=["click_element_by_ref", "input_text_by_ref"])
        for name in ("click_element_by_ref", "input_text_by_ref"):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_categories(browser, "action")
        names = {spec.to_tool().name for spec in builder.build()["tool_specs"]}
        assert {"click_element_by_ref", "input_text_by_ref"} <= names

    def test_for_tool_names_raises_on_unknown(self):
        """for_tool_names raises when unknown tool names are provided."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock(spec=["search"])
        search_method = MagicMock()
        search_method.__name__ = "search"
        search_method.__doc__ = "Mock search method."
        setattr(browser, "search", search_method)

        with pytest.raises(ValueError, match="Unknown tool name"):
            BrowserToolSetBuilder.for_tool_names(browser, "search", "not_a_real_tool")

    def test_for_tool_names_raises_when_method_missing_on_browser(self):
        """for_tool_names should raise when known tool is unavailable on browser."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock(spec=["search"])
        search_method = MagicMock()
        search_method.__name__ = "search"
        search_method.__doc__ = "Mock search method."
        setattr(browser, "search", search_method)

        with pytest.raises(ValueError, match="not available on browser instance"):
            BrowserToolSetBuilder.for_tool_names(browser, "search", "navigate_to")

    def test_for_tool_names_builds_specs(self):
        """Test for_tool_names returns a configured builder and builds specs."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        # Create a mock with proper __name__ on returned attributes
        browser = MagicMock()
        for name in ("search", "navigate_to"):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_tool_names(
            browser,
            "search",
            "navigate_to",
        )
        assert isinstance(builder, BrowserToolSetBuilder)
        specs = builder.build()["tool_specs"]

        names = {spec.to_tool().name for spec in specs}
        assert "search" in names
        assert "navigate_to" in names

    def test_for_tool_names_non_strict_ignores_unavailable(self):
        """strict=False should keep available names and ignore the rest."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock(spec=["search"])
        search_method = MagicMock()
        search_method.__name__ = "search"
        search_method.__doc__ = "Mock search method."
        setattr(browser, "search", search_method)

        builder = BrowserToolSetBuilder.for_tool_names(
            browser,
            "search",
            "navigate_to",
            "not_a_real_tool",
            strict=False,
        )
        specs = builder.build()["tool_specs"]
        names = [spec.to_tool().name for spec in specs]
        assert names == ["search"]

    def test_for_categories_all_builds_expected_count(self):
        """ALL category should produce deterministic tool counts."""
        from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

        browser = MagicMock()
        for category_tools in BrowserToolSetBuilder._CATEGORIES.values():
            for name in category_tools:
                method = MagicMock()
                method.__name__ = name
                method.__doc__ = f"Mock {name} method."
                setattr(browser, name, method)

        expected_count = len(
            {
                name
                for category_tools in BrowserToolSetBuilder._CATEGORIES.values()
                for name in category_tools
            }
        )

        builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.ALL)
        assert len(builder.build()["tool_specs"]) == expected_count

        builder = BrowserToolSetBuilder.for_categories(browser, "all")
        assert len(builder.build()["tool_specs"]) == expected_count

    def test_build_has_deterministic_tool_order(self):
        """Build output should be deterministic using CLI catalog category order."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock()
        for name in (
            "go_forward",
            "search",
            "navigate_to",
            "click_element_by_ref",
            "take_screenshot",
        ):
            method = MagicMock()
            method.__name__ = name
            method.__doc__ = f"Mock {name} method."
            setattr(browser, name, method)

        builder = BrowserToolSetBuilder.for_tool_names(
            browser,
            "go_forward",
            "click_element_by_ref",
            "search",
            "take_screenshot",
            "navigate_to",
        )

        names = [spec.to_tool().name for spec in builder.build()["tool_specs"]]
        assert names == [
            "navigate_to",
            "search",
            "go_forward",
            "click_element_by_ref",
            "take_screenshot",
        ]

    def test_categories_match_cli_sections(self):
        """Tool categories should stay aligned with CLI command sections."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        assert CLI_TOOL_CATEGORIES == BrowserToolSetBuilder._CATEGORIES

    def test_builder_tool_inventory_matches_cli_command_mapping(self):
        """Total SDK tool inventory should exactly match mapped CLI command capabilities."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        expected = set(map_cli_commands_to_tool_methods(CLI_ALL_COMMANDS))
        actual = {
            tool_name
            for names in BrowserToolSetBuilder._CATEGORIES.values()
            for tool_name in names
        }
        assert actual == expected
        assert len(actual) == len(expected)

    def test_cli_section_commands_are_mapped(self):
        """Every CLI section command should have a tool mapping."""
        from bridgic.browser._cli_catalog import CLI_NON_TOOL_COMMANDS

        for command in CLI_ALL_COMMANDS:
            if command in CLI_NON_TOOL_COMMANDS:
                continue
            assert command in CLI_COMMAND_TO_TOOL_METHOD
