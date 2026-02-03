# Browser Tools Selection Guide

This guide helps you choose the right tools for different browser automation scenarios.

## Tool Categories Overview

| Category | Tools Count | Primary Use Case |
|----------|-------------|------------------|
| Navigation | 4 | Page navigation, search |
| Page | 9 | Page control, tabs |
| Action (ref-based) | 13 | Element interaction |
| Mouse (coordinate) | 6 | Precise mouse control |
| Keyboard | 5 | Text input, shortcuts |
| Screenshot | 2 | Capture visuals |
| Network | 5 | Monitor requests/console |
| Dialog | 3 | Handle popups |
| Storage | 5 | Cookies, state |
| Verify | 6 | Assertions |
| DevTools | 5 | Tracing, video |
| Control | 3 | Browser lifecycle |

## Ref-based vs Coordinate-based Tools

### When to Use Ref-based Tools

**Ref-based tools** use element references (e.g., "e1", "e2") from the page snapshot:

```python
# Get snapshot with element refs
snapshot = await browser.get_snapshot()
# Output: "- button 'Submit' [ref=e5]"

# Use ref to interact
await click_element_by_ref(browser, "e5")
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
await mouse_click(browser, x=500, y=300)

# Drag from one point to another
await mouse_drag(browser, start_x=100, start_y=100, end_x=300, end_y=200)
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

### 1. `input_text_by_ref(browser, ref, text)`

The **default choice** for most text input scenarios.

```python
await input_text_by_ref(browser, "e3", "hello@example.com")
```

- Uses Playwright's `.fill()` method
- Fast and reliable
- Clears existing text by default
- Triggers `input` and `change` events

### 2. `input_text_by_ref(browser, ref, text, slowly=True)`

For inputs that need character-by-character typing:

```python
await input_text_by_ref(browser, "e3", "search query", slowly=True)
```

- Types each character with 100ms delay
- Triggers `keydown`, `keypress`, `keyup` for each character
- Use when autocomplete or real-time validation is needed

### 3. `press_sequentially(browser, text)`

For typing at the current focus position:

```python
await press_sequentially(browser, "hello world")
```

- Types at cursor position (no ref needed)
- Triggers all keyboard events
- Good for search boxes with autocomplete
- Can add `submit=True` to press Enter after

### 4. `insert_text(browser, text)`

Fastest method for bulk text:

```python
await insert_text(browser, "Large amount of text...")
```

- Direct text insertion
- May not trigger all events
- Best for performance-critical scenarios

### Comparison Table

| Method | Speed | Events | Use Case |
|--------|-------|--------|----------|
| `input_text_by_ref` | Fast | input, change | Standard forms |
| `input_text_by_ref(slowly=True)` | Slow | All keyboard | Autocomplete |
| `press_sequentially` | Medium | All keyboard | At cursor |
| `insert_text` | Fastest | Minimal | Bulk text |

## Click Operations Comparison

### `click_element_by_ref` vs `mouse_click`

```python
# Ref-based - preferred for standard elements
await click_element_by_ref(browser, "e5")

# Coordinate-based - for special cases
await mouse_click(browser, x=500, y=300)
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
await double_click_element_by_ref(browser, "e5")

# Coordinate-based
await mouse_click(browser, x=500, y=300, click_count=2)
```

### Right-click

```python
# Ref-based
await click_element_by_ref(browser, "e5", button="right")

# Coordinate-based
await mouse_click(browser, x=500, y=300, button="right")
```

## Scrolling Methods

### `scroll_to_text`

Scroll to bring specific text into view:

```python
await scroll_to_text(browser, "Contact Us")
```

### `scroll_element_into_view_by_ref`

Scroll to bring element with ref into view:

```python
await scroll_element_into_view_by_ref(browser, "e15")
```

### `mouse_wheel`

Scroll by pixel amount:

```python
# Scroll down 500 pixels
await mouse_wheel(browser, delta_y=500)

# Scroll up 300 pixels
await mouse_wheel(browser, delta_y=-300)

# Scroll right 200 pixels
await mouse_wheel(browser, delta_x=200)
```

## Drag Operations

### `drag_element_by_ref`

Drag one element to another:

```python
await drag_element_by_ref(browser, start_ref="e3", end_ref="e7")
```

### `mouse_drag`

Drag from coordinates to coordinates:

```python
await mouse_drag(browser,
    start_x=100, start_y=100,
    end_x=300, end_y=200,
    steps=10  # Smoothness
)
```

## Waiting Strategies

### `wait_for`

Flexible waiting with multiple conditions:

```python
# Wait for time (seconds)
await wait_for(browser, time=2.0)

# Wait for text to appear
await wait_for(browser, text="Loading complete", timeout=10000)

# Wait for text to disappear
await wait_for(browser, text_gone="Please wait...", timeout=10000)

# Wait for element state
await wait_for(browser, selector=".modal", state="visible", timeout=5000)
```

### `wait_for_network_idle`

Wait for network activity to settle:

```python
await wait_for_network_idle(browser, timeout=30000)
```

## Verification Tools

All verification tools return strings with `PASS:` or `FAIL:` prefix:

```python
result = await verify_text_visible(browser, text="Welcome")
# Returns: "PASS: Text 'Welcome' is visible" or "FAIL: Text 'Welcome' not found"

result = await verify_url(browser, expected_url="dashboard")
# Returns: "PASS: URL contains 'dashboard'" or "FAIL: URL mismatch..."
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

## Preset Selection Guide

| Scenario | Recommended Preset | Tools Count |
|----------|-------------------|-------------|
| Simple navigation | MINIMAL | 10 |
| Data scraping | SCRAPING | 13 |
| Form automation | FORM_FILLING | 20 |
| E2E testing | TESTING | 28 |
| Complex interactions | INTERACTIVE | 40 |
| Debugging | DEVELOPER | 18 |
| Full access | COMPLETE | 68 |

## Common Patterns

### Form Filling

```python
# Using fill_form for multiple fields
await fill_form(browser, [
    {"ref": "e1", "value": "John Doe"},
    {"ref": "e2", "value": "john@example.com"},
    {"ref": "e3", "value": "secret123"},
], submit=True)
```

### Dropdown Selection

```python
# First, get available options
options = await get_dropdown_options_by_ref(browser, "e5")
# Returns: "1. Option A (value: a)\n2. Option B (value: b)"

# Then select by text or value
await select_dropdown_option_by_ref(browser, "e5", "Option A")
# or
await select_dropdown_option_by_ref(browser, "e5", "a")
```

### File Upload

```python
await upload_file_by_ref(browser, "e10", "/path/to/file.pdf")
```

### Handling Dialogs

```python
# Set up auto-handling
await setup_dialog_handler(browser, default_action="accept")

# Or handle next dialog manually
await handle_dialog(browser, accept=True, prompt_text="My input")
```

## Error Handling

All tools return string messages. Check for error patterns:

```python
result = await click_element_by_ref(browser, "e999")

if "not available" in result or "Failed" in result:
    # Element not found or operation failed
    print(f"Error: {result}")
else:
    # Success
    print(f"Success: {result}")
```
