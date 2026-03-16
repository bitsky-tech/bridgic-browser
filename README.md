# Bridgic Browser

[English](#english) | [中文](#中文)

---

<a name="english"></a>

## English

**Bridgic Browser** is a Python library for LLM-driven browser automation built on [Playwright](https://playwright.dev/). It provides a high-level API for building AI agents that can interact with web browsers, featuring built-in stealth mode for bypassing bot detection.

### Features

- **LLM-Ready Browser Automation** - Designed for AI agents with structured page snapshots and element references
- **Stealth Mode (Enabled by Default)** - 50+ Chrome args and optimizations to bypass bot detection
- **Dual Launch Mode** - Automatically switches between isolated sessions and persistent contexts
- **Download Management** - Automatic download handling with proper filename preservation
- **Comprehensive Tool Set** - Navigation, element interaction, tab management, and more

### Installation

```bash
pip install bridgic-browser
```

After installation, install Playwright browsers:

```bash
playwright install chromium
```

### Quick Start

#### Basic Usage

```python
import asyncio
from bridgic.browser.session import Browser

async def main():
    # Create browser with stealth mode (enabled by default)
    browser = Browser(headless=False)

    # Start browser
    await browser.start()

    try:
        # Navigate to a URL
        await browser.navigate_to("https://example.com")

        # Get page snapshot for LLM (returns EnhancedSnapshot with .tree and .refs)
        snapshot = await browser.get_snapshot()
        print(snapshot.tree)  # Tree format: - role "name" [ref=e1]

        # Interact with elements by ref (use refs from the snapshot)
        element = await browser.get_element_by_ref("e1")
        if element:
            await element.click()
    finally:
        await browser.stop()

asyncio.run(main())
```

#### Using with AI Agents

```python
from bridgic.browser.session import Browser
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

async def create_agent():
    browser = Browser(headless=False)
    await browser.start()

    # Build tool set for your agent
    builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)
    tools = builder.build()["tool_specs"]

    # Use tools with your LLM agent
    # tools include: navigate, click, input_text, scroll, etc.
    return browser, tools
```

### CLI Tool

`bridgic-browser` ships with a command-line interface for controlling a browser from the terminal without writing Python. A persistent daemon process holds the browser; each CLI invocation connects over a Unix socket and exits immediately.

```bash
bridgic-browser open https://example.com   # auto-starts daemon
bridgic-browser snapshot                    # print accessibility tree
bridgic-browser click @e2
bridgic-browser fill @e3 "hello@example.com"
bridgic-browser screenshot page.png
bridgic-browser close                       # stop the daemon
```

#### Configuration

Browser options are read at daemon startup from the following sources, in priority order (highest last wins):

| Source | Example |
|--------|---------|
| Defaults | `headless=True` |
| `~/.bridgic/bridgic-browser.json` | User-level persistent config |
| `./bridgic-browser.json` | Project-local config (in cwd at daemon start) |
| `BRIDGIC_BROWSER_JSON` env var | Full JSON override for any `Browser` parameter |
| `BRIDGIC_HEADLESS` env var | `0` = show window, any other value = headless |
| `BRIDGIC_MAX_CHARS` env var | Max characters per `snapshot`/`get_snapshot_text` response before pagination (default `30000`) |

The JSON sources accept any `Browser` constructor parameter:

```json
{
  "headless": false,
  "channel": "chrome",
  "proxy": {"server": "http://proxy:8080", "username": "u", "password": "p"},
  "viewport": {"width": 1280, "height": 720},
  "locale": "zh-CN",
  "timezone_id": "Asia/Shanghai"
}
```

```bash
# One-shot env override
BRIDGIC_BROWSER_JSON='{"channel":"chrome","headless":false}' bridgic-browser open URL
```

#### Commands

| Category | Commands |
|----------|----------|
| Navigation | `open`, `back`, `forward`, `reload`, `search`, `info` |
| Snapshot | `snapshot [-i] [-f\|-F] [-s N]` |
| Element Interaction | `click`, `double-click`, `hover`, `focus`, `fill`, `select`, `options`, `check`, `uncheck`, `scroll-to`, `drag`, `upload`, `fill-form` |
| Keyboard | `press`, `type`, `key-down`, `key-up` |
| Mouse | `scroll`, `mouse-move`, `mouse-click`, `mouse-drag`, `mouse-down`, `mouse-up` |
| Wait | `wait [SECONDS] [TEXT] [--gone]` |
| Tabs | `tabs`, `new-tab`, `switch-tab`, `close-tab` |
| Evaluate | `eval`, `eval-on` |
| Capture | `screenshot`, `pdf` |
| Network | `network-start`, `network-stop`, `network`, `wait-network` |
| Dialog | `dialog-setup`, `dialog`, `dialog-remove` |
| Storage | `storage-save`, `storage-load`, `cookies-clear`, `cookies`, `cookie-set` |
| Verify | `verify-visible`, `verify-text`, `verify-value`, `verify-state`, `verify-url`, `verify-title` |
| Developer | `console-start`, `console-stop`, `console`, `trace-start`, `trace-stop`, `trace-chunk`, `video-start`, `video-stop` |
| Lifecycle | `close`, `resize` |
| Discovery | `commands [--section|--preset|--list-presets|--list-sections]` |

Use `-h` or `--help` on any command for details. For agent-oriented planning, prefer `bridgic-browser commands --preset PRESET`:

```bash
bridgic-browser -h
bridgic-browser scroll -h
```

### Core Components

#### Browser

The main class for browser automation with automatic launch mode selection:

```python
from bridgic.browser.session import Browser

# Isolated session (no persistence)
browser = Browser(
    headless=True,
    viewport={"width": 1920, "height": 1080},
)

# Persistent session (with user data)
browser = Browser(
    headless=False,
    user_data_dir="./user_data",
    stealth=True,  # Enabled by default
)
```

**Key Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `headless` | bool | True | Run in headless mode |
| `viewport` | dict | 1920x1080 | Browser viewport size |
| `user_data_dir` | str/Path | None | Path for persistent context |
| `stealth` | bool/StealthConfig | True | Stealth mode configuration |
| `channel` | str | None | Browser channel (chrome, msedge, etc.) |
| `proxy` | dict | None | Proxy settings |
| `downloads_path` | str/Path | None | Download directory |

**Snapshot:** Use `get_snapshot(interactive=False, full_page=True)` to get an `EnhancedSnapshot` with `.tree` (accessibility tree string) and `.refs` (ref → locator data). By default `full_page=True` includes all elements regardless of viewport position. Pass `interactive=True` for clickable/editable elements only (flattened output), or `full_page=False` to limit to viewport-only elements. Use `get_element_by_ref(ref)` to get a Playwright Locator from a ref (e.g. `"e1"`) for click, fill, etc.

#### StealthConfig

Configure stealth mode for bypassing bot detection:

```python
from bridgic.browser.session import StealthConfig, Browser

# Custom stealth configuration
config = StealthConfig(
    enabled=True,
    enable_extensions=True,  # Requires headless=False
    disable_security=False,
    cookie_whitelist_domains=["example.com"],
)

browser = Browser(stealth=config, headless=False)
```

#### DownloadManager

Handle file downloads with proper filename preservation:

```python
# Pass downloads_path to Browser — it creates and manages the DownloadManager internally
browser = Browser(downloads_path="./downloads", headless=True)
await browser.start()

# Access downloaded files via the built-in manager
for file in browser.download_manager.downloaded_files:
    print(f"Downloaded: {file.file_name} ({file.file_size} bytes)")
```

### Browser Tools

Bridgic Browser provides 67 tools organized into categories. Use `BrowserToolSetBuilder` with `ToolPreset` for scenario-based tool selection.

#### Quick Start with Presets

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

# Choose a preset for your use case
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)       # 9 tools
tools = builder.build()["tool_specs"]
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)  # 18 tools
tools = builder.build()["tool_specs"]
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)       # 26 tools
tools = builder.build()["tool_specs"]
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.COMPLETE)      # 67 tools
tools = builder.build()["tool_specs"]
```

#### Available Presets

| Preset | Tools | Description |
|--------|-------|-------------|
| `MINIMAL` | 9 | Navigate, click, input, snapshot |
| `NAVIGATION` | 3 | Navigate, back/forward |
| `SCRAPING` | 10 | Navigation + snapshot + scroll |
| `FORM_FILLING` | 18 | Navigation + input + dropdown + checkbox |
| `TESTING` | 26 | Form filling + verification + screenshot |
| `INTERACTIVE` | 32 | All action tools + mouse + keyboard |
| `DEVELOPER` | 23 | Network + console + tracing |
| `COMPLETE` | 67 | All available tools |

#### Category-based Selection

```python
# Select by category
builder = BrowserToolSetBuilder.for_categories(
    browser, "navigation", "action", "capture"
)
tools = builder.build()["tool_specs"]
```

#### Name-based Selection (by function name)

```python
# Select by tool function names
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to_url",
    "click_element_by_ref",
)
tools = builder.build()["tool_specs"]

