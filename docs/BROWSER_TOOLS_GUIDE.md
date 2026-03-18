# Browser Tools Selection Guide

This guide helps you choose the right tools for different browser automation scenarios.

## Tool Categories Overview

| Category | Count | Primary Use Case |
|----------|-------|------------------|
| Navigation | 6 | URL/search/navigation info and history |
| Snapshot | 1 | LLM page state with refs |
| Element Interaction | 13 | Ref-based click/input/form/file interactions |
| Tabs | 4 | Tab lifecycle and switching |
| Evaluate | 2 | Execute JavaScript in page or on element |
| Keyboard | 4 | Keyboard typing and key state |
| Mouse | 6 | Coordinate-based pointer control |
| Wait | 1 | Time/text/selector waits |
| Capture | 2 | Screenshot and PDF |
| Network | 4 | Request capture and network idle waits |
| Dialog | 3 | Alert/confirm/prompt handling |
| Storage | 5 | Cookies and storage state |
| Verify | 6 | Assertions for text/value/state/url/title |
| Developer | 8 | Console, tracing, and video |
| Lifecycle | 2 | Browser close/resize |

## Page state and get_snapshot_text

**Call `browser.get_snapshot_text()` first** to get element refs (e.g. `e1`, `e2`) before using ref-based action tools. It returns a string representation of the accessibility tree that you can pass to your LLM; refs in that string are stable for the current page and can be used with `click_element_by_ref`, `input_text_by_ref`, etc.

### Parameters

- **start_from_char** (int, default 0): Pagination offset, must be `>= 0`. When the page state is long, the returned text may be truncated at ~30,000 characters. A `[notice]` at the end of the string tells you the **next_start_char** value to use for the next call to get the rest of the content.
- **interactive** (bool, default False): If True, only clickable/editable elements are included (buttons, links, inputs, checkboxes, elements with `cursor:pointer`, etc.), with flattened output. Use for action-focused tasks.
- **full_page** (bool, default True): If True (default), include all elements regardless of viewport position; if False, only viewport content.

### Truncation and pagination

When the full tree exceeds the character limit, the tool returns a segment and appends a notice like:

```
[notice] Current page text is too long, returned portion starting from character 0 (this segment length 30000 / total length 45000 characters). To continue getting subsequent content: call get_snapshot_text(start_from_char=30000, interactive=False, full_page=True)
```

Use the given `start_from_char` in the next call to continue reading.

### Examples

```python
# First call – get initial page state
state = await browser.get_snapshot_text()
# If state ends with [notice] and next_start_char=30000:
# state_more = await browser.get_snapshot_text(start_from_char=30000)

# Only interactive elements (good for "what can I click?")
state = await browser.get_snapshot_text(interactive=True)

# Viewport-only (override default full_page=True)
state = await browser.get_snapshot_text(full_page=False)
```

## Ref-based vs Coordinate-based Tools

### When to Use Ref-based Tools

**Ref-based tools** use element references (e.g., "e1", "e2") from the page state:

```python
# Get page state with element refs
state = await browser.get_snapshot_text()
# Tree lines look like: "- button 'Submit' [ref=e5]"

# Use ref to interact
await browser.click_element_by_ref("e5")
```

**Advantages**:
- Stable across page changes (as long as element exists)
- Works with accessibility tree
- Handles element visibility and scrolling automatically
- More reliable for standard web elements

**Best for**:
- Buttons, links, inputs
- Forms and dropdowns
- Checkboxes and radio buttons
- Any element visible in snapshot

### When to Use Coordinate-based Tools

**Coordinate-based tools** use pixel positions:

```python
# Click at specific coordinates
await browser.mouse_click(x=500, y=300)

# Drag from one point to another
await browser.mouse_drag(start_x=100, start_y=100, end_x=300, end_y=200)
```

**Advantages**:
- Works with any visual element
- Required for canvas and SVG
- Precise positioning control
- Can interact with elements not in accessibility tree

**Best for**:
- Canvas-based applications
- SVG graphics
- Custom UI components
- Drag-and-drop operations
- Game-like interfaces

## Text Input Methods Comparison

### 1. `browser.input_text_by_ref(ref, text, ...)`

The **default choice** for most text input scenarios.

```python
await browser.input_text_by_ref("e3", "hello@example.com")
```

