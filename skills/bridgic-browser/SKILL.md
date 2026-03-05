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

# snapshot.tree  →  "- button "Submit" [ref=e5]\n- link "Home" [ref=e6]\n..."
# snapshot.refs  →  Dict[str, RefData]

locator = await browser.get_element_by_ref("e5")  # Playwright Locator | None
if locator:
    await locator.click()

# LLM-powered lookup (requires bridgic-llms-openai)
element = await browser.get_element_by_prompt("the login button", llm)
```

Refs are valid for the current snapshot only. Re-call `get_snapshot()` after any page change.

## Browser Methods

```python
await browser.start() / close() / kill()
await browser.navigate_to(url)
await browser.new_page(url)
pages = browser.get_pages()
await browser.switch_to_page(page_id)
info = await browser.get_current_page_info()   # PageInfo (url, title, viewport, scroll)
for f in browser.downloaded_files:             # List[DownloadedFile] — needs downloads_path
    print(f.file_name, f.file_size, f.path)
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

# Fluent builder
tools = (BrowserToolSetBuilder(browser)
    .with_preset(ToolPreset.MINIMAL)
    .with_category("screenshot")
    .with_tools("wait_for")          # add by name or function ref
    .without_tools("go_forward")
    .build_specs())
```

## Ref-based vs Coordinate-based

Use **ref-based** when element appears in snapshot. Use **coordinate-based** for canvas/SVG/custom UI.

| Task | Ref-based | Coordinate-based |
|------|-----------|-----------------|
| Click | `click_element_by_ref` | `mouse_click` |
| Type | `input_text_by_ref` | `press_sequentially` |
| Drag | `drag_element_by_ref` | `mouse_drag` |
| Scroll | `scroll_to_text` | `mouse_wheel` |

**Text input — choose the right method:**

| Method | Mechanism | Best for |
|--------|-----------|----------|
| `input_text_by_ref(ref, text)` | `.fill()` | standard inputs, fastest |
| `input_text_by_ref(ref, text, slowly=True)` | char-by-char | autocomplete / live search |
| `press_sequentially(text)` | key events per char | JS event handlers |
| `insert_text(text)` | paste at cursor | fastest for long text |

**Tool return values** — all tools return `str`:
- Success: `"Clicked element e1"` / `"Navigated to https://..."`
- Stale ref: `"Element ref e1 is not available - page may have changed."`
- Verify: `"PASS: Element is visible"` / `"FAIL: Element not visible - element not found"`

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
