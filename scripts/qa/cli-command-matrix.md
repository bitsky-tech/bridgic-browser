# Bridgic Browser CLI Command Coverage Matrix

Source of truth: `uv run bridgic-browser -h` (captured on 2026-04-21).

Latest execution:
- Run id: `20260421-182513`
- Summary: `PASS=90 FAIL=0 N/A=0`
- Report: `/tmp/bridgic-qa-20260421-182513/cli-full-coverage/coverage-report.md`

Status legend:
- `PENDING`: not executed yet
- `PASS`: executed and verified
- `FAIL`: executed but did not satisfy success criteria
- `N/A`: intentionally not applicable in current environment (must include reason)

Evidence conventions:
- `log:<path>#<command>` for command logs
- `artifact:<path>` for screenshots/traces/videos/files

| Group | Command | Scenario | Success criteria | Evidence slot | Status | Notes |
|---|---|---|---|---|---|---|
| Navigation | `open` | S1-basic-navigation | Exit 0 and URL changes |  | PENDING |  |
| Navigation | `search` | S2-search-flow | Exit 0 and results page loaded |  | PENDING |  |
| Navigation | `info` | S1-basic-navigation | Exit 0 and JSON/text includes URL/title |  | PENDING |  |
| Navigation | `reload` | S1-basic-navigation | Exit 0 |  | PENDING |  |
| Navigation | `back` | S1-basic-navigation | Exit 0 and URL reverses |  | PENDING |  |
| Navigation | `forward` | S1-basic-navigation | Exit 0 and URL advances |  | PENDING |  |
| Snapshot | `snapshot` | S1-basic-navigation | Exit 0 and snapshot text returned/saved |  | PENDING | includes `-i`, `-F`, `-l`, `-s` variants |
| Element Interaction | `click` | S3-elements | Exit 0 and target state changes |  | PENDING |  |
| Element Interaction | `fill` | S3-elements | Exit 0 and input value changes |  | PENDING | includes `--submit` variant |
| Element Interaction | `fill-form` | S3-elements | Exit 0 and multiple inputs updated |  | PENDING |  |
| Element Interaction | `scroll-to` | S3-elements | Exit 0 and element becomes in-view |  | PENDING |  |
| Element Interaction | `select` | S3-elements | Exit 0 and selected option changes |  | PENDING |  |
| Element Interaction | `options` | S3-elements | Exit 0 and options list returned |  | PENDING |  |
| Element Interaction | `check` | S3-elements | Exit 0 and checked state true |  | PENDING |  |
| Element Interaction | `uncheck` | S3-elements | Exit 0 and checked state false |  | PENDING |  |
| Element Interaction | `focus` | S3-elements | Exit 0 |  | PENDING |  |
| Element Interaction | `hover` | S3-elements | Exit 0 |  | PENDING |  |
| Element Interaction | `double-click` | S3-elements | Exit 0 and double-click handler fires |  | PENDING |  |
| Element Interaction | `upload` | S3-elements | Exit 0 and filename appears in UI |  | PENDING | requires temp file |
| Element Interaction | `drag` | S3-elements | Exit 0 and drag target state changes |  | PENDING |  |
| Tabs | `tabs` | S4-tabs | Exit 0 and tab list returned |  | PENDING |  |
| Tabs | `new-tab` | S4-tabs | Exit 0 and new page_id exists |  | PENDING | with/without URL |
| Tabs | `switch-tab` | S4-tabs | Exit 0 and active page_id switches |  | PENDING |  |
| Tabs | `close-tab` | S4-tabs | Exit 0 and tab closes |  | PENDING | with explicit page_id |
| Evaluate | `eval` | S5-eval-keyboard-mouse | Exit 0 and JS result returned |  | PENDING |  |
| Evaluate | `eval-on` | S5-eval-keyboard-mouse | Exit 0 and element-derived result returned |  | PENDING |  |
| Keyboard | `type` | S5-eval-keyboard-mouse | Exit 0 and typed text appears |  | PENDING | includes `--submit` variant |
| Keyboard | `press` | S5-eval-keyboard-mouse | Exit 0 and key action takes effect |  | PENDING |  |
| Keyboard | `key-down` | S5-eval-keyboard-mouse | Exit 0 |  | PENDING |  |
| Keyboard | `key-up` | S5-eval-keyboard-mouse | Exit 0 |  | PENDING |  |
| Mouse | `scroll` | S5-eval-keyboard-mouse | Exit 0 |  | PENDING | test `--dy`, `--dx` |
| Mouse | `mouse-click` | S5-eval-keyboard-mouse | Exit 0 and click handler fires |  | PENDING |  |
| Mouse | `mouse-move` | S5-eval-keyboard-mouse | Exit 0 |  | PENDING |  |
| Mouse | `mouse-drag` | S5-eval-keyboard-mouse | Exit 0 |  | PENDING |  |
| Mouse | `mouse-down` | S5-eval-keyboard-mouse | Exit 0 |  | PENDING |  |
| Mouse | `mouse-up` | S5-eval-keyboard-mouse | Exit 0 |  | PENDING |  |
| Wait | `wait` | S6-wait-capture-network | Exit 0 for seconds/text/gone variants |  | PENDING |  |
| Capture | `screenshot` | S6-wait-capture-network | Exit 0 and image file exists |  | PENDING | includes `--full-page` |
| Capture | `pdf` | S6-wait-capture-network | Exit 0 and PDF file exists |  | PENDING |  |
| Network | `network-start` | S6-wait-capture-network | Exit 0 |  | PENDING |  |
| Network | `network` | S6-wait-capture-network | Exit 0 and request list returned |  | PENDING | includes `--static`, `--no-clear` |
| Network | `network-stop` | S6-wait-capture-network | Exit 0 |  | PENDING |  |
| Network | `wait-network` | S6-wait-capture-network | Exit 0 after idle wait |  | PENDING |  |
| Dialog | `dialog-setup` | S7-dialog-storage-verify | Exit 0 and next dialog auto-handled |  | PENDING |  |
| Dialog | `dialog` | S7-dialog-storage-verify | Exit 0 and one dialog handled |  | PENDING |  |
| Dialog | `dialog-remove` | S7-dialog-storage-verify | Exit 0 |  | PENDING |  |
| Storage | `cookies` | S7-dialog-storage-verify | Exit 0 and cookie list returned |  | PENDING | with filters |
| Storage | `cookie-set` | S7-dialog-storage-verify | Exit 0 and cookie visible in `cookies` |  | PENDING |  |
| Storage | `cookies-clear` | S7-dialog-storage-verify | Exit 0 and cookie removed |  | PENDING |  |
| Storage | `storage-save` | S7-dialog-storage-verify | Exit 0 and state file exists |  | PENDING |  |
| Storage | `storage-load` | S7-dialog-storage-verify | Exit 0 and state restored |  | PENDING |  |
| Verify | `verify-text` | S7-dialog-storage-verify | Exit 0 on expected text |  | PENDING |  |
| Verify | `verify-visible` | S7-dialog-storage-verify | Exit 0 for role+name |  | PENDING |  |
| Verify | `verify-url` | S7-dialog-storage-verify | Exit 0 for URL check |  | PENDING | exact/non-exact |
| Verify | `verify-title` | S7-dialog-storage-verify | Exit 0 for title check |  | PENDING | exact/non-exact |
| Verify | `verify-state` | S7-dialog-storage-verify | Exit 0 for chosen state |  | PENDING | includes disabled boundary |
| Verify | `verify-value` | S7-dialog-storage-verify | Exit 0 for input value check |  | PENDING |  |
| Developer | `console-start` | S8-devtools | Exit 0 |  | PENDING |  |
| Developer | `console` | S8-devtools | Exit 0 and console entries returned |  | PENDING | with `--filter`, `--no-clear` |
| Developer | `console-stop` | S8-devtools | Exit 0 |  | PENDING |  |
| Developer | `trace-start` | S8-devtools | Exit 0 |  | PENDING | includes disable flags |
| Developer | `trace-chunk` | S8-devtools | Exit 0 |  | PENDING |  |
| Developer | `trace-stop` | S8-devtools | Exit 0 and trace zip exists |  | PENDING |  |
| Developer | `video-start` | S8-devtools | Exit 0 |  | PENDING |  |
| Developer | `video-stop` | S8-devtools | Exit 0 and webm exists |  | PENDING |  |
| Lifecycle | `close` | S9-lifecycle-cdp | Exit 0 and session closed |  | PENDING |  |
| Lifecycle | `resize` | S9-lifecycle-cdp | Exit 0 and viewport changes |  | PENDING |  |