Full signature: `input_text_by_ref(ref, text, clear=True, is_secret=False, slowly=False, submit=False)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | str | — | Element ref from snapshot (e.g. `"e3"`) |
| `text` | str | — | Text to input |
| `clear` | bool | `True` | Clear the field before typing |
| `is_secret` | bool | `False` | Hide text value in the result message (e.g. passwords) |
| `slowly` | bool | `False` | Type character-by-character with ~100ms delays (triggers all keyboard events) |
| `submit` | bool | `False` | Press Enter after typing |

- Uses Playwright's `.fill()` method by default (fast, triggers `input`/`change` events)
- With `slowly=True`: types each character with 100ms delay, triggering `keydown`/`keypress`/`keyup`

### 2. `browser.input_text_by_ref(ref, text, slowly=True)`

For inputs that need character-by-character typing:

```python
await browser.input_text_by_ref("e3", "search query", slowly=True)
```

- Types each character with 100ms delay
- Triggers `keydown`, `keypress`, `keyup` for each character
- Use when autocomplete or real-time validation is needed

### 3. `browser.type_text(text)`

For typing at the current focus position:

```python
await browser.type_text("hello world")
```

- Types at cursor position (no ref needed)
- Triggers all keyboard events
- Good for search boxes with autocomplete
- Can add `submit=True` to press Enter after

### Comparison Table

| Method | Speed | Events | Use Case |
|--------|-------|--------|----------|
| `input_text_by_ref` | Fast | input, change | Standard forms |
| `input_text_by_ref(slowly=True)` | Slow | All keyboard | Autocomplete |
| `type_text` | Medium | All keyboard | At cursor (no ref) |

## Click Operations Comparison

### `click_element_by_ref` vs `mouse_click`

```python
# Ref-based - preferred for standard elements
await browser.click_element_by_ref("e5")

# Coordinate-based - for special cases
await browser.mouse_click(x=500, y=300)
```

| Feature | click_element_by_ref | mouse_click |
|---------|---------------------|-------------|
| Element scroll | Automatic | Manual |
| Wait for visible | Yes | No |
| Canvas support | No | Yes |
| SVG support | Limited | Yes |
| Reliability | Higher | Depends on layout |

### Double-click

```python
# Ref-based
await browser.double_click_element_by_ref("e5")

# Coordinate-based
await browser.mouse_click(x=500, y=300, click_count=2)
```

### Right-click

```python
# Coordinate-based (supported)
await browser.mouse_click(x=500, y=300, button="right")
```

## Scrolling Methods

### `scroll_element_into_view_by_ref`

Scroll to bring element with ref into view:

```python
await browser.scroll_element_into_view_by_ref("e15")
```

### `mouse_wheel`

Scroll by pixel amount:

```python
# Scroll down 500 pixels
await browser.mouse_wheel(delta_y=500)

# Scroll up 300 pixels
await browser.mouse_wheel(delta_y=-300)

# Scroll right 200 pixels
await browser.mouse_wheel(delta_x=200)
```

## Drag Operations

### `drag_element_by_ref`

Drag one element to another:

```python
await browser.drag_element_by_ref(start_ref="e3", end_ref="e7")
```

### `mouse_drag`

Drag from coordinates to coordinates:

```python
await browser.mouse_drag(
    start_x=100, start_y=100,
    end_x=300, end_y=200
)
```

## Waiting Strategies

### `wait_for`

Flexible waiting with multiple conditions. Only one condition is used; priority is: **time_seconds** > **text** > **text_gone** > **selector**.

```python
# Wait for time (seconds, max 60)
await browser.wait_for(time_seconds=2.0)

# Wait for text to appear (timeout in seconds)
await browser.wait_for(text="Loading complete", timeout=10.0)

# Wait for text to disappear
await browser.wait_for(text_gone="Please wait...", timeout=10.0)

# Wait for element state
await browser.wait_for(selector=".modal", state="visible", timeout=5.0)
```

### `wait_for_network_idle`

Wait for network activity to settle. **timeout** is in seconds.

```python
await browser.wait_for_network_idle(timeout=30.0)
```

## Verification Tools

Verification tools return `PASS: ...` on success. On mismatch, SDK raises
`VerificationError` (structured error with `code/message/details/retryable`).

```python
from bridgic.browser.errors import VerificationError

result = await browser.verify_text_visible(text="Welcome")
# Success: "PASS: Text 'Welcome' is visible on the page"

try:
    await browser.verify_url(expected_url="dashboard")
except VerificationError as exc:
    # exc.code == "VERIFICATION_FAILED"
    # exc.details includes expected/actual values when available
    print(exc.code, exc.message, exc.details)
