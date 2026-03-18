# CLI Guide

Use this guide when the task should be executed directly from terminal commands (`bridgic-browser ...`).

## Table of Contents

1. [Quick Start](#quick-start)
2. [Standard CLI Session Pattern](#standard-cli-session-pattern)
3. [Command Groups](#command-groups)
4. [High-Frequency Examples](#high-frequency-examples)
5. [Runtime and Configuration](#runtime-and-configuration)
6. [Non-Obvious CLI Behavior](#non-obvious-cli-behavior)
7. [When to Load Other References](#when-to-load-other-references)

## Quick Start

```bash
pip install bridgic-browser
playwright install chromium
```

```bash
bridgic-browser open https://example.com
bridgic-browser snapshot
bridgic-browser click @e2
bridgic-browser fill @e3 "hello@example.com"
bridgic-browser screenshot page.png
bridgic-browser close
```

## Standard CLI Session Pattern

1. Navigate (`open` or `search`).
2. Capture refs (`snapshot`).
3. Interact by ref (`click`, `fill`, `select`, ...).
4. Verify/capture (`verify-*`, `screenshot`, `pdf`, `network`, `console`).
5. Close daemon when done (`close`) if session should not remain alive.

## Command Groups

| Category | Commands |
|---|---|
| Navigation | `open`, `search`, `info`, `reload`, `back`, `forward` |
| Snapshot | `snapshot` |
| Element Interaction | `click`, `double-click`, `hover`, `focus`, `fill`, `select`, `options`, `check`, `uncheck`, `scroll-to`, `drag`, `upload`, `fill-form` |
| Tabs | `tabs`, `new-tab`, `switch-tab`, `close-tab` |
| Evaluate | `eval`, `eval-on` |
| Keyboard | `press`, `type`, `key-down`, `key-up` |
| Mouse | `scroll`, `mouse-click`, `mouse-move`, `mouse-drag`, `mouse-down`, `mouse-up` |
| Wait | `wait` |
| Capture | `screenshot`, `pdf` |
| Network | `network-start`, `network`, `network-stop`, `wait-network` |
| Dialog | `dialog-setup`, `dialog`, `dialog-remove` |
| Storage | `cookies`, `cookie-set`, `cookies-clear`, `storage-save`, `storage-load` |
| Verify | `verify-text`, `verify-visible`, `verify-url`, `verify-title`, `verify-state`, `verify-value` |
| Developer | `console-start`, `console`, `console-stop`, `trace-start`, `trace-chunk`, `trace-stop`, `video-start`, `video-stop` |
| Lifecycle | `close`, `resize` |

Use `-h` on any command for exact flags:

```bash
bridgic-browser -h
bridgic-browser snapshot -h
```

## High-Frequency Examples

```bash
# Fill and press Enter in one step
bridgic-browser fill @e3 "hello@example.com" --submit

# Snapshot only interactive elements
bridgic-browser snapshot -i

# Snapshot viewport only (exclude off-screen nodes)
bridgic-browser snapshot -F

# Continue truncated snapshot output
bridgic-browser snapshot -s 30000

# Scroll: use --dy / --dx (not positional), supports negative values
bridgic-browser scroll --dy 500        # scroll down 500px
bridgic-browser scroll --dy -300       # scroll up 300px
bridgic-browser scroll --dx 200        # scroll right 200px

# Wait modes
bridgic-browser wait 2.5
bridgic-browser wait "Submit"
bridgic-browser wait --gone "Loading"

# Network capture flow
bridgic-browser network-start
bridgic-browser open https://example.com
bridgic-browser network
bridgic-browser network-stop

# Dialog handling
bridgic-browser dialog-setup --action accept
bridgic-browser open https://example.com    # any alert triggered will be auto-accepted
bridgic-browser dialog-remove
```

## Runtime and Configuration

Config precedence (low -> high):

| Source | Notes |
|---|---|
| Defaults | `headless=True` |
| `~/.bridgic/bridgic-browser.json` | User-level persistent config |
| `./bridgic-browser.json` | Project-local config (daemon startup cwd) |
| `BRIDGIC_BROWSER_JSON` | Full JSON override for any Browser parameter |
| `BRIDGIC_HEADLESS` | `0` means headed mode |
| `BRIDGIC_MAX_CHARS` | Max chars per `snapshot` page before pagination notice |

Environment variables and login state persistence are documented in `env-vars.md`.

## Non-Obvious CLI Behavior

- Refs come from the latest snapshot. If page changed, re-run `snapshot` before ref operations.
- `snapshot` pagination is explicit: use `-s <offset>` from truncation notice.
- `snapshot -i` returns only clickable/editable elements — use for action selection, not full-page inspection.
- CLI uses a persistent daemon/browser. State survives across commands until `close`.
- After local Python code changes, restart daemon to pick up new code:
  - `bridgic-browser close`
  - run next command (`open`, `search`, etc.) to auto-start again.
- `scroll` uses `--dy`/`--dx` options (not positional arguments) so negative values work correctly.
- `screenshot`, `pdf`, `upload`, `storage-save`, `storage-load`, `trace-stop` convert their path argument to an absolute path on the **client side** before sending to daemon (daemon's working directory may differ).
- For `network-start`, start capture before navigation if page-load requests are needed.

## When to Load Other References

- Need Python code instead of CLI commands: read `sdk-guide.md`.
- Need CLI and SDK translation (for example, CLI steps -> SDK automation code): read `cli-sdk-api-mapping.md`.
- Need environment variables or login state persistence details: read `env-vars.md`.
