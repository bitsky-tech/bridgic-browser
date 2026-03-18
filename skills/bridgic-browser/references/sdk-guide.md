# SDK Guide

Use this guide when the output should be Python automation code (`bridgic.browser.*`) instead of shell commands.

## Table of Contents

1. [Installation and Imports](#installation-and-imports)
2. [Preferred Lifecycle Pattern](#preferred-lifecycle-pattern)
3. [Core SDK Decision: Raw Methods vs Tool Methods](#core-sdk-decision-raw-methods-vs-tool-methods)
4. [Snapshot and Ref Rules](#snapshot-and-ref-rules)
5. [Frequent SDK Methods](#frequent-sdk-methods)
6. [Tool Set Builder (for Agent Integration)](#tool-set-builder-for-agent-integration)
7. [Non-Obvious SDK Behavior](#non-obvious-sdk-behavior)
8. [SDK Error Handling](#sdk-error-handling)
9. [When to Load Other References](#when-to-load-other-references)

## Installation and Imports

```bash
pip install bridgic-browser
playwright install chromium
```

```python
from bridgic.browser.session import Browser, StealthConfig
from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory
```

## Preferred Lifecycle Pattern

```python
import asyncio
from bridgic.browser.session import Browser

async def run() -> None:
    async with Browser(headless=False) as browser:
        await browser.navigate_to("https://example.com")
        snap = await browser.get_snapshot(interactive=True)
        print(snap.tree)

if __name__ == "__main__":
    asyncio.run(run())
```

Notes:
- `navigate_to(...)` requires a started context/page.
- `async with Browser(...)` handles start/stop automatically.
- `get_snapshot(...)` returns `EnhancedSnapshot` (never `None`); raises `StateError` if no active page, `OperationError` if generation fails.

## Core SDK Decision: Raw Methods vs Tool Methods

Two surfaces serve different purposes — pick the right one:

| Surface | When to use | Examples |
|---|---|---|
| **Raw methods** | Direct Playwright-level control in scripts | `get_current_page()`, `take_screenshot(filename=...)`, `get_snapshot()` |
| **Tool methods** | Align with CLI behavior or expose to an LLM agent | `click_element_by_ref()`, `wait_for()`, `verify_*()`, `get_snapshot_text()` |

Rule of thumb: if you're building an agent or want your script to behave like the CLI, prefer tool methods. If you need low-level page/script control, use raw methods.

## Snapshot and Ref Rules

- Refs are emitted in snapshot tree entries like `[ref=e5]`.
- Resolve refs with ref-based methods (for example `click_element_by_ref("e5")`).
- `navigate_to(...)` clears cached snapshot refs. Take a new snapshot after page changes.
- `get_element_by_ref(...)` depends on the last snapshot cache.

## Frequent SDK Methods

| Objective | Preferred SDK method(s) |
|---|---|
| Navigate URL with URL normalization/safety checks | `navigate_to(url)` |
| Navigate with explicit Playwright wait strategy | `navigate_to(url, wait_until=..., timeout=...)` |
| Capture ref snapshot as string for LLM/tooling | `get_snapshot_text(interactive=..., full_page=..., start_from_char=...)` |
| Capture structured snapshot object | `get_snapshot(interactive=..., full_page=...)` |
| Interact with element by ref | `click_element_by_ref`, `input_text_by_ref(ref, text, clear=True, is_secret=False, slowly=False, submit=False)`, `select_dropdown_option_by_ref`, `check_checkbox_by_ref`, ... |
| Coordinate mouse operations | `mouse_click`, `mouse_move`, `mouse_drag`, `mouse_wheel` |
| Waits | `wait_for(time_seconds=... | text=... | text_gone=... | selector=..., timeout=30.0)` |
| Verification | `verify_text_visible`, `verify_element_visible`, `verify_url`, `verify_title`, `verify_element_state`, `verify_value` |
| Capture artifacts | `take_screenshot`, `save_pdf`, `start_tracing`, `stop_tracing` |

## Tool Set Builder (for Agent Integration)

```python
builder = BrowserToolSetBuilder.for_categories(
    browser, ToolCategory.NAVIGATION, ToolCategory.ELEMENT_INTERACTION, ToolCategory.CAPTURE
)
tools = builder.build()["tool_specs"]
```

```python
builder = BrowserToolSetBuilder.for_categories(
    browser, "navigation", "element_interaction", "capture"
)
tools = builder.build()["tool_specs"]
```

```python
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "navigate_to",
    "get_snapshot_text",
    "click_element_by_ref",
    strict=True,
)
tools = builder.build()["tool_specs"]
```

```python
# Combine multiple selections (categories + specific tool names)
builder1 = BrowserToolSetBuilder.for_categories(
    browser, ToolCategory.NAVIGATION, ToolCategory.ELEMENT_INTERACTION, ToolCategory.CAPTURE
)
builder2 = BrowserToolSetBuilder.for_tool_names(browser, "verify_url")
tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]
```

## Non-Obvious SDK Behavior

- `wait_for` uses seconds for all time parameters:
  - `time_seconds` — fixed delay in seconds
  - `timeout` — max wait for text/selector conditions, in seconds (default `30.0`)
- `wait_for` condition priority: `time_seconds` > `text` > `text_gone` > `selector`.
- `take_screenshot(filename=None)` returns base64 data URL string.
- `take_screenshot(filename="path.png")` writes file and returns a status string.
- `verify_element_visible` uses `(role, accessible_name)` rather than ref.
- `start_video` must run before `stop_video`; `stop_video` registers the destination path but does **not** close any pages. The actual `.webm` file is written by Playwright when pages close (via `stop()` or `close_tab()`).

## SDK Error Handling

Use structured exceptions from `bridgic.browser.errors` (for example `StateError`, `InvalidInputError`, `VerificationError`) instead of string matching on messages.

## When to Load Other References

- Need shell commands: read `cli-guide.md`.
- Need CLI <-> SDK conversion or mapping: read `cli-sdk-api-mapping.md`.
- Need environment variables or login state persistence details: read `env-vars.md`.
