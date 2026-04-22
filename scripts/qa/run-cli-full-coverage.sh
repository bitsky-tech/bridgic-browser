#!/usr/bin/env bash
# Run all bridgic-browser CLI subcommands at least once and collect evidence.

set -uo pipefail

THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$THIS_DIR/.." && pwd)/.."
source "$THIS_DIR/env.sh"

REPORT_DIR="$QA_DIR/cli-full-coverage"
LOG_DIR="$REPORT_DIR/logs"
ART_DIR="$REPORT_DIR/artifacts"
mkdir -p "$LOG_DIR" "$ART_DIR"

PLAYGROUND_URL="file://$THIS_DIR/cli-full-coverage.html"
INJECT_MODAL_URL="file://$THIS_DIR/inject-modal.html"
TMP_UPLOAD_FILE="$THIS_DIR/tmp-upload.txt"
SNAPSHOT_FILE="$ART_DIR/snapshot.txt"
RESULTS_FILE="$REPORT_DIR/coverage-results.tsv"

echo -e "command\tstatus\tevidence\tnote" > "$RESULTS_FILE"
echo "upload-fixture" > "$TMP_UPLOAD_FILE"

run_cli() {
  local key="$1"
  shift
  local logfile="$LOG_DIR/${key// /_}.log"
  local cmd=(uv run bridgic-browser "$@")
  {
    echo "## $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "$ ${cmd[*]}"
  } >>"$logfile"
  "${cmd[@]}" >>"$logfile" 2>&1
  return $?
}

record_result() {
  local command="$1"
  local status="$2"
  local evidence="$3"
  local note="${4:-}"
  echo -e "${command}\t${status}\t${evidence}\t${note}" >> "$RESULTS_FILE"
}

run_and_record() {
  local command="$1"
  shift
  if run_cli "$command" "$@"; then
    record_result "$command" "PASS" "log:$LOG_DIR/${command// /_}.log"
  else
    record_result "$command" "FAIL" "log:$LOG_DIR/${command// /_}.log" "exit=$?"
  fi
}

refresh_snapshot() {
  uv run bridgic-browser snapshot -i -s "$SNAPSHOT_FILE" >/dev/null 2>&1
}

ref_by_text() {
  local text="$1"
  python3 - <<'PY' "$SNAPSHOT_FILE" "$text"
import re, sys
snapshot_path, text = sys.argv[1], sys.argv[2]
pat = re.compile(r"ref=([a-f0-9]{8})")
with open(snapshot_path, "r", encoding="utf-8") as f:
    for line in f:
        if text in line:
            m = pat.search(line)
            if m:
                print(m.group(1))
                raise SystemExit(0)
raise SystemExit(1)
PY
}

mark_na() {
  local command="$1"
  local reason="$2"
  record_result "$command" "N/A" "-" "$reason"
}

qa_log "cli-full-coverage: starting, report_dir=$REPORT_DIR"

# Navigation + baseline
run_and_record "open" open https://example.com
run_and_record "info" info
run_and_record "reload" reload
run_and_record "search" search "bridgic browser cli" --engine duckduckgo
run_and_record "back" back
run_and_record "forward" forward
run_and_record "snapshot" snapshot
run_and_record "snapshot_i" snapshot -i
run_and_record "snapshot_F" snapshot -F
run_and_record "snapshot_l" snapshot -l 300
run_and_record "snapshot_s" snapshot -s "$ART_DIR/snapshot-full.txt"

# Playground for element/ref driven actions
run_and_record "open_playground" open "$PLAYGROUND_URL"
refresh_snapshot

