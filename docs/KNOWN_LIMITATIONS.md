# Known Limitations

## Chrome "Show in Folder" Does Not Work for Downloads

### Symptom

When using bridgic-browser in headed mode, files download successfully with correct
filenames to the configured `downloads_path`. However, clicking **"Show in Folder"**
(or "Show in Finder" on macOS) in Chrome's download panel has no effect — the
button does nothing, or shows "file deleted".

### Root Cause

This is a **Chromium bug**: when the CDP command `Browser.setDownloadBehavior`
is called with `eventsEnabled: true`, links on Chrome's download page and
download bubble become non-clickable. The bug was originally reported by a
Puppeteer user ([puppeteer #11871](https://github.com/puppeteer/puppeteer/issues/11871))
and then filed upstream on the Chromium bug tracker:
[chromium #324282051](https://issues.chromium.org/issues/324282051).

**Any tool that uses this CDP command (Puppeteer, Playwright, etc.) is affected.**

Playwright internally uses `Browser.setDownloadBehavior` with
`behavior: 'allowAndName'` to intercept all downloads, so it is equally
affected:

```js
// Playwright internal code (chromium/crBrowser.ts)
behavior: this._options.acceptDownloads === 'accept' ? 'allowAndName' : 'deny'
```

Once `setDownloadBehavior(allowAndName)` is active:

1. Links on `chrome://downloads` page and inside the download bubble
   (including "Show in Folder") become broken.
2. Playwright saves files to an internal temp directory with UUID filenames.
3. bridgic-browser's `DownloadManager` then copies files via `download.save_as()`
   to the user's `downloads_path` with correct filenames.

### Verification

This was verified by testing with **raw Playwright** (no bridgic-browser code):

```python
context = await p.chromium.launch_persistent_context(
    user_data_dir="...",
    headless=False,
    accept_downloads=True,
    downloads_path=str(Path.home() / "Downloads"),
)
```

The same "Show in Folder" failure occurs — confirming it is a Chromium-level bug
triggered by the CDP `setDownloadBehavior` command, not a bridgic-browser issue.

### Workarounds

- **Manual navigation**: Open the downloads folder directly in your file manager.
  The files are saved with correct filenames at the configured `downloads_path`
  (defaults to `~/Downloads` in daemon mode).
- **Programmatic access**: Use `DownloadManager.downloaded_files` to get the list
  of downloaded files with their paths.

### References

- **[Chromium #324282051 — setDownloadBehavior breaks download page links and download bubble (root cause)](https://issues.chromium.org/issues/324282051)**
- [Puppeteer #11871 — Original bug report with reproduction steps](https://github.com/puppeteer/puppeteer/issues/11871)
- [Playwright #19885 — Playwright maintainer confirms setDownloadBehavior as the cause](https://github.com/microsoft/playwright/issues/19885)
- [Playwright Downloads Documentation](https://playwright.dev/python/docs/downloads)
