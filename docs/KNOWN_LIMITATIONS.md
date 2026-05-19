# Known Limitations

## Chrome "Show in Folder" Does Not Work for Downloads

### Symptom

When using bridgic-browser, files download successfully with correct
filenames to the configured target path. However, clicking **"Show in Folder"**
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

`eventsEnabled: true` is required for download tracking in every bridgic
download pipeline:

- **Non-CDP / CDP-owned**: Playwright sets it internally via
  `Browser.setDownloadBehavior(allowAndName, ..., eventsEnabled: true)` on
  bridgic's context (`crBrowser.ts`); `DownloadManager.save_as()` copies the
  GUID-named tempfile to `downloads_path` with the real filename.
- **CDP-borrowed**: bridgic sets it directly via a page-level CDP session,
  and `CdpDownloadRenamer` renames the GUID → real filename post-completion.
  See [CLAUDE.md → Downloads](../CLAUDE.md#downloads).

All three pipelines therefore trip the same Chromium bug — files end up
at the configured target with correct filenames, but Chrome's *own* "Show
in Folder" UI cannot resolve them.

### Verification

Verified with **raw Playwright** (no bridgic-browser code):

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

- **Manual navigation**: Open the downloads folder directly in your file
  manager. Files are saved with correct filenames at the target path — see
  the [download path matrix](../README.md#downloads).
- **Programmatic access (non-CDP / CDP-owned)**: Use
  `browser.download_manager.downloaded_files` to get the list of downloaded
  files with their paths.
- **Programmatic access (CDP-borrowed)**: `download_manager` is `None`; the
  renamer logs each completed download to the daemon log
  (`[CdpDownloadRenamer] <guid-prefix> → <real-name>`). For deterministic
  capture, set `downloads_path` and poll the directory after the click.

### References

- **[Chromium #324282051 — setDownloadBehavior breaks download page links and download bubble (root cause)](https://issues.chromium.org/issues/324282051)**
- [Puppeteer #11871 — Original bug report with reproduction steps](https://github.com/puppeteer/puppeteer/issues/11871)
- [Playwright #19885 — Playwright maintainer confirms setDownloadBehavior as the cause](https://github.com/microsoft/playwright/issues/19885)
- [Playwright Downloads Documentation](https://playwright.dev/python/docs/downloads)

---

## Popup-triggered Downloads in CDP-borrowed Mode Are Not Captured

### Symptom

In **CDP-borrowed** mode (`Browser(cdp=...)` against a user's running Chrome), if bridgic opens a popup (e.g. a `<a target="_blank">` click that auto-follows, or a `window.open()` flow) and the download is triggered **from that popup**, then:

- `await browser.wait_for_next_download(timeout=N)` times out and returns the "No download completed within Ns timeout." message.
- `await browser.get_downloaded_files_text()` does not list the file.
- `browser.downloaded_files` does not contain the file.
- Chrome may show its native "Save As" dialog (depending on the user's "Ask where to save each file" preference) or save silently to Chrome's configured Downloads directory.

Downloads triggered from **bridgic's primary tab** (`self._page`) work normally and surface through all the APIs above.

### Root Cause

In CDP-borrowed mode bridgic takes over downloads by sending
`Browser.setDownloadBehavior(behavior="allowAndName", downloadPath=..., eventsEnabled=true)` over a **page-level CDP session** attached to `self._page`. This is the only CDP form that bypasses Chrome's "Ask where to save each file" user preference (Chrome 138+); a browser-level session still pops the dialog.

The trade-off: a page-level `setDownloadBehavior` scopes to **that target only**. When bridgic auto-follows a popup, `self._page` moves to the popup, but the CDP session and `CdpDownloadRenamer` stay bound to the original page session — they cannot observe download events fired on the popup target.

### Workarounds

1. **Avoid auto-follow**: trigger the download from a regular link in bridgic's primary tab, not a `target="_blank"` link.
2. **Pre-arm the popup**: if you control the page, change `target="_blank"` to a same-page link, or download via `fetch()` + `URL.createObjectURL(...)` from bridgic's primary tab.
3. **Switch modes**: use **CDP-owned** (`Browser(cdp=...)` against a remote Chrome with no contexts yet) or non-CDP mode — both attach `DownloadManager` to the whole context, so any tab's downloads are captured.

### Verification

`tests/integration/test_owned_pages.py::test_popup_follow_does_not_attach_download_manager` guards the invariant that DM stays unattached after popup follow in CDP-borrowed mode — confirming the limitation is by design, not an accidental wiring issue. (Earlier versions of the code attempted a page-scoped attach + migration; that approach was reverted after empirical testing showed it produced 0-byte placeholder files via Playwright `download` events that still fire in CDP-borrowed mode.)

### References

- [bridgic CLAUDE.md → Downloads](../CLAUDE.md#downloads) — full design including empirically-tried alternatives for CDP-borrowed downloads.
- `bridgic/browser/session/_browser.py::_start()` — CDP-borrowed L1 override is sent on `await self._context.new_cdp_session(self._page)`, the page-scoped session.
- `bridgic/browser/session/_cdp_download_renamer.py` — the renamer attaches to that single page session.

---

## Stealth: TLS Fingerprint Cannot Be Matched in Headless Mode

### Symptom

Sites that perform server-side TLS fingerprinting (JA3 / JA4 / Akamai) — most
visibly `demo.fingerprint.com/web-scraping` — block requests from headless mode
with messages like `Malicious bot detected, access denied.`, even with stealth
fully enabled. The same site loads normally in headed mode.

### Root Cause

bridgic's stealth layer operates at the **JavaScript + CDP layer** (navigator
overrides, UA rewrite via `Emulation.setUserAgentOverride`, worker injection).
TLS fingerprint is computed by the OS / Chromium TLS stack at TCP handshake
time — **before any JS or CDP message can intervene**.

Playwright's bundled "Chrome for Testing" Chromium binary has a slightly
different CipherSuite ordering and HTTP/2 frame layout than real Chrome, so
its TLS fingerprint does not match what server-side detectors expect from a
real Chrome user.

### Workaround

Use **headed mode** (`Browser(headless=False)` or `--headed` on the CLI). In
headed mode bridgic auto-switches to the real system Chrome binary
(`channel="chrome"`), whose TLS stack is identical to a normal Chrome user.

### Verification

This was confirmed against `demo.fingerprint.com/web-scraping`:

| Mode | Result |
|---|---|
| Headless (Playwright Chromium 143) | `Malicious bot detected, access denied.` |
| Headed (system Chrome 147) | Flight data renders normally |

See the [Anti-Detection benchmark](../README.md#anti-detection) in the project
README for the current benchmark matrix.

### References

- [JA3 fingerprinting](https://github.com/salesforce/ja3) — the canonical TLS
  fingerprint algorithm used by Akamai, Cloudflare, and Fingerprint.com.

---

## Stealth: `isAutomatedWithCDPInWebWorker` Detected in Headed Mode

### Symptom

`deviceandbrowserinfo.com/are_you_a_bot` reports `isBot: true` in headed mode,
with the single triggered flag being `isAutomatedWithCDPInWebWorker` (the
main-thread `isAutomatedWithCDP` flag is clean). The same site reports
`isBot: false` in headless mode.

### Root Cause

The detector spawns a Web Worker and inside it calls `console.log(error)` with
an `Error` object that has a getter trap on `error.stack`. When a CDP session
is attached to the worker target, `Runtime.consoleAPICalled` serializes the
arguments, which triggers the `error.stack` getter — that's the bot signal.

In **headless mode** bridgic neutralizes this via the R5-lite patch
(pre-stringify any `Error` argument before it hits `console.*`). The worker
patch is delivered through `page.on('worker')` + `worker.evaluate`, which
patches *every* worker the page spawns.

In **headed mode** this patch is intentionally *disabled* — see
[CLAUDE.md → Iframe-safety rule](../CLAUDE.md#stealth) and
`_arm_worker_stealth` in `_browser.py`. Patching workers spawned by a
cross-origin Cloudflare Turnstile iframe would alter their navigator/console
behavior and trip Cloudflare's bot signal.

### Workaround

This is a deliberate trade-off, not a bug:

- **If you only need to bypass `deviceandbrowserinfo`-style worker CDP probes**
  and you are not using Cloudflare Turnstile, you can manually re-enable
  `_arm_worker_stealth` for headed mode (remove the `not self._headless` guard
  in `_browser.py`). Test against your Cloudflare site afterwards.
- **A proper fix** would be same-origin filtering in `_arm_worker_stealth`:
  only patch workers whose origin matches the top frame, skipping
  cross-origin iframe workers. This is feasible (~0.5–1 day of work) but not
  yet implemented.

### Verification

| Mode | `isBot` | Triggered flags |
|---|---|---|
| Headless | `false` | none |
| Headed | `true` | `isAutomatedWithCDPInWebWorker` only |

---

## Stealth: `Worker` Constructor Is Wrapped in Headless Mode

### Symptom

In headless mode with stealth enabled, `Worker.toString()` reports the wrapped
function rather than the native `Worker`. Code that does
`Worker.toString().includes('[native code]')` will fail unless they call
`Function.prototype.toString.call(Worker)` (which our `_mkNative` covers).

More importantly, the script source passed to `new Worker(scriptURL)` is
wrapped via `importScripts(scriptURL)` inside a generated blob URL — the
worker's *initial source line* runs our stealth patches before any user code.
Special-purpose workers that depend on synchronous behavior at the very first
instruction may observe a tiny additional latency.

### Root Cause

By design (R3 race-proof patch). `page.on('worker') + worker.evaluate()` loses
the race against detector code that synchronously postMessages navigator props
back to the main thread. The constructor wrap is the only way to inject our
worker-stealth code *before* the worker's first instruction.

### Constraints

- Only active in **headless mode + top frame** (gated by both `self._headless`
  and `if (window === window.top)` in the wrapped section).
- Falls through transparently for non-string scriptURL or anything that throws
  during URL re-wrap.
- **Same-origin filter** — to avoid breaking cross-origin workers (we observed
  this with reCAPTCHA v3's worker on `gstatic.com`, which hung indefinitely
  because our blob worker's `importScripts(crossOriginURL)` failed CORS), the
  wrap is gated by origin: only `blob:`, `data:`, and same-origin `scriptURL`s
  are wrapped. Cross-origin worker URLs are passed through to the original
  `Worker` constructor unchanged. Cross-origin workers therefore do not
  receive the worker stealth patch — but detector libraries always build
  workers from inline `URL.createObjectURL(new Blob([code]))` (which is
  `blob:` and same-origin), so the filter has no real coverage cost.

### Workaround

Disable stealth (`Browser(stealth=False)`) or pass an explicit `user_agent`
(disables R1 + the implicit gating side-effects but keeps other patches).

---
