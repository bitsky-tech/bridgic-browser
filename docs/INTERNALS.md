# Implementation Internals

Deep implementation details for bridgic-browser. Read this when debugging ref lookup, iframe handling, stealth detection, or CLI daemon issues. For everyday development, see [CLAUDE.md](../CLAUDE.md).

## Two Co-existing Ref Systems (Foundation for Understanding the Entire Chain)

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

## Playwright Source: Ref Generation Rules

All source paths are under `.venv/lib/python3.10/site-packages/playwright/driver/package/lib/`.

### 1. `lastRef` Counter and `computeAriaRef()`
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

### 2. Source of `refPrefix`: frame.seq
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

### 3. Building the `snapshot.elements` Map
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

### 4. Writing to `_lastAriaSnapshotForQuery`
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

## Playwright Source: Ref Lookup Rules

### 5. aria-ref Engine: `_createAriaRefEngine()`
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

### 6. `_jumpToAriaRefFrameIfNeeded()`: Cross-frame Routing
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

## bridgic Source: Ref Generation Rules

### 7. Generating the bridgic ref (stable ID)
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

### 8. Extracting and Storing `playwright_ref`
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

### 9. Generating `frame_path`
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

## bridgic Source: Ref Lookup Rules

### 10. Two-phase Lookup in `get_element_by_ref()`
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

## Covered-element Check

**6 locations**: `_click_checkable_target` (`_browser.py:239`), `click_element_by_ref` (`~3151`), `hover_element_by_ref` (`~3393`), `check_checkbox_or_radio_by_ref` (`~3645`), `uncheck_checkbox_by_ref` (`~3751`), `double_click_element_by_ref` (`~3847`)

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

## Nested iframes and frame_path

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

## Interactive Element Detection — Small Icon Rule

`_is_element_interactive()` (`_snapshot.py`) rule 9: small icon (10–50 px) is treated as interactive only when it carries **strong semantic signals**:

- `data-action` attribute → explicit author intent
- `aria-label` → screen-reader accessible name

**`classAndId` is intentionally excluded**: almost every element carries a CSS class, so including it causes false positives for purely decorative elements (badges, avatars, dividers) that happen to be small. `cursor=pointer` is covered by rule 10 (separate check) and is a stronger signal.

Impact on `get_snapshot(interactive=True)`: a small icon with only a CSS class (no `data-action`, no `aria-label`, no `cursor:pointer`) will **not** appear in the interactive snapshot. If an icon is missing, add `data-action` or `aria-label` to the element.

---

## Stealth JS Init Script — Patched Properties

`_STEALTH_INIT_SCRIPT_TEMPLATE` in `_stealth.py` — **headless mode only**. Skipped entirely in headed mode because `context.add_init_script()` runs in ALL frames including Cloudflare Turnstile's challenge iframe; patching `window.chrome` (`configurable:false`), `navigator.permissions.query`, and WebGL prototype inside the iframe causes detectable API inconsistencies that fail the challenge.

### Anti-toString-detection (`_mkNative` framework)

All patched functions are registered in a `WeakSet` (`_nativeFns`) via `_mkNative(fn, name)`. `Function.prototype.toString` is itself intercepted to return `"function foo() { [native code] }"` for any registered function. This closes the entire class of "call `.toString()` on a function to detect monkey-patching" attacks used by DataDome, PerimeterX, and Cloudflare bot detectors.

```javascript
const _nativeFns = new WeakSet();
const _nativeFnNames = new WeakMap();
const _mkNative = (fn, name) => { _nativeFns.add(fn); _nativeFnNames.set(fn, name); return fn; };
Function.prototype.toString = _mkNative(function toString() {
  if (_nativeFns.has(this)) return `function ${_nativeFnNames.get(this) ?? this.name}() { [native code] }`;
  return _origFnToString.call(this);
}, 'toString');
```

### Patched properties

