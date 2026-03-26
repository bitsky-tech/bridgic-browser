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

1. **`Browser`** (`session/_browser.py`) — instantiate; browser starts lazily on first `navigate_to` / `search`, or explicitly via `async with Browser(...) as b:` (calls `_start()`). Auto-selects:
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
- `await browser.get_snapshot_text(...)` — returns a truncated string ready for LLM context, with pagination via `offset` and `limit` (default 10000)

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

`get_init_script(locale=None)` accepts the locale and performs the `__BRIDGIC_LANGS__` substitution before returning the script. Called from `_browser.py:_start()` with `self._locale`.

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
- **`_commands.py`**: 67 Click commands in 15 sections via `SectionedGroup`. `scroll` uses `--dy`/`--dx` options (not positional) to support negative values. `screenshot`/`pdf`/`upload`/`storage-save`/`storage-load`/`trace-stop` call `os.path.abspath()` on the client side before sending (daemon cwd may differ). `snapshot` supports `-i`/`--interactive`, `-f/-F`/`--full-page/--no-full-page`, `-o`/`--offset` (default 0), and `-l`/`--limit` (default 10000); it delegates to `browser.get_snapshot_text()` (which adds truncation/pagination).
- **`_build_browser_kwargs()`** priority chain (lowest → highest): defaults → `~/.bridgic/bridgic-browser.json` → `./bridgic-browser.json` → `BRIDGIC_BROWSER_JSON` env var. The `--headed` CLI flag merges `{"headless": false}` into `BRIDGIC_BROWSER_JSON` before spawning the daemon.
- **`close` command fast-path**: the daemon calls `browser.inspect_pending_close_artifacts()` to pre-allocate a session dir and trace path, responds to the client immediately with those paths, then sets `stop_event`. Actual `browser.close()` runs after the client disconnects. After close, `_write_close_report()` writes `~/.bridgic/tmp/close-<timestamp>-<rand>/close-report.json` with status, artifact paths, and any errors.
- **Daemon cleanup ownership guard**: after `browser.close()` finishes, `run_daemon()` reads the run-info file and compares its `pid` field to `os.getpid()` before calling `transport.cleanup()` / `remove_run_info()`. This prevents the outgoing daemon from deleting the new daemon's socket when a `close` is followed immediately by a new command (which starts a new daemon before the old one's shutdown completes). If the run-info is gone (`None`) the old daemon is still the owner and cleans up normally.

Socket path: `BRIDGIC_SOCKET` env var (default `~/.bridgic/run/bridgic-browser.sock`).
The directory is created with `0o700` permissions on first use. Users upgrading from an older version that used `/tmp/bridgic-browser.sock` should stop any running daemon first (`bridgic-browser close`) before upgrading.

Snapshot pagination: `get_snapshot_text(offset=0, limit=10000, ...)` — `offset` and `limit` are the two pagination parameters. Both must be ≥ 0 / ≥ 1 respectively.

## Key Implementation Details & Playwright Internals

### Two Co-existing Ref Systems (Foundation for Understanding the Entire Chain)

bridgic has **two distinct ref systems** that must not be confused:

| | bridgic ref | playwright_ref |
|---|---|---|
| Example | `"8d4b03a9"` | `"e369"` / `"f1e5"` |
| Generated in | `_snapshot.py:_compute_stable_ref()` | Playwright injected script `computeAriaRef()` |
| Format | SHA-256(namespace+role+name+frame_path+nth) first 4 bytes hex | `{refPrefix}e{lastRef}` incrementing integer |
| Stability | **Stable across snapshots** (same element, same ref) | **Resets after each snapshot** (valid only within current snapshotForAI) |
| Purpose | Exposed to LLM / tool calls / CLI | O(1) DOM pointer lookup for aria-ref fast path |
| Stored in | `EnhancedSnapshot.refs: Dict[str, RefData]` | `RefData.playwright_ref` |

---

### Playwright Source: Ref Generation Rules

All source paths are under `.venv/lib/python3.10/site-packages/playwright/driver/package/lib/`.

#### 1. `lastRef` Counter and `computeAriaRef()`
**File**: `generated/injectedScriptSource.js` (this script is injected into each frame; each frame has its own independent instance)

```javascript
// injectedScriptSource.js — module-level variable in injected script (independent per frame)
var lastRef = 0;

function computeAriaRef(ariaNode, options) {
  if (options.refs === "none") return;
  // when mode="ai", refs="interactable" — only assigns refs to visible elements that receive pointer events
  if (options.refs === "interactable" && (!ariaNode.box.visible || !ariaNode.receivesPointerEvents))
    return;

  let ariaRef = ariaNode.element._ariaRef;  // cache on the DOM element
  if (!ariaRef || ariaRef.role !== ariaNode.role || ariaRef.name !== ariaNode.name) {
    // cache miss (first time / role or name changed) → generate new ref
    ariaRef = {
      role: ariaNode.role,
      name: ariaNode.name,
      ref: (options.refPrefix ?? "") + "e" + ++lastRef   // ← core format
    };
    ariaNode.element._ariaRef = ariaRef;  // write back to DOM element
  }
  ariaNode.ref = ariaRef.ref;
}
```

