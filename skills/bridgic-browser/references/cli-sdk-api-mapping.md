# CLI and SDK API Mapping Guide

Use this guide when the task needs CLI/SDK relationship reasoning:
- migrate code from CLI to Python SDK;
- explain command-to-method correspondence;
- generate Python code from CLI action steps;
- compare parity and behavior differences.

Canonical source in this repo: `bridgic/browser/_cli_catalog.py` (`CLI_COMMAND_TO_TOOL_METHOD`).

## Table of Contents

1. [Relationship Model](#relationship-model)
2. [Canonical Command -> Method Mapping](#canonical-command---method-mapping)
3. [Parameter Translation Rules (Important for Code Generation)](#parameter-translation-rules-important-for-code-generation)
4. [CLI-First -> SDK Code Generation Workflow](#cli-first---sdk-code-generation-workflow)
5. [Example: Convert CLI Flow to SDK Code](#example-convert-cli-flow-to-sdk-code)
6. [Behavior Differences (Non-1:1 Cases)](#behavior-differences-non-11-cases)
7. [Practical Rule for Mixed Tasks](#practical-rule-for-mixed-tasks)

## Relationship Model

CLI commands and SDK tool methods are **intentionally aligned**:
- Most CLI commands are thin wrappers over exactly one SDK tool method with parameter adaptation.
- Both surfaces share the same underlying `Browser` instance — CLI accesses it through the daemon process; SDK code holds a direct Python reference.
- This means CLI behavior and SDK tool-method behavior are equivalent by design. Understanding one side gives you the other.

State model difference:
- **CLI**: browser state lives in the daemon process, persists across multiple short-lived CLI invocations, resets on `close`.
- **SDK**: browser state lives in the Python process, scoped to the `Browser` object lifetime (`async with Browser(...) as browser:`).

This model is the foundation of all correspondence in this guide.

## Canonical Command -> Method Mapping

| CLI command | SDK tool method |
|---|---|
| `open` | `navigate_to` |
| `search` | `search` |
| `info` | `get_current_page_info` |
| `reload` | `reload_page` |
| `back` | `go_back` |
| `forward` | `go_forward` |
| `snapshot` | `get_snapshot_text` |
| `click` | `click_element_by_ref` |
| `fill` | `input_text_by_ref` |
| `fill-form` | `fill_form` |
| `scroll-to` | `scroll_element_into_view_by_ref` |
| `select` | `select_dropdown_option_by_ref` |
| `options` | `get_dropdown_options_by_ref` |
| `check` | `check_checkbox_or_radio_by_ref` |
| `uncheck` | `uncheck_checkbox_by_ref` |
| `focus` | `focus_element_by_ref` |
| `hover` | `hover_element_by_ref` |
| `double-click` | `double_click_element_by_ref` |
| `upload` | `upload_file_by_ref` |
| `drag` | `drag_element_by_ref` |
| `tabs` | `get_tabs` |
| `new-tab` | `new_tab` |
| `switch-tab` | `switch_tab` |
| `close-tab` | `close_tab` |
| `eval` | `evaluate_javascript` |
| `eval-on` | `evaluate_javascript_on_ref` |
| `press` | `press_key` |
| `type` | `type_text` |
| `key-down` | `key_down` |
| `key-up` | `key_up` |
| `scroll` | `mouse_wheel` |
| `mouse-click` | `mouse_click` |
| `mouse-move` | `mouse_move` |
| `mouse-drag` | `mouse_drag` |
| `mouse-down` | `mouse_down` |
| `mouse-up` | `mouse_up` |
| `wait` | `wait_for` |
| `screenshot` | `take_screenshot` |
| `pdf` | `save_pdf` |
| `network-start` | `start_network_capture` |
| `network` | `get_network_requests` |
| `network-stop` | `stop_network_capture` |
| `wait-network` | `wait_for_network_idle` |
| `dialog-setup` | `setup_dialog_handler` |
| `dialog` | `handle_dialog` |
| `dialog-remove` | `remove_dialog_handler` |
| `cookies` | `get_cookies` |
| `cookie-set` | `set_cookie` |
| `cookies-clear` | `clear_cookies` |
| `storage-save` | `save_storage_state` |
| `storage-load` | `restore_storage_state` |
| `verify-text` | `verify_text_visible` |
| `verify-visible` | `verify_element_visible` |
| `verify-url` | `verify_url` |
| `verify-title` | `verify_title` |
| `verify-state` | `verify_element_state` |
| `verify-value` | `verify_value` |
| `console-start` | `start_console_capture` |
| `console` | `get_console_messages` |
| `console-stop` | `stop_console_capture` |
| `trace-start` | `start_tracing` |
| `trace-chunk` | `add_trace_chunk` |
| `trace-stop` | `stop_tracing` |
| `video-start` | `start_video` |
| `video-stop` | `stop_video` |
| `close` | `close` |
| `resize` | `browser_resize` |

## Parameter Translation Rules (Important for Code Generation)

- Ref normalization:
  - CLI accepts `@8d4b03a9` and `8d4b03a9`.
  - SDK ref methods use plain `"8d4b03a9"`.
- `snapshot`:
  - `-i` -> `interactive=True`
  - `-F` -> `full_page=False`
  - `-l N` -> `limit=N`
  - `-s PATH` -> `file="PATH"` (always saves to file; omit for auto-generated only when over limit)
- `wait` (**unit is SECONDS, not milliseconds**):
  - `wait 2.5` -> `wait_for(time_seconds=2.5)` — numeric argument always takes the time path; `--gone` is ignored
  - `wait "Done"` -> `wait_for(text="Done")`
  - `wait --gone "Loading"` -> `wait_for(text_gone="Loading")` — `--gone` only works with a text argument
  - SDK-only (no CLI equivalent): `wait_for(selector=".spinner", state="hidden", timeout=10.0)`
- `fill REF TEXT [--submit]` -> `input_text_by_ref(ref, text, submit=False)`
  - SDK-only params: `clear=True` (clear field before typing), `is_secret=False` (mask value in logs), `slowly=False` (type char-by-char with key events)
- `scroll --dy Y --dx X` -> `mouse_wheel(delta_x=X, delta_y=Y)`
- `mouse-click X Y` / `mouse-move X Y` / `mouse-drag X1 Y1 X2 Y2` — **coordinates are viewport pixels from the top-left corner**
  - `mouse-click X Y --button right --count 2` -> `mouse_click(X, Y, button="right", click_count=2)`
  - `mouse-drag X1 Y1 X2 Y2` -> `mouse_drag(X1, Y1, X2, Y2)` (positional only; params named `start_x, start_y, end_x, end_y`)
- `fill-form '<json>'`:
  - CLI passes JSON string.
  - SDK uses parsed list: `fill_form(fields=[{"ref":"1f79fe5e","value":"..."}], submit=False)`
- `dialog --dismiss --text T` -> `handle_dialog(accept=False, prompt_text=T)`
- `dialog-setup --action dismiss --text T` -> `setup_dialog_handler(default_action="dismiss", default_prompt_text=T)`
- `verify-visible ROLE NAME --timeout 5.0` -> `verify_element_visible(role=ROLE, accessible_name=NAME, timeout=5.0)`
- `network --no-clear` -> `get_network_requests(clear=False)`
- `console --no-clear` -> `get_console_messages(clear=False)`
- `screenshot path.png --full-page` -> `take_screenshot(filename="path.png", full_page=True)`
  - SDK-only params: `ref` (screenshot a specific element by ref), `type="png"|"jpeg"`, `quality` (0-100, JPEG only)
- `pdf path.pdf` -> `save_pdf(filename="path.pdf")`
  - SDK-only params: `display_header_footer`, `print_background`, `scale`, `paper_width`, `paper_height`, `margin_top`, `margin_bottom`, `margin_left`, `margin_right`, `landscape`
- `video-stop path.webm` -> `stop_video(filename="path.webm")`
- `type TEXT [--submit]` -> `type_text(text, submit=False)` — **requires a focused element**; call `focus_element_by_ref` or `click_element_by_ref` on the target before `type`
- `eval-on REF CODE` -> `evaluate_javascript_on_ref(ref, code)` — **CODE must be an arrow or named function** that accepts the element:
  - `"(el) => el.textContent"` ✓
  - `"el.textContent"` ✗ (not a function, will throw)

## CLI-First -> SDK Code Generation Workflow

1. Parse CLI steps in exact order.
2. Map each command to SDK method using the table above.
3. Translate options using the parameter rules.
4. Produce runnable async Python with explicit lifecycle.
5. Add snapshot refresh points whenever steps imply page changes.

## Example: Convert CLI Flow to SDK Code

CLI flow:

```bash
bridgic-browser open https://example.com/login
bridgic-browser snapshot -i
bridgic-browser fill @d6a530b4 "alice@example.com"
bridgic-browser fill @1f79fe5e "secret"
bridgic-browser click @8d4b03a9
bridgic-browser wait "Dashboard"
bridgic-browser screenshot logged-in.png
```

SDK alternative:

```python
import asyncio
from bridgic.browser.session import Browser

async def run() -> None:
    async with Browser(headless=False) as browser:
        await browser.navigate_to("https://example.com/login")
        await browser.get_snapshot_text(interactive=True)
        await browser.input_text_by_ref("d6a530b4", "alice@example.com")
        await browser.input_text_by_ref("1f79fe5e", "secret")
        await browser.click_element_by_ref("8d4b03a9")
        await browser.wait_for(text="Dashboard")
        await browser.take_screenshot(filename="logged-in.png")

if __name__ == "__main__":
    asyncio.run(run())
```

## Behavior Differences (Non-1:1 Cases)

These CLI behaviors have no direct SDK equivalent or work differently:

| Behavior | CLI | SDK |
|---|---|---|
| File path resolution | `screenshot`, `pdf`, `upload`, `storage-save`, `storage-load`, `trace-stop` convert path to absolute on **client side** before sending to daemon | SDK caller is responsible for path; no automatic absolutization |
| State persistence model | Daemon keeps browser alive across many short commands; restart daemon with `close` to reset | `Browser` instance lifetime; `async with Browser(...)` handles start/stop |
| `scroll` argument style | `--dy`/`--dx` flag options (not positional) to allow negative values | `mouse_wheel(delta_x=X, delta_y=Y)` keyword args |
| `fill-form` input format | JSON string on command line | Python list of dicts |
| `take_screenshot` return value | CLI always writes to a file path | SDK: `filename=None` returns base64 data URL; `filename="path.png"` writes file |
| Video file write timing | `video-stop` registers path; file is written when daemon/browser closes | Same for SDK: `.webm` is written when page closes via `close()` or `close_tab()` |

## Practical Rule for Mixed Tasks

If execution is done via CLI but final deliverable must be Python code, use this guide first, then verify final code shape with `sdk-guide.md`.