- `navigator.webdriver` → **conditionally** `undefined`; checks `Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver')` first and patches the prototype descriptor. Falls back to instance property only if the prototype has no descriptor but the value is non-undefined. Avoids creating an own-property (which makes `'webdriver' in navigator` = true — detectable in real Chrome where the property is absent).
- `navigator.plugins` / `navigator.mimeTypes` → realistic PDF Viewer entries (5 plugins, 2 MIME types); each plugin holds its own per-plugin mime copies so `enabledPlugin` refs are correct. The `item(i)` accessor truncates `i` to uint32 via `i >>> 0` to match Web IDL §3.2.4 indexed-property semantics — without this `plugins.item(4294967296)` returns `null` instead of `plugins[0]`, which `incolumitas` `overflowTest` flags.
- `navigator.languages` → derived from `Browser(locale=...)` to keep `navigator.language === navigator.languages[0]` (e.g. `["zh-CN", "zh", "en"]` for `locale="zh-CN"`); defaults to `["en-US", "en"]`
- `window.chrome` → complete object with `runtime`, `csi()`, `loadTimes()` (all wrapped with `_mkNative`)
- `navigator.permissions.query` → returns `"default"` for notifications (not `"denied"`); wrapped with `_mkNative`
- `window.outerWidth/Height` → matches `innerWidth/Height` when zero (guard for edge cases; with `--headless=new` + `screen` context option these are already correctly set by Chrome)
- `navigator.deviceMemory` → `8` (headless environments may return `undefined`)
- `navigator.hardwareConcurrency` → `8` when value is 0 or 1 (headless may report fewer cores)
- `navigator.connection` → `{ effectiveType: '4g', downlink: 10, rtt: 100, saveData: false }` when absent
- `WebGLRenderingContext` / `WebGL2RenderingContext` → `getParameter(37445/37446)` **conditionally** returns `'Intel Inc.'` / `'Intel Iris OpenGL Engine'` only when the real vendor contains `'Google'` or `'SwiftShader'` (masks SwiftShader which is a well-known bot signal). On headed Apple Silicon Mac the real `'Apple Inc.'` value is preserved so the WebGL fingerprint stays consistent with DPI, Canvas, and font rendering signals. `getParameter` is wrapped with `_mkNative`.
- `document.hasFocus()` → always returns `true` (headless tabs return `false` by default; Cloudflare and DataDome probe this); wrapped with `_mkNative`
- `document.hidden` → always `false` (via `Object.defineProperty`)
- `document.visibilityState` → always `'visible'` (via `Object.defineProperty`); headless tabs default to `'hidden'` which is a strong bot signal
- `Notification.permission` → guarded: only patched if `Notification` exists and its permission is `'denied'`; returns `'default'`
- `navigator.plugins` / `navigator.mimeTypes` **prototype identity** (R2): after the array is built, `Object.setPrototypeOf(_pluginList, PluginArray.prototype)` and `_plugins.forEach(p => Object.setPrototypeOf(p, Plugin.prototype))` (same for `MimeTypeArray` / `MimeType`). Without this, `Object.getPrototypeOf(navigator.plugins).constructor.name === 'Array'` and `navigator.plugins instanceof PluginArray` is `false` — sannysoft and incolumitas both probe these.
- `navigator.webdriver` **deleted from `Navigator.prototype`** (R4 + R6): when `Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver')` returns a `configurable: true` descriptor (the typical case under Web IDL §3.7.6), we `delete Navigator.prototype.webdriver` outright — matching the state real Chrome reaches with `--disable-blink-features=AutomationControlled` working correctly. After the delete: `'webdriver' in navigator === false`, `navigator.webdriver === undefined`, `Object.keys(navigator)` excludes it. Fallbacks: non-configurable descriptor → undefined-getter with `enumerable: false`; sub-frame edge case where the property is on the instance not the prototype → undefined-getter on instance.
- **`Worker` / `SharedWorker` / `navigator.serviceWorker.register` constructor wrap** (R3): top-frame-only (gated by `if (window === window.top)`). Each new worker's `scriptURL` is wrapped via:
  ```javascript
  const wrapper = "<inlined worker stealth body>;importScripts(<originalURL>);";
  return new OrigWorker(URL.createObjectURL(new Blob([wrapper])), options);
  ```
  This wins the race against detector code that synchronously postMessages `navigator.*` from inside the worker — our patches run before any user line of the worker. The wrap falls through transparently for non-string `scriptURL` or any error during URL construction.

### console.* `Error` pre-stringify (R5-lite)

In the anti-devtools-detector script (which has its own `if (window !== window.top) return;` top-frame guard), every `console.{log, debug, info, warn, error, trace}` is wrapped to coerce any `Error` argument to a plain string (`name + ": " + message`) before forwarding to the original method:

