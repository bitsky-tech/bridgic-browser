"""
Shared CLI catalog and Browser-method mapping.

Single source of truth for:
- CLI help sections
- CLI command metadata
- CLI preset command sets
- CLI command -> Browser method mapping
- Derived tool categories and preset method lists for BrowserToolSetBuilder
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Dict, List

from ._constants import ToolPreset

# Ordered list of (section_title, [command_names]) for top-level `-h` output.
# Commands not listed here appear in a trailing "Other" section.
CLI_HELP_SECTIONS: list[tuple[str, list[str]]] = [
    ("Navigation", ["open", "search", "info", "reload", "back", "forward"]),
    ("Snapshot", ["snapshot"]),
    (
        "Element Interaction",
        [
            "click",
            "fill",
            "fill-form",
            "scroll-to",
            "select",
            "options",
            "check",
            "uncheck",
            "focus",
            "hover",
            "double-click",
            "upload",
            "drag",
        ],
    ),
    ("Tabs", ["tabs", "new-tab", "switch-tab", "close-tab"]),
    ("Evaluate", ["eval", "eval-on"]),
    ("Keyboard", ["type", "press", "key-down", "key-up"]),
    ("Mouse", ["scroll", "mouse-click", "mouse-move", "mouse-drag", "mouse-down", "mouse-up"]),
    ("Wait", ["wait"]),
    ("Capture", ["screenshot", "pdf"]),
    ("Network", ["network-start", "network", "network-stop", "wait-network"]),
    ("Dialog", ["dialog-setup", "dialog", "dialog-remove"]),
    ("Storage", ["cookies", "cookie-set", "cookies-clear", "storage-save", "storage-load"]),
    ("Verify", ["verify-text", "verify-visible", "verify-url", "verify-title", "verify-state", "verify-value"]),
    (
        "Developer",
        [
            "console-start",
            "console",
            "console-stop",
            "trace-start",
            "trace-chunk",
            "trace-stop",
            "video-start",
            "video-stop",
        ],
    ),
    ("Lifecycle", ["close", "resize"]),
]

# command_name -> (section, one-line description)
CLI_COMMAND_META: dict[str, tuple[str, str]] = {
    "open": ("navigation", "Navigate to URL (starts browser if needed)"),
    "back": ("navigation", "Go back to the previous page"),
    "forward": ("navigation", "Go forward to the next page"),
    "reload": ("navigation", "Reload the current page"),
    "search": ("navigation", "Search the web for QUERY [--engine duckduckgo|google|bing]"),
    "info": ("navigation", "Show current page URL, title, viewport, scroll position"),
    "snapshot": ("snapshot", "Get accessibility tree + refs [-i] [-F] [-s OFFSET]"),
    "click": ("element_interaction", "Click an element by ref (@e2 or e2)"),
    "double-click": ("element_interaction", "Double-click an element by ref"),
    "hover": ("element_interaction", "Hover over an element by ref"),
    "focus": ("element_interaction", "Focus an element by ref"),
    "fill": ("element_interaction", "Fill an input element by ref with TEXT"),
    "select": ("element_interaction", "Select a dropdown option by ref and option text"),
    "check": ("element_interaction", "Set checkbox/radio to checked by ref"),
    "uncheck": ("element_interaction", "Uncheck checkbox by ref (radio usually cannot be unchecked)"),
    "scroll-to": ("element_interaction", "Scroll an element into view by ref"),
    "drag": ("element_interaction", "Drag from START_REF to END_REF"),
    "options": ("element_interaction", "Get all options for a dropdown element by ref"),
    "upload": ("element_interaction", "Upload a file at PATH to a file input element by ref"),
    "fill-form": ("element_interaction", "Fill multiple form fields via JSON array [--submit]"),
    "press": ("keyboard", "Press a key or combination (Enter, Control+A, ...)"),
    "type": ("keyboard", "Type text character-by-character (triggers key events) [--submit]"),
    "key-down": ("keyboard", "Press and hold a keyboard key"),
    "key-up": ("keyboard", "Release a held keyboard key"),
    "scroll": ("mouse", "Scroll page [--dy pixels] [--dx pixels]"),
    "mouse-move": ("mouse", "Move the mouse to coordinates (X Y)"),
    "mouse-click": ("mouse", "Click mouse at (X Y) [--button left|right|middle] [--count N]"),
    "mouse-drag": ("mouse", "Drag mouse from (X1 Y1) to (X2 Y2)"),
    "mouse-down": ("mouse", "Press and hold a mouse button [--button left]"),
    "mouse-up": ("mouse", "Release a held mouse button [--button left]"),
    "wait": ("wait", "Wait for SECONDS or until TEXT appears [--gone]"),
    "tabs": ("tabs", "List all open tabs"),
    "new-tab": ("tabs", "Open a new tab [URL]"),
    "switch-tab": ("tabs", "Switch to a tab by page_id"),
    "close-tab": ("tabs", "Close a tab by page_id (or current tab if omitted)"),
    "screenshot": ("capture", "Save a screenshot to PATH [--full-page]"),
    "pdf": ("capture", "Save the current page as PDF"),
    "console-start": ("developer", "Start capturing browser console output"),
    "console-stop": ("developer", "Stop capturing browser console output"),
    "console": ("developer", "Get captured console messages [--filter TYPE] [--no-clear]"),
    "network-start": ("network", "Start capturing network requests"),
    "network-stop": ("network", "Stop capturing network requests"),
    "network": ("network", "Get captured network requests [--static] [--no-clear]"),
    "wait-network": ("network", "Wait until network is idle [--timeout MS]"),
    "dialog-setup": ("dialog", "Set up automatic dialog handling [--action accept|dismiss] [--text TEXT]"),
    "dialog": ("dialog", "Handle the next dialog [--dismiss] [--text TEXT]"),
    "dialog-remove": ("dialog", "Remove the automatic dialog handler"),
    "storage-save": ("storage", "Save browser storage state (cookies, localStorage) to PATH"),
    "storage-load": ("storage", "Restore browser storage state from PATH"),
    "cookies-clear": ("storage", "Clear all cookies from the browser context"),
    "cookies": ("storage", "Get cookies from the browser context [--url URL]"),
    "cookie-set": (
        "storage",
        "Set a cookie NAME VALUE [--url] [--domain] [--path] [--expires] [--http-only] [--secure] [--same-site]",
    ),
    "verify-visible": ("verify", "Verify element with ROLE and NAME is visible [--timeout MS]"),
    "verify-text": ("verify", "Verify TEXT is visible on the page [--exact] [--timeout MS]"),
    "verify-value": ("verify", "Verify value of REF element matches EXPECTED"),
    "verify-state": ("verify", "Verify REF state: visible|hidden|enabled|disabled|checked|unchecked"),
    "verify-url": ("verify", "Verify current page URL matches URL [--exact]"),
    "verify-title": ("verify", "Verify current page title matches TITLE [--exact]"),
    "eval": ("evaluate", "Evaluate JavaScript in the page context"),
    "eval-on": ("evaluate", "Evaluate JavaScript with REF element as argument"),
    "trace-start": ("developer", "Start browser tracing [--no-screenshots] [--no-snapshots]"),
    "trace-stop": ("developer", "Stop tracing and save to PATH (.zip)"),
    "trace-chunk": ("developer", "Add a named chunk marker to the current trace"),
    "video-start": ("developer", "Start video recording [--width W] [--height H]"),
    "video-stop": ("developer", "Stop video recording [PATH]"),
    "close": ("lifecycle", "Close the browser and stop the daemon"),
    "resize": ("lifecycle", "Resize the browser viewport to WIDTH x HEIGHT"),
}


CLI_PRESET_COMMANDS: dict[ToolPreset, list[str]] = {
    ToolPreset.MINIMAL: [
        "open",
        "back",
        "forward",
        "reload",
        "info",
        "snapshot",
        "click",
        "fill",
        "close",
    ],
    ToolPreset.NAVIGATION: [
        "open",
        "back",
        "forward",
    ],
    ToolPreset.SCRAPING: [
        "open",
        "back",
        "forward",
        "reload",
        "info",
        "snapshot",
        "scroll",
        "wait",
        "screenshot",
        "close",
    ],
    ToolPreset.FORM_FILLING: [
        "open",
        "back",
        "forward",
        "reload",
        "info",
        "snapshot",
        "click",
        "double-click",
        "hover",
        "focus",
        "fill",
        "select",
        "check",
        "uncheck",
        "press",
        "type",
        "wait",
        "close",
    ],
    ToolPreset.TESTING: [
        "open",
        "back",
        "forward",
        "reload",
        "info",
        "snapshot",
        "click",
        "double-click",
        "hover",
        "focus",
        "fill",
        "select",
        "check",
        "uncheck",
        "press",
        "type",
        "scroll",
        "wait",
        "verify-visible",
        "verify-text",
        "verify-value",
        "verify-state",
        "verify-url",
        "verify-title",
        "screenshot",
        "close",
    ],
    ToolPreset.INTERACTIVE: [
        "open",
        "back",
        "forward",
        "reload",
        "search",
        "info",
        "snapshot",
        "click",
        "double-click",
        "hover",
        "focus",
        "fill",
        "select",
        "check",
        "uncheck",
        "scroll-to",
        "drag",
        "options",
        "upload",
        "fill-form",
        "press",
        "type",
        "scroll",
        "mouse-move",
        "mouse-click",
        "wait",
        "tabs",
        "new-tab",
        "switch-tab",
        "close-tab",
        "screenshot",
        "close",
    ],
    ToolPreset.DEVELOPER: [
        "open",
        "back",
        "reload",
        "info",
        "snapshot",
        "click",
        "fill",
        "eval",
        "eval-on",
        "trace-start",
        "trace-stop",
        "trace-chunk",
        "video-start",
        "video-stop",
        "console-start",
        "console-stop",
        "console",
        "network-start",
        "network-stop",
        "network",
        "screenshot",
        "pdf",
        "close",
    ],
    ToolPreset.COMPLETE: list(CLI_COMMAND_META.keys()),
}


CLI_SECTION_ORDER: list[str] = [
    "navigation",
    "snapshot",
    "element_interaction",
    "keyboard",
    "mouse",
    "wait",
    "tabs",
    "evaluate",
    "capture",
    "network",
    "dialog",
    "storage",
    "verify",
    "developer",
    "lifecycle",
]


CLI_COMMAND_TO_TOOL_METHOD: Dict[str, str] = {
    "open": "navigate_to_url",
    "search": "search",
    "info": "get_current_page_info_str",
    "reload": "reload_page",
    "back": "go_back",
    "forward": "go_forward",
    "snapshot": "get_snapshot_text",
    "click": "click_element_by_ref",
    "fill": "input_text_by_ref",
    "fill-form": "fill_form",
    "scroll-to": "scroll_element_into_view_by_ref",
    "select": "select_dropdown_option_by_ref",
    "options": "get_dropdown_options_by_ref",
    "check": "check_checkbox_by_ref",
    "uncheck": "uncheck_checkbox_by_ref",
    "focus": "focus_element_by_ref",
    "hover": "hover_element_by_ref",
    "double-click": "double_click_element_by_ref",
    "upload": "upload_file_by_ref",
    "drag": "drag_element_by_ref",
    "tabs": "get_tabs",
    "new-tab": "new_tab",
    "switch-tab": "switch_tab",
    "close-tab": "close_tab",
    "eval": "evaluate_javascript",
    "eval-on": "evaluate_javascript_on_ref",
    "press": "press_key",
    "type": "type_text",
    "key-down": "key_down",
    "key-up": "key_up",
    "scroll": "mouse_wheel",
    "mouse-click": "mouse_click",
    "mouse-move": "mouse_move",
    "mouse-drag": "mouse_drag",
    "mouse-down": "mouse_down",
    "mouse-up": "mouse_up",
    "wait": "wait_for",
    "screenshot": "take_screenshot",
    "pdf": "save_pdf",
    "network-start": "start_network_capture",
    "network": "get_network_requests",
    "network-stop": "stop_network_capture",
    "wait-network": "wait_for_network_idle",
    "dialog-setup": "setup_dialog_handler",
    "dialog": "handle_dialog",
    "dialog-remove": "remove_dialog_handler",
    "cookies": "get_cookies",
    "cookie-set": "set_cookie",
    "cookies-clear": "clear_cookies",
    "storage-save": "save_storage_state",
    "storage-load": "restore_storage_state",
    "verify-text": "verify_text_visible",
    "verify-visible": "verify_element_visible",
    "verify-url": "verify_url",
    "verify-title": "verify_title",
    "verify-state": "verify_element_state",
    "verify-value": "verify_value",
    "console-start": "start_console_capture",
    "console": "get_console_messages",
    "console-stop": "stop_console_capture",
    "trace-start": "start_tracing",
    "trace-chunk": "add_trace_chunk",
    "trace-stop": "stop_tracing",
    "video-start": "start_video",
    "video-stop": "stop_video",
    "close": "browser_close",
    "resize": "browser_resize",
}



def section_title_to_category(section_title: str) -> str:
    """Convert CLI section title to BrowserToolSetBuilder category format."""
    return section_title.strip().lower().replace(" ", "_")


def map_cli_commands_to_tool_methods(commands: Sequence[str]) -> List[str]:
    """Map ordered CLI command names to ordered, deduplicated Browser methods."""
    ordered_methods: List[str] = []
    seen: set[str] = set()

    for command in commands:
        method = CLI_COMMAND_TO_TOOL_METHOD.get(command)
        if method is None or method in seen:
            continue
        ordered_methods.append(method)
        seen.add(method)

    return ordered_methods


def build_tool_categories_from_help_sections(
    sections: Sequence[tuple[str, list[str]]],
) -> Dict[str, List[str]]:
    """Build BrowserToolSetBuilder categories from CLI help sections."""
    categories: Dict[str, List[str]] = {}

    for section_title, commands in sections:
        method_names = map_cli_commands_to_tool_methods(commands)
        if not method_names:
            continue
        categories[section_title_to_category(section_title)] = method_names

    return categories


def build_tool_presets_from_cli_preset_commands(
    preset_commands: Mapping[ToolPreset, Sequence[str]],
) -> Dict[ToolPreset, List[str]]:
    """Build BrowserToolSetBuilder preset method lists from CLI preset commands."""
    return {
        preset: map_cli_commands_to_tool_methods(commands)
        for preset, commands in preset_commands.items()
    }


CLI_TOOL_CATEGORIES: Dict[str, List[str]] = build_tool_categories_from_help_sections(CLI_HELP_SECTIONS)
CLI_PRESET_TOOL_METHODS: Dict[ToolPreset, List[str]] = build_tool_presets_from_cli_preset_commands(CLI_PRESET_COMMANDS)


def _validate_catalog() -> None:
    """Fail fast when catalog constants become inconsistent."""
    known_commands = set(CLI_COMMAND_TO_TOOL_METHOD)

    for _, commands in CLI_HELP_SECTIONS:
        for command in commands:
            if command not in known_commands:
                raise ValueError(f"CLI help command not mapped: {command}")

    for commands in CLI_PRESET_COMMANDS.values():
        for command in commands:
            if command not in known_commands:
                raise ValueError(f"CLI preset command not mapped: {command}")

    help_section_commands = {command for _, commands in CLI_HELP_SECTIONS for command in commands}
    missing_from_help = set(CLI_COMMAND_META) - help_section_commands
    if missing_from_help:
        missing_sorted = ", ".join(sorted(missing_from_help))
        raise ValueError(f"CLI command metadata missing from help sections: {missing_sorted}")


_validate_catalog()

