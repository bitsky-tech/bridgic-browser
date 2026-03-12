# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Bridgic Browser** is an LLM-driven browser automation library built on Playwright with built-in stealth mode. It provides 69 browser tools organized into categories, an accessibility tree-based snapshot system, a stable element reference system (`e1`, `e2`, …) designed for reliable AI agent interactions, and a `bridgic-browser` CLI tool backed by a persistent daemon.

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
make build
make publish repo=testpypi   # or repo=btsk or repo=pypi
```

**Playwright browser binaries**:
```bash
make playwright-install
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
├── tools/            # 69 automation tools
│   ├── _browser_tool_set_builder.py  # BrowserToolSetBuilder + ToolPreset
│   ├── _browser_tool_spec.py         # BrowserToolSpec (wraps tool for agents)
│   ├── _browser_tools.py             # Navigation + page control tools
│   ├── _browser_action_tools.py      # Ref-based element interaction
│   ├── _browser_mouse_tools.py       # Coordinate-based mouse tools
│   └── _browser_*.py                 # Other tool categories
└── cli/              # CLI tool (bridgic-browser command)
    ├── __init__.py       # Exports main()
    ├── _commands.py      # Click command definitions (30 commands, SectionedGroup)
    ├── _client.py        # Socket client: send_command(), ensure_daemon_running()
    └── _daemon.py        # Daemon: asyncio Unix socket server + Browser instance
```

### Core data flow

1. **`Browser`** (`session/_browser.py`) — instantiate and `await start()`. Auto-selects:
   - Isolated mode: `launch()` + `new_context()` (default, no `user_data_dir`)
   - Persistent mode: `launch_persistent_context(user_data_dir)` (preserves cookies/session)

2. **`await browser.get_snapshot()`** → returns `EnhancedSnapshot`:
   - `.tree: str` — accessibility tree lines like `- button "Submit" [ref=e5]`
   - `.refs: Dict[str, RefData]` — maps ref IDs to locator data

3. **`await browser.get_element_by_ref(ref)`** → returns a Playwright `Locator` resolved from the snapshot refs dict.

4. **Tools** are plain async functions that accept `browser` as first arg. Pass them to an LLM agent via `BrowserToolSetBuilder`.

### Element reference system

Refs (`e1`, `e2`, …) are generated during snapshot and stored in `EnhancedSnapshot.refs`. They are the stable, accessibility-aware identifiers used by all `*_by_ref` tools. When a page changes, call `get_snapshot()` again to refresh refs.

### Tool selection

`BrowserToolSetBuilder` supports multiple selection strategies:

```python
# By preset
tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)

# By category
tools = BrowserToolSetBuilder.for_categories(browser, "navigation", "action")

# By function reference
tools = BrowserToolSetBuilder.from_funcs(browser, click_element_by_ref, input_text_by_ref)

# Fluent builder
tools = (BrowserToolSetBuilder(browser)
         .with_preset(ToolPreset.INTERACTIVE)
         .without_tools("take_screenshot")
         .build_specs())
```

**`ToolPreset` sizes**: MINIMAL (11), NAVIGATION (4), SCRAPING (14), FORM_FILLING (20), TESTING (28), INTERACTIVE (39), DEVELOPER (22), COMPLETE (69).

### Snapshot modes

`get_snapshot(interactive=False, full_page=True)`:
- `interactive=True` — flattened list of clickable/editable elements only (best for LLM action selection)
- `full_page=False` — limit to viewport content only
- `get_llm_repr(browser)` tool — returns a truncated string ready for LLM context, with pagination via `start_from_char`

### Stealth

`StealthConfig` (default enabled) applies 50+ Chrome arguments to evade bot detection. Key options:
- `enable_extensions=True` requires `headless=False`
- `docker_mode=True` for container environments
- `cookie_whitelist_domains` for selective cookie retention

### CLI architecture

The `bridgic-browser` CLI uses a **daemon + Unix socket** pattern so the Playwright `Browser` instance persists across multiple short-lived CLI invocations.

```
bridgic-browser click @e2
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
- **`_commands.py`**: 30 Click commands in 10 sections via `SectionedGroup`. `scroll` uses `--dy`/`--dx` options (not positional) to support negative values. `screenshot`/`pdf` call `os.path.abspath()` in the client before sending (daemon cwd may differ). `snapshot` supports `-i`/`--interactive`, `-f/-F`/`--full-page/--no-full-page`, and `-s`/`--start-from-char`; it delegates to `get_llm_repr` (which adds truncation/pagination).
- **`_build_browser_kwargs()`** priority chain (lowest → highest): defaults → `~/.bridgic/bridgic-browser.json` → `./bridgic-browser.json` → `BRIDGIC_BROWSER_JSON` env var → `BRIDGIC_HEADLESS` env var.

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