```javascript
['log', 'debug', 'info', 'warn', 'error', 'trace'].forEach((m) => {
  const _orig = console[m];
  console[m] = _mkNative(function () {
    const args = Array.from(arguments).map(a => a instanceof Error ? `${a.name}: ${a.message}` : a);
    return _orig.apply(console, args);
  }, m);
});
```

This blocks the well-known CDP-attach detection where a detector installs a getter trap on `error.stack` and calls `console.log(error)` — when CDP is attached, `Runtime.consoleAPICalled` serializes args (calling the trapped getter) and the detector observes the trap firing. Pre-stringifying the `Error` defeats the entire class.

`get_init_script(locale=None)` accepts the locale and performs three substitutions before returning the script:
- `__BRIDGIC_LANGS__` → `JSON.stringify([locale, lang, "en"])` for both main-thread `navigator.languages` and the inlined worker stealth body.
- `"__BRIDGIC_WORKER_STEALTH__"` → JSON-encoded full worker stealth body (used by the `Worker` constructor wrap to inject via `importScripts`).

Called from `_browser.py:_start()` with `self._locale` only when `self._headless=True`.

---

## Mode-aware Stealth Design

bridgic's stealth layer is intentionally **bimodal**. Headless and headed mode apply different sets of patches because they exploit different strengths and have different risks.

### The core invariant

> Any patch that can propagate into a cross-origin iframe MUST be gated to `self._headless`.

The Cloudflare Turnstile / hCaptcha challenge runs inside a cross-origin iframe (`challenges.cloudflare.com`). When a patch leaks into that iframe, the challenge worker sees navigator/Worker/UA values that don't match Cloudflare's edge-server expectation → bot signal → silent fail.

### Per-mode patch matrix

| Mechanism | Headless | Headed | Iframe-propagating? |
|---|---|---|---|
| Chrome launch args (`CHROME_STEALTH_ARGS_HEADED` ~10 flags / `CHROME_STEALTH_ARGS` ~50 flags) | full | minimal | n/a (process-level) |
| `--headless=new` redirect | yes | n/a | n/a |
| Auto-switch to system Chrome (`channel="chrome"`) | n/a | yes | n/a |
| `_STEALTH_INIT_SCRIPT_TEMPLATE` (webdriver / plugins / chrome / WebGL / …) | injected | **skipped** | yes — `add_init_script` runs in all frames |
| `_ANTI_DEVTOOLS_DETECTOR_SCRIPT` | injected | injected | **no** — self-gated to top frame inside the script |
| **R1 — context `user_agent` fallback (`Chrome/143`)** | active | **skipped** | yes — context option applies to all requests in all frames |
| **R1 — CDP `Emulation.setUserAgentOverride` + UA-CH brands** | active | **skipped** | yes — CDP override propagates to all frames in target |
| **R3 — `Worker` / `SharedWorker` / SW.register constructor wrap** (in main init script) | active | **skipped** (whole script is) | self-gated to top frame |
| **R3 — `page.on('worker')` post-spawn injection** | active | **skipped** | yes — `page.workers` includes workers spawned by cross-origin iframes |
| **R6 — main-frame `delete Navigator.prototype.webdriver`** via `page.evaluate` + `framenavigated` re-apply | covered by main init script | active | **no** — JS execution contexts are per-frame, so cross-origin iframes are untouched |
| Anchored CDP session for UA override (`setattr(page, "_bridgic_uad_cdp", sess)`) | yes | **skipped** | n/a (lifecycle) |
| `Debugger.setSkipAllPauses` | active | active | no — per-target CDP session |

### Why the trade-offs work

**Headed mode** (`Browser(headless=False)`):
- Wins on **L1 (TCP/TLS) + L2 (browser process behavior)**: real system Chrome, TLS fingerprint matches a normal user, navigator/window are already realistic.
- Loses on **L3 (CDP-side detection in workers)**: we cannot patch worker `console.*` without also patching the Cloudflare iframe's worker — so `deviceandbrowserinfo` `isAutomatedWithCDPInWebWorker` stays detected.

**Headless mode** (`Browser(headless=True)`):
- Loses on **L1 (TLS) + L2 (`HeadlessChrome` UA leak)**: Playwright Chromium's TLS fingerprint and default UA are detectable; we cover the UA leak via R1, but TLS is unreachable from JS/CDP.
- Wins on **L3**: the full R1+R2+R3+R4+R5-lite suite runs without the iframe-safety constraint, so `deviceandbrowserinfo` and worker-side CDP detection both pass.

