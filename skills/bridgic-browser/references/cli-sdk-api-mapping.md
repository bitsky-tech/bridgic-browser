# CLI and SDK API Mapping Guide

Use this guide when the task needs CLI/SDK relationship reasoning:
- migrate from CLI to Python SDK;
- explain command-to-method correspondence;
- generate SDK code from CLI action steps;
- compare parity and behavior differences.

Canonical source in this repo: `bridgic/browser/_cli_catalog.py` (`CLI_COMMAND_TO_TOOL_METHOD`).

## Table of Contents

1. [Relationship Model](#relationship-model)
2. [Canonical Command -> Method Mapping](#canonical-command---method-mapping)
3. [Parameter Translation Rules (Important for Code Generation)](#parameter-translation-rules-important-for-code-generation)
4. [CLI-First -> SDK Code Generation Workflow](#cli-first---sdk-code-generation-workflow)
5. [Example: Convert CLI Flow to SDK Code](#example-convert-cli-flow-to-sdk-code)
6. [Practical Rule for Mixed Tasks](#practical-rule-for-mixed-tasks)

## Relationship Model

- CLI command surface and SDK tool-method surface are intentionally aligned.
- Most CLI commands are thin wrappers over one SDK method with parameter adaptation.
- `commands` is metadata-only and has no SDK tool method mapping.

## Canonical Command -> Method Mapping

| CLI command | SDK tool method |
|---|---|
| `open` | `navigate_to` |
| `search` | `search` |
| `info` | `get_current_page_info_str` |
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
| `check` | `check_checkbox_by_ref` |
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
| `close` | `browser_close` |
| `resize` | `browser_resize` |

## Parameter Translation Rules (Important for Code Generation)

- Ref normalization:
  - CLI accepts `@e2` and `e2`.
  - SDK ref methods use plain `"e2"`.
- `snapshot`:
  - `-i` -> `interactive=True`
  - `-F` -> `full_page=False`
  - `-s N` -> `start_from_char=N`
- `wait`:
  - `wait 2.5` -> `wait_for(time_seconds=2.5)`
  - `wait "Done"` -> `wait_for(text="Done")`
  - `wait --gone "Loading"` -> `wait_for(text_gone="Loading")`
- `scroll --dy Y --dx X` -> `mouse_wheel(delta_x=X, delta_y=Y)`
- `mouse-click X Y --button right --count 2` -> `mouse_click(X, Y, button="right", click_count=2)`
- `fill-form '<json>'`:
  - CLI passes JSON string.
  - SDK uses parsed list: `fill_form(fields=[{"ref":"e1","value":"..."}], submit=False)`
- `dialog --dismiss --text T` -> `handle_dialog(accept=False, prompt_text=T)`
- `dialog-setup --action dismiss --text T` -> `setup_dialog_handler(default_action="dismiss", default_prompt_text=T)`
- `verify-visible ROLE NAME --timeout 5000` -> `verify_element_visible(role=ROLE, accessible_name=NAME, timeout=5000)`
- `network --no-clear` -> `get_network_requests(clear=False)`
- `console --no-clear` -> `get_console_messages(clear=False)`
- `screenshot path.png --full-page` -> `take_screenshot(filename="path.png", full_page=True)`
- `video-stop path.webm` -> `stop_video(filename="path.webm")`

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
bridgic-browser fill @e3 "alice@example.com"
bridgic-browser fill @e4 "secret"
bridgic-browser click @e5
bridgic-browser wait "Dashboard"
bridgic-browser screenshot logged-in.png
```

SDK output:

```python
import asyncio
from bridgic.browser.session import Browser

async def run() -> None:
    async with Browser(headless=False) as browser:
        await browser.navigate_to("https://example.com/login")
        await browser.get_snapshot_text(interactive=True)
        await browser.input_text_by_ref("e3", "alice@example.com")
        await browser.input_text_by_ref("e4", "secret")
        await browser.click_element_by_ref("e5")
        await browser.wait_for(text="Dashboard")
        await browser.take_screenshot(filename="logged-in.png")

if __name__ == "__main__":
    asyncio.run(run())
```

## Practical Rule for Mixed Tasks

If execution is done via CLI but final deliverable must be SDK code, use this guide first, then verify final code shape with `sdk-guide.md`.
