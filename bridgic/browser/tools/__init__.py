"""
Browser automation tools module.

This module provides browser automation tools that can be used with Bridgic agents.
Use BrowserToolSetBuilder with ToolPreset for scenario-based tool selection.

Quick Start
-----------
>>> from bridgic.browser.session import Browser
>>> from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset
>>>
>>> browser = Browser(headless=False)
>>> await browser.start()
>>>
>>> # Choose a preset for your use case
>>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)
>>> tools = builder.build()["tool_specs"]
>>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)
>>> tools = builder.build()["tool_specs"]
>>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)
>>> tools = builder.build()["tool_specs"]
>>>
>>> # Or select by category
>>> builder = BrowserToolSetBuilder.for_categories(browser, "navigation", "element_interaction")
>>> tools = builder.build()["tool_specs"]
>>>
>>> # Or select by tool method names
>>> builder = BrowserToolSetBuilder.for_tool_names(
...     browser,
...     "search",
...     "navigate_to_url",
...     "click_element_by_ref",
... )
>>> tools = builder.build()["tool_specs"]
>>>
>>> # Combine builders from different selection strategies
>>> builder1 = BrowserToolSetBuilder.for_categories(browser, "navigation", "element_interaction")
>>> builder2 = BrowserToolSetBuilder.for_tool_names(browser, "verify_url")
>>> tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]

Available Presets
-----------------
- MINIMAL: Minimal browser control mapped from CLI preset
- NAVIGATION: Navigation-only mapped from CLI preset
- SCRAPING: Scraping-focused mapped from CLI preset
- FORM_FILLING: Form filling mapped from CLI preset
- TESTING: Testing mapped from CLI preset
- INTERACTIVE: Interactive mapped from CLI preset
- DEVELOPER: Developer mapped from CLI preset
- COMPLETE: All CLI-mapped tools

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

**get_snapshot_text** returns the page state string (accessibility tree with refs). It may
include a pagination notice when truncated; use start_from_char, interactive, and
full_page to control pagination and scope (interactive-only or full-page by default).

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
from ._browser_tool_spec import BrowserToolSpec
from .._constants import ToolPreset
from ._browser_tool_set_builder import BrowserToolSetBuilder

__all__ = [
    # Core classes
    "BrowserToolSpec",
    "BrowserToolSetBuilder",
    "ToolPreset",
]
