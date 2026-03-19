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

## Testing notes

- All tests are async; `asyncio_mode = "auto"` is configured in `pyproject.toml`.
- `@pytest.mark.integration` tests require a real browser and are excluded from `make test-quick`.
- `@pytest.mark.slow` tests can be skipped with `-m "not slow"`.
- The `tests/conftest.py` provides `event_loop` (session-scoped) and `temp_dir` fixtures.
- CLI unit tests in `tests/unit/test_cli.py` (no real browser required).

## Namespace packaging

`bridgic` is a pkgutil-style namespace package shared with `bridgic-core` and `bridgic-llms-openai`. Do not add an `__init__.py` to `bridgic/` itself. The `uv pip install --force-reinstall` in `make test` ensures all three packages coexist correctly in the venv.
