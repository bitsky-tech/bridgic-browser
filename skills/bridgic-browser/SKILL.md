---
name: bridgic-browser
description: |
  Activates when code imports from bridgic.browser (e.g. `from bridgic.browser.session import Browser`,
  `from bridgic.browser.tools import BrowserToolSetBuilder`), or when the user asks about browser
  automation, web scraping, form filling, accessibility tree, element refs, e2e testing, stealth
  browsing, or building AI browser agents. Covers Browser setup, the snapshot/ref system,
  69+ tool presets, element interaction by ref, stealth mode, downloads, and the CLI tool.
---

# bridgic-browser

Playwright-based browser automation for LLM/AI agents. Key abstractions:
- **Browser** — Playwright wrapper with stealth mode and auto launch-mode selection
- **EnhancedSnapshot** — accessibility tree with stable `[ref=eN]` element identifiers
- **BrowserToolSetBuilder** — 69 tools in presets/categories, ready for agent tool-use
- **CLI** — `bridgic-browser` shell command backed by a persistent daemon

```python
from bridgic.browser.session import Browser, StealthConfig, SnapshotOptions
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset
```

## Installation

```bash
pip install bridgic-browser
playwright install chromium   # install browser binaries (first time)
```

## Browser Constructor

```python
browser = Browser()                          # headless=True, stealth=True (defaults)
browser = Browser(headless=False)            # show window

# Persistent session — cookies/localStorage survive restarts
browser = Browser(headless=False, user_data_dir="~/.agent_data", channel="chrome")

# Proxy
browser = Browser(proxy={"server": "http://proxy:8080", "username": "u", "password": "p"})

# Mobile emulation
browser = Browser(viewport={"width": 375, "height": 812},
                  user_agent="Mozilla/5.0 (iPhone; ...)", is_mobile=True, has_touch=True)

# Stealth control
browser = Browser(stealth=StealthConfig(enable_extensions=False))
browser = Browser(stealth=False)
```

**Key params:** `headless` (True), `viewport` (1920×1080), `user_data_dir`, `stealth` (True), `channel`, `proxy`, `timeout` (30000ms), `slow_mo`, `downloads_path`, `locale`, `timezone_id`, `color_scheme`. All Playwright options via `**kwargs`.

## Snapshots & Refs

```python
await browser.start()
await browser.navigate_to("https://example.com")

snapshot = await browser.get_snapshot()                 # full page (default)
snapshot = await browser.get_snapshot(interactive=True) # clickable/editable only — best for agent action selection
snapshot = await browser.get_snapshot(full_page=False)  # viewport only

# get_snapshot() returns Optional[EnhancedSnapshot] — ALWAYS check for None before accessing
if snapshot:
    tree = snapshot.tree   # "- button "Submit" [ref=e5]\n- link "Home" [ref=e6]\n..."
    refs = snapshot.refs   # Dict[str, RefData]

locator = await browser.get_element_by_ref("e5")  # Playwright Locator | None
if locator:
    await locator.click()

# LLM-powered lookup (requires bridgic-llms-openai)
element = await browser.get_element_by_prompt("the login button", llm)
```

Refs are valid for the current snapshot only. Re-call `get_snapshot()` after any page change. **`navigate_to()` explicitly clears `_last_snapshot`**, so after navigation all previous refs become stale and `get_element_by_ref()` returns `None` until a new snapshot is taken.

**`get_element_by_ref()` requires a prior `get_snapshot()` call.** If no snapshot exists yet, it returns `None` immediately (with a warning). Always snapshot before resolving refs.

**`interactive=True` limits which refs are stored.** `get_element_by_ref()` always resolves against the *last* snapshot taken. If you took an `interactive=True` snapshot, refs for non-interactive elements (e.g. paragraphs, headings) are not in the refs dict. If you need to interact with a ref obtained from a full snapshot, ensure the last snapshot was also full:

```python
# ❌ Trap: interactive snapshot taken after full snapshot — full refs are now gone
snap_full = await browser.get_snapshot()                 # refs include e1..e50
snap_int  = await browser.get_snapshot(interactive=True) # overwrites _last_snapshot
locator   = await browser.get_element_by_ref("e3")       # None if e3 was non-interactive

# ✅ Use full snapshot if you need to resolve any ref
snap = await browser.get_snapshot()   # keep full; use snap.tree to find interactive refs
```

