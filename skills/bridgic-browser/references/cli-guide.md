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
uv init --bare
uv add bridgic-browser
uv run playwright install chromium
```

```bash
bridgic-browser open https://example.com
bridgic-browser snapshot
bridgic-browser click @8d4b03a9
bridgic-browser fill @d6a530b4 "hello@example.com"
bridgic-browser screenshot page.png
bridgic-browser close
```

## Standard CLI Session Pattern

1. Navigate (`open` or `search`).
2. Wait if needed, then get accessibility tree with refs (`snapshot`).
3. Interact by ref (`click`, `fill`, `select`, ...).
4. Verify / wait / capture (`verify-*`, `screenshot`, `pdf`, `wait`, `wait-network`).
5. Close when done (`close`) if session should not remain alive.

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
| Capture | `screenshot`, `pdf`, `downloads`, `wait-download` |
| Network | `network-start`, `network`, `network-stop`, `wait-network` |
| Dialog | `dialog-setup`, `dialog`, `dialog-remove` |
| Storage | `cookies`, `cookie-set`, `cookies-clear`, `storage-save`, `storage-load` |
| Verify | `verify-text`, `verify-visible`, `verify-url`, `verify-title`, `verify-state`, `verify-value` |
| Developer | `console-start`, `console`, `console-stop`, `trace-start`, `trace-chunk`, `trace-stop`, `video-start`, `video-stop` |
| Lifecycle | `close`, `resize` |

Use `-h` or `--help` on any command for exact usage information:

```bash
bridgic-browser -h
bridgic-browser snapshot -h
```

## High-Frequency Examples

```bash
# Open a browser if needed and Navigate to URL
bridgic-browser open https://example.com

# Fill and press Enter in one step
bridgic-browser fill @d6a530b4 "hello@example.com" --submit

# Snapshot only interactive elements
bridgic-browser snapshot -i

# Snapshot viewport only (exclude off-screen nodes)
bridgic-browser snapshot -F

# Save full snapshot to a specific file when output is too long
bridgic-browser snapshot -s /tmp/full-snapshot.txt

# Scroll: use --dy / --dx (not positional), supports negative values
bridgic-browser scroll --dy 500        # scroll down 500px
bridgic-browser scroll --dy -300       # scroll up 300px
bridgic-browser scroll --dx 200        # scroll right 200px

# Wait modes
bridgic-browser wait 2.5 # wait for 2.5 seconds
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

# File download workflow — click the trigger, then wait
bridgic-browser click @37015433          # click a download button/link
bridgic-browser wait-download 30         # block until download completes (default 30s)
bridgic-browser downloads                # list all downloads in this session

# Close the browser when everything is done
bridgic-browser close
```

## Runtime and Configuration

Config precedence (low -> high):

| Source | Notes |
|---|---|
| Defaults | `headless=True`, `clear_user_data=False` (persistent profile at `~/.bridgic/bridgic-browser/user_data/`) |
| `~/.bridgic/bridgic-browser/bridgic-browser.json` | User-level persistent config |
| `./bridgic-browser.json` | Project-specific config (daemon startup cwd) |
| `BRIDGIC_BROWSER_JSON` | Full JSON override for any Browser parameters (e.g. `{"headless":false}`) |

Environment variables and login state persistence are documented in `env-vars.md`.

## Non-Obvious CLI Behavior

- Refs come from the latest snapshot. If page changed, re-run `snapshot` before interaction.
- When `snapshot` output exceeds `-l <limit>`, or `-s <path>` is provided, full content is saved to a file (auto-generated under `~/.bridgic/bridgic-browser/snapshot/` or the specified path).
- `snapshot -i` returns only clickable/editable elements — use for action selection, not full-page inspection.
- CLI uses a persistent daemon/browser. State survives across commands until `close`.
- **`open` and `search` accept `--headed` and `--clear-user-data`** (startup flags only — ignored when a daemon is already running):
  - `bridgic-browser open --headed https://example.com` — start in headed mode
  - `bridgic-browser open --clear-user-data https://example.com` — start with ephemeral session (no persistent profile)
  - By default (no `--clear-user-data`), the browser uses a persistent profile saved at `~/.bridgic/bridgic-browser/user_data/`.
- After local Python code changes, restart daemon to pick up new code:
  - `bridgic-browser close`
  - run `open` or `search` command to auto-start again.
- `scroll` uses `--dy`/`--dx` options (not positional arguments) so negative values work correctly.
- `screenshot`, `pdf`, `upload`, `storage-save`, `storage-load`, `trace-stop` convert their path argument to an absolute path on the **client side** before sending to daemon (daemon's working directory may differ).
- For `network-start`, start capture before navigation if page-load requests are needed.
- **`wait` unit is SECONDS, not milliseconds**: `bridgic-browser wait 2` waits 2 seconds. A numeric argument always takes the time path; `--gone` is ignored for numbers.
  - `bridgic-browser wait 2.5` — wait for 2.5 seconds
  - `bridgic-browser wait "Submit"` — wait until "Submit" appears
  - `bridgic-browser wait --gone "Loading"` — wait until "Loading" disappears (`--gone` only works with a text argument)
- **`type` requires a focused element**: `type` sends keystrokes at the current cursor position. Run `bridgic-browser click @<ref>` or `bridgic-browser focus @<ref>` on the target input first.
- **`mouse-move`, `mouse-click`, `mouse-drag` use viewport pixel coordinates** measured from the top-left corner of the browser viewport. Example: `bridgic-browser mouse-click 500 300`.
- **File downloads require two commands**: `click` (or whatever triggers the download) followed immediately by `wait-download [SECONDS]`. `wait-download` blocks until the browser signals completion and returns the file name, size, and path. After that, `downloads` lists all files saved in the session. Downloads are always saved to `~/Downloads` unless a `downloads_path` is set in config.
- **`eval-on` CODE must be an arrow or named function** that accepts the element as its argument:
  - `bridgic-browser eval-on @8d4b03a9 "(el) => el.textContent"` ✓
  - `bridgic-browser eval-on @8d4b03a9 "el.textContent"` ✗ (not a function)

## When to Load Other References

- Need Python code instead of CLI commands: read `sdk-guide.md`.
- Need CLI and SDK mapping / migration (for example, CLI steps -> Python code generation): read `cli-sdk-api-mapping.md`.
- Need environment variables or login state persistence details: read `env-vars.md`.