### Decision flow when adding a new patch

```
new patch
    │
    ▼
runs in all frames (init script, CDP override,
context option, page.on('worker'), …)?
    │
   ┌┴┐
   no  yes
   │    │
   │    ▼
   │  could it create an inconsistency vs. what
   │  Cloudflare's edge-server logged for the
   │  parent page request?
   │    │
   │   ┌┴┐
   │   no  yes
   │   │    │
   │   │    ▼
   │   │  gate to `self._headless`
   │   │    │
   │   ▼    ▼
   ▼  ship safely
   ship safely
```

### CDP UA cleanup gotchas (R1)

These took multiple iterations to discover and are not obvious from CDP docs:

1. **Use `Emulation.setUserAgentOverride`, not `Network.setUserAgentOverride`.** In Chromium 145+ the `Network` variant silently drops the `userAgentMetadata` field, so Sec-CH-UA brands keep their default values (`Chromium`, `Not A(Brand`) even after the call returns success. The `Emulation` variant is the modern entry point and properly applies metadata.

2. **The CDP session must stay attached for the override to persist.** `Emulation.setUserAgentOverride` is scoped to the CDP session that issued it. If you call `await sess.detach()` after issuing the override, the next page navigation reverts `navigator.userAgentData.brands` to the Chromium defaults. bridgic anchors the session on the page object via `setattr(page, "_bridgic_uad_cdp", sess)` so it survives until the page closes.

3. **The override is per-target.** A new page in the same context needs its own override. We register a `context.on("page", lambda p: asyncio.create_task(self._apply_r1_ua_cleanup(p)))` listener (also gated to headless).

4. **Context-level `user_agent` option is a separate, independent layer.** It controls the HTTP UA header and `navigator.userAgent` for the very first request (before any CDP override has been issued). The CDP `Emulation.setUserAgentOverride` then takes over for both navigator + Sec-CH-UA. Both layers should agree on the UA string to avoid intra-session UA changes that detectors may flag.

### Worker stealth via constructor wrap (R3)

The naive approach — `page.on('worker', w => w.evaluate(stealth))` — loses a race against real-world detection libraries:

```
detector code (sync):           bridgic (async):
  new Worker(blobURL)
  worker boots
  worker reads navigator.*       page.on('worker') event fires
  worker postMessage(props)      we await session.send(...)
  main onmessage handler runs    we await worker.evaluate(stealth)
  detector compares → BOT        ← worker already shipped!
```

The constructor wrap fixes the race by injecting our patch at *worker construction* time, not after spawn. The wrap detects string/URL `scriptURL` and replaces it with a blob URL whose first instruction is our stealth body, followed by `importScripts(originalURL)`:

```
new Worker(externalURL)
   │
   ▼
construct trap intercepts
   │
   ▼
build wrapper = `<stealth body>;importScripts("<externalURL>");`
   │
   ▼
new Worker(URL.createObjectURL(new Blob([wrapper])))
   │
   ▼
worker first executes stealth body → patches navigator.deviceMemory,
                                     vendor, productSub, vendorSub,
                                     languages, WebGL, console.debug
then importScripts the original code → detector runs on patched values
```

The wrap is gated by both `self._headless` (R1/R3 mode gate) **and** `if (window === window.top)` (so even if the main script accidentally runs in an iframe, the wrap doesn't). Falls through transparently on non-string `scriptURL` or any throw.

**Same-origin filter** (added after a reCAPTCHA v3 regression). The wrap only intercepts `blob:`, `data:`, and same-origin `scriptURL`s. Cross-origin URLs (e.g. `https://www.gstatic.com/recaptcha/...js`) pass through to the original `Worker` constructor unchanged.

Why this is necessary: our wrapper does `importScripts(originalURL)` from inside a same-origin blob worker. For a cross-origin `originalURL` this fails CORS unless the cross-origin host serves `Access-Control-Allow-Origin: *` — most don't. A failed `importScripts` silently kills the worker, so any library that depends on a token / message from that worker hangs forever (reCAPTCHA v3's `grecaptcha.execute()` Promise never resolves, observed during headless verification).

Coverage cost is zero in practice: detector libraries (`incolumitas`, `fpscanner`, etc.) all build workers from inline `URL.createObjectURL(new Blob([code]))` — which is `blob:` and same-origin — so the filter still wraps everything we care about for fingerprint defense.

