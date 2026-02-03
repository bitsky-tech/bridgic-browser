"""
Browser automation tools module.

This module provides browser automation tools that can be used with Bridgic agents.
Use BrowserToolSetBuilder with ToolPreset for scenario-based tool selection.

Quick Start
-----------
>>> from bridgic.browser.session import Browser
>>> from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset
>>>
>>> browser = Browser(name="my_browser")
>>> await browser.start()
>>>
>>> # Choose a preset for your use case
>>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)      # 10 tools
>>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING) # 20 tools
>>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)      # 28 tools
>>>
>>> # Or select by category
>>> tools = BrowserToolSetBuilder.for_categories(browser, "navigation", "action")
>>>
>>> # Or use fluent builder for fine-grained control
>>> tools = (BrowserToolSetBuilder(browser)
...     .with_preset(ToolPreset.MINIMAL)
...     .with_category("screenshot")
...     .without_tools("go_forward")
...     .build_specs())

Available Presets
-----------------
- MINIMAL: Navigate, click, input, snapshot (10 tools)
- NAVIGATION: Search, navigate, back/forward (4 tools)
- SCRAPING: Navigation + snapshot + scroll (13 tools)
- FORM_FILLING: Navigation + input + dropdown + checkbox (20 tools)
- TESTING: Form filling + verification + screenshot (28 tools)
- INTERACTIVE: All action tools + mouse + keyboard (40 tools)
- DEVELOPER: Network + console + tracing (18 tools)
- COMPLETE: All available tools (68 tools)

Return Value Format
-------------------
All tools return a string message following a consistent format:

**Success messages**:
- Action confirmation: "Clicked element e1", "Navigated to https://..."
- Data results: JSON string or formatted text

**Error messages**:
- Element not found: "Element ref {ref} is not available - page may have changed."
- Operation failed: "Failed to {action}: {error details}"
- Invalid input: "{parameter} is empty/invalid"

**Verification tools** use special prefixes:
- Success: "PASS: {description}"
- Failure: "FAIL: {description} - {reason}"

Tool Selection Guide
--------------------
**Ref-based tools vs Coordinate-based tools**:

Use **ref-based tools** (e.g., `click_element_by_ref`) when:
- Element has a ref from `get_llm_repr()` snapshot
- Need reliable, stable element identification
- Working with standard web elements (buttons, inputs, links)
- Accessibility-aware interaction is important

Use **coordinate-based tools** (e.g., `mouse_click`) when:
- Need to click at specific pixel positions
- Interacting with canvas, SVG, or custom UI components
- Ref is not available or element is dynamically generated
- Precise mouse positioning is required (drag operations)

**Similar tools comparison**:

| Task | Preferred Tool | Alternative | When to use alternative |
|------|---------------|-------------|------------------------|
| Click element | click_element_by_ref | mouse_click | Canvas/SVG elements |
| Type text | input_text_by_ref | press_sequentially | Trigger key events |
| Drag element | drag_element_by_ref | mouse_drag | Custom drag behavior |
| Scroll | scroll_to_text | mouse_wheel | Scroll by exact pixels |
| Fill input | input_text_by_ref | insert_text | Fast bulk text insert |

**Text input methods**:
- `input_text_by_ref`: Standard input, uses .fill() - fast and reliable
- `input_text_by_ref(slowly=True)`: Character-by-character with delays
- `press_sequentially`: Key events for each character, triggers handlers
- `insert_text`: Direct text insertion at cursor, fastest but may skip events
"""

# ==================== Tool Spec and Builder ====================
from ._browser_tool_spec import BrowserToolSpec
from ._browser_tool_set_builder import BrowserToolSetBuilder, ToolPreset

# ==================== Browser State Tools ====================
from ._browser_state_tools import get_llm_repr

# ==================== Navigation Tools ====================
from ._browser_tools import (
    search,              # Search using search engine
    navigate_to_url,     # Navigate to specified URL
    go_back,            # Browser back navigation
    go_forward,         # Browser forward navigation
)

