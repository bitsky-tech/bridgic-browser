[English](#bridgic-browser) | [中文](README_zh.md)

---

## Bridgic Browser

**Bridgic Browser** is a Python library for LLM-driven browser automation built on [Playwright](https://playwright.dev/). It includes CLI tools, Python tools and skills for AI agents.

### Features

- **Comprehensive CLI Tools** - 69 tools organized into 15 categories; Designed to integrate with any AI agent
- **Python-based Tools** - Used for agent / workflow code generation; Easier integration with [Bridgic](https://github.com/bitsky-tech/bridgic) 
- **Snapshot with Semantic Invariance** - A representation of page snapshot based on accessibility tree and a specially designed ref-generation algorithm that ensures element refs remain unchanged across page reloads
- **Skills** - Used for guided exploration and code generation; Compatible with most of coding agents
- **Stealth Mode (Enabled by Default)** - Mode-aware anti-detection: 50+ Chrome args + JS patches in headless mode; minimal ~11 flags in headed mode to match real Chrome fingerprint
- **Persistent & Ephemeral Sessions** - Persistent profile by default (`~/.bridgic/bridgic-browser/user_data/`); pass `clear_user_data=True` for an ephemeral session with no profile
- **Nested iframe Support** - Supports DOM element operations within multi-level nested iframes

### Quick Start

#### Integration with AI

The easiest way to use **Bridgic Browser** is with a coding agent or AI assistant (such as Claude Code, Cursor, Codex, or OpenClaw). You can use it in two ways: via a Skill or a Plugin. In both cases, Bridgic Browser is installed automatically.

**Method 1: Use AI to directly control the browser and complete tasks in real time.**

<video src="https://github.com/user-attachments/assets/7ef9304a-34f1-4c87-8eb9-930f6378f020" controls></video>

To use this method, install the Skill provided by Bridgic Browser.

```bash
npx skills add bitsky-tech/bridgic-browser --skill bridgic-browser
```

After installation, the Skill will appear in your agent directories (for example, Claude Code typically under `.claude/skills/bridgic-browser/`, and Cursor under `.agents/skills/bridgic-browser/`).

**Method 2: Let AI generate repeatable browser automation scripts with minimal token usage.**

To use this method, install the **Plugin** provided by [AmphiLoop](https://github.com/bitsky-tech/AmphiLoop), a brand new methodology, tech stack and toolchain for building AI agents with natural language.

#### Manual Installation

```bash
pip install bridgic-browser
```

After installation, install Playwright browsers:

```bash
playwright install chromium
```

#### CLI Tools Usage

```shell
bridgic-browser open --headed https://example.com
bridgic-browser snapshot
# 'f0201d1c' is the ref value of the 'Learn more' link
bridgic-browser click f0201d1c
bridgic-browser screenshot page.png
bridgic-browser close
```

#### Python Tools Integration

First, build tools:

```python
from bridgic.browser.session import Browser
from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

# create a browser instance
browser = Browser(headless=False)

async def create_tools(browser):
    # Build a focused tool set for your agent
    builder = BrowserToolSetBuilder.for_categories(
        browser,
        ToolCategory.NAVIGATION,
        ToolCategory.SNAPSHOT,
        ToolCategory.ELEMENT_INTERACTION,
        ToolCategory.CAPTURE,
        ToolCategory.WAIT,
    )
    tools = builder.build()["tool_specs"]
    return tools
```

Second (optional), build a [Bridgic](https://github.com/bitsky-tech/bridgic) agent that uses this tool set:

```python
import os
from bridgic.llms.openai import OpenAILlm, OpenAIConfiguration
async def create_llm():
    _api_key = os.environ.get("OPENAI_API_KEY")
    _model_name = os.environ.get("OPENAI_MODEL_NAME")

    llm = OpenAILlm(
        api_key=_api_key,
        configuration=OpenAIConfiguration(model=_model_name),
        timeout=60,
    )
    return llm

from bridgic.core.agentic.recent import ReCentAutoma, StopCondition
from bridgic.core.automa import RunningOptions
async def create_agent(llm, tools):
    browser_agent = ReCentAutoma(
        llm=llm,
        tools=tools,
        stop_condition=StopCondition(max_iteration=10, max_consecutive_no_tool_selected=1),
        running_options=RunningOptions(debug=True),
    )
    return browser_agent

async def main():
    tools = await create_tools(browser)
    llm = await create_llm()
    agent = await create_agent(llm, tools)
    result = await agent.arun(
        goal=(
            "Summarize the 'Learn more' page of example.com for me"
        ),
        guidance=(
            "Do the following steps one by one:\n"
            "1. Navigate to https://example.com\n"
            "2. Click the 'Learn more' link\n"
            "3. Take a screenshot of the 'Learn more' page\n"
            "4. Summarize the page content in one sentence and tell me how to access the screenshot.\n"
        ),
    )
    print("\n\n*** Final Result: ***\n\n")
    print(result)

    await browser.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

#### Browser API Usage

You can also directly call the underlying `Browser` API to control the browser.

```python
from bridgic.browser.session import Browser

browser = Browser(headless=False)

async def main():
    await browser.navigate_to("https://example.com")
    snapshot = await browser.get_snapshot()
    print(snapshot.tree)  # Tree format: - role "name" [ref=f0201d1c]
    for ref, data in snapshot.refs.items():
        if data.name == "Learn more":
            learn_more_ref = ref
            break
    print(f"Found ref for 'Learn more': {learn_more_ref}")
    await browser.click_element_by_ref(learn_more_ref)
    await browser.take_screenshot(filename="page.png")
    await browser.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### CLI Tools

`bridgic-browser` ships with a command-line interface for controlling a browser from the terminal (69 tools organized into 15 categories). A persistent daemon process holds a browser instance; each CLI invocation connects over a Unix domain socket and exits immediately.

#### Configuration

Browser options are automatically loaded from the following sources (both CLI daemon and SDK `Browser()`), in priority order (highest last wins):

| Source | Example |
|--------|---------|
| Defaults | `headless=True`, `clear_user_data=False` (persistent profile) |
| `~/.bridgic/bridgic-browser/bridgic-browser.json` | User-level persistent config |
| `./bridgic-browser.json` | Project-local config (in cwd at daemon start) |
| Environment variables | See `skills/bridgic-browser/references/env-vars.md` |

**Headed browser note:**
When `headless=false` and stealth is enabled, bridgic auto-switches to system Chrome
(if installed) for better anti-detection (Chrome for Testing is blocked by Google OAuth).
To override, set:
- `channel`: e.g. `”chrome”`, `”msedge”`
- `executable_path`: absolute path to a browser binary

The JSON sources accept any `Browser` constructor parameter:

```json
{
  "headless": false,
  "proxy": {"server": "http://proxy:8080", "username": "u", "password": "p"},
  "viewport": {"width": 1280, "height": 720},
  "locale": "zh-CN",
  "timezone_id": "Asia/Shanghai"
}
```

```bash
# One-shot env override
BRIDGIC_BROWSER_JSON='{"headless":false,"locale":"zh-CN"}' bridgic-browser open URL
# One-shot ephemeral session (no persistent profile)
BRIDGIC_BROWSER_JSON='{"clear_user_data":true}' bridgic-browser open URL
```

#### Command List

| Category | Commands |
|----------|----------|
| Navigation | `open`, `back`, `forward`, `reload`, `search`, `info` |
| Snapshot | `snapshot [-i] [-f\|-F] [-l N] [-s FILE]` |
| Element Interaction | `click`, `double-click`, `hover`, `focus`, `fill`, `select`, `options`, `check`, `uncheck`, `scroll-to`, `drag`, `upload`, `fill-form` |
| Keyboard | `press`, `type`, `key-down`, `key-up` |
| Mouse | `scroll`, `mouse-move`, `mouse-click`, `mouse-drag`, `mouse-down`, `mouse-up` |
| Wait | `wait [SECONDS] [TEXT] [--gone]` |
| Tabs | `tabs`, `new-tab`, `switch-tab`, `close-tab` |
| Evaluate | `eval`, `eval-on` |
| Capture | `screenshot`, `pdf`, `downloads`, `wait-download` |
| Network | `network-start`, `network-stop`, `network`, `wait-network` |
| Dialog | `dialog-setup`, `dialog`, `dialog-remove` |
| Storage | `storage-save`, `storage-load`, `cookies-clear`, `cookies`, `cookie-set` |
| Verify | `verify-visible`, `verify-text`, `verify-value`, `verify-state`, `verify-url`, `verify-title` |
| Developer | `console-start`, `console-stop`, `console`, `trace-start`, `trace-stop`, `trace-chunk`, `video-start`, `video-stop` |
| Lifecycle | `close`, `resize` |

Use `-h` or `--help` on any command for details:

```bash
bridgic-browser -h
bridgic-browser scroll -h
```

### Python Tools

Bridgic Browser provides 69 tools organized into 15 categories. Use `BrowserToolSetBuilder` with category/name selection for scenario-focused tool sets.

#### Category-based Selection

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

# Focused set for your specific agent flows
builder = BrowserToolSetBuilder.for_categories(
    browser,
    ToolCategory.NAVIGATION,
    ToolCategory.ELEMENT_INTERACTION,
    ToolCategory.CAPTURE,
)
tools = builder.build()["tool_specs"]

# Include all available tools
builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.ALL)
tools = builder.build()["tool_specs"]
```

#### Name-based Selection (by function name)

```python
# Select by tool function names
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to",
    "click_element_by_ref",
)
tools = builder.build()["tool_specs"]

# Enable strict mode to catch typos and missing browser methods early
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to",
    strict=True,
)
tools = builder.build()["tool_specs"]
```

#### Mixed Selection

```python
builder1 = BrowserToolSetBuilder.for_categories(
    browser,
    ToolCategory.NAVIGATION,
    ToolCategory.ELEMENT_INTERACTION,
    ToolCategory.CAPTURE,
)
builder2 = BrowserToolSetBuilder.for_tool_names(
    browser, "verify_url", "verify_title"
)
tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]
```

#### Tool List

**Navigation (6 tools):**
- `navigate_to(url)` - Navigate to URL
- `search(query, engine)` - Search using search engine
- `get_current_page_info()` - Get current page info (URL, title, etc.)
- `reload_page()` - Reload current page
- `go_back()` / `go_forward()` - Browser history navigation

**Snapshot (1 tool):**
- `get_snapshot_text(limit=10000, interactive=False, full_page=True, file=None)` - Get page state string for LLM (accessibility tree with refs). **limit** (default 10000) controls the maximum characters returned. When the snapshot exceeds limit or **file** is explicitly provided, full content is saved to **file** (auto-generated under `~/.bridgic/bridgic-browser/snapshot/` if `None` and over limit) and only a notice with the file path is returned. **interactive** and **full_page** match `get_snapshot` (interactive-only or full-page by default).

**Element Interaction (13 tools) - by ref:**
- `click_element_by_ref(ref)` - Click element
- `input_text_by_ref(ref, text)` - Input text
- `fill_form(fields)` - Fill multiple form fields
- `scroll_element_into_view_by_ref(ref)` - Scroll element into view
- `select_dropdown_option_by_ref(ref, value)` - Select dropdown option
- `get_dropdown_options_by_ref(ref)` - Get dropdown options
- `check_checkbox_or_radio_by_ref(ref)` / `uncheck_checkbox_by_ref(ref)` - Checkbox control
- `focus_element_by_ref(ref)` - Focus element
- `hover_element_by_ref(ref)` - Hover over element
- `double_click_element_by_ref(ref)` - Double click
- `upload_file_by_ref(ref, path)` - Upload file
- `drag_element_by_ref(start_ref, end_ref)` - Drag and drop

**Tabs (4 tools):**
- `get_tabs()` / `new_tab(url)` / `switch_tab(page_id)` / `close_tab(page_id)` - Tab management

**Evaluate (2 tools):**
- `evaluate_javascript(code)` - Execute JavaScript
- `evaluate_javascript_on_ref(ref, code)` - Execute JavaScript on element

**Keyboard (4 tools):**
- `type_text(text)` - Type text character by character (key events, no ref — acts on focused element)
- `press_key(key)` - Press keyboard shortcut (e.g. `"Enter"`, `"Control+A"`)
- `key_down(key)` / `key_up(key)` - Key control

**Mouse (6 tools) - Coordinate-based:**
- `mouse_wheel(delta_x, delta_y)` - Scroll wheel
- `mouse_click(x, y)` - Click at position
- `mouse_move(x, y)` - Move mouse
- `mouse_drag(start_x, start_y, end_x, end_y)` - Drag operation
- `mouse_down()` / `mouse_up()` - Mouse button control

**Wait (1 tool):**
- `wait_for(time_seconds, text, text_gone, selector, state, timeout)` - Wait for conditions

**Capture (4 tools):**
- `take_screenshot(filename=None, ref=None, full_page=False, type="png")` - Capture screenshot
- `save_pdf(filename)` - Save page as PDF
- `get_downloaded_files_text()` - Numbered list of all files downloaded in this session
- `wait_for_next_download(timeout=30.0)` - Block until next download completes; returns a one-line summary or a timeout message

**Network (4 tools):**
- `start_network_capture()` / `stop_network_capture()` / `get_network_requests()` - Network monitoring
- `wait_for_network_idle()` - Wait for network idle

**Dialog (3 tools):**
- `setup_dialog_handler(default_action)` - Set up auto dialog handler
- `handle_dialog(accept, prompt_text)` - Handle dialog
- `remove_dialog_handler()` - Remove dialog handler

**Storage (5 tools):**
- `get_cookies()` / `set_cookie()` / `clear_cookies()` - Cookie management (`expires=0` is valid and preserved)
- `save_storage_state(filename)` / `restore_storage_state(filename)` - Session persistence

**Verify (6 tools):**
- `verify_text_visible(text)` - Check text visibility
- `verify_element_visible(role, accessible_name)` - Check element visibility by role and accessible name
- `verify_url(pattern)` / `verify_title(pattern)` - URL/title verification
- `verify_element_state(ref, state)` - Check element state
- `verify_value(ref, value)` - Check element value

**Developer (8 tools):**
- `start_console_capture()` / `stop_console_capture()` / `get_console_messages()` - Console monitoring
- `start_tracing()` / `stop_tracing()` / `add_trace_chunk()` - Performance tracing
- `start_video()` / `stop_video()` - Video recording

**Lifecycle (2 tools):**
- `close()` - Close browser
- `browser_resize(width, height)` - Resize viewport

### CLI Tools -> Python Tools Mapping

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
| `downloads` | `get_downloaded_files_text` |
| `wait-download` | `wait_for_next_download` |
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

### Core Components

#### Browser

The main class for browser automation with automatic launch mode selection:

```python
from bridgic.browser.session import Browser

# Persistent session (default — profile saved to ~/.bridgic/bridgic-browser/user_data/)
browser = Browser(
    headless=True,
    viewport={"width": 1600, "height": 900},
)

# Persistent session with custom profile path
browser = Browser(
    headless=False,
    user_data_dir="./user_data",
    stealth=True,  # Enabled by default
)

# Ephemeral session (no persistent profile)
browser = Browser(
    headless=True,
    clear_user_data=True,
)
```

**Key Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `headless` | bool | True | Run in headless mode |
| `viewport` | dict | 1600x900 | Browser viewport size |
| `user_data_dir` | str/Path | None | Custom path for persistent profile (ignored when `clear_user_data=True`) |
| `clear_user_data` | bool | False | If True, use ephemeral session (no profile); if False, use persistent profile |
| `stealth` | bool/StealthConfig | True | Stealth mode configuration |
| `channel` | str | None | Browser channel (chrome, msedge, etc.) |
| `proxy` | dict | None | Proxy settings |
| `downloads_path` | str/Path | None | Download directory |

**Snapshot:** Use `get_snapshot(interactive=False, full_page=True)` to get an `EnhancedSnapshot` with `.tree` (accessibility tree string) and `.refs` (ref → locator data). By default `full_page=True` includes all elements regardless of viewport position. Pass `interactive=True` for clickable/editable elements only (flattened output), or `full_page=False` to limit to viewport-only elements. Use `get_element_by_ref(ref)` to get a Playwright Locator from a ref (e.g. "1f79fe5e") for click, fill, etc.

#### StealthConfig

Configure stealth mode for bypassing bot detection:

```python
from bridgic.browser.session import StealthConfig, Browser

# Custom stealth configuration
config = StealthConfig(
    enabled=True,
    disable_security=False,
)

browser = Browser(stealth=config, headless=False)
```

#### DownloadManager

`Browser` always creates a `DownloadManager` and always accepts downloads. Files are saved to `downloads_path` if configured, or `~/Downloads` by default.

```python
# Optional: configure a custom download directory
browser = Browser(downloads_path="./downloads", headless=True)
await browser.navigate_to("https://example.com")  # lazy start triggers here

# Trigger a download, then wait for it to complete
await browser.click_element_by_ref("8d4b03a9")
result = await browser.wait_for_next_download(timeout=30.0)
# "Download complete: report.pdf — 261.0 KB — /home/user/Downloads/report.pdf"

# List all downloads in the session
print(await browser.get_downloaded_files_text())

# Or access the raw list
for file in browser.download_manager.downloaded_files:
    print(f"Downloaded: {file.file_name} ({file.file_size} bytes)")
```

### Stealth Mode

Stealth mode is **enabled by default** and includes:

- **Headless mode**: 50+ Chrome args + JS init script patching `navigator.webdriver`, `window.chrome`, WebGL, `document.hasFocus()`, `visibilityState`, and more. All patched functions spoof `Function.prototype.toString` to return `[native code]`.
- **Headed mode**: minimal ~11 flags only (matching real Chrome); JS patches are skipped entirely so third-party challenge iframes (e.g. Cloudflare Turnstile) see unmodified native APIs.

```python
# Stealth is ON by default
browser = Browser()  # stealth=True

# Disable stealth if needed
browser = Browser(stealth=False)

# Custom stealth settings
from bridgic.browser.session import create_stealth_config

config = create_stealth_config(
    disable_security=True,
)
browser = Browser(stealth=config)
```

### Error Model

SDK and CLI share one structured error protocol.

- Base type: `BridgicBrowserError`
- Stable fields: `code`, `message`, `details`, `retryable`
- Behavior subclasses:
  - `InvalidInputError` (invalid arguments/user input)
  - `StateError` (invalid runtime state, e.g. no active page/session)
  - `OperationError` (operation execution failures)
  - `VerificationError` (assertion/verification failures)

Why keep a small number of behavior subclasses:

- Lets callers catch by behavior when needed (e.g. retry only `StateError`)
- Encodes default retry semantics close to the failure source
- Avoids a large, hard-to-maintain class hierarchy while keeping error handling predictable

Daemon protocol is also structured:

- Success: `{"success": true, "result": "..."}`
- Failure: `{"success": false, "error_code": "...", "result": "...", "data": {...}, "meta": {"retryable": false}}`

CLI client converts daemon failures into `BridgicBrowserCommandError`, and CLI output keeps machine code visible as `Error[CODE]: ...`.

### Requirements

- Python 3.10+
- Playwright 1.57+
- Pydantic 2.11+

### Community

Join us to share feedback, ask questions, and keep up with what's new:

- 🐦 Twitter / X: [@bridgic](https://x.com/bridgic)
- 💬 Discord: [Join our server](https://discord.gg/5rQYnTKNCd)

### License

MIT License

## More documentation

- [Browser Tools Guide](docs/BROWSER_TOOLS_GUIDE.md) – Tool selection, ref vs coordinate, wait strategies, patterns.
- [Snapshot and Page State](docs/SNAPSHOT_AND_STATE.md) – SnapshotOptions, EnhancedSnapshot, get_snapshot_text, get_element_by_ref.
- [API Summary](docs/API.md) – Session and DownloadManager API reference.
