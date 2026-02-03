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

        # Get page snapshot for LLM
        snapshot = await browser.get_snapshot()
        print(snapshot.tree)  # Accessible element tree with refs

        # Interact with elements by ref
        element = await browser.get_element_by_ref("e1")
        if element:
            await element.click()
    finally:
        await browser.close()

asyncio.run(main())
```

#### Using with AI Agents

```python
from bridgic.browser.session import Browser
from bridgic.browser.tools import BrowserToolSetBuilder

async def create_agent():
    browser = Browser(headless=False)
    await browser.start()

    # Build tool set for your agent
    tools = BrowserToolSetBuilder.basic_tools(browser)

    # Use tools with your LLM agent
    # tools include: navigate, click, input_text, scroll, etc.
    return browser, tools
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
from bridgic.browser.session import DownloadManager, DownloadManagerConfig

config = DownloadManagerConfig(
    downloads_path="./downloads",
    auto_save=True,
    overwrite_existing=False,
)

manager = DownloadManager(config=config)

# Attach to browser context
manager.attach_to_context(browser.context)

# Access downloaded files
for file in manager.downloaded_files:
    print(f"Downloaded: {file.file_name} ({file.file_size} bytes)")
```

### Browser Tools

Bridgic Browser provides 68+ tools organized into categories. Use `BrowserToolSetBuilder` with `ToolPreset` for scenario-based tool selection.

#### Quick Start with Presets

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

# Choose a preset for your use case
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)       # 10 tools
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)  # 20 tools
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)       # 28 tools
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.COMPLETE)      # 68 tools
```

#### Available Presets

| Preset | Tools | Description |
|--------|-------|-------------|
| `MINIMAL` | 10 | Navigate, click, input, snapshot |
| `NAVIGATION` | 4 | Search, navigate, back/forward |
| `SCRAPING` | 13 | Navigation + snapshot + scroll |
| `FORM_FILLING` | 20 | Navigation + input + dropdown + checkbox |
| `TESTING` | 28 | Form filling + verification + screenshot |
| `INTERACTIVE` | 40 | All action tools + mouse + keyboard |
| `DEVELOPER` | 18 | Network + console + tracing |
| `COMPLETE` | 68 | All available tools |

#### Category-based Selection

```python
# Select by category
tools = BrowserToolSetBuilder.for_categories(
    browser, "navigation", "action", "screenshot"
)
```

#### Fluent Builder for Custom Selection

```python
from bridgic.browser.tools import take_screenshot, verify_url

tools = (BrowserToolSetBuilder(browser)
    .with_preset(ToolPreset.MINIMAL)
    .with_category("screenshot")
    .with_tools(verify_url)
    .without_tools("go_forward")
    .build_specs())
```

#### Tool Categories

**Navigation (4 tools):**
- `search(query, engine)` - Search using search engine
- `navigate_to_url(url)` - Navigate to URL
- `go_back()` / `go_forward()` - Browser history navigation

**Page (9 tools):**
- `reload_page()` - Reload current page
- `scroll_to_text(text)` - Scroll to text
- `press_key(key)` - Press keyboard key
- `evaluate_javascript(code)` - Execute JavaScript
- `get_current_page_info()` - Get current page info
- `new_tab(url)` / `get_tabs()` / `switch_tab(tab_id)` / `close_tab(tab_id)` - Tab management

**Action (13 tools) - Element interaction by ref:**
- `click_element_by_ref(ref)` - Click element
- `input_text_by_ref(ref, text)` - Input text
- `hover_element_by_ref(ref)` - Hover over element
- `focus_element_by_ref(ref)` - Focus element
- `double_click_element_by_ref(ref)` - Double click
- `scroll_element_into_view_by_ref(ref)` - Scroll element into view
- `drag_element_by_ref(start_ref, end_ref)` - Drag and drop

**Form (7 tools):**
- `get_dropdown_options_by_ref(ref)` - Get dropdown options
- `select_dropdown_option_by_ref(ref, value)` - Select dropdown option
- `check_element_by_ref(ref)` / `uncheck_element_by_ref(ref)` - Checkbox control
- `upload_file_by_ref(ref, path)` - Upload file
- `fill_form(fields)` - Fill multiple form fields

**Mouse (6 tools) - Coordinate-based:**
- `mouse_move(x, y)` - Move mouse
- `mouse_click(x, y)` - Click at position
- `mouse_drag(start_x, start_y, end_x, end_y)` - Drag operation
- `mouse_down()` / `mouse_up()` - Mouse button control
- `mouse_wheel(delta_x, delta_y)` - Scroll wheel