## Browser Methods

```python
# Lifecycle — manual
await browser.start()
await browser.close()   # alias for kill()

# Lifecycle — context manager (preferred for scripts: auto start + close)
async with Browser(headless=True) as browser:
    await browser.navigate_to("https://example.com")

# Navigation
await browser.navigate_to(url)                         # auto-starts browser if not started
await browser.navigate_to(url, wait_until="load")      # wait for full page load (images/CSS)
# wait_until options: "domcontentloaded" (default, fast), "load" (full), "commit" (response received)
# ⚠️ "networkidle" can hang indefinitely on SPAs — avoid unless the site is static
await browser.new_page(url)                            # open new tab, returns Optional[Page]

# Pages
pages = browser.get_pages()                            # List[Page]
success, msg = await browser.switch_to_page(page_id)  # returns tuple[bool, str]
success, msg = await browser.close_page(page_id)      # returns tuple[bool, str]

# Page info
url = browser.get_current_page_url()                   # sync — no await!
title = await browser.get_current_page_title()         # Optional[str]
info = await browser.get_current_page_info()           # Optional[PageInfo] (url, title, viewport, scroll)

# Screenshot — use browser method directly, no need to get raw page
screenshot_bytes = await browser.take_screenshot()              # viewport, returns Optional[bytes]
screenshot_bytes = await browser.take_screenshot(path="s.png") # also saves to file
screenshot_bytes = await browser.take_screenshot(full_page=True)

# Access raw Playwright Page when needed
page = await browser.get_current_page()   # Optional[Page]
if page:
    await page.keyboard.press("Tab")

# Downloads (requires downloads_path set at construction)
for f in browser.downloaded_files:        # List[DownloadedFile]
    print(f.file_name, f.file_size, f.path)
```

**Auto-start behaviour**: `navigate_to()` will call `start()` automatically if the browser hasn't been started. `get_snapshot()` does **not** — it returns `None` if called before `start()`.

**Launch mode is auto-selected based on stealth + headless:**

| Constructor | Internal mode | Data persists? |
|-------------|--------------|---------------|
| `Browser()` | isolated (`launch` + `new_context`) | No |
| `Browser(headless=False)` | persistent context with **auto temp dir** | No — temp dir deleted on `close()` |
| `Browser(headless=False, user_data_dir="~/.data")` | persistent context with explicit dir | Yes |
| `Browser(headless=False, stealth=StealthConfig(enable_extensions=False))` | isolated | No |

`Browser(headless=False)` uses persistent context mode internally (required for extensions) but the auto-created temp dir is **deleted on `browser.close()`** — no data survives. To actually persist cookies/sessions, always provide `user_data_dir` explicitly.

## Common Mistakes

```python
# ❌ browser has NO .page attribute
await browser.page.goto(url)        # AttributeError
await browser.page.screenshot(...)  # AttributeError

# ✅ Use browser methods for navigation and screenshots
await browser.navigate_to(url, wait_until="domcontentloaded", timeout=30000)
await browser.take_screenshot(path="shot.png")         # preferred
await browser.take_screenshot(path="shot.png", full_page=True)

# ✅ Use get_current_page() only when raw Playwright Page is truly needed
page = await browser.get_current_page()
if page:
    await page.keyboard.press("Tab")

# ❌ get_snapshot() can return None — never access without checking
tree = (await browser.get_snapshot()).tree   # AttributeError if None

# ✅ Always guard the return value
snap = await browser.get_snapshot()
if snap:
    tree = snap.tree

# ❌ get_element_by_ref() before any get_snapshot() — returns None silently
locator = await browser.get_element_by_ref("e5")   # None — no snapshot taken yet

# ✅ Always snapshot first
snap = await browser.get_snapshot()
if snap:
    locator = await browser.get_element_by_ref("e5")

# ❌ get_current_page_url() is sync — do not await it
url = await browser.get_current_page_url()   # TypeError

# ✅ It's a regular property
url = browser.get_current_page_url()         # Optional[str]

# ❌ Treating switch_to_page / close_page as returning bool
if await browser.switch_to_page(page_id):   # TypeError — returns tuple, not bool

# ✅ Unpack the tuple
success, msg = await browser.switch_to_page(page_id)
if not success:
    print(msg)

# ❌ Expecting get_snapshot() to auto-start the browser
snap = await browser.get_snapshot()   # returns None — browser not started yet

# ✅ Ensure start() before snapshot (navigate_to auto-starts, get_snapshot does not)
await browser.start()
await browser.navigate_to(url)
snap = await browser.get_snapshot()
```

