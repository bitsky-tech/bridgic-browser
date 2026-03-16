# API Summary

Short reference for the main session and download APIs. For tool lists and presets, see [README](../README.md) and [BROWSER_TOOLS_GUIDE.md](BROWSER_TOOLS_GUIDE.md). For snapshot and page state details, see [SNAPSHOT_AND_STATE.md](SNAPSHOT_AND_STATE.md).

## Session (Browser)

| Method / property | Description |
|------------------|-------------|
| `Browser(...)` | Constructor. Key args: `headless`, `viewport`, `user_data_dir`, `stealth`, `channel`, `proxy`, `downloads_path`, etc. |
| `await browser.start()` | Launch browser and create context. |
| `await browser.stop()` | Stop the browser, auto-cleans active capture listeners. |
| `await browser.navigate_to(url)` | Navigate current page to URL. |
| `await browser.get_snapshot(interactive=False, full_page=True)` | Get `EnhancedSnapshot` (`.tree`, `.refs`). |
| `await browser.get_element_by_ref(ref)` | Get Playwright `Locator` for ref (e.g. `"e1"`); uses last snapshot. |
| `await browser.get_current_page()` | Get current Playwright `Page` or None. |
| `browser.get_current_page_url()` | Get current page URL string. |
| `browser.download_manager` | `DownloadManager` instance (after `start()`), or None if `downloads_path` not set. |

## DownloadManager

`Browser` creates and manages a `DownloadManager` automatically when `downloads_path` is provided. Access it via `browser.download_manager` after `start()`.

| Method / property | Description |
|------------------|-------------|
| `browser.download_manager` | The auto-created `DownloadManager` (None if `downloads_path` not set). |
| `browser.download_manager.downloaded_files` | List of `DownloadedFile` (`.url`, `.path`, `.file_name`, `.file_size`). |

## Snapshot and state (see SNAPSHOT_AND_STATE.md)

- **SnapshotOptions**: `interactive`, `full_page`.
- **EnhancedSnapshot**: `.tree`, `.refs` (ref id → RefData).
- **await browser.get_snapshot_text(start_from_char=0, interactive=False, full_page=True)**: Page state string for LLM, with optional pagination.