**Key rules**:
- `lastRef` is a module-level integer that **monotonically increases throughout the lifetime of the injected script instance for the same frame and is never reset**
- If role+name is unchanged for the same element, **the previous ref is reused** (`element._ariaRef` cache), `lastRef` is not incremented
- Ref format: `{refPrefix}e{lastRef}`, e.g. `"e1"`, `"e5"`, `"f1e3"`, `"f2e7"`
- `refPrefix` is passed by the caller (see next section)

#### 2. Source of `refPrefix`: frame.seq
**File**: `server/page.js:825` (`snapshotFrameForAI` function)

```javascript
// page.js — snapshotFrameForAI()
injectedScript.evaluate((injected, options) => {
  return injected.incrementalAriaSnapshot(node, { mode: "ai", ...options });
}, {
  refPrefix: frame.seq ? "f" + frame.seq : "",  // ← main frame seq=0 → "", child frame seq=N → "fN"
  track: options.track
});
```

**File**: `server/frames.js:368` (Frame constructor)

```javascript
// frames.js — Frame constructor
this.seq = page.frameManager.nextFrameSeq();
// main frame seq=0; subsequent frames increment: 1, 2, 3...
// seq is not "the Nth iframe" — it is a globally unique sequence number
```

**Format summary**:
- Main frame (seq=0): `refPrefix=""` → refs are `"e1"`, `"e2"`, …
- Child frame (seq=1): `refPrefix="f1"` → refs are `"f1e1"`, `"f1e2"`, …
- Child frame (seq=2): `refPrefix="f2"` → refs are `"f2e1"`, `"f2e3"`, …
- **Note**: seq is a page-level global counter, unrelated to iframe position in the DOM

#### 3. Building the `snapshot.elements` Map
**File**: `generated/injectedScriptSource.js` (the `visit` callback inside `generateAriaTree`)

```javascript
// injectedScriptSource.js — generateAriaTree > visit()
if (childAriaNode.ref) {
  snapshot.elements.set(childAriaNode.ref, element);  // ref → DOM Element
  snapshot.refs.set(element, childAriaNode.ref);       // DOM Element → ref (reverse mapping)
  if (childAriaNode.role === "iframe")
    snapshot.iframeRefs.push(childAriaNode.ref);       // iframes collected separately for recursive child snapshots
}
```

#### 4. Writing to `_lastAriaSnapshotForQuery`
**File**: `generated/injectedScriptSource.js` (`InjectedScript.incrementalAriaSnapshot()` method)

```javascript
// injectedScriptSource.js — InjectedScript class
incrementalAriaSnapshot(node, options) {
  const ariaSnapshot = generateAriaTree(node, options);
  // ...
  this._lastAriaSnapshotForQuery = ariaSnapshot;  // ← overwritten after each snapshot
  return { full, incremental, iframeRefs: ariaSnapshot.iframeRefs };
}
```

**Key**: `_lastAriaSnapshotForQuery` is a property on each frame's injected script instance and is **completely independent per frame**. The L1 frame's injected script only holds L1's `elements` Map (with keys like `"f1e1"`).

---

### Playwright Source: Ref Lookup Rules

#### 5. aria-ref Engine: `_createAriaRefEngine()`
**File**: `generated/injectedScriptSource.js` (registered in the `InjectedScript` constructor)

```javascript
// injectedScriptSource.js — _createAriaRefEngine()
_createAriaRefEngine() {
  const queryAll = (root, selector) => {
    const result = this._lastAriaSnapshotForQuery?.elements?.get(selector);
    // selector = the raw string after "aria-ref=", e.g. "e369" or "f1e5"
    return result && result.isConnected ? [result] : [];
    // isConnected check: returns empty if element has been removed from DOM (stale case)
  };
  return { queryAll };
}
```

O(1) Map lookup; `isConnected` ensures stale refs return empty instead of throwing.

#### 6. `_jumpToAriaRefFrameIfNeeded()`: Cross-frame Routing
**File**: `server/frameSelectors.js:85`

