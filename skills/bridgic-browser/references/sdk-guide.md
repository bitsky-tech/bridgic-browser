# SDK Guide

Use this guide when the output should be Python automation code (`bridgic.browser.*`) instead of shell commands.

## Table of Contents

1. [Installation and Imports](#installation-and-imports)
2. [Preferred Lifecycle Pattern](#preferred-lifecycle-pattern)
3. [API Division: Raw Methods vs Tool Methods](#api-division-raw-methods-vs-tool-methods)
4. [Tool Methods](#tool-methods)
5. [Snapshot and Ref Rules](#snapshot-and-ref-rules)
6. [Tool Set Builder (for Agent Integration)](#tool-set-builder-for-agent-integration)
7. [CDP Mode (Connect to Existing Browser)](#cdp-mode-connect-to-existing-browser)
8. [Non-Obvious SDK Behavior](#non-obvious-sdk-behavior)
9. [SDK Error Handling](#sdk-error-handling)
10. [When to Load Other References](#when-to-load-other-references)

## Installation and Imports

```bash
uv init --bare
uv add bridgic-browser
uv run playwright install chromium
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
- `async with Browser(...)` calls `_start()` in `__aenter__` and `close()` in `__aexit__` automatically.
- Without the context manager, the browser starts lazily: `navigate_to(...)` and `search(...)` call `_ensure_started()` on first invocation.
- `get_snapshot(...)` returns `EnhancedSnapshot` (never `None`); raises `StateError` if no active page, `OperationError` if generation fails.
- **Default session is persistent**: `Browser()` (no args) saves the browser profile to `$BRIDGIC_HOME/bridgic-browser/user_data/` (default `~/.bridgic/bridgic-browser/user_data/`). Use `Browser(clear_user_data=True)` for an ephemeral session with no saved profile. Use `Browser(user_data_dir="./my-profile")` to specify a custom profile path.

## API Division: Raw Methods vs Tool Methods

Two parts of API serve different purposes — pick the right one:

| API | When to use | Examples |
|---|---|---|
| **Raw methods** | Direct Playwright-level control in scripts | `get_current_page()`, `take_screenshot(filename=...)`, `get_snapshot()` |
| **Tool methods** | Align with CLI behavior or expose to an LLM agent | `get_element_by_ref`, `click_element_by_ref()`, `wait_for()`, `verify_*()`, `get_snapshot_text()` |

Rule of thumb: if you're building an agent or want your script to behave like the CLI, prefer tool methods. If you need low-level page/script control, use raw methods.

## Tool Methods

| Category | SDK method(s) |
|---|---|
| Navigation | `navigate_to`, `go_back`, `go_forward`, `reload_page`, `get_current_page_info`, `search` |
| Snapshot | `get_snapshot_text` |
| Element interaction (by ref) | `click_element_by_ref`, `double_click_element_by_ref`, `hover_element_by_ref`, `focus_element_by_ref`, `input_text_by_ref`, `select_dropdown_option_by_ref`, `check_checkbox_or_radio_by_ref`, `uncheck_checkbox_by_ref`, `scroll_element_into_view_by_ref`, `drag_element_by_ref`, `get_dropdown_options_by_ref`, `upload_file_by_ref`, `fill_form` |
| Keyboard | `press_key`, `type_text`, `key_down`, `key_up` |
| Mouse | `mouse_wheel`, `mouse_move`, `mouse_click`, `mouse_drag`, `mouse_down`, `mouse_up` |
| Wait | `wait_for` |
| Tabs | `get_tabs`, `new_tab`, `switch_tab`, `close_tab` |
| Capture | `take_screenshot`, `save_pdf` |
| Network | `start_network_capture`, `stop_network_capture`, `get_network_requests`, `wait_for_network_idle` |
| Dialog | `setup_dialog_handler`, `handle_dialog`, `remove_dialog_handler` |
| Storage | `save_storage_state`, `restore_storage_state`, `clear_cookies`, `get_cookies`, `set_cookie` |
| Verify | `verify_element_visible`, `verify_text_visible`, `verify_value`, `verify_element_state`, `verify_url`, `verify_title` |
| Evaluate | `evaluate_javascript`, `evaluate_javascript_on_ref` |
| Developer | `start_console_capture`, `stop_console_capture`, `get_console_messages`, `start_tracing`, `stop_tracing`, `add_trace_chunk`, `start_video`, `stop_video` |
| Lifecycle | `close`, `browser_resize` |

## Snapshot and Ref Rules

- Refs are emitted in snapshot tree entries like `[ref=8d4b03a9]`.
- Resolve refs with ref-based methods (for example `click_element_by_ref("8d4b03a9")`).
- `navigate_to(...)`, `search(...)`, `new_tab(...)` or `switch_tab(...)` may clear cached snapshot refs. Take a new snapshot after page changes.
- `get_element_by_ref(...)` depends on the last snapshot cache.

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

## CDP Mode (Connect to Existing Browser)

To connect to an already-running Chrome instead of launching a new one, pass `cdp`:

```python
browser = Browser(cdp="9222")       # bare port, resolved lazily on _start()
browser = Browser(cdp="auto")       # scan local Chromium-family profiles (Chrome / Canary / Beta / Chromium / Brave / Edge)
browser = Browser(cdp="http://host:9222")
browser = Browser(cdp="ws://localhost:9222/devtools/browser/...")
```

`Browser(cdp=...)` accepts the same inputs as CLI `--cdp` and resolves them lazily on first use, so `Browser(cdp="auto")` is safe to construct inside a running event loop. A malformed value raises `InvalidInputError` on first use, not at construction time.

Quick notes (full details in [`cdp-mode.md`](cdp-mode.md)):
- Many `Browser(...)` parameters are ignored in CDP mode (`headless`, `args`, `proxy`, `channel`, `viewport`, `user_agent`, `locale`, …) because the browser is already running and the context is borrowed.
- Stealth launch args are **not** applied (browser already running). The main JS init script (navigator / webdriver / WebGL / plugins patches) is **headless-only** — it is skipped in CDP + headed mode. Only the anti-devtools timing script is registered in headed mode.
- **Tab visibility**: in CDP-borrowed mode bridgic only sees pages it itself opens, plus popups that satisfy both (a) Chromium populated `openerId` at attach time AND (b) that opener is already a bridgic-owned page. Concretely: popups **spawned from an owned page** via **plain left-click** on `<a target="_blank">` / `window.open()` are adopted (`rel="noopener"` does not block this — it only suppresses JS-level `window.opener`, not CDP-level openerId). Popups the user opens via **Cmd+click / Ctrl+click / middle-click** fail (a) — Chromium clears the opener at the browser-process level for background-tab navigations. Tabs opened via **Cmd+T / address bar** also fail (a) — they have no opener to begin with. Popups spawned from the user's pre-existing tabs fail (b) — the opener exists but isn't owned. The user's pre-existing tabs themselves are also invisible. This is a privacy boundary — see [`cdp-mode.md`](cdp-mode.md#tab-ownership) for the full truth table.
- **`auto_follow_popups`** (default `True`): when an adopted popup is spawned from the page bridgic is currently driving (`self._page`), `self._page` automatically follows the popup — mirroring Chrome's foreground-promotion UX. Pass `Browser(auto_follow_popups=False)` (or set the same key in config) to keep `self._page` fixed on the original tab; the popup is still adopted into the owned set, only the active-page pointer doesn't move.
- `close()` disconnects from the remote browser but does **not** terminate the Chrome process.
- The daemon auto-reconnects once if the CDP session drops; pick `cdp="9222"` / `"auto"` / `"http://..."` (not a raw `ws://.../<UUID>`) if the remote Chrome may restart.

For how to enable CDP on the target Chrome (Chrome 144+ `chrome://inspect/#remote-debugging` UI vs. legacy `--remote-debugging-port` launch flag) and the full limitation matrix, read [`cdp-mode.md`](cdp-mode.md).

## Non-Obvious SDK Behavior

- `wait_for` uses seconds for all time parameters:
  - `time_seconds` — fixed delay in seconds
  - `timeout` — max wait for text/selector conditions, in seconds (default `30.0`)
- `wait_for` condition priority: `time_seconds` > `text` > `text_gone` > `selector`.
- `take_screenshot(filename=None)` returns base64 data URL string.
- `take_screenshot(filename="path.png")` writes file and returns a status string.
- `verify_element_visible` uses `(role, accessible_name)` rather than ref.
- `start_video` must run before `stop_video`; `stop_video` stops the recorder and saves the `.webm` file immediately — no page close is needed.
- **Multi-instance isolation**: use `user_data_dir` to give each `Browser` its own persistent profile. Internal paths (tmp, snapshot) are shared but collision-free (all filenames use `mkstemp` or timestamp+random). For full process-level isolation (separate config, logs, socket), set `BRIDGIC_HOME` env var before spawning a subprocess — see `env-vars.md`.

## SDK Error Handling

Use structured exceptions from `bridgic.browser.errors` (for example `StateError`, `InvalidInputError`, `VerificationError`) instead of string matching on messages.

## When to Load Other References

- Need shell commands: read `cli-guide.md`.
- Need CLI <-> SDK conversion or mapping: read `cli-sdk-api-mapping.md`.
- Need environment variables or login state persistence details: read `env-vars.md`.
- Need to connect to an existing Chrome instance (chrome://inspect, `--remote-debugging-port`, cloud browser, Electron), or want the full CDP limitation / reconnect reference: read `cdp-mode.md`.
