# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Bridgic Browser** is an LLM-driven browser automation library built on Playwright with built-in stealth mode. It provides 67 browser tools organized into categories, an accessibility tree-based snapshot system, a stable element reference system (refs like "1f79fe5e", "8d4b03a9", …) designed for reliable AI agent interactions, and a `bridgic-browser` CLI tool backed by a persistent daemon.

## Commands

**Setup** (first time):
```bash
make init-dev          # Create .venv, install deps, install Playwright browsers
```

**Testing**:
```bash
make test-quick        # Run unit tests only (fast, no wheel rebuild)
make test              # Run all tests via wheel install (slower, simulates real install)
make test-integration  # Run integration tests only (requires real browser)

# Run a single test file or test:
uv run pytest tests/unit/test_snapshot_parse.py -v
uv run pytest tests/unit/test_tools.py::test_name -v
```

**Build & publish**:
```bash
make build                                    # Build only
make publish version=0.1.0 repo=testpypi      # Full release: version check → test → build → publish
./scripts/release.sh 0.1.0 pypi               # Or call release.sh directly
```

**Playwright browser binaries**:
```bash
make playwright-install
```

**Stealth extensions** (maintainers only, commit result):
```bash
make download-extensions   # downloads + packs all extensions into bridgic/browser/extensions/extensions.zip
```

## Architecture

### Package structure

```
bridgic/browser/
├── __main__.py       # Entry point: routes `daemon` subcommand vs CLI
├── session/          # Core browser session
│   ├── _browser.py       # Browser class – main entry point
│   ├── _snapshot.py      # SnapshotGenerator + EnhancedSnapshot + RefData
│   ├── _stealth.py       # StealthConfig + StealthArgsBuilder (50+ Chrome args)
│   ├── _download.py      # DownloadManager
│   └── _browser_model.py # Data models
├── tools/            # 67 automation tools (all implemented in _browser.py)
│   ├── _browser_tool_set_builder.py  # BrowserToolSetBuilder (category/name selection)
│   └── _browser_tool_spec.py         # BrowserToolSpec (wraps tool for agents)
└── cli/              # CLI tool (bridgic-browser command)
    ├── __init__.py       # Exports main()
    ├── _commands.py      # Click command definitions (68 commands incl. utility metadata command, SectionedGroup)
    ├── _client.py        # Socket client: send_command(), ensure_daemon_running()
    └── _daemon.py        # Daemon: asyncio Unix socket server + Browser instance
```

### Core data flow

1. **`Browser`** (`session/_browser.py`) — instantiate and `await start()`. Auto-selects:
   - Isolated mode: `launch()` + `new_context()` (default, no `user_data_dir`)
   - Persistent mode: `launch_persistent_context(user_data_dir)` (preserves cookies/session)

2. **`await browser.get_snapshot()`** → returns `EnhancedSnapshot`:
   - `.tree: str` — accessibility tree lines like `- button "Submit" [ref=8d4b03a9]`
   - `.refs: Dict[str, RefData]` — maps ref IDs to locator data

3. **`await browser.get_element_by_ref(ref)`** → returns a Playwright `Locator` resolved from the snapshot refs dict.

4. **Tools** are bound async methods on the `Browser` class. Pass them to an LLM agent via `BrowserToolSetBuilder`.

### Element reference system

Refs (like `1f79fe5e`, `8d4b03a9`, …) are generated during snapshot and stored in `EnhancedSnapshot.refs`. They are the stable, accessibility-aware identifiers used by all `*_by_ref` tools. When a page changes, call `get_snapshot()` again to refresh refs.

### Tool selection

`BrowserToolSetBuilder` supports multiple selection strategies:

```python
# By category
builder = BrowserToolSetBuilder.for_categories(browser, "navigation", "element_interaction")
tools = builder.build()["tool_specs"]

# By tool name
builder = BrowserToolSetBuilder.for_tool_names(
    browser, "click_element_by_ref", "input_text_by_ref"
)
tools = builder.build()["tool_specs"]

# Combine multiple for_* selections
builder1 = BrowserToolSetBuilder.for_categories(browser, "navigation", "element_interaction", "capture")
builder2 = BrowserToolSetBuilder.for_tool_names(browser, "verify_url")
tools = [*builder1.build()["tool_specs"], *builder2.build()["tool_specs"]]
```

### Snapshot modes