```javascript
// frameSelectors.js — FrameSelectors class
_jumpToAriaRefFrameIfNeeded(selector, info, frame) {
  if (info.parsed.parts[0].name !== "aria-ref") return frame;
  const body = info.parsed.parts[0].body;          // "f1e5" or "e369"
  const match = body.match(/^f(\d+)e\d+$/);        // only matches child frame refs (with "f" prefix)
  if (!match) return frame;                          // main frame ref → no jump
  const frameSeq = +match[1];                       // extract seq number
  const jumptToFrame = this.frame._page.frameManager.frames()
    .find(frame2 => frame2.seq === frameSeq);        // global linear search
  if (!jumptToFrame)
    throw new InvalidSelectorError(...);
  return jumptToFrame;
}
```

**Important**: `_jumpToAriaRefFrameIfNeeded` switches the execution target frame **before** running `queryAll`, so the query runs in the correct frame's injected script context (which holds the corresponding key in its `_lastAriaSnapshotForQuery`).

**This means**: from an element resolution perspective, both `page.locator("aria-ref=f1e5")` and `frame_locator("iframe").nth(0).locator("aria-ref=f1e5")` correctly find the L1 frame element, because `_jumpToAriaRefFrameIfNeeded` auto-routes. However, `locator.evaluate()`'s JS execution context is **not affected** — it always runs in the frame that **owns the locator's scope** (see below).

---

### bridgic Source: Ref Generation Rules

#### 7. Generating the bridgic ref (stable ID)
**File**: `bridgic/browser/session/_snapshot.py`

```python
# _snapshot.py:394
_REF_NAMESPACE = "bridgic-browser-v1"

# _snapshot.py:422 — _compute_stable_ref()
@staticmethod
def _compute_stable_ref(role, name, frame_path, nth) -> str:
    frame_str = ",".join(str(x) for x in frame_path) if frame_path else ""
    raw = f"{_REF_NAMESPACE}\x1f{role}\x1f{name or ''}\x1f{frame_str}\x1f{nth}"
    # \x1f (ASCII Unit Separator) used as field delimiter — cannot appear in HTML accessible names
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return digest[:4].hex()   # 8 hex characters, e.g. "8d4b03a9"
```

**Stability guarantee**: as long as the four fields role, name, frame_path, and nth remain unchanged, the same element always gets the same ref ID across snapshots — the LLM can use it persistently across snapshots.

#### 8. Extracting and Storing `playwright_ref`
**File**: `bridgic/browser/session/_snapshot.py`

```python
# _snapshot.py:374
_REF_EXTRACT_PATTERN = re.compile(r'\[ref=([a-zA-Z0-9]+)\]')

# _snapshot.py:1400-1491 — _process_page_snapshot_for_ai() parsing loop
# Extract before clean_suffix removes [ref=...]:
_pw_ref_match = ref_extract_pattern.search(suffix) if suffix else None
playwright_ref_for_element = _pw_ref_match.group(1) if _pw_ref_match else None

# Store in RefData:
refs[ref] = RefData(
    ...
    playwright_ref=playwright_ref_for_element,   # Playwright's "e369" / "f1e5"
)
```

`playwright_ref` is extracted from the `[ref=...]` suffix in Playwright's snapshot text lines and is only valid for the lifetime of the current `snapshotForAI` call.

#### 9. Generating `frame_path`
**File**: `bridgic/browser/session/_snapshot.py:1229` (parsing loop)

```python
# _snapshot.py — _process_page_snapshot_for_ai()
_iframe_local_counters: Dict[tuple, int] = {}   # key=parent path tuple, value=number of child iframes seen so far
# ...
# When an iframe node is encountered:
parent_path = tuple(iframe_stack[-1][1]) if iframe_stack else ()
local_idx = _iframe_local_counters.get(parent_path, 0)
_iframe_local_counters[parent_path] = local_idx + 1
iframe_stack.append((original_depth, list(parent_path) + [local_idx]))
```

`frame_path` records **the per-level local indices from the main frame to the target iframe** (same-level iframes start from index 0), and is unrelated to `frame.seq`.

---

### bridgic Source: Ref Lookup Rules

#### 10. Two-phase Lookup in `get_element_by_ref()`
**File**: `bridgic/browser/session/_browser.py`

