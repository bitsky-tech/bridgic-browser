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

**Mode matrix QA** (real-browser coverage across all link modes × display modes):
```bash
bash scripts/qa/run-mode-matrix.sh                            # full V1..V7 matrix
BRIDGIC_QA_VARIANTS="V1" bash scripts/qa/run-mode-matrix.sh   # single-variant regression
```
Report: `$QA_DIR/mode-matrix/mode-matrix-report.md`. Per-variant semantics and expected N/A in `scripts/qa/mode-matrix-scenarios.md`.

## Architecture

### Package structure

```
bridgic/browser/
├── __main__.py       # Entry point: routes `daemon` subcommand vs CLI
├── _config.py        # Config file loading (shared by SDK + CLI daemon)
├── _cli_catalog.py   # CLI_COMMAND_TO_TOOL_METHOD + CLI_HELP_SECTION_SPECS (SSoT for command/category mapping)
├── _constants.py     # ToolCategory enum + path constants (BRIDGIC_BROWSER_HOME, etc.)
├── _timeouts.py      # Shared timeout budgets + env-var-overridable knobs
├── _redact.py        # Log redaction helpers
├── errors.py         # Public BridgicBrowserError hierarchy
├── session/          # Core browser session
│   ├── _browser.py        # Browser class – main entry point (all 67 tool methods live here)
│   ├── _browser_model.py  # Data models
│   ├── _snapshot.py       # SnapshotGenerator + EnhancedSnapshot + RefData
│   ├── _stealth.py        # StealthConfig + StealthArgsBuilder (50+ Chrome args)
│   ├── _download.py       # DownloadManager
│   ├── _video_recorder.py # VideoRecorder (CDP screencast → ffmpeg)
│   ├── _cdp_discovery.py  # find_cdp_url + resolve_cdp_input (port / file / scan / service modes)
│   ├── _launch.py         # launch-mode helpers (retriable_launch, etc.)
│   ├── _locator_utils.py  # _click_checkable_target and other locator helpers
│   └── _errors.py         # session-internal error types
├── tools/            # 67 automation tools (all implemented in _browser.py)
│   ├── _browser_tool_set_builder.py  # BrowserToolSetBuilder (category/name selection)
│   └── _browser_tool_spec.py         # BrowserToolSpec (wraps tool for agents)
└── cli/              # CLI tool (bridgic-browser command)
    ├── __init__.py    # Exports main()
    ├── _commands.py   # Click command definitions (67 commands, SectionedGroup)
    ├── _client.py     # Socket client: send_command(), ensure_daemon_running()
    ├── _daemon.py     # Daemon: asyncio Unix socket server + Browser instance
    └── _transport.py  # Unix-socket transport layer (used by client and daemon)
```

### Core data flow

1. **`Browser`** (`session/_browser.py`) — instantiate; browser starts lazily on first `navigate_to` / `search`, or explicitly via `async with Browser(...) as b:` (calls `_start()`). `Browser()` **automatically loads config** from `~/.bridgic/bridgic-browser/bridgic-browser.json` → `./bridgic-browser.json` → `BRIDGIC_BROWSER_JSON` env var (via `_config.py:_load_config_sources()`). Explicit constructor params override config values; `headless` and `stealth` default to `None` (resolved to `True` if no config present). Auto-selects:
   - Persistent mode (default, `clear_user_data=False`): `launch_persistent_context(user_data_dir)` — uses provided `user_data_dir`, or `~/.bridgic/bridgic-browser/user_data/` by default. Actual profile is always placed under a mode-specific subdir (`<base>/headed` or `<base>/headless`) so headed/headless Chromium can't collide on `SingletonLock`. The public `Browser.user_data_dir` property still returns the base path the user supplied.
   - Ephemeral mode (`clear_user_data=True`): `launch()` + `new_context()` — no profile, `user_data_dir` ignored

2. **`await browser.get_snapshot()`** → returns `EnhancedSnapshot`:
   - `.tree: str` — accessibility tree lines like `- button "Submit" [ref=8d4b03a9]`
   - `.refs: Dict[str, RefData]` — maps ref IDs to locator data

