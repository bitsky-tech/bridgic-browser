"""
Unit tests verifying that the Browser class has all expected tool methods.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bridgic.browser.errors import StateError
from bridgic.browser.session import Browser


EXPECTED_METHODS = [
    "search", "navigate_to", "go_back", "go_forward",
    "reload_page", "scroll_to_text", "press_key", "evaluate_javascript",
    "get_current_page_info", "new_tab", "get_tabs", "switch_tab",
    "close_tab", "stop", "browser_resize", "wait_for",
    "get_snapshot_text",
    "input_text_by_ref", "click_element_by_ref", "get_dropdown_options_by_ref",
    "select_dropdown_option_by_ref", "hover_element_by_ref", "focus_element_by_ref",
    "evaluate_javascript_on_ref", "upload_file_by_ref", "drag_element_by_ref",
    "check_checkbox_or_radio_by_ref", "uncheck_checkbox_by_ref", "double_click_element_by_ref",
    "scroll_element_into_view_by_ref",
    "mouse_move", "mouse_click", "mouse_drag", "mouse_down", "mouse_up", "mouse_wheel",
    "type_text", "key_down", "key_up", "fill_form", "insert_text",
    "take_screenshot", "save_pdf",
    "start_console_capture", "stop_console_capture", "get_console_messages",
    "start_network_capture", "stop_network_capture", "get_network_requests",
    "wait_for_network_idle",
    "setup_dialog_handler", "handle_dialog", "remove_dialog_handler",
    "save_storage_state", "restore_storage_state", "clear_cookies",
    "get_cookies", "set_cookie",
    "verify_element_visible", "verify_text_visible", "verify_value",
    "verify_element_state", "verify_url", "verify_title",
    "start_tracing", "stop_tracing", "start_video", "stop_video", "add_trace_chunk",
]


def test_browser_has_all_tool_methods():
    """Browser class should have all expected tool methods."""
    for method_name in EXPECTED_METHODS:
        assert hasattr(Browser, method_name), f"Browser is missing method: {method_name}"


def test_browser_tool_set_builder():
    from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory
    mock_browser = MagicMock(spec=Browser)
    for name in EXPECTED_METHODS:
        mock_method = AsyncMock()
        mock_method.__name__ = name
        setattr(mock_browser, name, mock_method)

    # ALL category should include all CLI-mapped tools (67 tools)
    builder = BrowserToolSetBuilder.for_categories(mock_browser, ToolCategory.ALL)
    specs = builder.build()["tool_specs"]
    tool_names = {s._tool_name for s in specs}

    assert len(specs) >= 60, f"Expected >=60 tools, got {len(specs)}"
    for expected in ("click_element_by_ref", "input_text_by_ref", "navigate_to", "get_snapshot_text", "browser_resize"):
        assert expected in tool_names, f"Expected tool {expected!r} missing from ALL category"

    # NAVIGATION category should include navigation tools only
    nav_builder = BrowserToolSetBuilder.for_categories(mock_browser, ToolCategory.NAVIGATION)
    nav_specs = nav_builder.build()["tool_specs"]
    nav_names = {s._tool_name for s in nav_specs}
    assert "navigate_to" in nav_names
    assert "click_element_by_ref" not in nav_names


# ---------------------------------------------------------------------------
# State guard tests: stop_* methods raise structured state errors when inactive
# ---------------------------------------------------------------------------

def _make_browser_with_mock_page() -> tuple:
    """Create a Browser instance with a mocked page (no real Playwright)."""
    browser = Browser.__new__(Browser)
    # Minimal instance state so stop_* methods can run without start().
    browser._console_messages = {}
    browser._network_requests = {}
    browser._console_handlers = {}
    browser._network_handlers = {}
    browser._dialog_handlers = {}
    browser._tracing_state = {}
    browser._video_state = {}
    browser._context = MagicMock()
    browser._page = MagicMock()
    # get_current_page() returns self._page
    browser.get_current_page = AsyncMock(return_value=browser._page)
    return browser


@pytest.mark.asyncio
async def test_stop_console_capture_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_console_capture()
    assert exc_info.value.code == "NO_ACTIVE_CAPTURE"


@pytest.mark.asyncio
async def test_stop_network_capture_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_network_capture()
    assert exc_info.value.code == "NO_ACTIVE_CAPTURE"


@pytest.mark.asyncio
async def test_stop_video_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_video()
    assert exc_info.value.code == "NO_ACTIVE_RECORDING"


@pytest.mark.asyncio
async def test_stop_tracing_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_tracing()
    assert exc_info.value.code == "NO_ACTIVE_TRACING"