# Enable strict mode to catch typos and missing browser methods early
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to_url",
    strict=True,
)
tools = builder.build()["tool_specs"]
```

#### Combine `for_*` Builders

```python
builder1 = BrowserToolSetBuilder.for_categories(
    browser, "navigation", "action", "capture"
)
builder2 = BrowserToolSetBuilder.for_tool_names(
    browser, "verify_url", "verify_title"
)
tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]
```

#### Tool Categories

**Navigation (6 tools):**
- `navigate_to_url(url)` - Navigate to URL
- `search(query, engine)` - Search using search engine
- `get_current_page_info_str()` - Get current page info (URL, title, etc.)
- `reload_page()` - Reload current page
- `go_back()` / `go_forward()` - Browser history navigation

**Snapshot (1 tool):**
- `get_snapshot_text(start_from_char=0, interactive=False, full_page=True)` - Get page state string for LLM (accessibility tree with refs). Use **start_from_char** for pagination when the page is long: if the return value is truncated, a `[notice]` at the end gives **next_start_char** to call again. **interactive** and **full_page** match `get_snapshot` (interactive-only or full-page by default). Output is truncated at the `BRIDGIC_MAX_CHARS` limit (default 30,000 chars) with a notice explaining how to continue.

**Element Interaction (13 tools) - by ref:**
- `click_element_by_ref(ref)` - Click element
- `input_text_by_ref(ref, text)` - Input text
- `fill_form(fields)` - Fill multiple form fields
- `scroll_element_into_view_by_ref(ref)` - Scroll element into view
- `select_dropdown_option_by_ref(ref, value)` - Select dropdown option
- `get_dropdown_options_by_ref(ref)` - Get dropdown options
- `check_checkbox_by_ref(ref)` / `uncheck_checkbox_by_ref(ref)` - Checkbox control
- `focus_element_by_ref(ref)` - Focus element
- `hover_element_by_ref(ref)` - Hover over element
- `double_click_element_by_ref(ref)` - Double click
- `upload_file_by_ref(ref, path)` - Upload file
- `drag_element_by_ref(start_ref, end_ref)` - Drag and drop

**Tabs (4 tools):**
- `get_tabs()` / `new_tab(url)` / `switch_tab(tab_id)` / `close_tab(tab_id)` - Tab management

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
- `wait_for(time_seconds, text, text_gone, selector, state, timeout_ms)` - Wait for conditions

**Capture (2 tools):**
- `take_screenshot(type, filename)` - Capture screenshot
- `save_pdf(filename)` - Save page as PDF

**Network (4 tools):**
- `start_network_capture()` / `stop_network_capture()` / `get_network_requests()` - Network monitoring
- `wait_for_network_idle()` - Wait for network idle

**Dialog (3 tools):**
- `setup_dialog_handler(default_action)` - Set up auto dialog handler
- `handle_dialog(accept, prompt_text)` - Handle dialog
- `remove_dialog_handler()` - Remove dialog handler

**Storage (5 tools):**
- `get_cookies()` / `set_cookie()` / `clear_cookies()` - Cookie management
- `save_storage_state(filename)` / `restore_storage_state(filename)` - Session persistence

**Verify (6 tools):**
- `verify_text_visible(text)` - Check text visibility
- `verify_element_visible(role, name)` - Check element visibility by role and accessible name
- `verify_url(pattern)` / `verify_title(pattern)` - URL/title verification
- `verify_element_state(ref, state)` - Check element state
- `verify_value(ref, value)` - Check element value

**Developer (8 tools):**
- `start_console_capture()` / `stop_console_capture()` / `get_console_messages()` - Console monitoring
- `start_tracing()` / `stop_tracing()` / `add_trace_chunk()` - Performance tracing
- `start_video()` / `stop_video()` - Video recording

**Lifecycle (2 tools):**
- `browser_close()` - Stop browser (calls `stop()`)
- `browser_resize(width, height)` - Resize viewport

### Stealth Mode

Stealth mode is **enabled by default** and includes:

- 50+ Chrome arguments to disable automation detection
- Disabled automation-revealing features (`navigator.webdriver`, etc.)
- Human-like browser fingerprint
- Optional extensions (uBlock Origin, Cookie Consent) for non-headless mode

```python
# Stealth is ON by default
browser = Browser()  # stealth=True

