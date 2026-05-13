# Environment Variables and Login State

Use this reference when the task needs environment variable behavior or login state persistence.

## Environment Variables

| Variable | Applies to | Default | Purpose |
|---|---|---|---|
| `BRIDGIC_HOME` | SDK + CLI | `~/.bridgic` | Root directory for all Bridgic state. All daemon paths (run info, socket, logs, tmp, config, user data) derive from this. Set different values to run multiple independent daemon instances. |
| `BRIDGIC_LOG_LEVEL` | SDK + CLI | `INFO` | Log level for the `bridgic.browser` logger. |
| `BRIDGIC_BROWSER_JSON` | SDK + CLI | unset | JSON string to override Browser constructor kwargs. Loaded by `Browser()` and CLI daemon. |
| `BRIDGIC_CDP` | CLI daemon | unset | Connect to an existing Chrome via CDP. Accepts: port (`9222`), `ws://`/`wss://` URL, `http://host:port`, or `auto` (scan local profiles). Resolved at daemon startup. Also set internally by the CLI client (as an already-resolved `ws://` URL) when `--cdp` is passed, so the flag overrides any value inherited from the shell. Full enablement steps (Chrome 144+ `chrome://inspect` UI vs. `--remote-debugging-port`), limitation matrix, and reconnect tips: see `cdp-mode.md`. |
| `BRIDGIC_SOCKET` | CLI (Unix only) | platform default | Override Unix socket path for the daemon client/transport. |
| `BRIDGIC_DAEMON_RESPONSE_TIMEOUT` | CLI client | `90` | Seconds to wait for a daemon response. |
| `BRIDGIC_DAEMON_RESPONSE_TIMEOUT_BUFFER` | CLI client | `30` | Extra client-side wait above any arg-supplied `--timeout` (so the client does not abort while the daemon is still working on a long command). |
| `BRIDGIC_DAEMON_READY_TIMEOUT` | CLI client | `30` | Seconds the client waits for a freshly spawned daemon to emit its `BRIDGIC_DAEMON_READY` line. |
| `BRIDGIC_DAEMON_STOP_TIMEOUT` | CLI daemon | `60` | Global safety-net budget for `browser.close()` inside the daemon. Watchdog only — per-step shutdown budgets already cap at ≤ 30s; raise this if a legitimately slow close (e.g. multi-gigabyte video finalize on a slow disk) needs more headroom. |
| `BRIDGIC_CLICK_TIMEOUT` | SDK + CLI daemon | `10` | Hard ceiling (seconds) for a single `locator.click / dblclick / check / uncheck`. Caps Playwright's 30s retry loop so SPAs with sticky/animated headers don't block other CLI commands on the daemon. |
| `BRIDGIC_FALLBACK_DISPATCH_TIMEOUT_MS` | SDK + CLI daemon | `2000` | Ceiling (milliseconds) for the `locator.dispatch_event` click fallback. Prevents continuously animating elements from saturating Playwright's 30s default and defeating `BRIDGIC_CLICK_TIMEOUT`. |
| `BRIDGIC_CDP_PROBE_TIMEOUT` | CLI daemon | `1.5` | Per-probe TCP connect budget (seconds) for the CDP `/json/version` health check before reconnecting. |
| `SKIP_BROWSER_TESTS` | Tests | unset | If `1/true/yes`, skip browser tests. |

### Multi-Instance Isolation (`BRIDGIC_HOME`)

Set `BRIDGIC_HOME` to run multiple independent daemon instances in parallel. Each instance gets its own run info, socket, logs, tmp, config, and user data — zero shared state.

```bash
# Instance 1 (default)
bridgic-browser open https://site-a.com

# Instance 2 (separate home)
BRIDGIC_HOME=/tmp/b2 bridgic-browser open https://site-b.com

# Each instance operates independently
bridgic-browser snapshot                        # site-a snapshot
BRIDGIC_HOME=/tmp/b2 bridgic-browser snapshot   # site-b snapshot

# Close each instance separately (close only targets the matching BRIDGIC_HOME)
bridgic-browser close                           # closes instance 1 only
BRIDGIC_HOME=/tmp/b2 bridgic-browser close      # closes instance 2 only
```

Derived paths per instance:

| Path | Purpose |
|---|---|
| `$BRIDGIC_HOME/bridgic-browser/run/daemon.json` | Daemon run info |
| `$BRIDGIC_HOME/bridgic-browser/run/bridgic-browser.sock` | Unix socket (POSIX) |
| `$BRIDGIC_HOME/bridgic-browser/logs/daemon.log` | Daemon log |
| `$BRIDGIC_HOME/bridgic-browser/tmp/` | Temporary files (video, close reports) |
| `$BRIDGIC_HOME/bridgic-browser/user_data/` | Persistent browser profile |
| `$BRIDGIC_HOME/bridgic-browser/snapshot/` | Snapshot overflow files |
| `$BRIDGIC_HOME/bridgic-browser/bridgic-browser.json` | User config |

SDK isolation:

```python
# Same-process multi-instance: use user_data_dir (no BRIDGIC_HOME needed)
b1 = Browser(user_data_dir="/tmp/profile-1")
b2 = Browser(user_data_dir="/tmp/profile-2")
# tmp/snapshot dirs are shared but collision-free (unique filenames via mkstemp/random)

# Full process-level isolation: set env var before spawning a worker
import subprocess, os
subprocess.Popen(
    ["python", "worker.py"],
    env={**os.environ, "BRIDGIC_HOME": "/tmp/b2"},
)
```

`BRIDGIC_HOME` is a **process-level** setting (read once at module import). Within a single process, use `user_data_dir` / `downloads_path` for per-instance isolation — internal paths (tmp, snapshot) use unique filenames and never collide.

Notes:
- Config file precedence (SDK + CLI, lowest -> highest): defaults, `$BRIDGIC_HOME/bridgic-browser/bridgic-browser.json`, `./bridgic-browser.json`, `BRIDGIC_BROWSER_JSON`.
- To start the daemon in headed mode, pass `--headed` to `bridgic-browser open` / `bridgic-browser search`, or set `{"headless": false}` in `BRIDGIC_BROWSER_JSON`.
- To start with an ephemeral (no persistent profile) session, pass `--clear-user-data` to `bridgic-browser open` / `bridgic-browser search`, or set `{"clear_user_data": true}` in `BRIDGIC_BROWSER_JSON`. These flags are only meaningful when starting a new daemon; they are ignored if a session is already running.
- To connect to an existing Chrome via CDP, pass `--cdp` to `bridgic-browser open` or `bridgic-browser search`, or set the `BRIDGIC_CDP` env var. The `--cdp` flag accepts a port number, `ws://`/`wss://` URL, `http://host:port`, or `auto`. Full enablement and behavior reference: `cdp-mode.md`.
- When `headless=false` (headed mode) with stealth enabled and neither `channel` nor `executable_path` is specified, the daemon **auto-switches to system Chrome** (`channel=”chrome”`) if detected on the machine. This avoids Playwright’s bundled “Chrome for Testing” which is blocked by Google OAuth and shows a “test” label in the macOS Dock. If system Chrome is not installed, it falls back to Chrome for Testing.

### Config Files and `BRIDGIC_BROWSER_JSON` Values

`$BRIDGIC_HOME/bridgic-browser/bridgic-browser.json` (default `~/.bridgic/bridgic-browser/bridgic-browser.json`), `./bridgic-browser.json`, and `BRIDGIC_BROWSER_JSON` all accept the **same JSON shape**: any `Browser(...)` constructor parameter plus the supported `**kwargs` listed below. Unknown keys are ignored.

#### Top-level Browser parameters (direct)

| Key | Type / values | Notes |
|---|---|---|
| `headless` | `true | false` | Default `true`. If `devtools=true`, headless is forced to `false`. |
| `viewport` | `{ "width": int, "height": int }` or `null` | Default `1600x900` when `no_viewport` is not set. |
| `user_data_dir` | string (path) | Custom path for persistent profile. Ignored when `clear_user_data=true`. |
| `clear_user_data` | `true | false` | Default `false`. If `true`, use ephemeral session (`launch`+`new_context`, no profile saved). If `false`, use persistent profile (defaults to `$BRIDGIC_HOME/bridgic-browser/user_data/`). |
| `cdp` | string | Connect to existing Chrome via CDP instead of launching. Accepts any format supported by `resolve_cdp_input()` (port, `ws://`/`wss://` URL, `http://host:port`, `auto`); non-WebSocket values are auto-resolved at startup. Can be set via config JSON, `BRIDGIC_CDP` env var, or `--cdp` CLI flag. Full enablement and behavior reference: `cdp-mode.md`. |
| `stealth` | `true | false` or object | Object uses the StealthConfig keys below. |
| `channel` | string | Examples: `"chrome"`, `"msedge"`, `"chromium"`. |
| `executable_path` | string (path) | Custom browser binary path. |
| `proxy` | `{ "server": str, "bypass": str?, "username": str?, "password": str? }` | Proxy settings. |
| `timeout` | number (ms) | Launch timeout. |
| `slow_mo` | number (ms) | Slow down Playwright actions. |
| `args` | `string[]` | Extra launch arguments. |
| `ignore_default_args` | `true | false | string[]` | Ignore all defaults or a list. |
| `downloads_path` | string (path) | Target directory for downloads. In non-CDP / CDP-owned modes used by `DownloadManager` (auto-enables `accept_downloads` if not explicitly set). In CDP-borrowed mode used by `CdpDownloadRenamer` (page-level CDP `setDownloadBehavior(allowAndName)` + GUID→real-name rename); `DownloadManager` is not attached. CLI without this key falls back to: non-CDP → `~/Downloads`, CDP → the CLI client's CWD at command time. |
| `devtools` | `true | false` | Opens DevTools; forces `headless=false`. |
| `user_agent` | string | Context user agent. |
| `locale` | string | BCP-47 locale (for example `zh-CN`). |
| `timezone_id` | string | IANA timezone (for example `Asia/Shanghai`). |
| `ignore_https_errors` | `true | false` | Ignore TLS errors. |
| `extra_http_headers` | `{ "Header": "Value", ... }` | Extra HTTP headers. |
| `offline` | `true | false` | Emulate offline mode. |
| `color_scheme` | `"dark" | "light" | "no-preference" | "null"` | Emulates prefers-color-scheme. |

