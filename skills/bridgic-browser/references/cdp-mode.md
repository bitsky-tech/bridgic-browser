# CDP Mode — Connect to an Existing Chrome

Use this reference when the task needs bridgic-browser to attach to a Chrome/Chromium instance that is **already running**, instead of launching a fresh browser. Covers both how to enable CDP on the target Chrome and how to connect from the CLI or SDK.

## Table of Contents

1. [When to Use CDP Mode](#when-to-use-cdp-mode)
2. [Enable CDP on Chrome — Two Options](#enable-cdp-on-chrome--two-options)
   - [Option A — Chrome 144+ in-browser UI (no relaunch)](#option-a--chrome-144-in-browser-ui-no-relaunch)
   - [Option B — Launch flag (Chrome <144, or a dedicated profile)](#option-b--launch-flag-chrome-144-or-a-dedicated-profile)
3. [Connect From bridgic](#connect-from-bridgic)
   - [CLI: `--cdp`](#cli---cdp)
   - [SDK: `Browser(cdp=...)`](#sdk-browsercdp)
   - [Environment variable: `BRIDGIC_CDP`](#environment-variable-bridgic_cdp)
4. [Tab Ownership](#tab-ownership)
5. [Behavior Limitations in CDP Mode](#behavior-limitations-in-cdp-mode)
   - [Launch parameters ignored](#launch-parameters-ignored)
   - [Context options not applied to borrowed contexts](#context-options-not-applied-to-borrowed-contexts)
   - [Stealth is partially effective](#stealth-is-partially-effective)
   - [Video recording uses CDP screencast](#video-recording-uses-cdp-screencast)
   - [`close()` only disconnects](#close-only-disconnects)
6. [Reconnect Strategy and Choosing the Right `--cdp` Form](#reconnect-strategy-and-choosing-the-right---cdp-form)

## When to Use CDP Mode

- Reuse an existing Chrome session with its login state, extensions, and cookies.
- Connect to a cloud browser service (Browserless, Steel.dev, …) exposed over `ws://`/`wss://`.
- Automate an Electron app that exposes a CDP port.
- Let an agent share the user's real, logged-in Chrome without anyone passing command-line flags (Chrome 144+ consent flow).

If none of these apply, use the default launch mode instead — bridgic will start its own Chromium and have full control over launch args, headless, stealth, etc.

## Enable CDP on Chrome — Two Options

bridgic can connect to anything Chrome exposes over the DevTools Protocol, but Chrome must first expose that endpoint. Choose **one** of the two options below.

### Option A — Chrome 144+ in-browser UI (no relaunch)

Starting in Chrome 144, remote debugging can be enabled from the running browser without restarting it or passing any command-line flags.

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

bridgic's `--cdp auto` already scans the standard profile directories of Chrome / Chrome Canary / Chrome Beta / Chromium / Brave / Edge for an active `DevToolsActivePort`, so you can connect with no extra arguments:

```bash
bridgic-browser open https://example.com --cdp auto
```

Equivalent explicit forms (any one of them works):

```bash
bridgic-browser open https://example.com --cdp 9222
bridgic-browser open https://example.com \
  --cdp "ws://127.0.0.1:9222/devtools/browser/f8632266-41b6-4eb8-8239-d48a86bb44b1"
```

While the session is active Chrome shows a *"Chrome is being controlled by automated test software"* banner, and Chrome may prompt the user to confirm each new debugging session. This consent gate is the whole point of the Chrome 144+ flow.

### Option B — Launch flag (Chrome <144, or a dedicated profile)

For Chrome older than 144, or when you want a fresh dedicated profile that does not prompt for confirmation, start Chrome with `--remote-debugging-port`:

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/cdp-profile

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cdp-profile

# Windows (PowerShell)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
    --remote-debugging-port=9222 `
    --user-data-dir=C:\Temp\cdp-profile
```

Pass a `--user-data-dir` distinct from your normal profile so the debugging session never touches your everyday cookies / extensions. Then use `--cdp 9222`, `--cdp auto`, or the explicit `ws://` URL to connect (see below).

## Connect From bridgic

### CLI: `--cdp`

The `--cdp` flag is accepted by `bridgic-browser open` and `bridgic-browser search`. It is a **startup-only flag** — once a daemon is running, the flag is ignored on subsequent invocations.

```bash
bridgic-browser open https://example.com --cdp 9222
bridgic-browser open https://example.com --cdp auto
bridgic-browser open https://example.com --cdp ws://localhost:9222/devtools/browser/...
bridgic-browser open https://example.com --cdp wss://cloud.example.com/chromium?token=...
bridgic-browser search "site:example.com login" --cdp 9222
```

Accepted input formats:

| Format | Description |
|--------|-------------|
| `9222` | Bare port number — queries `http://localhost:9222/json/version` to resolve the WebSocket URL |
| `ws://...` / `wss://...` | Direct WebSocket URL (raw CDP or Playwright WS protocol), passed through as-is |
| `http://host:port` | HTTP discovery endpoint — queries `/json/version` on that host |
| `auto` | Auto-scan local profile directories for an active `DevToolsActivePort` file. Source enumerates: Chrome, Chrome Canary, Chrome Beta, Chromium, Brave, Edge (plus Snap / Flatpak variants on Linux) |

### SDK: `Browser(cdp=...)`

All four CLI `--cdp` input forms work as the `cdp=` argument:

```python
from bridgic.browser.session import Browser

Browser(cdp="9222")                                                # bare port
Browser(cdp="auto")                                                # scan local profiles
Browser(cdp="http://host:9222")                                    # HTTP discovery
Browser(cdp="ws://localhost:9222/devtools/browser/abc")            # explicit WebSocket
```

Full lifecycle example:

```python
from bridgic.browser.session import Browser

async with Browser(cdp="auto") as browser:
    await browser.navigate_to("https://example.com")
    snapshot = await browser.get_snapshot()
```

**Lazy resolution.** `Browser(cdp=...)` does not perform any network I/O at construction time — it merely stores the raw value. The input is normalised to a `ws://` URL on the first `await browser._start()` (also triggered automatically by `await browser.navigate_to(...)` / `await browser.search(...)`). This makes `Browser(cdp="auto")` safe to construct inside a running event loop. A malformed value raises `InvalidInputError` on first use, not at construction time.

`resolve_cdp_input()` is also exported (from `bridgic.browser.session`) for the rare case where you want to normalise the value up front.

### Environment variable: `BRIDGIC_CDP`

`BRIDGIC_CDP` accepts the same input formats as `--cdp` and `cdp=`. The CLI client sets it internally (as an already-resolved `ws://` URL) when `--cdp` is passed, so the flag overrides any value inherited from the shell. Useful for configuring a daemon environment without changing the invocation command:

```bash
export BRIDGIC_CDP=auto
bridgic-browser open https://example.com   # picks up BRIDGIC_CDP
```

You can also set `"cdp": "..."` inside `bridgic-browser.json` or `BRIDGIC_BROWSER_JSON`. See `env-vars.md` for full config precedence.

## Tab Ownership

After connecting via CDP, bridgic **always opens its own brand-new tab** in the borrowed browser context. **Your existing tabs are never navigated, refreshed, or closed — nor are they visible to bridgic.**

bridgic maintains an internal "owned pages" set:

- The brand-new tab bridgic opens at attach time is owned.
- Any tab bridgic creates afterwards via `new_tab` / `new-tab` is owned.
- A pop-up spawned **from an owned page** is auto-adopted iff Chromium reports it as opener-linked at attach time (detected via `Page.opener()`). The trigger path matters:
  - ✅ **Adopted**: bridgic-initiated click, programmatic `page.click()`, **user plain left-click** on `<a target="_blank">` or `window.open()`. `rel="noopener"` / `rel="noreferrer"` / `window.open(...,'noopener')` do not disable this — they suppress JS-level `window.opener` but not the CDP-level opener relationship.
  - ❌ **Not adopted**: user **Cmd+click** (macOS) / **Ctrl+click** (Win/Linux) / **middle-click** on a link, or any tab the user opens via Cmd+T / address bar / history. Chromium severs the opener at the browser-process level for these "background tab" navigations, so the CDP `openerId` is genuinely empty and bridgic cannot see them.

Only owned pages appear in `get_tabs` / `switch_tab` / `close_tab`. The user's pre-existing tabs — and any pop-ups they trigger via Cmd-click or open via Cmd+T / address bar — remain invisible to bridgic. This is a privacy boundary: it prevents the LLM / CLI from inadvertently switching to, reading, or closing the user's private work tabs.

If you need to operate on a page the user already has open, navigate to it through bridgic instead (`bridgic-browser navigate-to <url>` or `new-tab <url>`). bridgic cannot reach across into the user's existing tab.

When `close()` runs (or the daemon shuts down), bridgic **only disconnects** — no tabs are closed. The remote Chrome continues running exactly as the user left it.

The daemon log records which Chrome instance was joined and how many user tabs were preserved — useful with `--cdp auto` to confirm you attached to the expected browser:

```
[CDP] connected; created new bridgic tab (borrowed_context=True, preserved_existing_tabs=3)
```

### Popup-follow behavior

By default, when a popup is spawned from `self._page` (the tab bridgic is currently driving), bridgic's active page automatically follows the popup — mirroring Chrome's UX where the just-spawned tab takes the foreground. To keep `self._page` fixed on the original tab, pass `auto_follow_popups=False` to the `Browser(...)` constructor (or the same key in your config file). The popup is still adopted into the owned set; only the active-page pointer is unaffected.

## Behavior Limitations in CDP Mode

The browser is already running, so a number of `Browser(...)` parameters and stealth features have no effect.

### Launch parameters ignored

| Parameter | Reason |
|-----------|--------|
| `headless` | Cannot change headed/headless after launch |
| `args` / `ignore_default_args` | Chrome flags must be set at launch time |
| `channel` / `executable_path` | Binary already selected |
| `proxy` | Proxy must be configured at launch time |
| `slow_mo` / `timeout` | These are `launch()`-level parameters |
| `devtools` | Cannot toggle DevTools panel |

### Context options not applied to borrowed contexts

bridgic borrows the browser's existing default context (`browser.contexts[0]`); context-level options cannot be changed after creation:

| Parameter | Status |
|-----------|--------|
| `viewport` | Keeps the existing context's viewport |
| `user_agent` | Cannot modify |
| `locale` / `timezone_id` | Cannot modify |
| `color_scheme` | Cannot modify |
| `ignore_https_errors` | Cannot modify |
| `extra_http_headers` | Cannot modify |
| `user_data_dir` | Ignored — CDP mode never uses persistent context |

### Stealth is partially effective

| Stealth capability | CDP status | Reason |
|--------------------|-----------|--------|
| Chrome launch args (50+ flags) | **Not applied** | Browser already running |
| `--disable-component-update`, etc. | **Not applied** | Same as above |
| Main JS init script (navigator / webdriver / WebGL / plugins patches) | **Headless only** | Source gates on `self._headless`; same rationale as launch mode — `add_init_script()` runs in challenge iframes and breaks Cloudflare Turnstile in headed mode |
| Anti-devtools timing script | **Always applied** | Safe for both headed and headless (only patches timing probes) |
| Headed-mode system Chrome auto-switch | **Not applied** | Browser already running |

If the remote Chrome was not started with stealth flags, bridgic's headless-only JS patches can cover some fingerprints (navigator, webdriver, plugins) but cannot modify signals that require launch arguments (for example, Blink feature disabling). **In CDP + headed mode, only the anti-devtools timing script is active** — for fingerprint-heavy targets, prefer launching a fresh stealth-configured Chrome instead.

### Video recording uses CDP screencast

bridgic records video via Chrome's CDP `Page.startScreencast` (piped to ffmpeg), **not** Playwright's `record_video` context option — so recording works on borrowed contexts.

- **Only the active tab is recorded.** `start_video` opens a single screencast session on the active page. When bridgic switches the active tab (`switch_tab`, `new_tab`, `navigate_to` that creates a new page, or `close_tab`), the screencast source is hot-swapped. Background tabs / independent popups do not trigger a switch.
- **`stop_video` saves the file immediately** — no page close needed.
- **Recording stops cleanly without touching user tabs.** Tracing is unaffected — `tracing.stop()` works at any time.

### `close()` only disconnects

| Operation | Launch mode | CDP mode |
|-----------|------------|---------|
| Navigate pages to `about:blank` | Yes | **Skipped** |
| `page.close()` | Yes | **Skipped** |
| `context.close()` | Yes | **Skipped** |
| `browser.close()` | Kills Chrome process | **Disconnects only** |
| Save tracing artifacts | Yes | Yes |
| Save video artifacts | Yes | Yes (active-tab recording) |

After `close()`, the remote Chrome continues running with all tabs intact.

## Reconnect Strategy and Choosing the Right `--cdp` Form

The CDP WebSocket can drop because of remote browser close/crash, network interruption, or a cloud browser service timeout. The CLI daemon automatically attempts **one reconnect** when a command fails with a connection error. Reconnect re-resolves the CDP URL from scratch, so restarting Chrome on the same debugging port (new session UUID) is transparent to bridgic — the next command just works. After reconnect the session starts fresh (about:blank); previous page state is lost.

If the remote browser is gone (port no longer accepting), the reconnect fails and the error surfaces to the client as `BROWSER_CLOSED`.

**Tip — pick a CDP input form that supports reconnect across Chrome restart:**

| Form | Reconnects across Chrome restart? |
|---|---|
| `--cdp 9222` (bare port) | Yes — re-resolves fresh UUID on reconnect |
| `--cdp http://localhost:9222` | Yes — re-resolves fresh UUID on reconnect |
| `--cdp auto` | Yes — rescans localhost on reconnect |
| `--cdp ws://.../devtools/browser/<UUID>` | No — UUID is frozen; reconnect 404s |

When the use case involves long-lived or restart-prone Chrome instances (developer workflows, flaky cloud sessions), prefer `9222` / `http://` / `auto` over a raw `ws://` URL.