# ==================== Page and Tab Management Tools ====================
from ._browser_tools import (
    get_current_page_info,  # Get current page information
    reload_page,        # Reload current page
    scroll_to_text,       # Scroll to specified text position
    press_key,          # Press keyboard key
    evaluate_javascript, # Execute JavaScript code
    new_tab,            # Create new tab
    get_tabs,           # Get all tabs list
    switch_tab,         # Switch to specified tab
    close_tab,          # Close tab
)

# ==================== Browser Control Tools ====================
from ._browser_tools import (
    browser_close,      # Close browser
    browser_resize,     # Resize browser viewport
    wait_for,           # Wait for conditions (replaces wait)
    # run_playwright_code, # Execute Playwright code
)

# ==================== Element Action Tools (by ref) ====================
from ._browser_action_tools import (
    input_text_by_ref,  # Input text (supports slowly typing via slowly=True)
    click_element_by_ref,
    get_dropdown_options_by_ref,
    select_dropdown_option_by_ref,
    hover_element_by_ref,
    focus_element_by_ref,
    evaluate_javascript_on_ref,
    upload_file_by_ref,
    drag_element_by_ref,
    check_element_by_ref,
    uncheck_element_by_ref,
    double_click_element_by_ref,
    scroll_element_into_view_by_ref,
)

# ==================== Mouse Tools (coordinate-based) ====================
from ._browser_mouse_tools import (
    mouse_move,
    mouse_click,
    mouse_drag,
    mouse_down,
    mouse_up,
    mouse_wheel,  # Use this instead of scroll_page
)

# ==================== Keyboard Tools ====================
from ._browser_keyboard_tools import (
    press_sequentially,
    key_down,
    key_up,
    fill_form,
    insert_text,
)

# ==================== Screenshot and PDF Tools ====================
from ._browser_screenshot_tools import (
    take_screenshot,
    save_pdf,
)

# ==================== Network and Console Tools ====================
from ._browser_network_tools import (
    start_console_capture,
    get_console_messages,
    start_network_capture,
    get_network_requests,
    wait_for_network_idle,
)

# ==================== Dialog Tools ====================
from ._browser_dialog_tools import (
    setup_dialog_handler,
    handle_dialog,
    remove_dialog_handler,
)

# ==================== Storage Tools ====================
from ._browser_storage_tools import (
    save_storage_state,
    restore_storage_state,
    clear_cookies,
    get_cookies,
    set_cookie,
)

# ==================== Verification Tools ====================
from ._browser_verify_tools import (
    verify_element_visible,
    verify_text_visible,
    verify_value,
    verify_element_state,
    verify_url,
    verify_title,
)

# ==================== DevTools (Tracing and Video) ====================
from ._browser_devtools import (
    start_tracing,
    stop_tracing,
    start_video,
    stop_video,
    add_trace_chunk,
)

# Raw tool function lists (for use with BrowserToolSetBuilder)
BROWSER_NAVIGATION_TOOLS = [
    search,
    navigate_to_url,
    go_back,
    go_forward,
]

BROWSER_PAGE_TOOLS = [
    reload_page,
    scroll_to_text,
    press_key,
    evaluate_javascript,
    get_current_page_info,
    new_tab,
    get_tabs,
    switch_tab,
    close_tab,
]

BROWSER_ACTION_TOOLS = [
    input_text_by_ref,
    click_element_by_ref,
    get_dropdown_options_by_ref,
    select_dropdown_option_by_ref,
    hover_element_by_ref,
    focus_element_by_ref,
    evaluate_javascript_on_ref,
    upload_file_by_ref,
    drag_element_by_ref,
    check_element_by_ref,
    uncheck_element_by_ref,
    double_click_element_by_ref,
    scroll_element_into_view_by_ref,
]