```
Input: bridgic ref (e.g. "8d4b03a9")
   ↓
self._last_snapshot.refs.get(ref) → RefData
   ↓
Phase 1: aria-ref fast path (O(1))
  Condition: ref_data.playwright_ref is non-empty (i.e. no re-navigation since last snapshot)
  Implementation:
    scope = page
    for nth in ref_data.frame_path:          # build scope chain following frame_path
        scope = scope.frame_locator("iframe").nth(nth)
    locator = scope.locator(f"aria-ref={ref_data.playwright_ref}")
    count = await locator.count()
    count == 1 → return directly (Playwright's _jumpToAriaRefFrameIfNeeded guarantees routing)
    count == 0 → stale, fall through
    Exception  → engine unavailable, fall through

Phase 2: CSS rebuild path (get_locator_from_ref_async)
  Location: _snapshot.py:1830
  Strategy priority (by signal strength):
    1) get_by_role(role, name=name, exact=True)          ← most elements
    2) get_by_role(role).filter(has_text=...)            ← ROLE_TEXT_MATCH_ROLES
    3) get_by_text(text, exact=True)                     ← TEXT_LEAF_ROLES (text pseudo-role)
    4) STRUCTURAL_NOISE_ROLES with match_text            ← CSS-scoped + filter(has_text) + nth
    5) STRUCTURAL_NOISE_ROLES child-anchor path          ← unnamed noise with no text
    6) get_by_role(role)                                 ← bare role fallback when no name
  scope: chain frame_locator("iframe").nth(n) per frame_path level first
  nth: applied only when locator key space matches role:name key space (excluding STRUCTURAL_NOISE/TEXT_LEAF)

STRUCTURAL_NOISE child-anchor path (strategy 5) detail:
  Applies to: unnamed generic/group/none/presentation with no stored text
  Sub-strategies (tried in order):
    a) Find text-leaf child (role='text', parent_ref==ref) → CSS-scoped container locator (STRUCTURAL_NOISE_CSS)
    b) Find named STRUCTURAL_NOISE child (parent_ref==ref, role in STRUCTURAL_NOISE_ROLES, name non-empty)
       → scope.locator(STRUCTURAL_NOISE_CSS_NAMED).filter(has_text=name).locator('..')
         Note: locator('..') is auto-detected as XPath parent by Playwright (selectorParser.js:159)
         Note: STRUCTURAL_NOISE_CSS_NAMED adds span:not([role]) vs STRUCTURAL_NOISE_CSS because
               the child may be a <span> that Playwright maps to 'generic' role.
               nth is NOT applied; the parent is located structurally via the child.
    c) fallback: get_by_role(role) (returns 0 results for implicit generic — last resort)
```

---

### Covered-element Check

**6 locations**: `_click_checkable_target` (`_browser.py:239`), `click_element_by_ref` (`~3106`), `hover_element_by_ref` (`~3331`), `check_checkbox_or_radio_by_ref` (`~3611`), `uncheck_checkbox_by_ref` (`~3706`), `double_click_element_by_ref` (`~3794`)

```javascript
(el) => {
  if (window.parent !== window) return false;   // ← skip directly for iframe elements
  const t = document.elementFromPoint(cx, cy);
  return !!t && t !== el && !el.contains(t) && !t.contains(el);
}
```

**Do not change to `window.frameElement !== null`**: Chrome returns `null` for `window.frameElement` inside iframes under the `file://` protocol (security policy), causing false positives. `window.parent !== window` is a pure object comparison that is reliable across all protocols and origins.

**Why iframe elements must be skipped**: `bounding_box()` returns main-viewport coordinates, while `document.elementFromPoint(cx, cy)` inside the iframe JS context uses iframe-local coordinates. The coordinate systems differ, so `elementFromPoint` finds the wrong element (typically the child iframe node), triggering a false "covered" report. After skipping, `locator.click()` lets Playwright handle coordinate transformation internally.

---

### Nested iframes and frame_path

`RefData.frame_path: Optional[List[int]]`:
- `None` → main frame
- `[0]` → first top-level iframe (local index 0)
- `[0, 1]` → second iframe inside the first top-level iframe

All three locator-building code paths (aria-ref fast path, `get_locator_from_ref_async`, recovery path) use the same chained call:
```python
scope = page
for local_nth in frame_path:
    scope = scope.frame_locator("iframe").nth(local_nth)
```

`_iframe_local_counters: Dict[tuple, int]` (`_snapshot.py:1229`) tracks the iframe count under each parent path, ensuring per-level nth values are independent across multiple nesting levels.

---

### Interactive Element Detection — Small Icon Rule

`_is_element_interactive()` (`_snapshot.py`) rule 9: small icon (10–50 px) is treated as interactive only when it carries **strong semantic signals**:

- `data-action` attribute → explicit author intent
- `aria-label` → screen-reader accessible name

**`classAndId` is intentionally excluded**: almost every element carries a CSS class, so including it causes false positives for purely decorative elements (badges, avatars, dividers) that happen to be small. `cursor=pointer` is covered by rule 10 (separate check) and is a stronger signal.

Impact on `get_snapshot(interactive=True)`: a small icon with only a CSS class (no `data-action`, no `aria-label`, no `cursor:pointer`) will **not** appear in the interactive snapshot. If an icon is missing, add `data-action` or `aria-label` to the element.

---

### Debug Logging

```bash
BRIDGIC_LOG_LEVEL=DEBUG bridgic-browser snapshot -i
BRIDGIC_LOG_LEVEL=DEBUG bridgic-browser click <ref>
```

Key DEBUG log points (`_browser.py`):
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