# Disable stealth if needed
browser = Browser(stealth=False)

# Custom stealth settings
from bridgic.browser.session import create_stealth_config

config = create_stealth_config(
    enable_extensions=False,
    disable_security=True,
)
browser = Browser(stealth=config)
```

### Requirements

- Python 3.11+
- Playwright 1.57+
- Pydantic 2.11+

### License

MIT License

---

<a name="中文"></a>

## 中文

**Bridgic Browser** 是一个基于 [Playwright](https://playwright.dev/) 构建的 Python 库，专为 LLM 驱动的浏览器自动化设计。它提供了高级 API，用于构建能够与网页浏览器交互的 AI 智能体，并内置隐身模式以绕过机器人检测。

### 特性

- **LLM 就绪的浏览器自动化** - 专为 AI 智能体设计，提供结构化页面快照和元素引用
- **隐身模式（默认启用）** - 50+ Chrome 参数和优化，用于绕过机器人检测
- **双启动模式** - 自动在隔离会话和持久化上下文之间切换
- **下载管理** - 自动处理下载，正确保留文件名
- **完整工具集** - 导航、元素交互、标签页管理等

### 安装

```bash
pip install bridgic-browser
```

安装后，安装 Playwright 浏览器：

```bash
playwright install chromium
```

### 快速开始

#### 基本用法

```python
import asyncio
from bridgic.browser.session import Browser