**Keyboard (5 tools):**
- `press_sequentially(text)` - Type text character by character
- `key_down(key)` / `key_up(key)` - Key control
- `insert_text(text)` - Insert text at cursor
- `fill_form(fields)` - Fill form fields

**Screenshot (2 tools):**
- `take_screenshot(type, filename)` - Capture screenshot
- `save_pdf(filename)` - Save page as PDF

**Network (5 tools):**
- `start_console_capture()` / `get_console_messages()` - Console monitoring
- `start_network_capture()` / `get_network_requests()` - Network monitoring
- `wait_for_network_idle()` - Wait for network idle

**Dialog (3 tools):**
- `setup_dialog_handler(default_action)` - Set up auto dialog handler
- `handle_dialog(accept, prompt_text)` - Handle dialog
- `remove_dialog_handler()` - Remove dialog handler

**Storage (5 tools):**
- `save_storage_state(filename)` / `restore_storage_state(filename)` - Session persistence
- `clear_cookies()` / `get_cookies()` / `set_cookie()` - Cookie management

**Verify (6 tools):**
- `verify_element_visible(ref)` - Check element visibility
- `verify_text_visible(text)` - Check text visibility
- `verify_value(ref, value)` - Check element value
- `verify_element_state(ref, state)` - Check element state
- `verify_url(pattern)` / `verify_title(pattern)` - URL/title verification

**DevTools (5 tools):**
- `start_tracing()` / `stop_tracing()` - Performance tracing
- `start_video()` / `stop_video()` - Video recording
- `add_trace_chunk()` - Add trace data

**Control (3 tools):**
- `browser_close()` - Close browser
- `browser_resize(width, height)` - Resize viewport
- `wait_for(time, text, text_gone)` - Wait for conditions

**State (1 tool):**
- `get_llm_repr()` - Get page snapshot for LLM

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

        # 获取页面快照供 LLM 使用
        snapshot = await browser.get_snapshot()
        print(snapshot.tree)  # 带引用的可访问元素树

        # 通过引用与元素交互
        element = await browser.get_element_by_ref("e1")
        if element:
            await element.click()
    finally:
        await browser.close()

asyncio.run(main())
```

#### 与 AI 智能体配合使用

```python
from bridgic.browser.session import Browser
from bridgic.browser.tools import BrowserToolSetBuilder

async def create_agent():
    browser = Browser(headless=False)
    await browser.start()

    # 为智能体构建工具集
    tools = BrowserToolSetBuilder.basic_tools(browser)

    # 将工具与 LLM 智能体配合使用
    # 工具包括：导航、点击、输入文本、滚动等
    return browser, tools
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
from bridgic.browser.session import DownloadManager, DownloadManagerConfig

config = DownloadManagerConfig(
    downloads_path="./downloads",
    auto_save=True,
    overwrite_existing=False,
)

manager = DownloadManager(config=config)

# 附加到浏览器上下文
manager.attach_to_context(browser.context)

# 访问已下载的文件
for file in manager.downloaded_files:
    print(f"已下载：{file.file_name}（{file.file_size} 字节）")
```

### 浏览器工具

Bridgic Browser 提供 68+ 个工具，按类别组织。使用 `BrowserToolSetBuilder` 配合 `ToolPreset` 进行场景化工具选择。

#### 使用预设快速开始

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

# 根据使用场景选择预设
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)       # 10 个工具
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)  # 20 个工具
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)       # 28 个工具
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.COMPLETE)      # 68 个工具
```

#### 可用预设

| 预设 | 工具数 | 描述 |
|------|--------|------|
| `MINIMAL` | 10 | 导航、点击、输入、快照 |
| `NAVIGATION` | 4 | 搜索、导航、前进/后退 |
| `SCRAPING` | 13 | 导航 + 快照 + 滚动 |
| `FORM_FILLING` | 20 | 导航 + 输入 + 下拉框 + 复选框 |
| `TESTING` | 28 | 表单填写 + 验证 + 截图 |
| `INTERACTIVE` | 40 | 所有交互工具 + 鼠标 + 键盘 |
| `DEVELOPER` | 18 | 网络 + 控制台 + 追踪 |
| `COMPLETE` | 68 | 所有可用工具 |

#### 按类别选择

```python
# 按类别选择工具
tools = BrowserToolSetBuilder.for_categories(
    browser, "navigation", "action", "screenshot"
)
```

#### 流式构建器自定义选择

