"""
Shared CLI catalog and Browser-method mapping.

Single source of truth for:
- CLI help sections
- CLI command metadata
- CLI command -> Browser method mapping
- Derived tool categories for BrowserToolSetBuilder
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Dict, List

from ._constants import ToolCategory

# Ordered list of (ToolCategory, [command_names]).
# ToolCategory.value provides the display title for `bridgic-browser -h`.
CLI_HELP_SECTION_SPECS: list[tuple[ToolCategory, list[str]]] = [
    (ToolCategory.NAVIGATION, ["open", "search", "info", "reload", "back", "forward"]),
    (ToolCategory.SNAPSHOT, ["snapshot"]),
    (
        ToolCategory.ELEMENT_INTERACTION,
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
    (ToolCategory.TABS, ["tabs", "new-tab", "switch-tab", "close-tab"]),
    (ToolCategory.EVALUATE, ["eval", "eval-on"]),
    (ToolCategory.KEYBOARD, ["type", "press", "key-down", "key-up"]),
    (ToolCategory.MOUSE, ["scroll", "mouse-click", "mouse-move", "mouse-drag", "mouse-down", "mouse-up"]),
    (ToolCategory.WAIT, ["wait"]),
    (ToolCategory.CAPTURE, ["screenshot", "pdf"]),
    (ToolCategory.NETWORK, ["network-start", "network", "network-stop", "wait-network"]),
    (ToolCategory.DIALOG, ["dialog-setup", "dialog", "dialog-remove"]),
    (ToolCategory.STORAGE, ["cookies", "cookie-set", "cookies-clear", "storage-save", "storage-load"]),
    (ToolCategory.VERIFY, ["verify-text", "verify-visible", "verify-url", "verify-title", "verify-state", "verify-value"]),
    (
        ToolCategory.DEVELOPER,
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
    (ToolCategory.LIFECYCLE, ["close", "resize"]),
]

# Shape for Click group rendering: (display_title, [command_names]).
CLI_HELP_SECTIONS: list[tuple[str, list[str]]] = [
    (category.value, commands) for category, commands in CLI_HELP_SECTION_SPECS
]

# Flattened command list in CLI display order.
CLI_ALL_COMMANDS: list[str] = [
    command
    for _category, commands in CLI_HELP_SECTION_SPECS
    for command in commands
]

# command_name -> (ToolCategory, one-line description)
CLI_COMMAND_META: dict[str, tuple[ToolCategory, str]] = {
    "open": (ToolCategory.NAVIGATION, "Navigate to URL (starts a browser session if needed) [--headed] [--clear-user-data] [--cdp PORT_OR_URL]"),
    "back": (ToolCategory.NAVIGATION, "Go back to the previous page"),
    "forward": (ToolCategory.NAVIGATION, "Go forward to the next page"),
    "reload": (ToolCategory.NAVIGATION, "Reload the current page"),
    "search": (ToolCategory.NAVIGATION, "Search the web using a search engine (starts a browser session if needed) [--headed] [--clear-user-data] [--engine duckduckgo|google|bing]"),
    "info": (ToolCategory.NAVIGATION, "Show current page URL, title, viewport, scroll position"),
    "snapshot": (ToolCategory.SNAPSHOT, "Get accessibility tree of the current page (full-page by default) with refs [-i] [-F viewport-only] [-l LIMIT] [-s FILE]"),
    "click": (ToolCategory.ELEMENT_INTERACTION, "Click an element by ref (@80365bf7 or 80365bf7)"),
    "double-click": (ToolCategory.ELEMENT_INTERACTION, "Double-click an element by ref"),
    "hover": (ToolCategory.ELEMENT_INTERACTION, "Hover over an element by ref"),
    "focus": (ToolCategory.ELEMENT_INTERACTION, "Focus an element by ref"),
    "fill": (ToolCategory.ELEMENT_INTERACTION, "Fill an input element by ref with TEXT [--submit]"),
    "select": (ToolCategory.ELEMENT_INTERACTION, "Select an option by its text from a dropdown element represented by ref"),
    "check": (ToolCategory.ELEMENT_INTERACTION, "Set checkbox/radio to checked by ref"),
    "uncheck": (ToolCategory.ELEMENT_INTERACTION, "Uncheck checkbox by ref (radio usually cannot be unchecked)"),
    "scroll-to": (ToolCategory.ELEMENT_INTERACTION, "Scroll an element into view by ref"),
    "drag": (ToolCategory.ELEMENT_INTERACTION, "Drag from START_REF to END_REF"),
    "options": (ToolCategory.ELEMENT_INTERACTION, "Get all available options for a dropdown element by ref"),
    "upload": (ToolCategory.ELEMENT_INTERACTION, "Upload a file at PATH to a file input element by ref"),
    "fill-form": (ToolCategory.ELEMENT_INTERACTION, "Fill multiple form fields [--submit]; FIELDS_JSON: '[{\"ref\":\"REF\",\"value\":\"TEXT\"}]'"),
    "press": (ToolCategory.KEYBOARD, "Press a key or combination (Enter, Control+A, ...); macOS: use Meta for Cmd (Meta+A, Meta+C)"),
    "type": (ToolCategory.KEYBOARD, "Type TEXT into the focused element character-by-character (use 'click'/'focus' first) [--submit]"),
    "key-down": (ToolCategory.KEYBOARD, "Press and hold a keyboard key"),
    "key-up": (ToolCategory.KEYBOARD, "Release a held keyboard key"),
    "scroll": (ToolCategory.MOUSE, "Scroll page [--dy pixels] [--dx pixels]"),
    "mouse-move": (ToolCategory.MOUSE, "Move the mouse to viewport-pixel coordinates (X Y from top-left)"),
    "mouse-click": (ToolCategory.MOUSE, "Click mouse at viewport-pixel coordinates (X Y) [--button left|right|middle] [--count N]"),
    "mouse-drag": (ToolCategory.MOUSE, "Drag mouse from viewport-pixel (X1 Y1) to (X2 Y2)"),
    "mouse-down": (ToolCategory.MOUSE, "Press and hold a mouse button at current position [--button left]; call mouse-move first"),
    "mouse-up": (ToolCategory.MOUSE, "Release a held mouse button at current position [--button left]; call mouse-move first"),
    "wait": (ToolCategory.WAIT, "Wait N seconds (unit: SECONDS not ms) or until TEXT appears [--timeout S]; TEXT --gone waits for disappearance"),
    "tabs": (ToolCategory.TABS, "List all open tabs"),
    "new-tab": (ToolCategory.TABS, "Open a new tab [URL]"),
    "switch-tab": (ToolCategory.TABS, "Switch to a tab by page_id; run 'tabs' first to list available page IDs"),
    "close-tab": (ToolCategory.TABS, "Close a tab by page_id (or current tab if omitted); run 'tabs' first to list page IDs"),
    "screenshot": (ToolCategory.CAPTURE, "Save a screenshot to PATH [--full-page]"),
    "pdf": (ToolCategory.CAPTURE, "Save the current page as PDF"),
    "console-start": (ToolCategory.DEVELOPER, "Start capturing browser console output"),
    "console-stop": (ToolCategory.DEVELOPER, "Stop capturing browser console output"),
    "console": (ToolCategory.DEVELOPER, "Get captured console messages [--filter TYPE] [--no-clear]"),
    "network-start": (ToolCategory.NETWORK, "Start capturing network requests"),
    "network-stop": (ToolCategory.NETWORK, "Stop capturing network requests"),
    "network": (ToolCategory.NETWORK, "Get captured network requests [--static] [--no-clear]"),
    "wait-network": (ToolCategory.NETWORK, "Wait until network is idle [SECONDS=30]"),
    "dialog-setup": (ToolCategory.DIALOG, "Set up automatic dialog handling [--action accept|dismiss] [--text TEXT]"),
    "dialog": (ToolCategory.DIALOG, "Handle the next dialog [--dismiss] [--text TEXT]"),
    "dialog-remove": (ToolCategory.DIALOG, "Remove the automatic dialog handler"),
    "storage-save": (ToolCategory.STORAGE, "Save browser storage state (cookies, localStorage) to PATH"),
    "storage-load": (ToolCategory.STORAGE, "Restore browser storage state from PATH"),
    "cookies-clear": (ToolCategory.STORAGE, "Clear cookies [--name NAME] [--domain DOMAIN] [--path PATH]"),
    "cookies": (ToolCategory.STORAGE, "Get cookies [--domain DOMAIN] [--path PATH] [--name NAME]"),
    "cookie-set": (
        ToolCategory.STORAGE,
        "Set a cookie NAME VALUE [--domain] [--path] [--expires] [--http-only] [--secure] [--same-site]",
    ),
    "verify-visible": (ToolCategory.VERIFY, "Verify element with ROLE and NAME is visible [--timeout S]"),
    "verify-text": (ToolCategory.VERIFY, "Verify TEXT is visible on the page [--exact] [--timeout S]"),
    "verify-value": (ToolCategory.VERIFY, "Verify value of REF element matches EXPECTED"),
    "verify-state": (ToolCategory.VERIFY, "Verify REF state: visible|hidden|enabled|disabled|checked|unchecked"),
    "verify-url": (ToolCategory.VERIFY, "Verify current page URL matches URL [--exact]"),
    "verify-title": (ToolCategory.VERIFY, "Verify current page title matches TITLE [--exact]"),
    "eval": (ToolCategory.EVALUATE, "Evaluate JavaScript in the page context"),
    "eval-on": (ToolCategory.EVALUATE, "Evaluate JS arrow function with REF element as argument: '(el) => el.textContent'"),
    "trace-start": (ToolCategory.DEVELOPER, "Start browser tracing [--no-screenshots] [--no-snapshots]"),
    "trace-stop": (ToolCategory.DEVELOPER, "Stop tracing and save to PATH (.zip)"),
    "trace-chunk": (ToolCategory.DEVELOPER, "Add a named chunk marker to the current trace"),
    "video-start": (ToolCategory.DEVELOPER, "Start video recording [--width W] [--height H]"),
    "video-stop": (ToolCategory.DEVELOPER, "Stop video recording [PATH]"),
    "close": (ToolCategory.LIFECYCLE, "Close the browser session"),
    "resize": (ToolCategory.LIFECYCLE, "Resize the browser viewport to WIDTH x HEIGHT"),
}


CLI_COMMAND_TO_TOOL_METHOD: Dict[str, str] = {
    "open": "navigate_to",
    "search": "search",
    "info": "get_current_page_info",
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
    "check": "check_checkbox_or_radio_by_ref",
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
    "close": "close",
    "resize": "browser_resize",
}

# CLI commands that are informational and not backed by Browser tool methods.
# Keep this set for validation extension points; currently all sectioned commands
# are mapped to Browser tool methods.
CLI_NON_TOOL_COMMANDS: set[str] = set()


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


def _build_tool_categories() -> Dict[ToolCategory, List[str]]:
    """Build BrowserToolSetBuilder categories from CLI_HELP_SECTION_SPECS."""
    categories: Dict[ToolCategory, List[str]] = {}
    for category, commands in CLI_HELP_SECTION_SPECS:
        method_names = map_cli_commands_to_tool_methods(commands)
        if method_names:
            categories[category] = method_names
    return categories


CLI_TOOL_CATEGORIES: Dict[ToolCategory, List[str]] = _build_tool_categories()


def _find_duplicates(values: Sequence[str]) -> list[str]:
    """Return sorted duplicate values from an ordered string sequence."""
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _validate_catalog() -> None:
    """Fail fast when catalog constants become inconsistent."""
    help_command_duplicates = _find_duplicates(CLI_ALL_COMMANDS)
    if help_command_duplicates:
        raise ValueError(
            "Duplicate commands in CLI_HELP_SECTION_SPECS: "
            + ", ".join(help_command_duplicates)
        )

    for category, commands in CLI_HELP_SECTION_SPECS:
        duplicates = _find_duplicates(commands)
        if duplicates:
            raise ValueError(
                f"Duplicate commands in section {category.name!r}: "
                + ", ".join(duplicates)
            )

    help_command_set = set(CLI_ALL_COMMANDS)
    meta_command_set = set(CLI_COMMAND_META)
    mapped_command_set = set(CLI_COMMAND_TO_TOOL_METHOD)

    missing_in_meta = sorted(help_command_set - meta_command_set)
    if missing_in_meta:
        raise ValueError(
            "CLI commands missing metadata: " + ", ".join(missing_in_meta)
        )

    missing_in_help = sorted(meta_command_set - help_command_set)
    if missing_in_help:
        raise ValueError(
            "CLI command metadata missing from help sections: " + ", ".join(missing_in_help)
        )

    missing_in_mapping = sorted(
        command
        for command in (help_command_set - mapped_command_set)
        if command not in CLI_NON_TOOL_COMMANDS
    )
    if missing_in_mapping:
        raise ValueError(
            "CLI commands missing command-to-tool mapping: " + ", ".join(missing_in_mapping)
        )

    unknown_non_tool = sorted(CLI_NON_TOOL_COMMANDS - help_command_set)
    if unknown_non_tool:
        raise ValueError(
            "Non-tool command set contains unknown commands: " + ", ".join(unknown_non_tool)
        )

    non_tool_with_mapping = sorted(CLI_NON_TOOL_COMMANDS & mapped_command_set)
    if non_tool_with_mapping:
        raise ValueError(
            "Non-tool commands should not define command-to-tool mapping: "
            + ", ".join(non_tool_with_mapping)
        )

    extra_in_mapping = sorted(mapped_command_set - help_command_set)
    if extra_in_mapping:
        raise ValueError(
            "Mapped commands not present in CLI help sections: " + ", ".join(extra_in_mapping)
        )

    command_to_category = {
        command: category
        for category, commands in CLI_HELP_SECTION_SPECS
        for command in commands
    }
    for command, (meta_category, _description) in CLI_COMMAND_META.items():
        expected_category = command_to_category.get(command)
        if expected_category is None:
            continue
        if meta_category != expected_category:
            raise ValueError(
                f"CLI metadata section mismatch for {command!r}: "
                f"expected {expected_category.name!r}, got {meta_category.name!r}"
            )


_validate_catalog()