CLICK_REF="$(ref_by_text "Click Target" 2>/dev/null || true)"
DOUBLE_REF="$(ref_by_text "Double Target" 2>/dev/null || true)"
HOVER_REF="$(ref_by_text "Hover Target" 2>/dev/null || true)"
NAME_REF="$(ref_by_text "Name Input" 2>/dev/null || true)"
EMAIL_REF="$(ref_by_text "Email Input" 2>/dev/null || true)"
MSG_REF="$(ref_by_text "Message Input" 2>/dev/null || true)"
SELECT_REF="$(ref_by_text "Color Select" 2>/dev/null || true)"
CHECK_REF="$(ref_by_text "Agree Checkbox" 2>/dev/null || true)"
FILE_REF="$(ref_by_text "File Input" 2>/dev/null || true)"
DRAG_REF="$(ref_by_text "Drag Source" 2>/dev/null || true)"
DROP_REF="$(ref_by_text "Drop Target" 2>/dev/null || true)"
OFFSCREEN_REF="$(ref_by_text "Offscreen Target" 2>/dev/null || true)"
ALERT_REF="$(ref_by_text "Open Alert" 2>/dev/null || true)"
CONFIRM_REF="$(ref_by_text "Open Confirm" 2>/dev/null || true)"
PROMPT_REF="$(ref_by_text "Open Prompt" 2>/dev/null || true)"
SUBMIT_REF="$(ref_by_text "Submit Form" 2>/dev/null || true)"
LATER_REF="$(ref_by_text "Show Text Later" 2>/dev/null || true)"

if [[ -n "$CLICK_REF" ]]; then run_and_record "click" click "@$CLICK_REF"; else mark_na "click" "ref not found"; fi
if [[ -n "$DOUBLE_REF" ]]; then run_and_record "double-click" double-click "@$DOUBLE_REF"; else mark_na "double-click" "ref not found"; fi
if [[ -n "$HOVER_REF" ]]; then run_and_record "hover" hover "@$HOVER_REF"; else mark_na "hover" "ref not found"; fi
if [[ -n "$NAME_REF" ]]; then run_and_record "focus" focus "@$NAME_REF"; else mark_na "focus" "ref not found"; fi
if [[ -n "$NAME_REF" ]]; then run_and_record "fill" fill "@$NAME_REF" "alice"; else mark_na "fill" "ref not found"; fi
if [[ -n "$EMAIL_REF" && -n "$MSG_REF" ]]; then
  run_and_record "fill-form" fill-form "[{\"ref\":\"$EMAIL_REF\",\"value\":\"a@example.com\"},{\"ref\":\"$MSG_REF\",\"value\":\"hello\"}]"
else
  mark_na "fill-form" "refs not found"
fi
if [[ -n "$SELECT_REF" ]]; then run_and_record "options" options "@$SELECT_REF"; else mark_na "options" "ref not found"; fi
if [[ -n "$SELECT_REF" ]]; then run_and_record "select" select "@$SELECT_REF" "Green"; else mark_na "select" "ref not found"; fi
if [[ -n "$CHECK_REF" ]]; then run_and_record "check" check "@$CHECK_REF"; else mark_na "check" "ref not found"; fi
if [[ -n "$CHECK_REF" ]]; then run_and_record "uncheck" uncheck "@$CHECK_REF"; else mark_na "uncheck" "ref not found"; fi
if [[ -n "$OFFSCREEN_REF" ]]; then run_and_record "scroll-to" scroll-to "@$OFFSCREEN_REF"; else mark_na "scroll-to" "ref not found"; fi
if [[ -n "$DRAG_REF" && -n "$DROP_REF" ]]; then run_and_record "drag" drag "@$DRAG_REF" "@$DROP_REF"; else mark_na "drag" "refs not found"; fi
if [[ -n "$FILE_REF" ]]; then run_and_record "upload" upload "@$FILE_REF" "$TMP_UPLOAD_FILE"; else mark_na "upload" "ref not found"; fi

# Tabs
run_and_record "tabs" tabs
run_and_record "new-tab" new-tab https://example.com
TAB_LIST="$LOG_DIR/tabs_after_new-tab.log"
uv run bridgic-browser tabs > "$TAB_LIST" 2>&1 || true
NEW_PAGE_ID="$(python3 - <<'PY' "$TAB_LIST"
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
ids = re.findall(r'(page_[0-9]+)', text)
if ids:
    print(ids[-1])
