# API Summary

Short reference for the main session and download APIs. For tool lists and presets, see [README](../README.md) and [BROWSER_TOOLS_GUIDE.md](BROWSER_TOOLS_GUIDE.md). For snapshot and page state details, see [SNAPSHOT_AND_STATE.md](SNAPSHOT_AND_STATE.md).

## Session (Browser)

| Method / property | Description |
|------------------|-------------|
| `Browser(...)` | Constructor. Key args: `headless`, `viewport`, `user_data_dir`, `stealth`, `channel`, `proxy`, `downloads_path`, etc. |
| `await browser.start()` | Launch browser and create context. |
| `await browser.close()` | Close browser and release resources. |
| `await browser.kill()` | Force kill the browser process. |
| `await browser.navigate_to(url)` | Navigate current page to URL. |
| `await browser.get_snapshot(interactive=False, full_page=False)` | Get `EnhancedSnapshot` (`.tree`, `.refs`). |
| `await browser.get_element_by_ref(ref)` | Get Playwright `Locator` for ref (e.g. `"e1"`); uses last snapshot. |
| `await browser.get_current_page()` | Get current Playwright `Page` or None. |
| `browser.get_current_page_url()` | Get current page URL string. |
| `browser.context` | Playwright `BrowserContext` (after `start()`). |

## DownloadManager

| Method / property | Description |
|------------------|-------------|
| `DownloadManager(config=...)` | Config: `downloads_path`, `auto_save`, `overwrite`, optional callbacks. |
| `manager.attach_to_context(browser.context)` | Attach to a context so downloads are handled with correct filenames. |
| `manager.downloaded_files` | List of `DownloadedFile` (e.g. `url`, `path`, `file_name`, `file_size`). |

## Snapshot and state (see SNAPSHOT_AND_STATE.md)

- **SnapshotOptions**: `interactive`, `full_page`.
- **EnhancedSnapshot**: `.tree`, `.refs` (ref id → RefData).
- **get_llm_repr(browser, start_from_char=0, interactive=False, full_page=False)**: Page state string for LLM, with optional pagination.