#### StealthConfig object (when `stealth` is an object)

| Key | Type / values | Notes |
|---|---|---|
| `enabled` | `true | false` | Default `true`. |
| `disable_security` | `true | false` | Disables security features (testing only). |
| `use_new_headless` | `true | false` | Default `true`. Use full Chromium binary with `--headless=new` instead of headless-shell. Only active when `enabled=true`, `headless=true`, and not using system Chrome (`channel`/`executable_path`). |
| `in_docker` | `true | false` | Auto-detected by default. |
| `permissions` | `string[]` | Default permissions for stealth context; top-level `permissions` overrides. |

#### Launch kwargs (via `**kwargs`)

| Key | Type / values | Notes |
|---|---|---|
| `handle_sigint` | `true | false` | Playwright launch option. |
| `handle_sigterm` | `true | false` | Playwright launch option. |
| `handle_sighup` | `true | false` | Playwright launch option. |
| `env` | `{ "ENV": "VALUE", ... }` | Environment for browser process. |
| `traces_dir` | string (path) | Playwright traces directory. |
| `chromium_sandbox` | `true | false` | Chromium sandbox toggle. |
| `firefox_user_prefs` | `{ "pref": value, ... }` | Firefox prefs dict. |

#### Context kwargs (via `**kwargs`)

| Key | Type / values | Notes |
|---|---|---|
| `screen` | `{ "width": int, "height": int }` | Screen size. |
| `no_viewport` | `true | false` | If `true`, `viewport` must be `null` or omitted. |
| `java_script_enabled` | `true | false` | JS enabled toggle. |
| `bypass_csp` | `true | false` | Bypass Content Security Policy. |
| `geolocation` | `{ "latitude": number, "longitude": number, "accuracy": number? }` | Geolocation. |
| `permissions` | `string[]` | Overrides stealth permissions. |
| `http_credentials` | `{ "username": str, "password": str, "origin": str? }` | HTTP auth. |
| `device_scale_factor` | number | Device scale factor. |
| `is_mobile` | `true | false` | Mobile emulation. |
| `has_touch` | `true | false` | Touch emulation. |
| `reduced_motion` | `"reduce" | "no-preference" | "null"` | Prefers-reduced-motion. |
| `forced_colors` | `"active" | "none" | "null"` | Forced colors emulation. |
| `contrast` | `"more" | "no-preference" | "null"` | Prefers-contrast. |
| `accept_downloads` | `true | false` | Auto-downloads. |
| `base_url` | string | Base URL for relative navigations. |
| `strict_selectors` | `true | false` | Strict selectors mode. |
| `service_workers` | `"allow" | "block"` | Service workers policy. |
| `record_har_path` | string (path) | HAR output file. |
| `record_har_omit_content` | `true | false` | Omit request content. |
| `record_har_url_filter` | string | URL filter (regex string). |
| `record_har_mode` | `"full" | "minimal"` | HAR mode. |
| `record_har_content` | `"attach" | "embed" | "omit"` | HAR content handling. |
| `record_video_dir` | string (path) | Video output directory. |
| `record_video_size` | `{ "width": int, "height": int }` | Video size. |
| `client_certificates` | `array` | Each item: `{ "origin": str, "certPath": str? | "cert": bytes?, "keyPath": str? | "key": bytes?, "pfxPath": str? | "pfx": bytes?, "passphrase": str? }`. |

Examples:

Config file (`$BRIDGIC_HOME/bridgic-browser/bridgic-browser.json` or `./bridgic-browser.json`):
```json
{
  "headless": false,
  "proxy": {"server": "http://proxy:8080", "username": "u", "password": "p"},
  "viewport": {"width": 1280, "height": 720},
  "locale": "zh-CN",
  "timezone_id": "Asia/Shanghai",
  "user_data_dir": "/abs/path/to/profile"
}
```

Environment variable:
```bash
BRIDGIC_BROWSER_JSON='{"headless":false,"viewport":{"width":1280,"height":720}}'
```

## Login State Persistence (Storage)

CLI (cookies + localStorage):
```bash
bridgic-browser storage-save state.json
bridgic-browser storage-load state.json
```

SDK (cookies + localStorage):
```python
await browser.save_storage_state("state.json")
await browser.restore_storage_state("state.json")
```

Details:
- Requires an active page.
- LocalStorage is applied to the current page origin; multi-origin storage may require navigating per origin before restore.
- Playwright can include IndexedDB in storage state, but the wrapper does not expose that flag.
- For long-lived login across restarts, the default `Browser()` already saves state persistently to `$BRIDGIC_HOME/bridgic-browser/user_data/` (default `~/.bridgic/bridgic-browser/user_data/`). Use `Browser(user_data_dir="./my-profile")` to choose a custom profile path, or `Browser(clear_user_data=True)` to opt out of persistence.