PY
)"
if [[ -n "${NEW_PAGE_ID:-}" ]]; then
  run_and_record "switch-tab" switch-tab "$NEW_PAGE_ID"
  run_and_record "close-tab" close-tab "$NEW_PAGE_ID"
else
  mark_na "switch-tab" "page_id parse failed"
  mark_na "close-tab" "page_id parse failed"
fi

# Evaluate + keyboard + mouse
run_and_record "open_playground_for_eval" open "$PLAYGROUND_URL"
refresh_snapshot
CLICK_REF="$(ref_by_text "Click Target" 2>/dev/null || true)"
NAME_REF="$(ref_by_text "Name Input" 2>/dev/null || true)"
LATER_REF="$(ref_by_text "Show Text Later" 2>/dev/null || true)"
run_and_record "eval" eval "window.location.href"
if [[ -n "$CLICK_REF" ]]; then run_and_record "eval-on" eval-on "@$CLICK_REF" "(el) => el.id"; else mark_na "eval-on" "ref not found"; fi
if [[ -n "$NAME_REF" ]]; then run_and_record "focus_for_type" focus "@$NAME_REF"; fi
run_and_record "type" type " typed" --submit
run_and_record "press" press Enter
run_and_record "key-down" key-down Shift
run_and_record "key-up" key-up Shift
run_and_record "scroll" scroll --dy 120
run_and_record "mouse-move" mouse-move 120 120
run_and_record "mouse-down" mouse-down --button left
run_and_record "mouse-up" mouse-up --button left
run_and_record "mouse-click" mouse-click 140 140 --button left --count 1
run_and_record "mouse-drag" mouse-drag 180 180 260 220

# Wait + capture + network
run_and_record "wait_seconds" wait 1.2
if [[ -n "$LATER_REF" ]]; then run_and_record "click_show_later" click "@$LATER_REF"; fi
run_and_record "wait_text" wait "ASYNC READY"
run_and_record "wait_gone" wait --gone "NOT-PRESENT-TEXT"
run_and_record "screenshot" screenshot "$ART_DIR/page.png"
run_and_record "screenshot_full" screenshot "$ART_DIR/page-full.png" --full-page
run_and_record "pdf" pdf "$ART_DIR/page.pdf"
run_and_record "network-start" network-start
run_and_record "open_for_network" open https://example.com
run_and_record "wait-network" wait-network 10
run_and_record "network" network --no-clear
run_and_record "network_static" network --static
run_and_record "network-stop" network-stop

# Dialog + storage + verify
run_and_record "open_playground_again" open "$PLAYGROUND_URL"
refresh_snapshot
ALERT_REF="$(ref_by_text "Open Alert" 2>/dev/null || true)"
CONFIRM_REF="$(ref_by_text "Open Confirm" 2>/dev/null || true)"
PROMPT_REF="$(ref_by_text "Open Prompt" 2>/dev/null || true)"
NAME_REF="$(ref_by_text "Name Input" 2>/dev/null || true)"
CHECK_REF="$(ref_by_text "Agree Checkbox" 2>/dev/null || true)"
EMAIL_REF="$(ref_by_text "Email Input" 2>/dev/null || true)"
run_and_record "dialog-setup" dialog-setup --action dismiss
if [[ -n "$ALERT_REF" ]]; then run_and_record "click_alert" click "@$ALERT_REF"; fi
run_and_record "dialog-remove" dialog-remove
if [[ -n "$CONFIRM_REF" ]]; then
  run_and_record "dialog" dialog --dismiss
  run_and_record "click_confirm" click "@$CONFIRM_REF"
else
  mark_na "dialog" "confirm ref not found"
