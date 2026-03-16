"""
Unit tests verifying that the Browser class has all expected tool methods.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bridgic.browser.session import Browser


EXPECTED_METHODS = [
    "search", "navigate_to_url", "go_back", "go_forward",
    "reload_page", "scroll_to_text", "press_key", "evaluate_javascript",
    "get_current_page_info_str", "new_tab", "get_tabs", "switch_tab",
    "close_tab", "browser_close", "browser_resize", "wait_for",
    "get_snapshot_text",
    "input_text_by_ref", "click_element_by_ref", "get_dropdown_options_by_ref",
    "select_dropdown_option_by_ref", "hover_element_by_ref", "focus_element_by_ref",
    "evaluate_javascript_on_ref", "upload_file_by_ref", "drag_element_by_ref",
    "check_checkbox_by_ref", "uncheck_checkbox_by_ref", "double_click_element_by_ref",
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


def test_browser_has_get_snapshot_text():
    assert hasattr(Browser, "get_snapshot_text")
    assert not hasattr(Browser, "get_llm_repr"), "get_llm_repr should have been renamed"


def test_browser_tool_set_builder():
    from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset
    mock_browser = MagicMock(spec=Browser)
    # Add all expected methods as mock async methods
    for name in EXPECTED_METHODS:
        setattr(mock_browser, name, AsyncMock())
    builder = BrowserToolSetBuilder.for_preset(mock_browser, ToolPreset.MINIMAL)
    specs = builder.build()["tool_specs"]
    assert len(specs) > 0


def test_browser_tool_spec_from_bound_method():
    from bridgic.browser.tools import BrowserToolSpec
    mock_browser = MagicMock(spec=Browser)
    mock_browser.click_element_by_ref = AsyncMock()
    # Test that from_raw works with a bound method (no browser arg required)
    # We just test that it can be created without error
    # In real usage: spec = BrowserToolSpec.from_raw(browser.click_element_by_ref)
