# Environment Variables and Login State

Use this reference when the task needs environment variable behavior or login state persistence.

## Environment Variables

| Variable | Applies to | Default | Purpose |
|---|---|---|---|
| `BRIDGIC_LOG_LEVEL` | SDK + CLI | `INFO` | Log level for the `bridgic.browser` logger. |
| `BRIDGIC_BROWSER_JSON` | CLI daemon | unset | JSON string to override Browser constructor kwargs at daemon startup. |
| `BRIDGIC_SOCKET` | CLI (Unix only) | platform default | Override Unix socket path for the daemon client/transport. |
| `BRIDGIC_DAEMON_RESPONSE_TIMEOUT` | CLI client | `90` | Seconds to wait for a daemon response. |
| `BRIDGIC_DAEMON_STOP_TIMEOUT` | CLI daemon | `45` | Seconds to wait for daemon shutdown. |
| `SKIP_BROWSER_TESTS` | Tests | unset | If `1/true/yes`, skip browser tests. |

Notes:
- Config file precedence for CLI (lowest -> highest): defaults, `~/.bridgic/bridgic-browser.json`, `./bridgic-browser.json`, `BRIDGIC_BROWSER_JSON`.
- To start the daemon in headed mode, pass `--headed` to `bridgic-browser open` / `bridgic-browser search`, or set `{"headless": false}` in `BRIDGIC_BROWSER_JSON`.
- When `headless=false` and neither `channel` nor `executable_path` is specified, the CLI daemon uses Playwright’s bundled “Chrome for Testing” browser to keep extension loading working. `chromium_sandbox=True` is auto-set to prevent `--no-sandbox` warnings. To use system Chrome instead (shows “Google Chrome” in Dock, no TEST badge), set `channel=”chrome”` or `executable_path` in config — but note that system Chrome v137+ no longer supports `--load-extension`, so extensions won’t load.

### Config Files and `BRIDGIC_BROWSER_JSON` Values

`~/.bridgic/bridgic-browser.json`, `./bridgic-browser.json`, and `BRIDGIC_BROWSER_JSON` all accept the **same JSON shape**: any `Browser(...)` constructor parameter plus the supported `**kwargs` listed below. Unknown keys are ignored.

#### Top-level Browser parameters (direct)

| Key | Type / values | Notes |
|---|---|---|
| `headless` | `true | false` | Default `true`. If `devtools=true`, headless is forced to `false`. |
| `viewport` | `{ "width": int, "height": int }` or `null` | Default `1600x900` when `no_viewport` is not set. |
| `user_data_dir` | string (path) | Enables persistent context. |
| `stealth` | `true | false` or object | Object uses the StealthConfig keys below. |
| `channel` | string | Examples: `"chrome"`, `"msedge"`, `"chromium"`. |
| `executable_path` | string (path) | Custom browser binary path. |
| `proxy` | `{ "server": str, "bypass": str?, "username": str?, "password": str? }` | Proxy settings. |
| `timeout` | number (ms) | Launch timeout. |
| `slow_mo` | number (ms) | Slow down Playwright actions. |
| `args` | `string[]` | Extra launch arguments. |
| `ignore_default_args` | `true | false | string[]` | Ignore all defaults or a list. |
| `downloads_path` | string (path) | Used by DownloadManager. Auto-enables `accept_downloads` if not set. |
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

Config file (`~/.bridgic/bridgic-browser.json` or `./bridgic-browser.json`):
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
- For long-lived login across restarts, use a persistent context: `Browser(user_data_dir=...)`.