```

### Available Verifications

| Tool | Checks |
|------|--------|
| `verify_element_visible` | Element is visible by role/name |
| `verify_text_visible` | Text is visible on page |
| `verify_value` | Input has expected value |
| `verify_element_state` | Element state (visible/hidden/enabled/disabled) |
| `verify_url` | Current URL contains string |
| `verify_title` | Page title contains string |

## Category-based Tool Selection

Use `BrowserToolSetBuilder.for_categories()` with one or more `ToolCategory` values to pick the right tool set for your scenario:

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

# Simple navigation
builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.NAVIGATION)
tools = builder.build()["tool_specs"]

# Data scraping
builder = BrowserToolSetBuilder.for_categories(
    browser, ToolCategory.NAVIGATION, ToolCategory.SNAPSHOT, ToolCategory.EVALUATE
)
tools = builder.build()["tool_specs"]

# Form automation
builder = BrowserToolSetBuilder.for_categories(
    browser,
    ToolCategory.NAVIGATION,
    ToolCategory.SNAPSHOT,
    ToolCategory.ELEMENT_INTERACTION,
    ToolCategory.WAIT,
)
tools = builder.build()["tool_specs"]

# E2E testing
builder = BrowserToolSetBuilder.for_categories(
    browser,
    ToolCategory.NAVIGATION,
    ToolCategory.SNAPSHOT,
    ToolCategory.ELEMENT_INTERACTION,
    ToolCategory.WAIT,
    ToolCategory.VERIFY,
    ToolCategory.CAPTURE,
)
tools = builder.build()["tool_specs"]

# Full access (all 67 tools)
builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.ALL)
tools = builder.build()["tool_specs"]
```

Available categories: `NAVIGATION`, `SNAPSHOT`, `ELEMENT_INTERACTION`, `TABS`, `EVALUATE`, `KEYBOARD`, `MOUSE`, `WAIT`, `CAPTURE`, `NETWORK`, `DIALOG`, `STORAGE`, `VERIFY`, `DEVELOPER`, `LIFECYCLE`. Pass `ToolCategory.ALL` to include every tool.

## Picking by function names

Use name-based APIs when your tool list comes from config files, prompts, or other runtime inputs:

```python
from bridgic.browser.tools import BrowserToolSetBuilder

builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to",
    "click_element_by_ref",
)
tools = builder.build()["tool_specs"]
```

For custom composition:

```python
builder1 = BrowserToolSetBuilder.for_categories(browser, "navigation")
builder2 = BrowserToolSetBuilder.for_tool_names(
    browser, "click_element_by_ref", "verify_url"
)
tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]
```

`for_tool_names` validates names against the CLI-mapped tool inventory and fails fast on unknown names or methods missing on the provided browser:

```python
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to",
)
tools = builder.build()["tool_specs"]
```

## Common Patterns

### Form Filling

```python
# Using fill_form for multiple fields
await browser.fill_form([
    {"ref": "e1", "value": "John Doe"},
    {"ref": "e2", "value": "john@example.com"},
    {"ref": "e3", "value": "secret123"},
], submit=True)
```

### Dropdown Selection

```python
# First, get available options
options = await browser.get_dropdown_options_by_ref("e5")
# Returns: "1. Option A (value: a)\n2. Option B (value: b)"

# Then select by text or value
await browser.select_dropdown_option_by_ref("e5", "Option A")
# or
await browser.select_dropdown_option_by_ref("e5", "a")
```

### File Upload

```python
await browser.upload_file_by_ref("e10", "/path/to/file.pdf")
```

### Handling Dialogs

```python
# Set up auto-handling
await browser.setup_dialog_handler(default_action="accept")

# Or handle next dialog manually
await browser.handle_dialog(accept=True, prompt_text="My input")
```

## Error Handling

Use structured SDK exceptions instead of string matching:

```python
from bridgic.browser.errors import (
    InvalidInputError,
    StateError,
    OperationError,
    VerificationError,
)

try:
    result = await browser.click_element_by_ref("e999")
    print(f"Success: {result}")
except StateError as exc:
    # predictable runtime-state issue (e.g. REF_NOT_AVAILABLE)
    print(exc.code, exc.message, exc.details)
except InvalidInputError as exc:
    print(exc.code, exc.message)
except VerificationError as exc:
    print(exc.code, exc.message, exc.details)
except OperationError as exc:
    print(exc.code, exc.message)
```