async def main():
    # 创建浏览器，隐身模式默认启用
    browser = Browser(headless=False)

    # 启动浏览器
    await browser.start()

    try:
        # 导航到 URL
        await browser.navigate_to("https://example.com")

        # 获取页面快照供 LLM 使用（返回 EnhancedSnapshot，含 .tree 和 .refs）
        snapshot = await browser.get_snapshot()
        print(snapshot.tree)  # 树格式：- role "name" [ref=e1]

        # 通过引用与元素交互（使用快照中的 ref）
        element = await browser.get_element_by_ref("e1")
        if element:
            await element.click()
    finally:
        await browser.stop()

asyncio.run(main())
```

#### 与 AI 智能体配合使用

```python
from bridgic.browser.session import Browser
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

async def create_agent():
    browser = Browser(headless=False)
    await browser.start()

    # 为智能体构建工具集
    builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)
    tools = builder.build()["tool_specs"]

    # 将工具与 LLM 智能体配合使用
    # 工具包括：导航、点击、输入文本、滚动等
    return browser, tools
```

### CLI 工具

`bridgic-browser` 内置了命令行界面，无需编写 Python 代码即可从终端控制浏览器。一个持久化 daemon 进程持有浏览器实例，每次 CLI 调用通过 Unix socket 连接后立即退出。

```bash
bridgic-browser open https://example.com   # 自动启动 daemon
bridgic-browser snapshot                    # 打印可访问性树
bridgic-browser click @e2
bridgic-browser fill @e3 "hello@example.com"
bridgic-browser screenshot page.png
bridgic-browser close                       # 停止 daemon
```

#### 配置

浏览器参数在 daemon 启动时从以下来源读取，优先级从低到高：

| 来源 | 示例 |
|------|------|
| 默认值 | `headless=True` |
| `~/.bridgic/bridgic-browser.json` | 用户级持久配置 |
| `./bridgic-browser.json` | 项目本地配置（daemon 启动时的工作目录） |
| `BRIDGIC_BROWSER_JSON` 环境变量 | 完整 JSON，支持所有 `Browser` 参数 |
| `BRIDGIC_HEADLESS` 环境变量 | `0` = 显示窗口，其他值 = 无头模式 |
| `BRIDGIC_MAX_CHARS` 环境变量 | `snapshot`/`get_snapshot_text` 每次响应的最大字符数（超出则分页，默认 `30000`） |

JSON 来源支持所有 `Browser` 构造参数：

```json
{
  "headless": false,
  "channel": "chrome",
  "proxy": {"server": "http://proxy:8080", "username": "u", "password": "p"},
  "viewport": {"width": 1280, "height": 720},
  "locale": "zh-CN",
  "timezone_id": "Asia/Shanghai"
}
```

```bash
# 单次环境变量覆盖
BRIDGIC_BROWSER_JSON='{"channel":"chrome","headless":false}' bridgic-browser open URL
```

#### 命令列表

| 类别 | 命令 |
|------|------|
| 导航 | `open`、`back`、`forward`、`reload`、`search`、`info` |
| 快照 | `snapshot [-i] [-f\|-F] [-s N]` |
| 元素交互 | `click`、`double-click`、`hover`、`focus`、`fill`、`select`、`options`、`check`、`uncheck`、`scroll-to`、`drag`、`upload`、`fill-form` |
| 键盘 | `press`、`type`、`key-down`、`key-up` |
| 鼠标 | `scroll`、`mouse-move`、`mouse-click`、`mouse-drag`、`mouse-down`、`mouse-up` |
| 等待 | `wait [SECONDS] [TEXT] [--gone]` |
| 标签页 | `tabs`、`new-tab`、`switch-tab`、`close-tab` |
| 评估 | `eval`、`eval-on` |
| 截图 | `screenshot`、`pdf` |
| 网络 | `network-start`、`network-stop`、`network`、`wait-network` |
| 对话框 | `dialog-setup`、`dialog`、`dialog-remove` |
| 存储 | `storage-save`、`storage-load`、`cookies-clear`、`cookies`、`cookie-set` |
| 断言 | `verify-visible`、`verify-text`、`verify-value`、`verify-state`、`verify-url`、`verify-title` |
| 开发者 | `console-start`、`console-stop`、`console`、`trace-start`、`trace-stop`、`trace-chunk`、`video-start`、`video-stop` |
| 生命周期 | `close`、`resize` |
| 发现 | `commands [--section|--preset|--list-presets|--list-sections]` |

使用 `-h` 或 `--help` 查看任意命令的详细说明。面向 Agent 的能力发现，推荐优先使用 `bridgic-browser commands --preset PRESET`：

```bash
bridgic-browser -h
bridgic-browser scroll -h
```

### 核心组件

#### Browser

浏览器自动化的主类，支持自动启动模式选择：

```python
from bridgic.browser.session import Browser