BROWSER_MOUSE_TOOLS = [
    mouse_move,
    mouse_click,
    mouse_drag,
    mouse_down,
    mouse_up,
    mouse_wheel,
]

BROWSER_KEYBOARD_TOOLS = [
    press_sequentially,
    key_down,
    key_up,
    fill_form,
    insert_text,
]

BROWSER_SCREENSHOT_TOOLS = [
    take_screenshot,
    save_pdf,
]

BROWSER_NETWORK_TOOLS = [
    start_console_capture,
    get_console_messages,
    start_network_capture,
    get_network_requests,
    wait_for_network_idle,
]

BROWSER_DIALOG_TOOLS = [
    setup_dialog_handler,
    handle_dialog,
    remove_dialog_handler,
]

BROWSER_STORAGE_TOOLS = [
    save_storage_state,
    restore_storage_state,
    clear_cookies,
    get_cookies,
    set_cookie,
]

BROWSER_VERIFY_TOOLS = [
    verify_element_visible,
    verify_text_visible,
    verify_value,
    verify_element_state,
    verify_url,
    verify_title,
]

BROWSER_DEVTOOLS_TOOLS = [
    start_tracing,
    stop_tracing,
    start_video,
    stop_video,
    add_trace_chunk,
]

BROWSER_CONTROL_TOOLS = [
    browser_close,
    browser_resize,
    wait_for,
    # run_playwright_code,
]

BROWSER_BASIC_TOOLS = [
    *BROWSER_PAGE_TOOLS,
    *BROWSER_NAVIGATION_TOOLS,
    get_llm_repr,
]

BROWSER_ALL_TOOLS = [
    *BROWSER_BASIC_TOOLS,
    *BROWSER_ACTION_TOOLS,
    *BROWSER_MOUSE_TOOLS,
    *BROWSER_KEYBOARD_TOOLS,
    *BROWSER_SCREENSHOT_TOOLS,
    *BROWSER_NETWORK_TOOLS,
    *BROWSER_DIALOG_TOOLS,
    *BROWSER_STORAGE_TOOLS,
    *BROWSER_VERIFY_TOOLS,
    *BROWSER_DEVTOOLS_TOOLS,
    *BROWSER_CONTROL_TOOLS,
]


class ToolSet:
    """Tool set wrapper for retrieving different tool groups by scenario.

    This class provides static methods to access different categories of
    browser automation tools, making it easy to select appropriate tools
    for different use cases.

    Notes
    -----
    These methods return raw tool functions. For use with agents, you should
    use BrowserToolSetBuilder to bind them to a Browser instance:

    >>> browser = Browser(name="my_browser")
    >>> tools = BrowserToolSetBuilder.basic_tools(browser)

    Or for specific tools:

    >>> from bridgic.browser.tools import search, navigate_to_url
    >>> tools = BrowserToolSetBuilder.from_funcs(browser, search, navigate_to_url)
    """

    @staticmethod
    def browser_navigation_tools():
        """Get browser navigation tool functions."""
        return BROWSER_NAVIGATION_TOOLS

    @staticmethod
    def browser_page_tools():
        """Get browser page manipulation tool functions."""
        return BROWSER_PAGE_TOOLS

    @staticmethod
    def browser_action_tools():
        """Get element action tool functions (by ref)."""
        return BROWSER_ACTION_TOOLS

    @staticmethod
    def browser_mouse_tools():
        """Get mouse coordinate-based tool functions."""
        return BROWSER_MOUSE_TOOLS

    @staticmethod
    def browser_keyboard_tools():
        """Get keyboard tool functions."""
        return BROWSER_KEYBOARD_TOOLS

    @staticmethod
    def browser_screenshot_tools():
        """Get screenshot and PDF tool functions."""
        return BROWSER_SCREENSHOT_TOOLS

    @staticmethod
    def browser_network_tools():
        """Get network and console monitoring tool functions."""
        return BROWSER_NETWORK_TOOLS

    @staticmethod
    def browser_dialog_tools():
        """Get dialog handling tool functions."""
        return BROWSER_DIALOG_TOOLS

    @staticmethod
    def browser_storage_tools():
        """Get storage state tool functions."""
        return BROWSER_STORAGE_TOOLS

    @staticmethod
    def browser_verify_tools():
        """Get verification/assertion tool functions."""
        return BROWSER_VERIFY_TOOLS

    @staticmethod
    def browser_devtools_tools():
        """Get DevTools (tracing/video) tool functions."""
        return BROWSER_DEVTOOLS_TOOLS

    @staticmethod
    def browser_control_tools():
        """Get browser control tool functions."""
        return BROWSER_CONTROL_TOOLS

    @staticmethod
    def browser_basic_tools():
        """Get basic browser tool functions suitable as default tool set."""
        return BROWSER_BASIC_TOOLS

    @staticmethod
    def browser_all_tools():
        """Get all browser tool functions."""
        return BROWSER_ALL_TOOLS