## Tool System (for AI Agents)

### Presets

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)       # 11 — navigate, click, snapshot
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.NAVIGATION)    #  4 — navigate only
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.SCRAPING)      # 14 — + scroll, page info
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)  # 20 — + input, dropdown, checkbox
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)       # 28 — + verify, screenshot
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.INTERACTIVE)   # 39 — + mouse, keyboard
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.DEVELOPER)     # 22 — network, devtools, tracing
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.COMPLETE)      # 69 — all
```

### Fine-grained Selection

```python
# By category: navigation, page, action, form, mouse, keyboard, screenshot,
#              network, dialog, storage, verify, devtools, control, state, advanced
tools = BrowserToolSetBuilder.for_categories(browser, "navigation", "action", "screenshot")

# By function reference
from bridgic.browser.tools import click_element_by_ref, input_text_by_ref
tools = BrowserToolSetBuilder.from_funcs(browser, click_element_by_ref, input_text_by_ref)

# By tool name strings
tools = BrowserToolSetBuilder.from_tool_names(browser, "search", "click_element_by_ref")
tools = BrowserToolSetBuilder.from_tool_names(browser, "search", strict=True)  # raises ValueError for unknown names

# Fluent builder — build_specs() returns List[BrowserToolSpec]
tools = (BrowserToolSetBuilder(browser)
    .with_preset(ToolPreset.MINIMAL)
    .with_category("screenshot")
    .with_tools("wait_for")          # add by name or function ref
    .without_tools("go_forward")
    .build_specs())

# ⚠️ Empty builder (no with_* calls) silently defaults to ToolPreset.MINIMAL
tools = BrowserToolSetBuilder(browser).build_specs()   # → MINIMAL tools, not empty!
```

### Custom Tools

Add your own async functions to the tool set alongside built-ins:

```python
async def my_tool(browser: "Browser", query: str) -> str:
    """My custom tool description (used as LLM tool description)."""
    ...
    return "result"

custom_spec = BrowserToolSpec.from_raw(func=my_tool, browser=browser)
# The 'browser' parameter is auto-excluded from the LLM schema — LLM only sees 'query'

# Mix with preset tools
builtin_specs = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)
all_tools = builtin_specs + [custom_spec]
```

## Ref-based vs Coordinate-based

Use **ref-based** when element appears in snapshot. Use **coordinate-based** for canvas/SVG/custom UI.

| Task | Ref-based | Coordinate-based |
|------|-----------|-----------------|
| Click | `click_element_by_ref` | `mouse_click` |
| Right-click | — | `mouse_click(x, y, button="right")` |
| Double-click | `double_click_element_by_ref` | `mouse_click(x, y, click_count=2)` |
| Type | `input_text_by_ref` | `press_sequentially` (focused element) |
| Drag | `drag_element_by_ref` | `mouse_drag` |
| Scroll | `scroll_to_text` | `mouse_wheel` |

**Text input — choose the right method:**

| Method | Mechanism | Best for |
|--------|-----------|----------|
| `input_text_by_ref(ref, text)` | `.fill()` | standard inputs, fastest |
| `input_text_by_ref(ref, text, slowly=True)` | char-by-char | autocomplete / live search |
| `input_text_by_ref(ref, text, submit=True)` | fill + Enter | forms where you type then submit |
| `input_text_by_ref(ref, text, clear=False)` | append | add to existing value |
| `input_text_by_ref(ref, text, is_secret=True)` | `.fill()` | passwords — text masked in logs/return value |
| `press_sequentially(text)` | key events per char | JS event handlers |
| `press_sequentially(text, submit=True)` | key events + Enter | JS handlers + submit |
| `insert_text(text)` | paste at cursor | fastest for long text |
| `fill_form(fields, submit=False)` | batch `.fill()` | fill multiple fields at once |

`fill_form` accepts `fields = [{"ref": "e1", "value": "foo"}, {"ref": "e2", "value": "bar"}]` — more efficient than individual `input_text_by_ref` calls when filling a whole form.

**`press_sequentially`, `insert_text`, `key_down`, `key_up` all operate on the currently focused element** — they have no `ref` parameter. You must focus the element first:
```python
await focus_element_by_ref(browser, ref="e3")   # focus first
await press_sequentially(browser, text="hello") # then type
# Or use input_text_by_ref which handles focus internally
```

**Tool return values** — all tools return `str`:
- Success: `"Clicked element e1"` / `"Navigated to https://..."`
- Stale ref: `"Element ref e1 is not available - page may have changed."`
- Verify: `"PASS: Element is visible"` / `"FAIL: Element not visible - element not found"`

### Non-obvious Tool Behaviours

**`get_llm_repr`** — the canonical snapshot tool for agents (not `get_snapshot()`). Wraps `get_snapshot()` and adds truncation + pagination. Returns a `str`, not an object.

```python
from bridgic.browser.tools import get_llm_repr
tree_str = await get_llm_repr(browser)                          # full page string
tree_str = await get_llm_repr(browser, interactive=True)        # clickable only
tree_str = await get_llm_repr(browser, start_from_char=30000)   # pagination
```

**`take_screenshot` tool** (agent tool, distinct from `browser.take_screenshot()` method):
- No `filename` → returns **base64 data URL** string (`data:image/png;base64,...`), not bytes
- With `filename` → saves file, returns `"Screenshot saved to: /path/file.png"`
- With `ref=` → screenshots a single element, not the whole page

```python
from bridgic.browser.tools import take_screenshot
result = await take_screenshot(browser)                       # base64 data URL
result = await take_screenshot(browser, filename="out.png")  # saves file
result = await take_screenshot(browser, ref="e5")            # element only
```

**`navigate_to_url` tool** auto-prepends `http://` when no protocol given. Blocks `javascript:`, `data:`, `vbscript:`, `about:` schemes for security.