3. **`await browser.get_element_by_ref(ref)`** → returns a Playwright `Locator` resolved from the snapshot refs dict.

4. **Tools** are bound async methods on the `Browser` class. Pass them to an LLM agent via `BrowserToolSetBuilder`.

### Owned-page tracking

`Browser` maintains an internal `_owned_pages` set + `_focus_stack` so all public tab operations (`get_pages` / `get_tabs` / `switch_tab` / `close_tab`) only see pages bridgic created or adopted. In **CDP borrowed mode** (`connect_over_cdp` against a user's running Chrome) pre-existing user tabs stay invisible to bridgic; in **non-CDP modes** every page in the context is seeded as owned at start, so the filter degenerates to identity and behaviour matches pre-refactor semantics.

- **Adoption**: `context.on("page")` listener calls `_maybe_adopt_page` → adopts iff `await page.opener()` is already owned. Pages bridgic creates via `_new_page()` are owned unconditionally. *Whether `opener()` returns a parent depends on Chromium's navigation disposition, not on who clicked: foreground-tab navigations (programmatic click, user plain left-click, `window.open()` with user gesture) preserve `openerId`; background-tab navigations (Cmd/Ctrl+click, middle-click, Cmd+T, address bar) clear it at the browser-process level. `rel="noopener"` only suppresses JS-level `window.opener` and does NOT prevent adoption. bridgic itself can bypass adoption by holding `Meta` via `key-down` before click (CDP `Input.dispatchMouseEvent.modifiers` propagates the held key) — role-agnostic, behavior-driven. Full matrix in [`docs/INTERNALS.md#adoption-truth-table-cdp-borrowed-mode`](docs/INTERNALS.md#adoption-truth-table-cdp-borrowed-mode).*
- **Popup follow**: when `auto_follow_popups=True` (default) and the popup's opener is `self._page`, `self._page` moves to the popup (mirrors Chrome's "new tab takes foreground" UX). Disable by passing `auto_follow_popups=False` to the constructor or via the same key in the config file.
- **Close fallback**: `_close_page` resolves a successor via `_select_fallback_page` in four tiers — `closed_page.opener()` → `_focus_stack` top — `get_pages()[0]` → `None`. `closed_page.opener()` is queried *before* `page.close()` is awaited so the opener relationship is still resolvable.