```python
from bridgic.browser.tools import take_screenshot, verify_url

tools = (BrowserToolSetBuilder(browser)
    .with_preset(ToolPreset.MINIMAL)
    .with_category("screenshot")
    .with_tools(verify_url)
    .without_tools("go_forward")
    .build_specs())
```

#### 工具类别

**导航（4 个工具）：**
- `search(query, engine)` - 使用搜索引擎搜索
- `navigate_to_url(url)` - 导航到 URL
- `go_back()` / `go_forward()` - 浏览器历史导航

**页面（9 个工具）：**
- `reload_page()` - 重新加载当前页面
- `scroll_to_text(text)` - 滚动到指定文本
- `press_key(key)` - 按下键盘键
- `evaluate_javascript(code)` - 执行 JavaScript
- `get_current_page_info()` - 获取当前页面信息
- `new_tab(url)` / `get_tabs()` / `switch_tab(tab_id)` / `close_tab(tab_id)` - 标签页管理

**动作（13 个工具）- 通过引用操作元素：**
- `click_element_by_ref(ref)` - 点击元素
- `input_text_by_ref(ref, text)` - 输入文本
- `hover_element_by_ref(ref)` - 悬停在元素上
- `focus_element_by_ref(ref)` - 聚焦元素
- `double_click_element_by_ref(ref)` - 双击
- `scroll_element_into_view_by_ref(ref)` - 滚动元素到可视区域
- `drag_element_by_ref(start_ref, end_ref)` - 拖放

**表单（7 个工具）：**
- `get_dropdown_options_by_ref(ref)` - 获取下拉选项
- `select_dropdown_option_by_ref(ref, value)` - 选择下拉选项
- `check_element_by_ref(ref)` / `uncheck_element_by_ref(ref)` - 复选框控制
- `upload_file_by_ref(ref, path)` - 上传文件
- `fill_form(fields)` - 填写多个表单字段

**鼠标（6 个工具）- 基于坐标：**
- `mouse_move(x, y)` - 移动鼠标
- `mouse_click(x, y)` - 在指定位置点击
- `mouse_drag(start_x, start_y, end_x, end_y)` - 拖动操作
- `mouse_down()` / `mouse_up()` - 鼠标按钮控制
- `mouse_wheel(delta_x, delta_y)` - 滚轮

**键盘（5 个工具）：**
- `press_sequentially(text)` - 逐字符输入文本
- `key_down(key)` / `key_up(key)` - 按键控制
- `insert_text(text)` - 在光标处插入文本
- `fill_form(fields)` - 填写表单字段

**截图（2 个工具）：**
- `take_screenshot(type, filename)` - 截取屏幕截图
- `save_pdf(filename)` - 保存页面为 PDF

**网络（5 个工具）：**
- `start_console_capture()` / `get_console_messages()` - 控制台监控
- `start_network_capture()` / `get_network_requests()` - 网络监控
- `wait_for_network_idle()` - 等待网络空闲

**对话框（3 个工具）：**
- `setup_dialog_handler(default_action)` - 设置自动对话框处理
- `handle_dialog(accept, prompt_text)` - 处理对话框
- `remove_dialog_handler()` - 移除对话框处理器

**存储（5 个工具）：**
- `save_storage_state(filename)` / `restore_storage_state(filename)` - 会话持久化
- `clear_cookies()` / `get_cookies()` / `set_cookie()` - Cookie 管理

**验证（6 个工具）：**
- `verify_element_visible(ref)` - 检查元素可见性
- `verify_text_visible(text)` - 检查文本可见性
- `verify_value(ref, value)` - 检查元素值
- `verify_element_state(ref, state)` - 检查元素状态
- `verify_url(pattern)` / `verify_title(pattern)` - URL/标题验证

**开发者工具（5 个工具）：**
- `start_tracing()` / `stop_tracing()` - 性能追踪
- `start_video()` / `stop_video()` - 视频录制
- `add_trace_chunk()` - 添加追踪数据

**控制（3 个工具）：**
- `browser_close()` - 关闭浏览器
- `browser_resize(width, height)` - 调整视口大小
- `wait_for(time, text, text_gone)` - 等待条件

**状态（1 个工具）：**
- `get_llm_repr()` - 获取供 LLM 使用的页面快照

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

## Links

- [GitHub Repository](https://github.com/bitsky-tech/bridgic-browser)
- [Documentation](https://bridgic.dev)
- [Issue Tracker](https://github.com/bitsky-tech/bridgic-browser/issues)