**`wait_for` tool** — mixed time units; only first matching condition is used:
```python
# time_seconds is SECONDS;  timeout_ms is MILLISECONDS — different units!
await wait_for(browser, time_seconds=3)                        # wait 3 seconds
await wait_for(browser, text="Done", timeout_ms=10000)         # wait up to 10 s
await wait_for(browser, text_gone="Loading", timeout_ms=5000)
await wait_for(browser, selector=".modal", state="visible")
# Priority: time_seconds > text > text_gone > selector (only first provided is used)
```

**`verify_element_visible` uses ARIA role + accessible_name, NOT a ref:**
```python
# ❌ Wrong — does not accept ref
# await verify_element_visible(browser, ref="e5")

# ✅ Correct
from bridgic.browser.tools import verify_element_visible, verify_text_visible, verify_element_state
await verify_element_visible(browser, role="button", accessible_name="Submit")
await verify_text_visible(browser, text="Welcome")             # substring by default
await verify_text_visible(browser, text="Welcome", exact=True)
await verify_element_state(browser, ref="e5", state="enabled")
# state options: "visible", "hidden", "enabled", "disabled", "checked", "unchecked", "editable"

from bridgic.browser.tools import verify_url, verify_title
# ⚠️ verify_url and verify_title also default to exact=False (substring/contains match)
await verify_url(browser, expected_url="example.com")          # PASS if current URL contains "example.com"
await verify_url(browser, expected_url="https://example.com/path", exact=True)  # full URL match
await verify_title(browser, expected_title="Home")             # PASS if title contains "Home"
```

**`upload_file_by_ref`** — upload a local file via a file input element:
```python
from bridgic.browser.tools import upload_file_by_ref
await upload_file_by_ref(browser, ref="e3", file_path="/tmp/doc.pdf")
```

**`evaluate_javascript_on_ref`** — run JS scoped to a specific element:
```python
from bridgic.browser.tools import evaluate_javascript_on_ref
result = await evaluate_javascript_on_ref(browser, ref="e5", code="el => el.value")
```

**Dialog handling — register handler BEFORE the triggering action:**
```python
from bridgic.browser.tools import handle_dialog, setup_dialog_handler

# ❌ Wrong order — dialog fires before handler is registered
await click_element_by_ref(browser, ref="e5")   # triggers alert
await handle_dialog(browser, accept=True)        # too late, dialog already unhandled

# ✅ Register handler first, then trigger
await handle_dialog(browser, accept=True)        # one-time: handles the NEXT dialog
await click_element_by_ref(browser, ref="e5")   # now fires → auto-accepted

# For persistent handling of all dialogs on the page:
await setup_dialog_handler(browser, default_action="accept")
# ... do actions that may trigger dialogs ...
await remove_dialog_handler(browser)             # remove when done
```