```javascript
const _bridgicWorkerWrapSafe = (scriptURL) => {
  try {
    const u = String(scriptURL);
    if (u.startsWith('blob:') || u.startsWith('data:')) return true;
    return new URL(u, location.href).origin === location.origin;
  } catch (_) { return false; }
};
```

---

## CLI Architecture — Detailed Implementation

### Client (`_client.py`)
- `send_command()` auto-starts the daemon if no socket exists.
- `_spawn_daemon()` uses `select.select()` + `os.read()` for the 30-second ready timeout (avoids blocking `proc.stdout.read()`).
- `start_if_needed=False` prevents auto-start for the `close` command.

### Daemon (`_daemon.py`)
- `run_daemon()` creates a `Browser()` instance directly (lazy start — Playwright does **not** launch immediately; `Browser.__init__` auto-loads config from `_config.py`), writes `BRIDGIC_DAEMON_READY` to stdout, and serves one JSON command per connection.
- The browser's Playwright process starts on the first command that calls `_ensure_started()` (e.g. `navigate_to`).
- `asyncio.wait_for(reader.readline(), timeout=60)` prevents hanging on idle connections.
- Signal handling uses `loop.add_signal_handler()` (asyncio-safe).

### Commands (`_commands.py`)
- 67 Click commands in 15 sections via `SectionedGroup`.
- `scroll` uses `--dy`/`--dx` options (not positional) to support negative values.
- `screenshot`/`pdf`/`upload`/`storage-save`/`storage-load`/`trace-stop` call `os.path.abspath()` on the client side before sending (daemon cwd may differ).
- `snapshot` supports `-i`/`--interactive`, `-f/-F`/`--full-page/--no-full-page`, `-l`/`--limit` (default 10000), and `-s`/`--file` (overflow file path); it delegates to `browser.get_snapshot_text()`.
- **`wait`**: argument is named `SECONDS_OR_TEXT`. When the argument parses as a float it always takes the time-wait path (`wait_seconds`); when it is a string it takes the text-wait path (`text` or `text_gone` with `--gone`). The `--gone` flag is **only** meaningful with a string argument — a numeric argument with `--gone` is ignored (number always → time). Unit is **seconds**, not milliseconds. Text search traverses **all frames** (main + iframes) via polling.
- **`type`**: text goes into the **currently focused element**; the user must `click` or `focus` the target first.
- **`mouse-move` / `mouse-click` / `mouse-drag`**: coordinates are **viewport pixels from the top-left corner**.
- **`eval-on`**: CODE must be an arrow function or named function that receives the element as its argument (e.g. `"(el) => el.textContent"`).

### Close command fast-path
The daemon calls `browser.inspect_pending_close_artifacts()` to pre-allocate a session dir, trace path, and video paths (all grouped under `~/.bridgic/bridgic-browser/tmp/close-<timestamp>-<rand>/`), responds to the client immediately with those paths, then sets `stop_event`. Actual `browser.close()` runs after the client disconnects. After close, `_write_close_report()` writes `close-report.json` in the session dir with status (`"success"`, `"success_with_timeouts"`, `"error"`, or `"timeout"`), artifact paths, and any errors.

### Daemon cleanup ownership guard
After `browser.close()` finishes, `run_daemon()` reads the run-info file and compares its `pid` field to `os.getpid()` before calling `transport.cleanup()` / `remove_run_info()`. This prevents the outgoing daemon from deleting the new daemon's socket when a `close` is followed immediately by a new command (which starts a new daemon before the old one's shutdown completes). If the run-info is gone (`None`) the old daemon is still the owner and cleans up normally.

## Owned-page Tracking

`Browser` maintains an internal `_owned_pages: Set[Page]` plus a parallel LRU `_focus_stack: List[Page]`. Every public tab operation (`get_pages`, `get_tabs`, `switch_tab`, `close_tab`, `_close_page` fallback) filters through this set, so callers — including the LLM/CLI — never see tabs that bridgic does not own.

### Ownership rules