fi
if [[ -n "$EMAIL_REF" ]]; then run_and_record "fill_for_verify_value" fill "@$EMAIL_REF" "verify@example.com"; fi
if [[ -n "$EMAIL_REF" ]]; then run_and_record "verify-value" verify-value "@$EMAIL_REF" "verify@example.com"; else mark_na "verify-value" "ref not found"; fi
if [[ -n "$CHECK_REF" ]]; then run_and_record "verify-state" verify-state "@$CHECK_REF" unchecked; else mark_na "verify-state" "ref not found"; fi
run_and_record "verify-text" verify-text "CLI Full Coverage Playground"
run_and_record "verify-url" verify-url "$PLAYGROUND_URL"
run_and_record "verify-title" verify-title "QA: CLI full coverage playground"
run_and_record "verify-visible" verify-visible button "Click Target"
run_and_record "cookies" cookies
run_and_record "cookie-set" cookie-set cli_full_cov yes --domain example.com --path /
run_and_record "cookies_domain" cookies --domain example.com
run_and_record "cookies-clear" cookies-clear --name cli_full_cov --domain example.com --path /
run_and_record "storage-save" storage-save "$ART_DIR/storage-state.json"
run_and_record "storage-load" storage-load "$ART_DIR/storage-state.json"

# Developer tools
run_and_record "console-start" console-start
run_and_record "eval_console" eval "console.log('cli-full-coverage-console')"
run_and_record "console" console --no-clear
run_and_record "console_filter" console --filter log
run_and_record "console-stop" console-stop
run_and_record "trace-start" trace-start
run_and_record "trace-chunk" trace-chunk full-coverage-phase
run_and_record "wait_trace" wait 1
run_and_record "trace-stop" trace-stop "$ART_DIR/trace.zip"
run_and_record "video-start" video-start --width 800 --height 600
run_and_record "wait_video" wait 1
run_and_record "video-stop" video-stop "$ART_DIR/video.webm"

# Lifecycle + resize
run_and_record "resize" resize 1024 768
run_and_record "close" close

# CDP mode (optional)
if [[ -n "${QA_CHROME_BIN:-}" && -x "${QA_CHROME_BIN:-}" ]]; then
  "$QA_CHROME_BIN" --remote-debugging-port="$QA_CDP_PORT" --user-data-dir="$QA_USER_DATA" about:blank >/dev/null 2>&1 &
  sleep 2
  if run_cli "open_cdp" open https://example.com --cdp "$QA_CDP_PORT"; then
    record_result "open --cdp" "PASS" "log:$LOG_DIR/open_cdp.log"
    run_cli "close_cdp" close >/dev/null 2>&1 || true
  else
    record_result "open --cdp" "FAIL" "log:$LOG_DIR/open_cdp.log"
  fi
else
  mark_na "open --cdp" "QA_CHROME_BIN unavailable"
fi

python3 - <<'PY' "$RESULTS_FILE" "$REPORT_DIR/summary.txt"
import csv, sys
results_path, out_path = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(results_path, encoding="utf-8"), delimiter="\t"))
total = len(rows)
counts = {}
for row in rows:
    counts[row["status"]] = counts.get(row["status"], 0) + 1
with open(out_path, "w", encoding="utf-8") as f:
    f.write(f"total={total}\n")
    for k in ("PASS", "FAIL", "N/A"):
        f.write(f"{k}={counts.get(k, 0)}\n")
    if counts.get("FAIL", 0):
        f.write("\n[failures]\n")
        for row in rows:
            if row["status"] == "FAIL":
                f.write(f'{row["command"]}\t{row["evidence"]}\t{row["note"]}\n')
PY

python3 "$THIS_DIR/render-cli-coverage-report.py" \
  "$RESULTS_FILE" \
  "$REPORT_DIR/coverage-report.md" >/dev/null 2>&1 || true

bash "$THIS_DIR/collect-artifacts.sh" "cli-full-coverage" >/dev/null 2>&1 || true
qa_log "cli-full-coverage: done report=$REPORT_DIR/summary.txt"
cat "$REPORT_DIR/summary.txt"
