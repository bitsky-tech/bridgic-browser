[English](README.md) | [中文](#bridgic-browser-中文文档)

---

## Bridgic Browser 中文文档

**Bridgic Browser** 是一个基于 [Playwright](https://playwright.dev/) 构建、用于 LLM 驱动浏览器自动化的 Python 库。它提供 CLI 工具、Python 工具，以及面向 AI 智能体的 skills。

### 特性

- **完善的 CLI 工具** — 67 个工具分为 15 类；可与各类 AI 智能体集成
- **基于 Python 的工具** — 用于智能体 / 工作流代码生成；更易与 [Bridgic](https://github.com/bitsky-tech/bridgic) 集成
- **语义不变的快照** — 基于无障碍树与专门设计的 ref 生成算法，保证元素 ref 在页面重载后仍可对应同一元素
- **Skills** — 用于引导探索与代码生成；兼容多数编程类智能体
- **隐身模式（默认开启）** — 模式感知反检测策略：headless 模式使用 50+ Chrome 参数 + JS 补丁；headed 模式仅使用 ~11 个最小 flag，与真实 Chrome 指纹一致
- **持久化与临时会话** — 默认持久化 profile（`~/.bridgic/bridgic-browser/user_data/`）；传入 `clear_user_data=True` 可开启临时会话（无 profile）
- **嵌套 iframe 支持** — 支持在多层嵌套 iframe 内对 DOM 元素进行操作

### 快速开始

#### 与 AI 集成

使用 **Bridgic Browser** 最简单的方式，是配合编程智能体或 AI 助手（例如 Claude Code、Cursor、Codex、OpenClaw）。你可以通过两种方式使用：Skill 或 Plugin。在这两种方式下，Bridgic Browser 都会被自动安装。

**方式 1：让 AI 直接控制浏览器，实时完成任务。**

要使用这种方式，请安装 Bridgic Browser 提供的 Skill：

```bash
npx skills add bitsky-tech/bridgic-browser --skill bridgic-browser
```

安装后，Skill 会出现在你的 agent 目录中（例如 Claude Code 常见为 `.claude/skills/bridgic-browser/`，Cursor 常见为 `.agents/skills/bridgic-browser/`）。

**方式 2：让 AI 以更少 token 生成可复用的浏览器自动化脚本。**

要使用这种方式，请安装 [AmphiLoop](https://github.com/bitsky-tech/AmphiLoop) 提供的 **Plugin**。AmphiLoop 是一套新的 AI 原生工具链与范式，可通过自然语言构建 AI 智能体。

#### 手动安装

```bash
pip install bridgic-browser
```

安装后，安装 Playwright 浏览器：

```bash
playwright install chromium
```

#### CLI 工具用法

```shell
bridgic-browser open --headed https://example.com
bridgic-browser snapshot
# 'f0201d1c' 是「Learn more」链接的 ref
bridgic-browser click f0201d1c
bridgic-browser screenshot page.png
bridgic-browser close
```

#### Python 工具集成

首先构建工具：

```python
from bridgic.browser.session import Browser
from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

# 创建浏览器实例
browser = Browser(headless=False)

async def create_tools(browser):
    # 为智能体构建聚焦工具集
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

其次（可选），构建使用上述工具集的 [Bridgic](https://github.com/bitsky-tech/bridgic) 智能体：

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

#### 浏览器 API 用法

也可以直接调用底层 `Browser` API 控制浏览器。

```python
from bridgic.browser.session import Browser

browser = Browser(headless=False)

async def main():
    await browser.navigate_to("https://example.com")
    snapshot = await browser.get_snapshot()
    print(snapshot.tree)  # 树格式：- role "name" [ref=f0201d1c]
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

### CLI 工具

`bridgic-browser` 提供命令行界面，用于在终端控制浏览器（67 个工具、15 类）。持久化 daemon 进程持有浏览器实例；每次 CLI 调用通过 Unix 域套接字连接后立即退出。

#### 配置

浏览器选项自动从以下来源加载（CLI daemon 和 SDK `Browser()` 共用），优先级从低到高（后者覆盖前者）：

| 来源 | 示例 |
|--------|---------|
| 默认值 | `headless=True`，`clear_user_data=False`（持久化 profile） |
| `~/.bridgic/bridgic-browser/bridgic-browser.json` | 用户级持久配置 |
| `./bridgic-browser.json` | 项目本地配置（daemon 启动时的工作目录） |
| 环境变量 | 见 `skills/bridgic-browser/references/env-vars.md` |

**有界面浏览器说明：**
当 `headless=false` 且启用隐身模式时，bridgic 会自动切换到系统 Chrome（如已安装），以获得更好的反检测效果（Chrome for Testing 会被 Google OAuth 拦截）。
若需覆盖此行为，请设置：
- `channel`：例如 `”chrome”`、`”msedge”`
- `executable_path`：浏览器可执行文件的绝对路径

JSON 来源支持任意 `Browser` 构造参数：

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
# 单次环境变量覆盖
BRIDGIC_BROWSER_JSON='{"headless":false,"locale":"zh-CN"}' bridgic-browser open URL
# 单次临时会话（无持久化 profile）
BRIDGIC_BROWSER_JSON='{"clear_user_data":true}' bridgic-browser open URL
```

#### 命令列表

| 类别 | 命令 |
|----------|----------|
| 导航 | `open`, `back`, `forward`, `reload`, `search`, `info` |
| 快照 | `snapshot [-i] [-f\|-F] [-l N] [-s FILE]` |
| 元素交互 | `click`, `double-click`, `hover`, `focus`, `fill`, `select`, `options`, `check`, `uncheck`, `scroll-to`, `drag`, `upload`, `fill-form` |
| 键盘 | `press`, `type`, `key-down`, `key-up` |
| 鼠标 | `scroll`, `mouse-move`, `mouse-click`, `mouse-drag`, `mouse-down`, `mouse-up` |
| 等待 | `wait [SECONDS] [TEXT] [--gone]` |
| 标签页 | `tabs`, `new-tab`, `switch-tab`, `close-tab` |
| 执行 | `eval`, `eval-on` |
| 捕获 | `screenshot`, `pdf` |
| 网络 | `network-start`, `network-stop`, `network`, `wait-network` |
| 对话框 | `dialog-setup`, `dialog`, `dialog-remove` |
| 存储 | `storage-save`, `storage-load`, `cookies-clear`, `cookies`, `cookie-set` |
| 校验 | `verify-visible`, `verify-text`, `verify-value`, `verify-state`, `verify-url`, `verify-title` |
| 开发者 | `console-start`, `console-stop`, `console`, `trace-start`, `trace-stop`, `trace-chunk`, `video-start`, `video-stop` |
| 生命周期 | `close`, `resize` |

使用 `-h` 或 `--help` 查看任意命令的详细说明：

```bash
bridgic-browser -h
bridgic-browser scroll -h
```

### Python 工具

Bridgic Browser 提供 67 个工具，分为 15 类。使用 `BrowserToolSetBuilder` 按类别/名称选择，以适配不同场景。

#### 按类别选择

```python
from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory

# 针对具体智能体流程的聚焦集合
builder = BrowserToolSetBuilder.for_categories(
    browser,
    ToolCategory.NAVIGATION,
    ToolCategory.ELEMENT_INTERACTION,
    ToolCategory.CAPTURE,
)
tools = builder.build()["tool_specs"]

# 包含全部可用工具
builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.ALL)
tools = builder.build()["tool_specs"]
```

#### 按名称选择（按函数名）

```python
# 按工具函数名选择
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to",
    "click_element_by_ref",
)
tools = builder.build()["tool_specs"]

# 开启 strict 模式，尽早发现拼写错误与缺失的 browser 方法
builder = BrowserToolSetBuilder.for_tool_names(
    browser,
    "search",
    "navigate_to",
    strict=True,
)
tools = builder.build()["tool_specs"]
```

#### 混合选择

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

#### 工具列表

**导航（6 个工具）：**
- `navigate_to(url)` - 导航到 URL
- `search(query, engine)` - 使用搜索引擎搜索
- `get_current_page_info()` - 获取当前页面信息（URL、标题等）
- `reload_page()` - 重新加载当前页面
- `go_back()` / `go_forward()` - 浏览器历史导航

**快照（1 个工具）：**
- `get_snapshot_text(limit=10000, interactive=False, full_page=True, file=None)` - 获取供 LLM 使用的页面状态字符串（带 ref 的无障碍树）。**limit**（默认 10000）控制最多返回的字符数。当快照超过 limit 或显式提供了 **file** 时，完整内容会保存到 **file**（若为 `None` 且超限则自动生成至 `~/.bridgic/bridgic-browser/snapshot/`），仅返回包含文件路径的提示。**interactive** 与 **full_page** 与 `get_snapshot` 一致（仅交互元素或默认全页）。

**元素交互（13 个工具）- 通过 ref：**
- `click_element_by_ref(ref)` - 点击元素
- `input_text_by_ref(ref, text)` - 输入文本
- `fill_form(fields)` - 填写多个表单字段
- `scroll_element_into_view_by_ref(ref)` - 滚动元素到可视区域
- `select_dropdown_option_by_ref(ref, value)` - 选择下拉选项
- `get_dropdown_options_by_ref(ref)` - 获取下拉选项
- `check_checkbox_or_radio_by_ref(ref)` / `uncheck_checkbox_by_ref(ref)` - 复选框控制
- `focus_element_by_ref(ref)` - 聚焦元素
- `hover_element_by_ref(ref)` - 悬停在元素上
- `double_click_element_by_ref(ref)` - 双击
- `upload_file_by_ref(ref, path)` - 上传文件
- `drag_element_by_ref(start_ref, end_ref)` - 拖放

**标签页（4 个工具）：**
- `get_tabs()` / `new_tab(url)` / `switch_tab(page_id)` / `close_tab(page_id)` - 标签页管理

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
- `wait_for(time_seconds, text, text_gone, selector, state, timeout)` - 等待条件

**捕获（2 个工具）：**
- `take_screenshot(filename=None, ref=None, full_page=False, type="png")` - 截图
- `save_pdf(filename)` - 将页面保存为 PDF

**网络（4 个工具）：**
- `start_network_capture()` / `stop_network_capture()` / `get_network_requests()` - 网络监控
- `wait_for_network_idle()` - 等待网络空闲

**对话框（3 个工具）：**
- `setup_dialog_handler(default_action)` - 设置自动对话框处理
- `handle_dialog(accept, prompt_text)` - 处理对话框
- `remove_dialog_handler()` - 移除对话框处理器

**存储（5 个工具）：**
- `get_cookies()` / `set_cookie()` / `clear_cookies()` - Cookie 管理（`expires=0` 合法且会保留）
- `save_storage_state(filename)` / `restore_storage_state(filename)` - 会话持久化

**校验（6 个工具）：**
- `verify_text_visible(text)` - 检查文本可见性
- `verify_element_visible(role, accessible_name)` - 通过角色与可访问名称检查元素可见性
- `verify_url(pattern)` / `verify_title(pattern)` - URL/标题校验
- `verify_element_state(ref, state)` - 检查元素状态
- `verify_value(ref, value)` - 检查元素值

**开发者（8 个工具）：**
- `start_console_capture()` / `stop_console_capture()` / `get_console_messages()` - 控制台监控
- `start_tracing()` / `stop_tracing()` / `add_trace_chunk()` - 性能追踪
- `start_video()` / `stop_video()` - 视频录制

**生命周期（2 个工具）：**
- `close()` - 关闭浏览器
- `browser_resize(width, height)` - 调整视口大小

### CLI 工具与 Python 工具对应关系

| CLI 命令 | SDK 工具方法 |
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

### 核心组件

#### Browser

浏览器自动化的主类，支持自动启动模式选择：

```python
from bridgic.browser.session import Browser

# 持久化会话（默认 — profile 保存至 ~/.bridgic/bridgic-browser/user_data/）
browser = Browser(
    headless=True,
    viewport={"width": 1600, "height": 900},
)

# 持久化会话（自定义 profile 路径）
browser = Browser(
    headless=False,
    user_data_dir="./user_data",
    stealth=True,  # 默认启用
)

# 临时会话（无持久化 profile）
browser = Browser(
    headless=True,
    clear_user_data=True,
)
```

**主要参数：**

| 参数 | 类型 | 默认值 | 描述 |
|-----------|------|---------|-------------|
| `headless` | bool | True | 无头模式运行 |
| `viewport` | dict | 1600x900 | 浏览器视口大小 |
| `user_data_dir` | str/Path | None | 持久化 profile 自定义路径（`clear_user_data=True` 时忽略） |
| `clear_user_data` | bool | False | True 时使用临时会话（无 profile）；False 时使用持久化 profile |
| `stealth` | bool/StealthConfig | True | 隐身模式配置 |
| `channel` | str | None | 浏览器通道（chrome、msedge 等） |
| `proxy` | dict | None | 代理设置 |
| `downloads_path` | str/Path | None | 下载目录 |

**快照：** 使用 `get_snapshot(interactive=False, full_page=True)` 获取 `EnhancedSnapshot`，含 `.tree`（无障碍树字符串）和 `.refs`（ref → 定位数据）。默认 `full_page=True` 包含视口内外全部元素。`interactive=True` 仅包含可点击/可编辑元素（扁平输出），`full_page=False` 仅包含视口内元素。使用 `get_element_by_ref(ref)` 根据 ref（如 "1f79fe5e"）获取 Playwright Locator 后进行 click、fill 等操作。

#### StealthConfig

配置隐身模式以绕过机器人检测：

```python
from bridgic.browser.session import StealthConfig, Browser

# 自定义隐身配置
config = StealthConfig(
    enabled=True,
    disable_security=False,
)

browser = Browser(stealth=config, headless=False)
```

#### DownloadManager

处理文件下载，正确保留文件名：

```python
# 将 downloads_path 传给 Browser — 它会内部创建并管理 DownloadManager
browser = Browser(downloads_path="./downloads", headless=True)
await browser.navigate_to("https://example.com")  # 懒加载，首次导航时自动启动

# 通过内置管理器访问已下载的文件
for file in browser.download_manager.downloaded_files:
    print(f"已下载：{file.file_name}（{file.file_size} 字节）")
```

### 隐身模式

隐身模式**默认启用**，包括：

- **Headless 模式**：50+ Chrome 参数 + JS init script，修补 `navigator.webdriver`、`window.chrome`、WebGL、`document.hasFocus()`、`visibilityState` 等。所有被修补的函数均通过 `Function.prototype.toString` 欺骗返回 `[native code]`。
- **Headed 模式**：仅使用 ~11 个最小 flag（与真实 Chrome 一致），完全跳过 JS 补丁注入，确保 Cloudflare Turnstile 等第三方 challenge iframe 看到未经修改的原生 API。

```python
# 隐身默认开启
browser = Browser()  # stealth=True

# 如需关闭隐身
browser = Browser(stealth=False)

# 自定义隐身设置
from bridgic.browser.session import create_stealth_config

config = create_stealth_config(
    disable_security=True,
)
browser = Browser(stealth=config)
```

### 错误模型

SDK 与 CLI 共享统一的结构化错误协议。

- 基类：`BridgicBrowserError`
- 稳定字段：`code`、`message`、`details`、`retryable`
- 行为型子类：
  - `InvalidInputError`（参数/用户输入无效）
  - `StateError`（运行状态无效，例如无活动 page/session）
  - `OperationError`（操作执行失败）
  - `VerificationError`（断言/校验失败）

保留少量行为型子类的原因：

- 调用方可按需按行为捕获（例如仅对 `StateError` 重试）
- 在错误来源附近编码默认重试语义
- 避免庞大且难维护的类层次，同时保持错误处理可预期

Daemon 协议同样结构化：

- 成功：`{"success": true, "result": "..."}`
- 失败：`{"success": false, "error_code": "...", "result": "...", "data": {...}, "meta": {"retryable": false}}`

CLI 客户端将 daemon 失败转换为 `BridgicBrowserCommandError`，CLI 输出仍保留机器可读错误码：`Error[CODE]: ...`。

### 环境要求

- Python 3.10+
- Playwright 1.57+
- Pydantic 2.11+

### 社区

欢迎加入我们，反馈建议、交流问题、获取最新动态：

- 🐦 Twitter / X：[@bridgic](https://x.com/bridgic)
- 💬 Discord：[加入我们的服务器](https://discord.gg/5rQYnTKNCd)

### 许可证

MIT 许可证

## 更多文档

- [浏览器工具指南](docs/BROWSER_TOOLS_GUIDE.md) — 工具选择、ref 与坐标、等待策略、常见模式。
- [快照与页面状态](docs/SNAPSHOT_AND_STATE.md) — SnapshotOptions、EnhancedSnapshot、get_snapshot_text、get_element_by_ref。
- [API 摘要](docs/API.md) — Session 与 DownloadManager API 说明。