__all__ = [
    # Core classes
    "BrowserToolSpec",
    "BrowserToolSetBuilder",
    "ToolPreset",
    "ToolSet",
    # Tool function lists
    "BROWSER_NAVIGATION_TOOLS",
    "BROWSER_PAGE_TOOLS",
    "BROWSER_ACTION_TOOLS",
    "BROWSER_MOUSE_TOOLS",
    "BROWSER_KEYBOARD_TOOLS",
    "BROWSER_SCREENSHOT_TOOLS",
    "BROWSER_NETWORK_TOOLS",
    "BROWSER_DIALOG_TOOLS",
    "BROWSER_STORAGE_TOOLS",
    "BROWSER_VERIFY_TOOLS",
    "BROWSER_DEVTOOLS_TOOLS",
    "BROWSER_CONTROL_TOOLS",
    "BROWSER_BASIC_TOOLS",
    "BROWSER_ALL_TOOLS",
    # Navigation tools
    "search",
    "navigate_to_url",
    "go_back",
    "go_forward",
    # Page tools
    "get_current_page_info",
    "reload_page",
    "scroll_to_text",
    "press_key",
    "evaluate_javascript",
    "new_tab",
    "get_tabs",
    "switch_tab",
    "close_tab",
    # Browser control tools
    "browser_close",
    "browser_resize",
    "wait_for",
    # "run_playwright_code",
    "get_llm_repr",
    # Action tools (by ref)
    "input_text_by_ref",
    "click_element_by_ref",
    "get_dropdown_options_by_ref",
    "select_dropdown_option_by_ref",
    "hover_element_by_ref",
    "focus_element_by_ref",
    "evaluate_javascript_on_ref",
    "upload_file_by_ref",
    "drag_element_by_ref",
    "check_element_by_ref",
    "uncheck_element_by_ref",
    "double_click_element_by_ref",
    "scroll_element_into_view_by_ref",
    # Mouse tools
    "mouse_move",
    "mouse_click",
    "mouse_drag",
    "mouse_down",
    "mouse_up",
    "mouse_wheel",
    # Keyboard tools
    "press_sequentially",
    "key_down",
    "key_up",
    "fill_form",
    "insert_text",
    # Screenshot tools
    "take_screenshot",
    "save_pdf",
    # Network tools
    "start_console_capture",
    "get_console_messages",
    "start_network_capture",
    "get_network_requests",
    "wait_for_network_idle",
    # Dialog tools
    "setup_dialog_handler",
    "handle_dialog",
    "remove_dialog_handler",
    # Storage tools
    "save_storage_state",
    "restore_storage_state",
    "clear_cookies",
    "get_cookies",
    "set_cookie",
    # Verification tools
    "verify_element_visible",
    "verify_text_visible",
    "verify_value",
    "verify_element_state",
    "verify_url",
    "verify_title",
    # DevTools
    "start_tracing",
    "stop_tracing",
    "start_video",
    "stop_video",
    "add_trace_chunk",
]