# 隔离会话（无持久化）
browser = Browser(
    headless=True,
    viewport={"width": 1920, "height": 1080},
)

# 持久化会话（带用户数据）
browser = Browser(
    headless=False,
    user_data_dir="./user_data",
    stealth=True,  # 默认启用
)
```

**主要参数：**

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `headless` | bool | True | 无头模式运行 |
| `viewport` | dict | 1920x1080 | 浏览器视口大小 |
| `user_data_dir` | str/Path | None | 持久化上下文路径 |
| `stealth` | bool/StealthConfig | True | 隐身模式配置 |
| `channel` | str | None | 浏览器通道（chrome、msedge 等） |
| `proxy` | dict | None | 代理设置 |
| `downloads_path` | str/Path | None | 下载目录 |

**快照：** 使用 `get_snapshot(interactive=False, full_page=True)` 获取 `EnhancedSnapshot`，含 `.tree`（可访问性树字符串）和 `.refs`（ref → 定位数据）。默认 `full_page=True` 包含所有元素（不限于视口）。`interactive=True` 仅包含可点击/可编辑元素（扁平输出），`full_page=False` 仅包含视口内元素。使用 `get_element_by_ref(ref)` 根据 ref（如 `"e1"`）获取 Playwright Locator 后进行 click、fill 等操作。

#### StealthConfig

配置隐身模式以绕过机器人检测：

```python
from bridgic.browser.session import StealthConfig, Browser

# 自定义隐身配置
config = StealthConfig(
    enabled=True,
    enable_extensions=True,  # 需要 headless=False
    disable_security=False,
    cookie_whitelist_domains=["example.com"],
)