Note: `setup_dialog_handler` is page-specific — navigating to a new page may require re-setup.

**`select_dropdown_option_by_ref` accepts value attr OR visible text** — tries `value` attribute first, falls back to visible label. Both work:
```python
await select_dropdown_option_by_ref(browser, ref="e4", text="US")        # by value attr
await select_dropdown_option_by_ref(browser, ref="e4", text="United States")  # by visible text
# Call get_dropdown_options_by_ref first to see available options (handles portalized dropdowns)
```

**`check_element_by_ref` / `uncheck_element_by_ref` are idempotent** — they read the current checked state first and skip the click if already in the desired state. Safe to call multiple times.

**Console and network capture are page-specific and must be explicitly stopped:**
```python
from bridgic.browser.tools import (
    start_console_capture, get_console_messages, stop_console_capture,
    start_network_capture, get_network_requests, stop_network_capture,
)
await start_console_capture(browser)   # start listening
# ... do page actions ...
messages = await get_console_messages(browser)        # clear=True by default — empties buffer after retrieval
await stop_console_capture(browser)    # MUST stop — stored in memory until stopped

# ⚠️ start_network_capture MUST be called BEFORE navigation to catch page-load requests
await start_network_capture(browser)
await browser.navigate_to("https://example.com")      # requests captured from here
# ... do page actions ...
requests = await get_network_requests(browser)         # include_static=False and clear=True by default
await stop_network_capture(browser)    # MUST stop
```

Key defaults:
- `get_console_messages(clear=True)` — buffer is cleared after each retrieval. Calling twice returns empty list on second call.
- `get_network_requests(include_static=False, clear=True)` — images, CSS, and JS are filtered out by default. Buffer cleared after retrieval.
- `start_network_capture` called AFTER navigation will miss page-load requests (they fire before capture starts).

Note: capture is page-specific — navigating to a new page requires re-starting.

**`start_video` does NOT start video recording** — it only checks if video was already configured. Video recording must be enabled at `Browser()` construction time via `record_video_dir`:
```python
# ❌ start_video() alone does nothing — just returns a "not available" message
await start_video(browser)

# ✅ Configure at browser creation; videos auto-save when pages close
browser = Browser(record_video_dir="./videos")
# ... do actions ...
# call stop_video() to get path; file is finalized only after page is closed
result = await stop_video(browser, filename="session.webm")
# the .webm file may still be incomplete until close_page / browser.close()
```

**`restore_storage_state` adds to existing context (may conflict).** For a truly clean start, pass `storage_state=` to the `Browser` constructor instead:
```python
# ✅ Clean restoration — starts a fresh context with the saved state
browser = Browser(storage_state="auth_state.json")   # via Playwright kwargs

# ⚠️ Adds cookies/localStorage to existing context (may have conflicts)
await restore_storage_state(browser, filename="auth_state.json")
```

**`StealthConfig` key constraints:**
- `enable_extensions=True` (default) + `headless=True` → extensions silently not loaded (no error). Set `headless=False` or `StealthConfig(enable_extensions=False)` to avoid this.
- `in_docker` is auto-detected via `/.dockerenv`. Override with `StealthConfig(in_docker=True)`.
- `disable_security=True` disables CORS and cert checks — only for trusted local testing.

## Common Patterns

```python
# Web scraping
browser = Browser(headless=True)
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.SCRAPING)

# Form automation
browser = Browser(headless=False)
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)

# E2E testing (stealth off — reveals real browser to the site)
browser = Browser(headless=True, stealth=False)
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)

# Persistent login — cookies/state survive process restarts
browser = Browser(headless=False, user_data_dir="~/.agent_data", channel="chrome")
```

## CLI Tool

Persistent daemon (Unix socket) + short-lived client per command. Auto-starts on first use. Use `-h` on any command for full help.

### Snapshot

```bash
# Full accessibility tree (default)
bridgic-browser snapshot

# Interactive elements only — clickable/editable, best for agent action selection
bridgic-browser snapshot -i
bridgic-browser snapshot --interactive

# Viewport-only (exclude off-screen elements)
bridgic-browser snapshot -F
bridgic-browser snapshot --no-full-page

# Combine: interactive + viewport-only
bridgic-browser snapshot -i -F

# Pagination — when output is truncated, continue from offset
bridgic-browser snapshot -s 30000
bridgic-browser snapshot --start-from-char 30000
```