- **CDP borrowed mode** (connected via `connect_over_cdp` into a context that already exists): only pages bridgic *creates* are owned. The pre-existing user tabs stay invisible.
- **All other modes** (CDP-owned-context fallback, persistent, ephemeral): every page in the context at `_start()` time is seeded as owned (`for _p in self._context.pages: self._mark_owned(_p)`). bridgic owns the browser, so this is effectively `_owned_pages == set(context.pages)` and the filter degenerates to identity.
- **Popups** are adopted iff `await new_page.opener()` is already owned. The mechanism: a `context.on("page", self._on_new_page)` listener schedules an async task that queries `opener()`. Identity-based check (`opener in self._owned_pages`) — validated by `tests/integration/test_opener_api_probe.py` to be reliable in both launch and CDP-borrowed modes. Whether `opener()` returns the parent or `None` is itself decided by Chromium's navigation disposition, not by who triggered the click — see [Adoption truth table](#adoption-truth-table-cdp-borrowed-mode) below for the full matrix.
- **Auto-follow**: when `_auto_follow_popups=True` (default) AND the popup's opener is `self._page`, `self._page` is moved to the popup. This mirrors Chrome's UX where a just-spawned tab takes the foreground.

### `_close_page` fallback order

When `self._page` is closed, `_select_fallback_page` returns the first match from:

1. `await closed_page.opener()` if still owned and alive (Chrome's natural "go back to the spawner" semantics).
2. Top-down scan of `_focus_stack` for an owned & alive page.
3. First entry of `get_pages()` in `context.pages` order.
4. `None` — caller sets `self._page = None`; next `navigate_to` auto-creates a fresh page (existing branch in `navigate_to`).

### Lifecycle wiring

- `_mark_owned(page)` is idempotent: it adds to set + stack, then registers a `page.on("close", self._on_owned_page_close)` callback for automatic pruning.
- `_on_owned_page_close` only prunes bookkeeping; it deliberately does NOT touch `self._page` — the `_close_page` flow handles that, and double-handling would cause double video/download swaps.
- `_switch_self_page_to(new_page)` consolidates "move self._page" side effects: focus stack push, `_invalidate_page_state()`, `_switch_video_to_page()`. A legacy `DownloadManager.detach_from_page(old) + attach_to_page(new)` swap is kept as a no-op safety net — DownloadManager is not the active pipeline in CDP-borrowed mode; `CdpDownloadRenamer` is, and it stays bound to the original `self._page`'s CDP session. Downloads triggered from a followed popup therefore are **not** subject to the renamer; they fall back to Playwright's per-context override (artifactsDir) and rely on the L2 rescue net on close. See [CLAUDE.md → Downloads](../CLAUDE.md#downloads).

### Adoption truth table (CDP borrowed mode)

Adoption requires **both** conditions:

1. `Page.opener()` returns a non-`None` parent — i.e. Chromium's CDP `Target.attachedToTarget.openerId` was populated at attach time.
2. That parent is already in `_owned_pages`.

Condition (2) is the "opener-in-owned" rule from the [Ownership rules](#ownership-rules) above and filters out any popup spawned from a user tab. Condition (1) is what the truth table below characterizes — it depends entirely on **how the tab was opened**, not on origin, `rel`, or any HTML attribute, **and not on who triggered the click**. Chromium routes mouse events by their `modifiers` bitmask, not by the actor that produced them, so a Playwright-dispatched click with `modifiers=['Meta']` is indistinguishable from a real user Cmd+click.

Verified by manual test on Chrome 147 (file:// fixture + cross-origin and same-origin targets). Each row below assumes the popup is spawned from an owned page (so condition (2) is satisfied) — rows that end in ❌ fail condition (1) and therefore are not adopted. Standalone tabs (Cmd+T / address bar / history) are listed for completeness; they have no opener at all, so they also fail (1) and are never adopted regardless of which page is "active":

| How the popup was triggered | Chromium path | openerId at attach | Adopted |
|---|---|:-:|:-:|
| `bridgic-browser click <ref>` on `<a target=_blank>` | foreground tab | populated | ✅ |
| Programmatic `await page.click(...)` from Playwright | foreground tab | populated | ✅ |
| User **plain left-click** on `<a target=_blank>` in an owned page | foreground tab | populated | ✅ |
| User left-click on `<a href="javascript:window.open(...)">` | foreground tab | populated | ✅ |
| User **Cmd+click** (macOS) / **Ctrl+click** (Win/Linux) / **middle-click** | background tab | empty | ❌ |
| **`bridgic-browser key-down Meta && click <ref> && key-up Meta`** (Playwright keyboard state propagates the modifier into `Input.dispatchMouseEvent`) | background tab | empty | ❌ |
| Equivalent `locator.click(modifiers=['Meta'])` Playwright call | background tab | empty | ❌ |
| User opens a new tab via Cmd+T / address bar / Chrome history | background tab / standalone | empty | ❌ |
| `<a target=_blank rel="noopener">` / `rel="noreferrer"` / `window.open(...,'noopener')` — under any click above | (same as the underlying path — `rel=noopener` does not clear `openerId`) | (same) | (same) |
| JavaScript-initiated `el.click()` / `window.open()` without a user gesture | n/a — popup blocker fires, no new tab attaches | n/a | n/a |

**Two observations from the table**:

1. **Role-agnostic, behavior-driven.** Chromium does not know or care whether a click came from a human or from Playwright/CDP. The deciding variable is the mouse event's `modifiers` field plus the navigation disposition derived from it (`NEW_FOREGROUND_TAB` vs `NEW_BACKGROUND_TAB`). bridgic inherits this exact line.
2. **bridgic can deliberately open tabs it cannot see.** Holding `Meta` (or any modifier that triggers the background-tab path) while clicking causes bridgic to lose adoption for the popup it just spawned. This is a real but rarely-useful capability — it leaves an "orphan" tab in the user's Chrome that bridgic has no handle on. Do not rely on it as an API; if you ever need "spawn an orphan tab" semantics, prefer an explicit mechanism rather than `key-down Meta` + `click`.

The asymmetry between left-click and Cmd-click is **Chromium behavior, not a bridgic decision**. See "Why Cmd-click strips openerId" below.

### Why Cmd-click strips openerId

Chromium routes new-tab creation through two different code paths inside the browser process, and they treat the parent-tab linkage differently:

| Path | Disposition | New WebContents `opener_` | Resulting `TargetInfo.openerId` |
|---|---|---|---|
| Plain left-click on `<a target=_blank>` (or `window.open()` with user gesture) | `NEW_FOREGROUND_TAB` (or `NEW_POPUP`) via renderer's `CreateNewWindow` IPC | set to the spawner's `WebContents*` | populated |
| Cmd/Ctrl+click, middle-click, "Open Link in New Tab" menu | `NEW_BACKGROUND_TAB` handled directly in the browser process; since Chrome 88 also forces a fresh `BrowsingInstance` | `nullptr` | empty |

The background-tab path is intentionally treated as "user explicitly wanted a detached new tab" — Chromium severs the opener relationship at the browser-process level (not just `window.opener` at the renderer level) so the new tab can be sited in a different process group without security ambiguity. This matters because:

1. `rel="noopener"` / `rel="noreferrer"` / the `'noopener'` feature in `window.open()` only suppress the renderer-level `window.opener` reference. The browser process still records the `opener_` field for its own bookkeeping, and CDP exposes that via `openerId`. So **`rel=noopener` does NOT prevent bridgic adoption** — bridgic operates at the CDP/browser level, below where `rel=noopener` takes effect.
2. Cmd+click clears the opener at the **browser-process** level, which is the same level CDP reads. Hence `openerId` is genuinely empty and bridgic has no signal to adopt.

For bridgic this is convenient: it means the adoption rule double-serves as a permission boundary — when the user *explicitly* opens a tab as "detached" (Cmd+click, Cmd+T, address bar), Chromium itself classifies it as non-bridgic territory, and bridgic naturally inherits that classification.

### Tradeoffs / known limitations

- In CDP-borrowed mode bridgic cannot distinguish a popup that bridgic itself spawned (via `click <ref>`) from one the user spawned by plain left-clicking the same link — both go through the foreground-tab path and both carry `openerId`. By design both are adopted; the popup is in bridgic's working tree either way.
- Conversely, a Cmd+click popup *from* the bridgic-owned tab is invisible to bridgic, even though it is conceptually "spawned by bridgic's page". This is acceptable: Cmd+click is the user's explicit gesture for "detached new tab", and the user can still see/close it in Chrome.
- The popup-follow listener uses `asyncio.create_task` because `context.on("page")` is synchronous but `page.opener()` is async. There is a small window between the new page attaching and adoption finishing; tests use `expect_page` + a short poll loop to bridge it.
- `_close_page` deliberately resolves the fallback target *before* calling `page.close()`, so `closed_page.opener()` is queried while the closed page is still alive.
