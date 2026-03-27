# API Summary

Short reference for the main session and download APIs. For tool lists and selection strategies, see [README](../README.md) and [BROWSER_TOOLS_GUIDE.md](BROWSER_TOOLS_GUIDE.md). For snapshot and page state details, see [SNAPSHOT_AND_STATE.md](SNAPSHOT_AND_STATE.md).

## Session (Browser)

| Method / property | Description |
|------------------|-------------|
| `Browser(...)` | Constructor. Key args: `headless`, `viewport`, `user_data_dir`, `stealth`, `channel`, `proxy`, `downloads_path`, etc. |
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
| `browser.user_data_dir` | `Path` or `None` — persistent context directory, or `None` for isolated mode. |
| `browser.stealth_enabled` | `bool` — whether stealth mode is active. |
| `browser.stealth_config` | `StealthConfig` or `None` — current stealth configuration. |
| `browser.use_persistent_context` | `bool` — `True` when using `launch_persistent_context` (either `user_data_dir` set or headed mode). |

## DownloadManager

`Browser` creates and manages a `DownloadManager` automatically when `downloads_path` is provided. Access it via `browser.download_manager` after the browser has started (via `navigate_to()`, `search()`, or `_start()`).

| Method / property | Description |
|------------------|-------------|
| `browser.download_manager` | The auto-created `DownloadManager` (None if `downloads_path` not set). |
| `browser.download_manager.downloaded_files` | List of `DownloadedFile` (`.url`, `.path`, `.file_name`, `.file_size`). |

## Snapshot and state (see SNAPSHOT_AND_STATE.md)

- **SnapshotOptions**: `interactive`, `full_page`.
- **EnhancedSnapshot**: `.tree`, `.refs` (ref id → RefData).
- **await browser.get_snapshot_text(offset=0, limit=10000, interactive=False, full_page=True)**: Page state string for LLM, with optional pagination.