See [`docs/INTERNALS.md` — Owned-page Tracking](docs/INTERNALS.md#owned-page-tracking) for the full design and tradeoffs.

### Downloads

bridgic has two independent download pipelines, picked by mode:

| Mode | Pipeline | Notes |
|---|---|---|
| non-CDP (launch / persistent_context) | Playwright's per-context `setDownloadBehavior(allowAndName, downloadPath=<artifactsDir>)` → `download` events fire → `DownloadManager.save_as()` copies to `downloads_path` with the real filename. | Files land at the real filename in `downloads_path`. If `downloads_path` is unset, DownloadManager is not attached and files are lost when Playwright deletes `artifactsDir` on close. |
| CDP-owned (bridgic creates its own context on the remote Chrome) | Same as non-CDP: Playwright's per-context `allowAndName` routes through `artifactsDir`, DownloadManager copies. | Per-context override targets bridgic's own context, doesn't touch the user. |
| **CDP-borrowed** (`Browser(cdp=...)` against a user's running Chrome) | bridgic's own override on bridgic's tab: `Browser.setDownloadBehavior(allowAndName, downloadPath=<effective>, eventsEnabled=true)` sent **via the page CDP session** (`BrowserContext.new_cdp_session(self._page)`). `CdpDownloadRenamer` subscribes to `Browser.downloadWillBegin/downloadProgress` on the same session and renames `<dir>/<guid>` → `<dir>/<real name>` on completion. | Page-session routing is the *only* form Chrome 138+ honors when the user has "Ask where to save each file" enabled — `Browser.setDownloadBehavior` over a browser-level session and `Page.setDownloadBehavior(allow, ...)` both still pop the dialog. See [empirically-tried alternatives](#empirically-tried-alternatives-for-cdp-borrowed-downloads) below. |

#### Effective download path

`Browser._effective_cdp_downloads_path(client_cwd=None)` resolves the path in CDP-borrowed mode:

1. Explicit `Browser(downloads_path=...)` constructor arg or `bridgic-browser.json` config — always wins.
2. `client_cwd` (per-command) or `self._pending_client_cwd` (set by the daemon before `_start()`). The CLI client puts `os.getcwd()` in every socket request; the daemon sets the hint pre-dispatch so the first lazy-start L1 sees it too. Gives `bridgic-browser` `curl -O`-style ergonomics — files land where the user ran the command.
3. `~/Downloads` fallback.

In non-CDP / CDP-owned modes the path is `self._downloads_path` only (CWD plumbing doesn't apply because DownloadManager is the pipeline). The CLI daemon **skips** its auto-default `downloads_path=~/Downloads` when `BRIDGIC_CDP` is set — otherwise that default would be indistinguishable from a user-explicit value and silently win over the CWD priority above.

#### CDP-borrowed flow detail

**L1 (post-connect, `_set_cdp_download_behavior` with `session=<page CDP session>`)**: after creating bridgic's tab, send `Browser.setDownloadBehavior(allowAndName, downloadPath=<effective>, eventsEnabled=true)` via `self._cdp_download_session = await self._context.new_cdp_session(self._page)`. Same session attaches `CdpDownloadRenamer`. Only runs in CDP-borrowed mode (`not self._cdp_context_owned`).

**Per-command `cwd-update` (`update_cdp_downloads_path`)**: re-sends the same command (still via the page session) when the daemon's `client_cwd` resolves to a different effective path than `self._current_cdp_download_path`. Short-circuits when path is unchanged or in non-borrowed modes. The renamer's default target is updated for *future* downloads — in-flight downloads keep the dir captured at their `downloadWillBegin` time.

**L2 rescue (pre-close, `_rescue_cdp_orphan_downloads`)**: scans every `playwright-artifacts-*` under the OS tempdir and moves orphan files to `~/Downloads/bridgic-rescue-<name>` before `browser.close()` triggers Playwright's `removeFolders([artifactsDir])`. The defense covers downloads from the user's other tabs that Playwright captured into its tempdir (per-context override on the borrowed default context still routes there); skips trace/video/HAR artifacts and files DownloadManager already saved. Mostly a no-op now that bridgic's own tab uses page-session routing, but kept as defense in depth.

**L3 (pre-close)**: send `Browser.setDownloadBehavior(behavior="default")` over the page session, then detach renamer + session. Chrome reverts to its native prefs for any post-disconnect downloads on the user's tabs.

#### Filename preservation (CdpDownloadRenamer)

`allowAndName` writes files as `<downloadPath>/<guid>` (e.g. `08d0c134-9231-478e-aca1-08b3e0ec1798`). `_cdp_download_renamer.py:CdpDownloadRenamer`:

1. On `Browser.downloadWillBegin`, records `{guid → (sanitized suggestedFilename, target_dir)}` — target dir snapshotted so a concurrent CWD swap doesn't retarget files mid-flight.
2. On `Browser.downloadProgress.state="completed"`, renames `<target_dir>/<guid>` → `<target_dir>/<real name>`. Conflicts resolve to `name (1).ext`, `name (2).ext` (Chrome's scheme).
3. On `state="canceled"`, removes the GUID stub.

`sanitize_filename()` strips path separators, Windows-forbidden chars (`< > : " | ? *`), control bytes, and truncates to 255 bytes while preserving the extension. Empty result → `"download"`.

#### Empirically-tried alternatives for CDP-borrowed downloads

Verified against Chrome 138, macOS, with "Ask where to save each file" preference **on** (the default in many regions):

| Attempt | Result | Verdict |
|---|---|---|
| `Page.setDownloadBehavior(allow, downloadPath=...)` | Dialog still pops. | ❌ |
| `Browser.setDownloadBehavior(allow, downloadPath=...)` via browser CDP session | Dialog still pops; CDP accepts but Chrome honors user pref. | ❌ |
| `Browser.setDownloadBehavior(allowAndName, downloadPath=...)` via browser CDP session | Dialog still pops. | ❌ |
| `Browser.setDownloadBehavior(allowAndName, downloadPath=..., browserContextId=<defaultBrowserContextId from Target.getBrowserContexts>)` | Chrome rejects: `Failed to find browser context for id <X>`. (Playwright's own call uses `browserContextId=undefined` — see `crBrowser.js:89 new CRBrowserContext(browser, void 0, ...)`.) | ❌ |
| `Browser.setDownloadBehavior(allowAndName, downloadPath=..., eventsEnabled=true)` via **page** CDP session (`ctx.new_cdp_session(page)`) | Silent download, real filename via post-completion rename, `downloadWillBegin/Progress` events fire on the same session. | ✅ chosen |

agent-browser's `Some(session_id)` argument is the same trick — page-level CDP routing.

#### Caveats

- **bridgic's tab gets the override; user's tabs keep their normal Chrome UX** (intentional — the page-session scope is bridgic's tab only). User-initiated downloads in their other tabs still go to their Chrome's configured directory and obey their "Ask where to save" pref. This is by design and matches the "I gave you full control of *my agent's* tab via `--cdp`" semantics — user's private workspace is untouched.
- **DownloadManager is not attached in CDP-borrowed mode.** Chrome writes directly to the final path; Playwright's per-context `download` event doesn't fire when the file is routed away from `artifactsDir`. `wait_for_download()` is correspondingly **unsupported in CDP-borrowed mode** — use CDP-owned or non-CDP for that.
- **The renamer is best-effort.** If a CDP event is missed or the OS rename fails (cross-FS, permission, etc.) the file stays at its GUID path with a warning logged. It never deletes content.
- **`last_close_artifacts()`** exposes a `rescued_downloads` list when L2 actually moved anything.
- **"Show in Folder"** in Chrome's download bubble is broken whenever `setDownloadBehavior(allowAndName, eventsEnabled=true)` is active. This is a Chromium bug (`#324282051`) affecting all CDP-using tools. See [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

### Tool selection

`BrowserToolSetBuilder` selects tools by category or name (combinable):

```python
builder = BrowserToolSetBuilder.for_categories(browser, "navigation", "element_interaction")
tools = builder.build()["tool_specs"]
```

Also available: `for_tool_names(browser, "click_element_by_ref", ...)` and combining multiple builders. See `docs/BROWSER_TOOLS_GUIDE.md` for full examples.

### Snapshot modes

`get_snapshot(interactive=False, full_page=True)`:
- `interactive=True` — flattened list of clickable/editable elements only (best for LLM action selection)
- `full_page=False` — limit to viewport content only
- `await browser.get_snapshot_text(...)` — returns a string ready for LLM context; when content exceeds `limit` (default 10000) or `file` is explicitly provided, full snapshot is saved to a file and only a notice with the file path is returned

### Stealth

`StealthConfig` (default enabled) applies Chrome arguments and a JS init script to evade bot detection. The strategy is **mode-aware**: headless mode uses a full 50+ flag set; headed mode uses a minimal ~11 flag set to match real Chrome user behavior.

Key decisions and constraints:
- **New headless redirect** (`use_new_headless=True`, default): bridgic passes `headless=False` to Playwright (selecting the full Chromium binary) and manually adds `--headless=new` + scrollbar/audio/blink flags. `Browser._headless` = user's intent; `options["headless"]` = binary selection.
- **Headed mode auto-switches to system Chrome**: Playwright's bundled "Chrome for Testing" is blocked by Google OAuth. When stealth is enabled in headed mode and system Chrome is detected, bridgic sets `channel="chrome"` automatically. `--test-type=` suppresses the "unsupported flag" warning banner.
- **JS init script is headless-only**: skipped in headed mode because `add_init_script()` runs in ALL frames including Cloudflare Turnstile's challenge iframe — patching `window.chrome`/`navigator.permissions.query`/WebGL inside it causes detectable inconsistencies that fail the challenge.
- **Anti-toString (`_mkNative`)**: all patched functions return `"function name() { [native code] }"` via intercepted `Function.prototype.toString` to defeat DataDome/PerimeterX/Cloudflare `.toString()` probing.

#### Iframe-safety rule (CRITICAL)

> **Any patch that can propagate into a cross-origin iframe MUST be gated to `self._headless`.**

The Cloudflare Turnstile / hCaptcha challenge runs inside a cross-origin iframe (`challenges.cloudflare.com`). When a patch leaks into that iframe, the challenge worker sees navigator/Worker/UA values that don't match Cloudflare's edge-server expectation → instant bot signal → challenge fails silently.

The currently gated mechanisms are:

| Mechanism | Headless | Headed | Why |
|---|---|---|---|
| Main `_STEALTH_INIT_SCRIPT_TEMPLATE` (webdriver, plugins, chrome obj, WebGL, …) | ✅ injected | ❌ skipped | `add_init_script` runs in all frames |
| **R1 — Context `user_agent` fallback + CDP `Emulation.setUserAgentOverride`** | ✅ active | ❌ skipped | CDP UA override propagates to all frames in the target |
| **R3 — `page.on('worker')` worker stealth injection** | ✅ active | ❌ skipped | `page.workers` includes workers spawned by cross-origin iframes |
| Anti-devtools-detector script | ✅ injected | ✅ injected (with `if (window !== window.top) return;` guard inside) | Self-gates to top frame |
| **R3 — `Worker` / `SharedWorker` / `serviceWorker.register` constructor wrap** (in main init script) | ✅ wrapped | ❌ (whole script skipped) | Wrapped section has its own `if (window === window.top)` guard so even if main script runs in iframes, this part doesn't |

#### Iframe-safe checklist (run before merging any new stealth patch)

1. Does the patch live in `_STEALTH_INIT_SCRIPT_TEMPLATE` (runs in all frames)? If yes, ask: would patching this in a cross-origin Cloudflare iframe create an inconsistency vs. what Cloudflare's server logged for the parent page request?
2. Does the patch use a CDP override (`Emulation.*`, `Network.*`, `Page.*`)? CDP overrides apply to the whole target including all its frames. Gate to `self._headless` unless you've verified iframe consistency.
3. Does the patch hook `page.on('worker')` / `context.on('serviceworker')`? Workers can be spawned by any frame in the page tree — same rule.
4. Does the patch wrap a global constructor like `Worker`, `WebSocket`, `RTCPeerConnection`? Wrap inside `if (window === window.top) { ... }` if the wrap result is observably different from the original.
5. Run the 3-site headed verification (`bash scripts/qa/...` or manual): `https://chat-auto-team.pages.dev/redeem` (Cloudflare), `https://x.com` (server-side detection), `https://blog.aepkill.com/demos/devtools-detector/` (devtools probe). Any of these breaking is a hard block.

For the full list of patched navigator/window properties, see [`docs/INTERNALS.md` — Stealth JS Init Script](docs/INTERNALS.md#stealth-js-init-script--patched-properties). For the design rationale of mode-aware stealth, see [`docs/INTERNALS.md` — Mode-aware stealth design](docs/INTERNALS.md#mode-aware-stealth-design). For known capability boundaries, see [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md).

### CLI architecture

The `bridgic-browser` CLI uses a **daemon + Unix socket** pattern so the Playwright `Browser` instance persists across multiple short-lived CLI invocations.

```
bridgic-browser click @8d4b03a9
       │
       ▼
  _client.py                 Unix socket                   _daemon.py
 send_command("click",...)   ~/.bridgic/bridgic-browser/run/bridgic-browser.sock    asyncio server
       │──── JSON request ─────────────────────────────►  + Browser instance
       │◄─── JSON response ────────────────────────────   dispatch → tool fn()
```

Key behaviors:
- **Lazy start**: daemon creates `Browser()` but Playwright doesn't launch until the first command that needs a page (e.g. `navigate_to`).
- **Config flags**: `--headed` merges `{"headless": false}` into `BRIDGIC_BROWSER_JSON`; `--clear-user-data` merges `{"clear_user_data": true}`; `--cdp` resolves CDP input via `resolve_cdp_input()` on the client side and passes the `ws://` URL to the daemon via `BRIDGIC_CDP` env var.
- **Close fast-path**: daemon pre-allocates artifact paths, responds immediately, then runs `browser.close()` after the client disconnects. `close-report.json` records status and artifact paths.
- **Cleanup ownership guard**: after close, the daemon compares the run-info `pid` to `os.getpid()` before deleting the socket — prevents a new daemon's socket from being deleted by an old daemon still shutting down.
- **Socket path**: `BRIDGIC_SOCKET` env var (default `$BRIDGIC_HOME/bridgic-browser/run/bridgic-browser.sock`), directory created with `0o700` permissions.
- **Home directory**: `BRIDGIC_HOME` env var (default `~/.bridgic`). All daemon state paths (run info, socket, logs, tmp, user config, user data) derive from this. Set different values to run multiple independent daemon instances.
For detailed implementation notes on client/daemon/commands, see [`docs/INTERNALS.md` — CLI Architecture](docs/INTERNALS.md#cli-architecture--detailed-implementation).

## Ref System Internals

bridgic has **two co-existing ref systems**: the stable bridgic ref (`"8d4b03a9"`, SHA-256 based, stable across snapshots) and the ephemeral playwright_ref (`"e369"`, per-snapshot incrementing integer, used for O(1) DOM lookup). `get_element_by_ref()` uses a **two-phase lookup**: first tries the aria-ref fast path (O(1) Map lookup via playwright_ref), then falls back to a CSS rebuild path with 6 strategy tiers. All paths chain `frame_locator("iframe").nth(n)` per `frame_path` level for iframe support.

Key constraints:
- `frame_path` (per-level local indices) is unrelated to Playwright's `frame.seq` (page-level global counter).
- **Covered-element check** uses `window.parent !== window` (not `window.frameElement !== null`) to detect iframes — the latter returns `null` under `file://` protocol. Iframe elements skip the check entirely because `bounding_box()` returns main-viewport coordinates while `elementFromPoint()` uses iframe-local coordinates.
- **Small icon rule**: icons 10–50 px are interactive only with `data-action` or `aria-label` (not `classAndId` — too many false positives).

For complete source-level documentation of Playwright internals, ref generation, lookup strategies, and iframe handling, see [`docs/INTERNALS.md`](docs/INTERNALS.md).

## Debug Logging

```bash
BRIDGIC_LOG_LEVEL=DEBUG bridgic-browser snapshot -i
BRIDGIC_LOG_LEVEL=DEBUG bridgic-browser click <ref>
```

Key DEBUG log points (`_browser.py`):
- `[get_element_by_ref] aria-ref fast-path hit/stale/exception` — ref lookup phase transitions
- `[get_element_by_ref] CSS path: ref=... role=... name=... nth=... frame_path=...` — fallback strategy
- `[click_element_by_ref] covered at (x, y), clicking intercepting element` — covered-element redirect

## Testing notes

- All tests are async; `asyncio_mode = "auto"` is configured in `pyproject.toml`.
- `@pytest.mark.integration` tests require a real browser and are excluded from `make test-quick`.
- `@pytest.mark.slow` tests can be skipped with `-m "not slow"`.
- The `tests/conftest.py` provides `event_loop` (session-scoped) and `temp_dir` fixtures.
- CLI unit tests in `tests/unit/test_cli.py` (no real browser required).

## Namespace packaging

`bridgic` is a pkgutil-style namespace package shared with `bridgic-core` and `bridgic-llms-openai`. Do not add an `__init__.py` to `bridgic/` itself. The `uv pip install --force-reinstall` in `make test` ensures all three packages coexist correctly in the venv.
