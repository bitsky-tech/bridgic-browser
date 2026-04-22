# CLI Full Coverage Scenarios

This file defines the minimum scenario set used by `run-cli-full-coverage.sh`.

## Scenario Map

| Scenario ID | Target page | Commands covered |
|---|---|---|
| S1-basic-navigation | `https://example.com` | `open`, `info`, `reload`, `back`, `forward`, `snapshot`, `verify-url`, `verify-title`, `resize`, `close` |
| S2-search-flow | Search engine result page | `search`, `wait`, `verify-text` |
| S3-elements | `scripts/qa/cli-full-coverage.html` | `click`, `double-click`, `hover`, `focus`, `fill`, `fill-form`, `select`, `options`, `check`, `uncheck`, `scroll-to`, `drag`, `upload`, `verify-state`, `verify-value`, `verify-visible` |
| S4-tabs | mixed local + public pages | `tabs`, `new-tab`, `switch-tab`, `close-tab` |
| S5-eval-keyboard-mouse | `scripts/qa/cli-full-coverage.html` | `eval`, `eval-on`, `type`, `press`, `key-down`, `key-up`, `scroll`, `mouse-click`, `mouse-move`, `mouse-drag`, `mouse-down`, `mouse-up` |
| S6-wait-capture-network | `https://example.com` | `wait`, `screenshot`, `pdf`, `network-start`, `network`, `network-stop`, `wait-network` |
| S7-dialog-storage-verify | `scripts/qa/inject-modal.html` + full-coverage page | `dialog-setup`, `dialog`, `dialog-remove`, `cookies`, `cookie-set`, `cookies-clear`, `storage-save`, `storage-load`, `verify-*` |
| S8-devtools | `scripts/qa/cli-full-coverage.html` | `console-start`, `console`, `console-stop`, `trace-start`, `trace-chunk`, `trace-stop`, `video-start`, `video-stop` |
| S9-lifecycle-cdp | local Chrome CDP session | `open --cdp`, `close` (disconnect behavior) |

## Test Inputs/Fixtures

- Main synthetic page: `scripts/qa/cli-full-coverage.html` (multi-widget page for refs, form, select, drag/drop, uploads, keyboard/mouse, delayed text, dialogs).
- Existing pages reused:
  - `scripts/qa/disabled-button.html`
  - `scripts/qa/inject-modal.html`
  - `scripts/qa/stable-flap.html`
  - `scripts/qa/shake-button.html`
- Temp upload file: `scripts/qa/tmp-upload.txt` created by runner.

## Execution Rules

- Always call CLI as `uv run bridgic-browser ...`.
- Capture command output and exit code per command.
- Re-run `snapshot` after each operation that may invalidate refs.
- Keep daemon state explicit: use `close` between major sections when needed.
- Mark `N/A` only with a concrete reason and evidence.