browser = Browser(stealth=config, headless=False)
```

#### DownloadManager

处理文件下载，正确保留文件名：

```python
# 将 downloads_path 传给 Browser — 它会内部创建并管理 DownloadManager
browser = Browser(downloads_path="./downloads", headless=True)
await browser.start()

# 通过内置管理器访问已下载的文件
for file in browser.download_manager.downloaded_files:
    print(f"已下载：{file.file_name}（{file.file_size} 字节）")
```

### 浏览器工具

Bridgic Browser 提供 67 个工具，按类别组织。使用 `BrowserToolSetBuilder` 配合 `ToolPreset` 进行场景化工具选择。

#### 使用预设快速开始

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

# 根据使用场景选择预设
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)       # 9 个工具
tools = builder.build()["tool_specs"]
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)  # 18 个工具
tools = builder.build()["tool_specs"]
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)       # 26 个工具
tools = builder.build()["tool_specs"]
builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.COMPLETE)      # 67 个工具
tools = builder.build()["tool_specs"]
```

#### 可用预设

| 预设 | 工具数 | 描述 |
|------|--------|------|
| `MINIMAL` | 9 | 导航、点击、输入、快照 |
| `NAVIGATION` | 3 | 导航、前进/后退 |
| `SCRAPING` | 10 | 导航 + 快照 + 滚动 |
| `FORM_FILLING` | 18 | 导航 + 输入 + 下拉框 + 复选框 |
| `TESTING` | 26 | 表单填写 + 验证 + 截图 |
| `INTERACTIVE` | 32 | 所有交互工具 + 鼠标 + 键盘 |
| `DEVELOPER` | 23 | 网络 + 控制台 + 追踪 |
| `COMPLETE` | 67 | 所有可用工具 |

#### 按类别选择

```python
# 按类别选择工具
builder = BrowserToolSetBuilder.for_categories(
    browser, "navigation", "action", "capture"
)
tools = builder.build()["tool_specs"]
```

#### 按名称选择（按函数名）

```python
# 按工具函数名选择
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to_url",
    "click_element_by_ref",
)
tools = builder.build()["tool_specs"]

# 开启 strict 模式，及时发现拼写错误和 browser 缺失方法
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to_url",
    strict=True,
)
tools = builder.build()["tool_specs"]
```

#### 组合 `for_*` 构建器

```python
builder1 = BrowserToolSetBuilder.for_categories(
    browser, "navigation", "action", "capture"
)
builder2 = BrowserToolSetBuilder.for_tool_names(
    browser, "verify_url", "verify_title"
)
tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]
```

#### 工具类别

**导航（6 个工具）：**
- `navigate_to_url(url)` - 导航到 URL
- `search(query, engine)` - 使用搜索引擎搜索
- `get_current_page_info_str()` - 获取当前页面信息（URL、标题等）
- `reload_page()` - 重新加载当前页面
- `go_back()` / `go_forward()` - 浏览器历史导航

**快照（1 个工具）：**
- `get_snapshot_text(start_from_char=0, interactive=False, full_page=True)` - 获取供 LLM 使用的页面状态字符串（带 ref 的可访问性树）。长页面可用 **start_from_char** 分页：若返回值被截断，末尾会有 `[notice]` 给出 **next_start_char** 供再次调用。**interactive** 与 **full_page** 与 `get_snapshot` 一致（默认全页面）。输出在 `BRIDGIC_MAX_CHARS` 上限处（默认 3 万字符）截断并附带续读说明。

**元素交互（13 个工具）- 通过引用操作元素：**
- `click_element_by_ref(ref)` - 点击元素
- `input_text_by_ref(ref, text)` - 输入文本
- `fill_form(fields)` - 填写多个表单字段
- `scroll_element_into_view_by_ref(ref)` - 滚动元素到可视区域
- `select_dropdown_option_by_ref(ref, value)` - 选择下拉选项
- `get_dropdown_options_by_ref(ref)` - 获取下拉选项
- `check_checkbox_by_ref(ref)` / `uncheck_checkbox_by_ref(ref)` - 复选框控制
- `focus_element_by_ref(ref)` - 聚焦元素
- `hover_element_by_ref(ref)` - 悬停在元素上
- `double_click_element_by_ref(ref)` - 双击
- `upload_file_by_ref(ref, path)` - 上传文件
- `drag_element_by_ref(start_ref, end_ref)` - 拖放

