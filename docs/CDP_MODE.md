# CDP Connection Mode

Connect to an already-running Chrome instance instead of launching a new one.

```python
from bridgic.browser import Browser

# SDK
browser = Browser(cdp_url="ws://localhost:9222/devtools/browser/abc")

# CLI
bridgic-browser open https://example.com --cdp 9222
bridgic-browser open https://example.com --cdp auto
bridgic-browser open https://example.com --cdp "ws://localhost:9222/..."
```

## How it works

`Browser(cdp_url=...)` calls Playwright's `connect_over_cdp()` instead of `launch()`. The existing browser's default context is borrowed — bridgic operates as a guest on someone else's browser, sharing cookies, localStorage, and login state with the user's real Chrome session. (That session sharing is the whole point of CDP mode.)

## Tab ownership in CDP mode

After connecting via CDP, bridgic **always opens its own brand-new tab** in the borrowed browser context. **Your existing tabs are never navigated, refreshed, or closed.** When `close()` runs (or the daemon shuts down), bridgic only closes the tabs it created itself.

Each call to `bridgic-browser new-tab` creates an additional bridgic-owned tab; all of them are tracked and cleaned up on shutdown. Tabs you opened manually in Chrome — or pop-ups (`target=_blank` etc.) spawned by pages bridgic was driving — are **not** tracked and will not be touched by bridgic.

When bridgic connects, the daemon log records which Chrome instance was joined and how many user tabs were preserved:

```
[CDP] connected; created new bridgic tab (borrowed_context=True, preserved_existing_tabs=3)
```

This is especially useful with `--cdp auto` (scan mode), where bridgic auto-discovers a running Chrome instance — check this log line to confirm bridgic actually attached to the browser you expected.

## Limitations

### Launch parameters are ignored

The browser is already running, so these constructor parameters have **no effect** in CDP mode:

| Parameter | Reason |
|-----------|--------|
| `headless` | Cannot change headed/headless after launch |
| `args` / `ignore_default_args` | Chrome flags must be set at launch time |
| `channel` / `executable_path` | Binary already selected |
| `proxy` | Proxy must be configured at launch time |
| `slow_mo` / `timeout` | These are `launch()`-level parameters |
| `devtools` | Cannot toggle DevTools panel |

### Context options do not apply to borrowed contexts

When connecting via CDP, bridgic borrows the browser's existing default context (`browser.contexts[0]`). Context-level options cannot be changed after creation:

| Parameter | Status |
|-----------|--------|
| `viewport` | Keeps the existing context's viewport |
| `user_agent` | Cannot modify |
| `locale` / `timezone_id` | Cannot modify |
| `color_scheme` | Cannot modify |
| `ignore_https_errors` | Cannot modify |
| `extra_http_headers` | Cannot modify |
| `user_data_dir` | Ignored — CDP mode never uses persistent context |

### Stealth mode is partially effective

| Stealth capability | CDP status | Reason |
|--------------------|-----------|--------|
| Chrome launch args (50+ flags) | **Not applied** | Browser already running |
| `--disable-component-update`, etc. | **Not applied** | Same as above |
| JS init script (navigator patches) | **Headless only** | Injected via `add_init_script()` — works on new pages |
| Headed-mode system Chrome switch | **Not applied** | Browser already running |

If the remote Chrome was not started with stealth flags, bridgic's JS patches can cover some fingerprints (navigator, webdriver, plugins) but cannot modify signals that require launch arguments (e.g., Blink feature disabling).

### Video recording is restricted to bridgic-owned tabs

bridgic records video via Chrome's CDP `Page.startScreencast` (piped to ffmpeg), **not** Playwright's `record_video` context option — so video recording works on borrowed contexts. There are two CDP-mode constraints worth knowing:

- **Only bridgic's own tabs are recorded.** `start_video()` skips every page in the borrowed context that bridgic did not create itself, and the future-page listener applies the same filter. The user's banking, email, or chat tabs are never captured. Pop-ups (`target=_blank`) spawned by pages bridgic was driving are also untracked, and therefore not recorded either.
- **Recording stops cleanly without touching user tabs.** `stop_video()` only finalizes the screencast sessions for bridgic-owned pages, so no user page is closed or refreshed.

**Tracing is not affected** — `tracing.stop()` works at any time without closing pages or contexts.

### `close()` only disconnects

`close()` in CDP mode preserves the remote browser state — only bridgic's own tabs are cleaned up:

| Operation | Launch mode | CDP (borrowed context) |
|-----------|------------|----------------------|
| Navigate pages to about:blank | Yes | **Skipped** |
| `page.close()` on user tabs | Yes | **Skipped** |
| `page.close()` on bridgic-owned tabs | Yes | Yes |
| `context.close()` | Yes | **Skipped** |
| `browser.close()` | Kills process | **Disconnects only** |
| Save tracing artifacts | Yes | Yes |
| Save video artifacts | Yes | Yes (bridgic-owned tabs only) |

After `close()`, the remote Chrome continues running with all of the **user's** tabs intact; only the tabs bridgic explicitly created are gone.

### Connection drops

The CDP WebSocket connection can be lost due to:

- Remote browser closed or crashed
- Network interruption
- Cloud browser service timeout (Browserless, Steel.dev, etc.)

The CLI daemon automatically attempts **one reconnect** when a command fails with a connection error. After reconnect the session starts fresh (about:blank) — previous page state is lost. If the remote browser is gone, the reconnect fails and the error is reported to the user.
