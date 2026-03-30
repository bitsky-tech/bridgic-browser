"""
Browser automation tools module.

This module provides browser automation tools that can be used with Bridgic agents.
Use BrowserToolSetBuilder with category-based tool selection.

Quick Start
-----------
>>> from bridgic.browser.session import Browser
>>> from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory
>>>
>>> async with Browser(headless=False) as browser:
>>>     pass  # browser auto-started via context manager
>>>
>>> # Select by category
>>> builder = BrowserToolSetBuilder.for_categories(
...     browser, ToolCategory.NAVIGATION, ToolCategory.ELEMENT_INTERACTION,
... )
>>> tools = builder.build()["tool_specs"]
>>>
>>> # Select all tools
>>> builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.ALL)
>>> tools = builder.build()["tool_specs"]
>>>
>>> # Or select by tool method names
>>> builder = BrowserToolSetBuilder.for_tool_names(
...     browser,
...     "search",
...     "navigate_to",
...     "click_element_by_ref",
... )
>>> tools = builder.build()["tool_specs"]
>>>
>>> # Combine builders from different selection strategies
>>> builder1 = BrowserToolSetBuilder.for_categories(
...     browser, ToolCategory.NAVIGATION, ToolCategory.ELEMENT_INTERACTION,
... )
>>> builder2 = BrowserToolSetBuilder.for_tool_names(browser, "verify_url")
>>> tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]

Available Categories (ToolCategory enum)
----------------------------------------
NAVIGATION, SNAPSHOT, ELEMENT_INTERACTION, TABS, EVALUATE, KEYBOARD,
MOUSE, WAIT, CAPTURE, NETWORK, DIALOG, STORAGE, VERIFY, DEVELOPER, LIFECYCLE

Pass ``ToolCategory.ALL`` to ``for_categories()`` to include every tool.

Return Value Format
-------------------
All tools return a string message following a consistent format:

**Success messages**:
- Action confirmation: "Clicked element 8d4b03a9", "Navigated to https://..."
- Data results: JSON string or formatted text

**Exceptions raised**:
- Element not found: raises `StateError` with message "Element ref {ref} is not available - page may have changed."
- Operation failed: raises `OperationError` with message "Failed to {action}: {error details}"
- Invalid input: raises `InvalidInputError` with message "{parameter} is empty/invalid"

**Verification tools** use special prefixes:
- Success: "PASS: {description}"
- Failure: "FAIL: {description} - {reason}"

**get_snapshot_text** returns the page state string (accessibility tree with refs). When
content exceeds limit or file is explicitly provided, full snapshot is saved to a file
and only a notice with the file path is returned. Use limit, interactive, full_page,
and file to control scope.

Tool Selection Guide
--------------------
**Ref-based tools vs Coordinate-based tools**:

Use **ref-based tools** (e.g., `click_element_by_ref`) when:
- Element has a ref from `get_snapshot_text()` snapshot
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
| Type text | input_text_by_ref | type_text | Trigger key events |
| Drag element | drag_element_by_ref | mouse_drag | Custom drag behavior |
| Scroll element into view | scroll_element_into_view_by_ref | mouse_wheel | Scroll by exact pixels |
| Fill multiple fields | fill_form | input_text_by_ref | Structured multi-field input |

**Text input methods**:
- `input_text_by_ref`: Standard input, uses .fill() - fast and reliable
- `input_text_by_ref(slowly=True)`: Character-by-character with delays
- `type_text`: Key events for each character, triggers handlers
"""

# ==================== Tool Spec and Builder ====================
from .._constants import ToolCategory
from ._browser_tool_spec import BrowserToolSpec
from ._browser_tool_set_builder import BrowserToolSetBuilder

__all__ = [
    "ToolCategory",
    "BrowserToolSpec",
    "BrowserToolSetBuilder",
]