`get_snapshot(interactive=False, full_page=True)`:
- `interactive=True` — flattened list of clickable/editable elements only (best for LLM action selection)
- `full_page=False` — limit to viewport content only
- `await browser.get_snapshot_text(...)` — returns a truncated string ready for LLM context, with pagination via `start_from_char`

### Stealth

`StealthConfig` (default enabled) applies 50+ Chrome arguments to evade bot detection, plus a JS init script injected into every page via `context.add_init_script()`.

Key options:
- `enable_extensions=True` requires `headless=False` (Playwright's headless-shell does not support extensions)
- `docker_mode=True` for container environments
- `cookie_whitelist_domains` for selective cookie retention

**System Chrome vs extensions trade-off**:
System Chrome (v137+) removed `--load-extension` CLI support, so extensions only work with Playwright's bundled "Chrome for Testing" (the default). The CLI daemon does NOT auto-switch to system Chrome — it uses Playwright's default browser to keep extensions working. Users who explicitly set `channel="chrome"` or `executable_path` in config get the system Chrome Dock icon but lose extension loading.

**JS init script** (`_STEALTH_INIT_SCRIPT_TEMPLATE` in `_stealth.py`) patches these navigator/window properties before any page script runs:
- `navigator.webdriver` → `undefined`
- `navigator.plugins` / `navigator.mimeTypes` → realistic PDF Viewer entries (5 plugins, 2 MIME types); each plugin holds its own per-plugin mime copies so `enabledPlugin` refs are correct
- `navigator.languages` → derived from `Browser(locale=...)` to keep `navigator.language === navigator.languages[0]` (e.g. `["zh-CN", "zh", "en"]` for `locale="zh-CN"`); defaults to `["en-US", "en"]`
- `window.chrome` → complete object with `runtime`, `csi()`, `loadTimes()`
- `navigator.permissions.query` → returns `"default"` for notifications (not `"denied"`)
- `window.outerWidth/Height` → matches `innerWidth/Height` (fixes headless zero-value)

`get_init_script(locale=None)` accepts the locale and performs the `__BRIDGIC_LANGS__` substitution before returning the script. Called from `_browser.py:start()` with `self._locale`.

**Extensions** (headed mode only, all MV3):
- uBlock Origin Lite — content/ad blocking
- I don't care about cookies — auto-dismiss cookie banners
- Force Background Tab — prevent new-tab focus stealing

Extensions are bundled as `bridgic/browser/extensions/extensions.zip` (shipped with the package). On first use they are extracted to `~/.cache/bridgic-browser/extensions/<id>/`. Subsequent launches reuse the cache. Network download only happens if the zip is absent.

### CLI architecture

The `bridgic-browser` CLI uses a **daemon + Unix socket** pattern so the Playwright `Browser` instance persists across multiple short-lived CLI invocations.

```
bridgic-browser click @8d4b03a9
       │
       ▼
  _client.py                 Unix socket                   _daemon.py
 send_command("click",...)   ~/.bridgic/run/bridgic-browser.sock    asyncio server
       │──── JSON request ─────────────────────────────►  + Browser instance
       │◄─── JSON response ────────────────────────────   dispatch → tool fn()
```

Key implementation details:
- **`_client.py`**: `send_command()` auto-starts the daemon if no socket exists. `_spawn_daemon()` uses `select.select()` + `os.read()` for the 30-second ready timeout (avoids blocking `proc.stdout.read()`). `start_if_needed=False` prevents auto-start for the `close` command.
- **`_daemon.py`**: `run_daemon()` calls `_build_browser_kwargs()` then launches `Browser(**kwargs)`, writes `BRIDGIC_DAEMON_READY` to stdout, and serves one JSON command per connection. `asyncio.wait_for(reader.readline(), timeout=60)` prevents hanging on idle connections. Signal handling uses `loop.add_signal_handler()` (asyncio-safe).
- **`_commands.py`**: 67 Click commands in 15 sections via `SectionedGroup`. `scroll` uses `--dy`/`--dx` options (not positional) to support negative values. `screenshot`/`pdf`/`upload`/`storage-save`/`storage-load`/`trace-stop` call `os.path.abspath()` on the client side before sending (daemon cwd may differ). `snapshot` supports `-i`/`--interactive`, `-f/-F`/`--full-page/--no-full-page`, and `-s`/`--start-from-char`; it delegates to `browser.get_snapshot_text()` (which adds truncation/pagination).
- **`_build_browser_kwargs()`** priority chain (lowest → highest): defaults → `~/.bridgic/bridgic-browser.json` → `./bridgic-browser.json` → `BRIDGIC_BROWSER_JSON` env var → `BRIDGIC_HEADLESS` env var.
- **`close` command fast-path**: the daemon calls `browser.inspect_pending_close_artifacts()` to pre-allocate a session dir and trace path, responds to the client immediately with those paths, then sets `stop_event`. Actual `browser.stop()` runs after the client disconnects. After stop, `_write_close_report()` writes `~/.bridgic/tmp/close-<timestamp>-<rand>/close-report.json` with status, artifact paths, and any errors.

Socket path: `BRIDGIC_SOCKET` env var (default `~/.bridgic/run/bridgic-browser.sock`).
The directory is created with `0o700` permissions on first use. Users upgrading from an older version that used `/tmp/bridgic-browser.sock` should stop any running daemon first (`bridgic-browser close`) before upgrading.

Snapshot truncation limit: `BRIDGIC_MAX_CHARS` env var (default `30000`).

## Key Implementation Details & Playwright Internals

### 两套 ref 系统并存（理解整个链路的基础）

bridgic 里同时存在**两套互不相同的 ref**，必须区分清楚：

| | bridgic ref | playwright_ref |
|---|---|---|
| 例子 | `"8d4b03a9"` | `"e369"` / `"f1e5"` |
| 生成位置 | `_snapshot.py:_compute_stable_ref()` | Playwright 注入脚本 `computeAriaRef()` |
| 格式 | SHA-256(namespace+role+name+frame_path+nth) 前4字节 hex | `{refPrefix}e{lastRef}` 递增整数 |
| 稳定性 | **跨快照稳定**（同元素同 ref）| **每次快照后重置**（仅在本次 snapshotForAI 有效）|
| 用途 | 暴露给 LLM / 工具调用 / CLI | aria-ref 快速路径的 O(1) DOM 指针查找 |
| 存储 | `EnhancedSnapshot.refs: Dict[str, RefData]` | `RefData.playwright_ref` |

---

### Playwright 源码：ref 生成规则

所有源码路径均在 `.venv/lib/python3.10/site-packages/playwright/driver/package/lib/` 下。

#### 1. `lastRef` 计数器与 `computeAriaRef()`
**文件**：`generated/injectedScriptSource.js`（此文件是注入到每个 frame 的脚本，每个 frame 有独立实例）

```javascript
// injectedScriptSource.js — 注入脚本模块级变量（每 frame 独立）
var lastRef = 0;

function computeAriaRef(ariaNode, options) {
  if (options.refs === "none") return;
  // mode="ai" 时 refs="interactable"，只给可见且可接收指针事件的元素分配 ref
  if (options.refs === "interactable" && (!ariaNode.box.visible || !ariaNode.receivesPointerEvents))
    return;

  let ariaRef = ariaNode.element._ariaRef;  // DOM 元素上的缓存
  if (!ariaRef || ariaRef.role !== ariaNode.role || ariaRef.name !== ariaNode.name) {
    // 缓存失效（首次 / role 或 name 变化）→ 生成新 ref
    ariaRef = {
      role: ariaNode.role,
      name: ariaNode.name,
      ref: (options.refPrefix ?? "") + "e" + ++lastRef   // ← 核心格式
    };
    ariaNode.element._ariaRef = ariaRef;  // 写回 DOM 元素
  }
  ariaNode.ref = ariaRef.ref;
}
```

**关键规则**：
- `lastRef` 是模块级整数，**在同一 frame 注入脚本实例的整个生命周期内单调递增，永不重置**
- 同一元素若 role+name 未变，**复用上次 ref**（`element._ariaRef` 缓存），不递增 `lastRef`
- ref 格式：`{refPrefix}e{lastRef}`，例如 `"e1"`, `"e5"`, `"f1e3"`, `"f2e7"`
- `refPrefix` 由调用方传入（见下一节）

#### 2. `refPrefix` 的来源：frame.seq
**文件**：`server/page.js:825`（`snapshotFrameForAI` 函数）

```javascript
// page.js — snapshotFrameForAI()
injectedScript.evaluate((injected, options) => {
  return injected.incrementalAriaSnapshot(node, { mode: "ai", ...options });
}, {
  refPrefix: frame.seq ? "f" + frame.seq : "",  // ← 主 frame seq=0 → "", 子 frame seq=N → "fN"
  track: options.track
});
```

**文件**：`server/frames.js:368`（Frame 构造函数）

```javascript
// frames.js — Frame constructor
this.seq = page.frameManager.nextFrameSeq();
// 主 frame seq=0；后续每个新 frame seq 递增：1, 2, 3...
// seq 不等于"第几个 iframe"，是全局唯一序号
```

**格式总结**：
- 主 frame（seq=0）：`refPrefix=""` → ref 为 `"e1"`, `"e2"`, …
- 子 frame（seq=1）：`refPrefix="f1"` → ref 为 `"f1e1"`, `"f1e2"`, …
- 子 frame（seq=2）：`refPrefix="f2"` → ref 为 `"f2e1"`, `"f2e3"`, …
- **注意**：seq 是页面级全局计数，与 iframe 在 DOM 中的位置无关

#### 3. `snapshot.elements` Map 的构建
**文件**：`generated/injectedScriptSource.js`（`generateAriaTree` 函数内的 `visit` 回调）

```javascript
// injectedScriptSource.js — generateAriaTree > visit()
if (childAriaNode.ref) {
  snapshot.elements.set(childAriaNode.ref, element);  // ref → DOM Element
  snapshot.refs.set(element, childAriaNode.ref);       // DOM Element → ref（反向）
  if (childAriaNode.role === "iframe")
    snapshot.iframeRefs.push(childAriaNode.ref);       // iframe 单独收集，用于递归子快照
}
```

#### 4. `_lastAriaSnapshotForQuery` 的写入
**文件**：`generated/injectedScriptSource.js`（`InjectedScript.incrementalAriaSnapshot()` 方法）

```javascript
// injectedScriptSource.js — InjectedScript class
incrementalAriaSnapshot(node, options) {
  const ariaSnapshot = generateAriaTree(node, options);
  // ...
  this._lastAriaSnapshotForQuery = ariaSnapshot;  // ← 每次快照后覆盖写入
  return { full, incremental, iframeRefs: ariaSnapshot.iframeRefs };
}
```

**关键**：`_lastAriaSnapshotForQuery` 是每个 frame 注入脚本实例上的属性，**各 frame 完全独立**。L1 frame 的注入脚本只持有 L1 的 `elements` Map（key 为 `"f1e1"` 等）。

---

### Playwright 源码：ref 查找规则

#### 5. aria-ref 引擎：`_createAriaRefEngine()`
**文件**：`generated/injectedScriptSource.js`（`InjectedScript` 构造函数注册）

```javascript
// injectedScriptSource.js — _createAriaRefEngine()
_createAriaRefEngine() {
  const queryAll = (root, selector) => {
    const result = this._lastAriaSnapshotForQuery?.elements?.get(selector);
    // selector = "aria-ref=" 后面的原始字符串，如 "e369" 或 "f1e5"
    return result && result.isConnected ? [result] : [];
    // isConnected 检查：元素已从 DOM 移除时返回空（stale 情况）
  };
  return { queryAll };
}
```

O(1) Map 查找，`isConnected` 保证 stale ref 返回空而非报错。

#### 6. `_jumpToAriaRefFrameIfNeeded()`：跨 frame 路由
**文件**：`server/frameSelectors.js:85`

```javascript
// frameSelectors.js — FrameSelectors class
_jumpToAriaRefFrameIfNeeded(selector, info, frame) {
  if (info.parsed.parts[0].name !== "aria-ref") return frame;
  const body = info.parsed.parts[0].body;          // "f1e5" 或 "e369"
  const match = body.match(/^f(\d+)e\d+$/);        // 只匹配子 frame ref（有 "f" 前缀）
  if (!match) return frame;                          // 主 frame ref → 不跳转
  const frameSeq = +match[1];                       // 提取 seq 号
  const jumptToFrame = this.frame._page.frameManager.frames()
    .find(frame2 => frame2.seq === frameSeq);        // 全局线性搜索
  if (!jumptToFrame)
    throw new InvalidSelectorError(...);
  return jumptToFrame;
}
```

**重要**：`_jumpToAriaRefFrameIfNeeded` 在执行 `queryAll` **之前**切换执行目标 frame，使得查询运行在正确 frame 的注入脚本上下文中（该 frame 的 `_lastAriaSnapshotForQuery` 才持有对应 key）。

**这意味着**：从 element resolution 角度，`page.locator("aria-ref=f1e5")` 和 `frame_locator("iframe").nth(0).locator("aria-ref=f1e5")` 都能正确找到 L1 frame 的元素，因为 `_jumpToAriaRefFrameIfNeeded` 会自动跳转。但 `locator.evaluate()` 的 JS 执行上下文**不受此影响**——它始终在 locator **所属 scope 的 frame** 中执行（见下文）。

---

### bridgic 源码：ref 生成规则

#### 7. bridgic ref（稳定 ID）的生成
**文件**：`bridgic/browser/session/_snapshot.py`

```python
# _snapshot.py:394
_REF_NAMESPACE = "bridgic-browser-v1"

# _snapshot.py:422 — _compute_stable_ref()
@staticmethod
def _compute_stable_ref(role, name, frame_path, nth) -> str:
    frame_str = ",".join(str(x) for x in frame_path) if frame_path else ""
    raw = f"{_REF_NAMESPACE}\x1f{role}\x1f{name or ''}\x1f{frame_str}\x1f{nth}"
    # \x1f (ASCII Unit Separator) 作为字段分隔符，不可能出现在 HTML accessible name 中
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return digest[:4].hex()   # 8 个 hex 字符，例如 "8d4b03a9"
```

**稳定性保证**：只要 role、name、frame_path、nth 四个字段不变，同一元素在不同快照中始终得到相同 ref ID，LLM 可以跨快照持续使用。

#### 8. `playwright_ref` 的提取与存储
**文件**：`bridgic/browser/session/_snapshot.py`

```python
# _snapshot.py:374
_REF_EXTRACT_PATTERN = re.compile(r'\[ref=([a-zA-Z0-9]+)\]')

# _snapshot.py:1400-1491 — _process_page_snapshot_for_ai() 解析循环
# 在 clean_suffix 清除 [ref=...] 之前提取：
_pw_ref_match = ref_extract_pattern.search(suffix) if suffix else None
playwright_ref_for_element = _pw_ref_match.group(1) if _pw_ref_match else None

# 存入 RefData：
refs[ref] = RefData(
    ...
    playwright_ref=playwright_ref_for_element,   # Playwright 的 "e369" / "f1e5"
)
```

`playwright_ref` 从 Playwright 快照文本行的 `[ref=...]` 后缀中提取，仅在本次 `snapshotForAI` 的生命周期内有效。

#### 9. `frame_path` 的生成
**文件**：`bridgic/browser/session/_snapshot.py:1229`（解析循环）

```python
# _snapshot.py — _process_page_snapshot_for_ai()
_iframe_local_counters: Dict[tuple, int] = {}   # key=父路径tuple, value=已遇到的子iframe数
# ...
# 遇到 iframe 节点时：
parent_path = tuple(iframe_stack[-1][1]) if iframe_stack else ()
local_idx = _iframe_local_counters.get(parent_path, 0)
_iframe_local_counters[parent_path] = local_idx + 1
iframe_stack.append((original_depth, list(parent_path) + [local_idx]))
```

`frame_path` 记录的是**从主 frame 到目标 iframe 的各层本地索引**（同层 iframe 从 0 开始计数），与 `frame.seq` 无关。

---

### bridgic 源码：ref 查找规则

#### 10. `get_element_by_ref()` 两阶段查找
**文件**：`bridgic/browser/session/_browser.py`

```
输入: bridgic ref (如 "8d4b03a9")
   ↓
self._last_snapshot.refs.get(ref) → RefData
   ↓
阶段1: aria-ref 快速路径（O(1)）
  条件: ref_data.playwright_ref 非空（即本次快照后未重新导航）
  实现:
    scope = page
    for nth in ref_data.frame_path:          # 按 frame_path 链式构建作用域
        scope = scope.frame_locator("iframe").nth(nth)
    locator = scope.locator(f"aria-ref={ref_data.playwright_ref}")
    count = await locator.count()
    count == 1 → 直接返回（Playwright 内部 _jumpToAriaRefFrameIfNeeded 保证路由正确）
    count == 0 → stale，fall-through
    Exception  → 引擎不可用，fall-through

阶段2: CSS 重建路径（get_locator_from_ref_async）
  位置: _snapshot.py:1830
  策略优先级（按信号强度）:
    1) get_by_role(role, name=name, exact=True)          ← 绝大多数元素
    2) get_by_role(role).filter(has_text=...)            ← ROLE_TEXT_MATCH_ROLES
    3) get_by_text(text, exact=True)                     ← TEXT_LEAF_ROLES (text 伪角色)
    4) STRUCTURAL_NOISE_ROLES 的 child-anchor 路径       ← div/span 等结构噪声
    5) get_by_role(role)                                 ← 无 name 时的 bare role fallback
  scope: 先按 frame_path 链式 frame_locator("iframe").nth(n) 确定作用域
  nth: 仅在 locator key space 与 role:name key space 一致时应用（STRUCTURAL_NOISE/TEXT_LEAF 除外）
```

---

### 覆盖元素检测（covered-element check）

**6 处位置**：`_click_checkable_target`（`_browser.py:239`）、`click_element_by_ref`（`~3106`）、`hover_element_by_ref`（`~3331`）、`check_checkbox_or_radio_by_ref`（`~3611`）、`uncheck_checkbox_by_ref`（`~3706`）、`double_click_element_by_ref`（`~3794`）

```javascript
(el) => {
  if (window.parent !== window) return false;   // ← iframe 元素直接跳过
  const t = document.elementFromPoint(cx, cy);
  return !!t && t !== el && !el.contains(t) && !t.contains(el);
}
```

**禁止改为 `window.frameElement !== null`**：Chrome 在 `file://` 协议的 iframe 中将 `window.frameElement` 返回 `null`（安全策略），导致误判。`window.parent !== window` 是纯对象比较，在任何协议、任何 origin 均可靠。

**为何 iframe 元素必须跳过**：`bounding_box()` 返回主视口坐标，`document.elementFromPoint(cx, cy)` 在 iframe JS context 中使用 iframe 本地坐标。坐标系不同，`elementFromPoint` 会找到错误元素（通常是子 iframe 节点），触发误报"被覆盖"。跳过后 `locator.click()` 由 Playwright 内部做坐标转换。

---

### 嵌套 iframe 与 frame_path

`RefData.frame_path: Optional[List[int]]`：
- `None` → 主 frame
- `[0]` → 顶层第一个 iframe（本地索引 0）
- `[0, 1]` → 顶层第一个 iframe 内的第二个 iframe

构建 locator 时三处（aria-ref 快速路径、`get_locator_from_ref_async`、recovery 路径）均使用相同的链式调用：
```python
scope = page
for local_nth in frame_path:
    scope = scope.frame_locator("iframe").nth(local_nth)
```

`_iframe_local_counters: Dict[tuple, int]`（`_snapshot.py:1229`）记录每个父路径下已计数的 iframe 数，保证多层嵌套时各级 nth 互相独立。

---

### 调试日志

```bash
BRIDGIC_LOG_LEVEL=DEBUG bridgic-browser snapshot -i
BRIDGIC_LOG_LEVEL=DEBUG bridgic-browser click <ref>
```

关键 DEBUG 日志节点（`_browser.py`）：
- `[get_element_by_ref] aria-ref fast-path hit: ref=... playwright_ref=... frame_path=...`
- `[get_element_by_ref] aria-ref stale (count=N), falling through to CSS: ...`
- `[get_element_by_ref] aria-ref exception (...), falling through to CSS: ...`
- `[get_element_by_ref] CSS path: ref=... role=... name=... nth=... frame_path=...`
- `[click_element_by_ref] covered at (x, y), clicking intercepting element`
- `_click_checkable_target: covered at (x, y), clicking intercepting element`

---

## Testing notes

- All tests are async; `asyncio_mode = "auto"` is configured in `pyproject.toml`.
- `@pytest.mark.integration` tests require a real browser and are excluded from `make test-quick`.
- `@pytest.mark.slow` tests can be skipped with `-m "not slow"`.
- The `tests/conftest.py` provides `event_loop` (session-scoped) and `temp_dir` fixtures.
- CLI unit tests in `tests/unit/test_cli.py` (no real browser required).

## Namespace packaging

`bridgic` is a pkgutil-style namespace package shared with `bridgic-core` and `bridgic-llms-openai`. Do not add an `__init__.py` to `bridgic/` itself. The `uv pip install --force-reinstall` in `make test` ensures all three packages coexist correctly in the venv.