Snapshot output format: accessibility tree lines like `- button "Submit" [ref=e5]`. Refs (`@e5`) are used by all element interaction commands. Re-run `snapshot` after any page change to refresh refs.

When output is truncated, a notice like `[... truncated at char 30000, use -s 30000 to continue]` appears at the end. The truncation limit is set by the `BRIDGIC_MAX_CHARS` env var (default `30000`).

### Navigation

```bash
bridgic-browser open https://example.com      # navigate (starts daemon/browser if needed)
bridgic-browser navigate https://example.com  # navigate in current tab
bridgic-browser back
bridgic-browser forward
bridgic-browser reload
bridgic-browser search "python tutorials"                        # DuckDuckGo (default)
bridgic-browser search "python tutorials" --engine google        # or bing
bridgic-browser info                          # URL, title, viewport, scroll position
```

### Element Interaction

Refs come from `snapshot` output. Accept `@e2` or bare `e2`.

```bash
bridgic-browser click @e2
bridgic-browser double-click @e3
bridgic-browser hover @e5
bridgic-browser focus @e4
bridgic-browser fill @e3 "text"              # fill input (fastest)
bridgic-browser select @e4 "Option A"        # dropdown
bridgic-browser check @e6
bridgic-browser uncheck @e6
bridgic-browser get text @e1                 # get element text content
```

### Keyboard & Mouse

```bash
bridgic-browser press "Enter"
bridgic-browser press "Control+A"
bridgic-browser press "Shift+Tab"
bridgic-browser type "hello world"           # type into focused element

bridgic-browser scroll --dy 300             # scroll down 300px
bridgic-browser scroll --dy -200            # scroll up 200px
bridgic-browser scroll --dx 100             # scroll right 100px
```

### Wait

```bash
bridgic-browser wait 2.5                    # wait N seconds (max 60)
bridgic-browser wait-for "Done"             # wait until text appears on page
bridgic-browser wait-for "Loading" --gone   # wait until text disappears
```

### Tabs

```bash
bridgic-browser tabs                        # list all open tabs (shows page_id)
bridgic-browser new-tab                     # open blank tab
bridgic-browser new-tab https://example.com
bridgic-browser switch-tab page_1234        # page_id from 'tabs' output
bridgic-browser close-tab                   # close current tab
bridgic-browser close-tab page_1234
```

### Capture

```bash
bridgic-browser screenshot page.png         # viewport screenshot
bridgic-browser screenshot page.png --full-page   # full scrollable page
bridgic-browser pdf report.pdf              # save as PDF (headless mode only)
```

### Developer

```bash
bridgic-browser eval "() => document.title"
bridgic-browser eval "() => document.querySelector('h1').textContent"
```

### Lifecycle

```bash
bridgic-browser close    # close browser and stop daemon
```

**The daemon caches the browser process across CLI calls.** After changing Python library code, you must restart the daemon to pick up the changes:
```bash
bridgic-browser close    # stop daemon (picks up code changes on next command)
bridgic-browser open URL # restarts daemon with new code
```

### Browser Configuration (priority: lowest → highest)

| Source | Notes |
|--------|-------|
| defaults | `headless=True` |
| `~/.bridgic/bridgic-browser.json` | user persistent config |
| `./bridgic-browser.json` | project-local (daemon cwd at startup) |
| `BRIDGIC_BROWSER_JSON` env var | full JSON — any `Browser` param |
| `BRIDGIC_HEADLESS` env var | `0` = show window |

```bash
# One-shot CI override (supports all Browser params including nested dicts)
BRIDGIC_BROWSER_JSON='{"channel":"chrome","proxy":{"server":"http://proxy:8080"}}' \
  bridgic-browser open URL
```

`stealth` accepts `true`/`false` in JSON. For `StealthConfig`, use the Python API.

## Namespace

```python
from bridgic.browser.session import Browser, EnhancedSnapshot, SnapshotOptions, StealthConfig
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset, BrowserToolSpec
```

`bridgic` is a shared namespace: `bridgic-browser` → `bridgic.browser`, `bridgic-core` → `bridgic.core`, `bridgic-llms-openai` → `bridgic.llms.openai`.