**标签页（4 个工具）：**
- `get_tabs()` / `new_tab(url)` / `switch_tab(tab_id)` / `close_tab(tab_id)` - 标签页管理

**执行（2 个工具）：**
- `evaluate_javascript(code)` - 执行 JavaScript
- `evaluate_javascript_on_ref(ref, code)` - 在元素上执行 JavaScript

**键盘（4 个工具）：**
- `type_text(text)` - 逐字符输入文本（键盘事件，无 ref — 作用于当前焦点元素）
- `press_key(key)` - 按键快捷键（如 `"Enter"`, `"Control+A"`）
- `key_down(key)` / `key_up(key)` - 按键控制

**鼠标（6 个工具）- 基于坐标：**
- `mouse_wheel(delta_x, delta_y)` - 滚轮
- `mouse_click(x, y)` - 在指定位置点击
- `mouse_move(x, y)` - 移动鼠标
- `mouse_drag(start_x, start_y, end_x, end_y)` - 拖动操作
- `mouse_down()` / `mouse_up()` - 鼠标按钮控制

**等待（1 个工具）：**
- `wait_for(time_seconds, text, text_gone, selector, state, timeout_ms)` - 等待条件

**截图（2 个工具）：**
- `take_screenshot(type, filename)` - 截取屏幕截图
- `save_pdf(filename)` - 保存页面为 PDF

**网络（4 个工具）：**
- `start_network_capture()` / `stop_network_capture()` / `get_network_requests()` - 网络监控
- `wait_for_network_idle()` - 等待网络空闲

**对话框（3 个工具）：**
- `setup_dialog_handler(default_action)` - 设置自动对话框处理
- `handle_dialog(accept, prompt_text)` - 处理对话框
- `remove_dialog_handler()` - 移除对话框处理器

**存储（5 个工具）：**
- `get_cookies()` / `set_cookie()` / `clear_cookies()` - Cookie 管理
- `save_storage_state(filename)` / `restore_storage_state(filename)` - 会话持久化

**验证（6 个工具）：**
- `verify_text_visible(text)` - 检查文本可见性
- `verify_element_visible(role, name)` - 通过角色和可访问名称检查元素可见性
- `verify_url(pattern)` / `verify_title(pattern)` - URL/标题验证
- `verify_element_state(ref, state)` - 检查元素状态
- `verify_value(ref, value)` - 检查元素值

**开发者工具（8 个工具）：**
- `start_console_capture()` / `stop_console_capture()` / `get_console_messages()` - 控制台监控
- `start_tracing()` / `stop_tracing()` / `add_trace_chunk()` - 性能追踪
- `start_video()` / `stop_video()` - 视频录制

**生命周期（2 个工具）：**
- `browser_close()` - 停止浏览器（调用 `stop()`）
- `browser_resize(width, height)` - 调整视口大小

### 隐身模式

隐身模式**默认启用**，包括：

- 50+ Chrome 参数禁用自动化检测
- 禁用暴露自动化的特性（`navigator.webdriver` 等）
- 类人的浏览器指纹
- 可选扩展（uBlock Origin、Cookie Consent）用于非无头模式

```python
# 隐身模式默认开启
browser = Browser()  # stealth=True

# 如需禁用隐身模式
browser = Browser(stealth=False)

# 自定义隐身设置
from bridgic.browser.session import create_stealth_config

config = create_stealth_config(
    enable_extensions=False,
    disable_security=True,
)
browser = Browser(stealth=config)
```

### 环境要求

- Python 3.11+
- Playwright 1.57+
- Pydantic 2.11+

### 许可证

MIT 许可证

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## More documentation

- [Browser Tools Guide](docs/BROWSER_TOOLS_GUIDE.md) – Tool selection, ref vs coordinate, wait strategies, patterns.
- [Snapshot and Page State](docs/SNAPSHOT_AND_STATE.md) – SnapshotOptions, EnhancedSnapshot, get_snapshot_text, get_element_by_ref.
- [API Summary](docs/API.md) – Session and DownloadManager API reference.

## Links

- [GitHub Repository](https://github.com/bitsky-tech/bridgic-browser)
- [Issue Tracker](https://github.com/bitsky-tech/bridgic-browser/issues)
