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
