# CDP Connection Mode

Connect to an already-running Chrome instance instead of launching a new one.

```python
from bridgic.browser import Browser

# SDK — accepts the same inputs as CLI --cdp (port / ws / wss / http / "auto")
browser = Browser(cdp="9222")
browser = Browser(cdp="auto")
browser = Browser(cdp="http://host:9222")
browser = Browser(cdp="ws://localhost:9222/devtools/browser/abc")

# CLI (both open and search support --cdp)
bridgic-browser open https://example.com --cdp 9222
bridgic-browser open https://example.com --cdp auto
bridgic-browser open https://example.com --cdp "ws://localhost:9222/..."
bridgic-browser search "query" --cdp 9222
```

> **Lazy resolution.** The SDK's `Browser(cdp=...)` constructor does not perform any network I/O — it merely stores the raw value. The input is normalised to a `ws://` URL (via `/json/version` probes for port / http / auto) on the first `await browser._start()`, which is also what `await browser.navigate_to(...)` / `await browser.search(...)` trigger automatically. This makes `Browser(cdp="auto")` safe to construct inside a running event loop. A malformed value raises `InvalidInputError` on first use, not at construction time.

## Starting Chrome with CDP enabled

Before bridgic can connect, the target Chrome must expose a remote debugging endpoint. There are two ways to do this.

### Chrome 144+ — in-browser UI (no relaunch)

Starting in Chrome 144, remote debugging can be enabled from the running browser without restarting it or passing any command-line flags:

1. Open `chrome://inspect/#remote-debugging` in your everyday Chrome window.
2. Follow the dialog to **allow** incoming debugging connections.

Chrome then opens a local endpoint and writes the connection info to a `DevToolsActivePort` file at the **root of the user data directory** (not inside a profile subfolder like `Default/`):

| Platform | Path |
|----------|------|
| macOS    | `~/Library/Application Support/Google/Chrome/DevToolsActivePort` |
| Linux    | `~/.config/google-chrome/DevToolsActivePort` |
| Windows  | `%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort` |

The file is exactly two lines — the port and the browser-level WebSocket path:

```
9222
/devtools/browser/f8632266-41b6-4eb8-8239-d48a86bb44b1
```

Join them as `ws://127.0.0.1:<port><path>` and pass the result to bridgic:

```bash
bridgic-browser open https://example.com \
  --cdp "ws://127.0.0.1:9222/devtools/browser/f8632266-41b6-4eb8-8239-d48a86bb44b1"
```

> `--cdp 9222` and `--cdp auto` also work against this endpoint, since bridgic resolves a bare port to a `ws://` URL by querying `http://127.0.0.1:<port>/json/version`.

While the session is active Chrome shows a *"Chrome is being controlled by automated test software"* banner, and Chrome may prompt you to confirm each new debugging session. This consent gate is the whole point of the Chrome 144+ flow — it lets agents share a real, logged-in profile without anyone passing `--remote-debugging-port` on the command line.

**Sources:**
- [Let your Coding Agent debug your browser session with Chrome DevTools MCP — Chrome for Developers blog](https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session) — describes the Chrome 144 permission dialog and the `chrome://inspect/#remote-debugging` setup step.
- [ChromeDevTools/chrome-devtools-mcp README](https://github.com/ChromeDevTools/chrome-devtools-mcp/) — documents the `--autoConnect` (Chrome 144+) requirement that the user has enabled remote debugging via `chrome://inspect/#remote-debugging`.

### Legacy — launch flag

For Chrome < 144, or when you want a fresh dedicated profile that won't prompt:

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp-profile
```

Then use `--cdp 9222`, `--cdp auto` (scan), or the explicit `ws://` URL.

## How it works

`Browser(cdp=...)` calls Playwright's `connect_over_cdp()` instead of `launch()`. The existing browser's default context is borrowed — bridgic operates as a guest on someone else's browser, sharing cookies, localStorage, and login state with the user's real Chrome session. (That session sharing is the whole point of CDP mode.)

## Tab ownership in CDP mode

After connecting via CDP, bridgic **always opens its own brand-new tab** in the borrowed browser context. **Your existing tabs are never navigated, refreshed, or closed.**

All tabs in the context — including the ones you had open before bridgic connected, and any pop-up tabs (`target=_blank`, `window.open()`) spawned by pages bridgic is driving — are fully visible via `get_tabs` / `switch_tab` / `close_tab`.

When `close()` runs (or the daemon shuts down), bridgic **only disconnects** — no tabs are closed. The remote Chrome continues running exactly as the user left it.

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

### Video recording (single-stream, active tab)

bridgic records video via Chrome's CDP `Page.startScreencast` (piped to ffmpeg), **not** Playwright's `record_video` context option — so video recording works on borrowed contexts.

- **Only the active tab is recorded.** `start_video()` starts a single screencast session on the currently active page. When bridgic switches the active tab (via `switch_tab`, `new_tab`, `navigate_to` when it creates a new page, or `close_tab`) the CDP screencast source is hot-swapped to the new page. Background tabs/popups created independently do not trigger a switch. ffmpeg stays alive and the output is a single continuous `.webm` file.
- **`stop_video()` saves the file immediately.** The `.webm` is written as soon as the recorder stops; no page close is needed.
- **Recording stops cleanly without touching user tabs.** No page is closed or navigated.

**Tracing is not affected** — `tracing.stop()` works at any time without closing pages or contexts.

### `close()` only disconnects

`close()` in CDP mode is a pure disconnect — no pages or contexts are touched:

| Operation | Launch mode | CDP mode |
|-----------|------------|---------|
| Navigate pages to about:blank | Yes | **Skipped** |
| `page.close()` | Yes | **Skipped** |
| `context.close()` | Yes | **Skipped** |
| `browser.close()` | Kills process | **Disconnects only** |
| Save tracing artifacts | Yes | Yes |
| Save video artifacts | Yes | Yes (active tab recording) |

After `close()`, the remote Chrome continues running with all tabs intact.

### Connection drops

The CDP WebSocket connection can be lost due to:

- Remote browser closed or crashed
- Network interruption
- Cloud browser service timeout (Browserless, Steel.dev, etc.)

The CLI daemon automatically attempts **one reconnect** when a command fails with a connection error. Reconnect re-resolves the CDP URL from scratch, so restarting Chrome on the same debugging port (new session UUID) is transparent to bridgic — the next command just works. After reconnect the session starts fresh (about:blank); previous page state is lost.

If the remote browser is gone (port no longer accepting), the reconnect fails and the error is reported to the client as `BROWSER_CLOSED`.

**Tip — pick a CDP input form that supports reconnect**

| Form | Reconnects across Chrome restart? |
|---|---|
| `--cdp 9222` (bare port) | ✅ resolves fresh UUID on reconnect |
| `--cdp http://localhost:9222` | ✅ resolves fresh UUID on reconnect |
| `--cdp auto` | ✅ rescans localhost on reconnect |
| `--cdp ws://.../devtools/browser/<UUID>` | ❌ UUID is frozen; reconnect 404s |
