# API Summary

Short reference for the main session and download APIs. For tool lists and selection strategies, see [README](../README.md) and [BROWSER_TOOLS_GUIDE.md](BROWSER_TOOLS_GUIDE.md). For snapshot and page state details, see [SNAPSHOT_AND_STATE.md](SNAPSHOT_AND_STATE.md).

## Session (Browser)

| Method / property | Description |
|------------------|-------------|
| `Browser(...)` | Constructor. Key args: `headless`, `viewport`, `user_data_dir`, `clear_user_data`, `stealth`, `cdp`, `channel`, `proxy`, `downloads_path`, etc. When `cdp` is set, connects to an existing Chrome via CDP (`connect_over_cdp`) instead of launching a new browser. Accepts the same inputs as the CLI `--cdp` flag: port number (`"9222"`), `ws://`/`wss://` URL, `http://host:port`, or `"auto"` (scan local Chrome profiles). **The constructor never performs network I/O** — values are stored as-is and resolved to a `ws://` URL lazily inside `await browser._start()` (safe to call from within an event loop). A malformed `cdp` value therefore surfaces as `InvalidInputError` on first use, not at construction. |
| `find_cdp_url(mode, port, host, ...)` | Resolve a Chrome CDP WebSocket URL. `mode`: `"port"` (HTTP `/json/version`), `"file"` (read `DevToolsActivePort`), `"scan"` (auto-discover running Chrome/Chromium/Brave), `"service"` (return `ws_endpoint` as-is). Returns `ws://` URL. |
| `resolve_cdp_input(value)` | Normalize user-supplied CDP input to a `ws://` URL. Accepts: bare port (`"9222"`), `ws://`/`wss://` URL, `http://host:port`, or `"auto"`/`"scan"`. |
| `await browser._start()` | Launch browser and create context. Called automatically by `navigate_to` / `search` (lazy start); call directly only when you need explicit startup before any navigation. |
| `await browser.close()` | Stop the browser, auto-cleans active capture listeners. No-op if never started. |
| `await browser.navigate_to(url, wait_until="domcontentloaded", timeout=None)` | Navigate to URL with optional auto-prefix when missing protocol. `wait_until`: `"domcontentloaded"` (default), `"load"`, `"networkidle"`, or `"commit"`. `timeout` in seconds. |
| `await browser.get_snapshot(interactive=False, full_page=True)` | Get `EnhancedSnapshot` (`.tree`, `.refs`). Raises `StateError` if no active page, `OperationError` if generation fails. Never returns `None`. |
| `await browser.get_element_by_ref(ref)` | Get Playwright `Locator` for ref (e.g. `"8d4b03a9"`) or `None` if not found. Uses last cached snapshot refs — call `get_snapshot()` first. |
| `await browser.get_current_page()` | Get current Playwright `Page` or `None`. |
| `await browser.get_current_page_title()` | Get current page title string, or `None` if no page is open. |
| `browser.get_current_page_url()` | Get current page URL string, or `None` if no page is open. (sync) |
| `browser.get_config()` | Return dict of all current browser configuration options. |
| `browser.download_manager` | `DownloadManager` instance (after `start()`), or `None` if `downloads_path` not set. |
| `browser.downloaded_files` | Shortcut for `browser.download_manager.downloaded_files`. Returns `[]` if no download manager. |
| `browser.headless` | `bool` — whether the browser runs in headless mode. |
| `browser.viewport` | `dict` or `None` — current viewport size configuration. |
| `browser.channel` | `str` or `None` — browser distribution channel. |
| `browser.user_data_dir` | `Path` or `None` — explicit custom profile directory, or `None` when using the default `~/.bridgic/bridgic-browser/user_data/`. Ignored when `clear_user_data=True` (ephemeral mode). |
| `browser.clear_user_data` | `bool` — `True` for ephemeral mode (no persistent profile); `False` (default) for persistent profile. |
| `browser.stealth_enabled` | `bool` — whether stealth mode is active. |
| `browser.stealth_config` | `StealthConfig` or `None` — current stealth configuration. |
| `browser.use_persistent_context` | `bool` — `True` when using `launch_persistent_context` (`clear_user_data=False`); `False` when using ephemeral `launch`+`new_context` (`clear_user_data=True`). |
| `browser.last_close_artifacts` | `dict` — trace and video paths produced by the most recent `close()` call. Shape: `{"trace": [str, ...], "video": [str, ...]}`. Empty lists before the first close, or when no tracing/video was active. Returns a fresh shallow copy on every access — mutating it does not affect the browser's internal state. |
| `browser.last_close_errors` | `list[str]` — warnings/errors collected during the most recent `close()` call (e.g. trace-stop timeouts, video-finalize failures). Empty list before the first close, or on a clean shutdown. Returns a fresh copy on every access. |

## DownloadManager

`Browser` creates and manages a `DownloadManager` automatically when `downloads_path` is provided. Access it via `browser.download_manager` after the browser has started (via `navigate_to()`, `search()`, or `_start()`).

| Method / property | Description |
|------------------|-------------|
| `browser.download_manager` | The auto-created `DownloadManager` (None if `downloads_path` not set). |
| `browser.download_manager.downloaded_files` | List of `DownloadedFile` (`.url`, `.path`, `.file_name`, `.file_size`). |

## Snapshot and state (see SNAPSHOT_AND_STATE.md)

- **SnapshotOptions**: `interactive`, `full_page`.
- **EnhancedSnapshot**: `.tree`, `.refs` (ref id → RefData).
- **await browser.get_snapshot_text(limit=10000, interactive=False, full_page=True, file=None)**: Page state string for LLM; when content exceeds limit or file is explicitly provided, full snapshot is saved to file and only a notice with the file path is returned.
