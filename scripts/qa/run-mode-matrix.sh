#!/usr/bin/env bash
# scripts/qa/run-mode-matrix.sh
#
# Orchestrates run-cli-full-coverage.sh across 7 mode variants:
#
#   V1  Persistent × Headless × Stealth=on     (baseline, production default)
#   V2  Persistent × Headed   × Stealth=on     (auto channel=chrome)
#   V3  Ephemeral  × Headless × Stealth=on     (full 50+ stealth flags)
#   V4  Ephemeral  × Headed   × Stealth=on     (minimal stealth flags)
#   V5  CDP attach × Headless × Stealth=on     (connect to external --headless=new Chrome)
#   V6  CDP attach × Headed   × Stealth=on     (connect to external headed Chrome)
#   V7  Persistent × Headless × Stealth=off    (smoke subset only)
#
# Usage:
#   bash scripts/qa/run-mode-matrix.sh
#   BRIDGIC_QA_VARIANTS="V1 V5" bash scripts/qa/run-mode-matrix.sh   # subset
#
# Env overrides (see env.sh): QA_TS, QA_DIR, QA_CHROME_BIN, QA_CDP_PORT

set -uo pipefail

THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$THIS_DIR/env.sh"

PLAYGROUND_URL="file://$THIS_DIR/cli-full-coverage.html"
MATRIX_DIR="$QA_DIR/mode-matrix"
mkdir -p "$MATRIX_DIR"

: "${BRIDGIC_QA_VARIANTS:=V1 V2 V3 V4 V5 V6 V7}"
# SDK differential pass runs after CLI for V1/V3/V5 only; set to 0 to skip.
: "${BRIDGIC_QA_SDK:=1}"

CDP_PID=""
cleanup_cdp() {
  if [[ -n "$CDP_PID" ]] && kill -0 "$CDP_PID" 2>/dev/null; then
    kill "$CDP_PID" 2>/dev/null || true
    qa_log "mode-matrix: killed CDP chrome pid=$CDP_PID"
  fi
  CDP_PID=""
  reset_qa_chrome
}
trap cleanup_cdp EXIT

configure_variant() {
  local v="$1"
  case "$v" in
    V1) export BRIDGIC_QA_HEADED=0 BRIDGIC_QA_CLEAR_USER_DATA=0 BRIDGIC_QA_CDP="" BRIDGIC_QA_STEALTH=1 ;;
    V2) export BRIDGIC_QA_HEADED=1 BRIDGIC_QA_CLEAR_USER_DATA=0 BRIDGIC_QA_CDP="" BRIDGIC_QA_STEALTH=1 ;;
    V3) export BRIDGIC_QA_HEADED=0 BRIDGIC_QA_CLEAR_USER_DATA=1 BRIDGIC_QA_CDP="" BRIDGIC_QA_STEALTH=1 ;;
    V4) export BRIDGIC_QA_HEADED=1 BRIDGIC_QA_CLEAR_USER_DATA=1 BRIDGIC_QA_CDP="" BRIDGIC_QA_STEALTH=1 ;;
    V5) export BRIDGIC_QA_HEADED=0 BRIDGIC_QA_CLEAR_USER_DATA=0 BRIDGIC_QA_CDP="$QA_CDP_PORT" BRIDGIC_QA_STEALTH=1 ;;
    V6) export BRIDGIC_QA_HEADED=1 BRIDGIC_QA_CLEAR_USER_DATA=0 BRIDGIC_QA_CDP="$QA_CDP_PORT" BRIDGIC_QA_STEALTH=1 ;;
    V7) export BRIDGIC_QA_HEADED=0 BRIDGIC_QA_CLEAR_USER_DATA=0 BRIDGIC_QA_CDP="" BRIDGIC_QA_STEALTH=0 ;;
    *) echo "[mode-matrix] unknown variant: $v" >&2; return 1 ;;
  esac
  export BRIDGIC_QA_VARIANT="$v"
}

headed_prerequisite_ok() {
  [[ -n "${QA_CHROME_BIN:-}" && -x "${QA_CHROME_BIN:-}" ]]
}

_pick_free_port() {
  # Walk a few candidate ports and pick the first not already bound on 127.0.0.1.
  # User's regular Chrome sometimes listens on 9222, so we cannot assume it.
  for port in "$QA_CDP_PORT" 19222 29222 39222 49222; do
    if ! nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      echo "$port"
      return 0
    fi
  done
  return 1
}

start_cdp_chrome() {
  local mode="$1"  # headless | headed
  local extra_args=()
  if [[ "$mode" == "headless" ]]; then
    extra_args+=(--headless=new)
  fi
  reset_qa_chrome

  # Pick a free port — user may have another Chrome on the default 9222.
  local free_port
  free_port=$(_pick_free_port) || {
    qa_log "mode-matrix: no free CDP port among {$QA_CDP_PORT,19222,29222,39222,49222}"
    return 1
  }
  export QA_CDP_PORT="$free_port"
  # Propagate to the child CLI so --cdp matches.
  export BRIDGIC_QA_CDP="$free_port"

  mkdir -p "$QA_USER_DATA"
  # Note: "${extra_args[@]+"${extra_args[@]}"}" safely expands to nothing
  # when extra_args is empty (headed mode). Bash 3.2 + set -u blows up on
  # the plain "${extra_args[@]}" form.
  "$QA_CHROME_BIN" \
    --remote-debugging-port="$QA_CDP_PORT" \
    --user-data-dir="$QA_USER_DATA" \
    --no-first-run --no-default-browser-check \
    --disable-session-crashed-bubble \
    --password-store=basic --use-mock-keychain \
    ${extra_args[@]+"${extra_args[@]}"} \
    about:blank \
    > "$QA_DIR/chrome-$QA_CDP_PORT.log" 2>&1 &
  CDP_PID=$!
  # Cold Chrome + headless=new can take several seconds on macOS; poll up to 15s.
  for _ in $(seq 1 150); do
    if curl -fsS "http://127.0.0.1:$QA_CDP_PORT/json/version" >/dev/null 2>&1; then
      qa_log "mode-matrix: CDP chrome ($mode) ready pid=$CDP_PID port=$QA_CDP_PORT"
      return 0
    fi
    sleep 0.1
  done
  qa_log "mode-matrix: CDP chrome failed to open DevTools port $QA_CDP_PORT (15s)"
  return 1
}

# V7 smoke: minimal path to confirm stealth=off doesn't break basic flow.
# Not a full coverage pass; intended solely as a regression canary for the
# stealth=False code path.
run_v7_smoke() {
  local REPORT_DIR="$QA_DIR/cli-full-coverage/V7"
  local LOG_DIR="$REPORT_DIR/logs"
  local ART_DIR="$REPORT_DIR/artifacts"
  mkdir -p "$LOG_DIR" "$ART_DIR"
  local RESULTS_FILE="$REPORT_DIR/coverage-results.tsv"
  echo -e "command\tstatus\tevidence\tnote" > "$RESULTS_FILE"

  run_v7() {
    local name="$1"; shift
    local logfile="$LOG_DIR/${name}.log"
    {
      echo "## $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
      echo "\$ uv run bridgic-browser $*"
    } >>"$logfile"
    if uv run bridgic-browser "$@" >>"$logfile" 2>&1; then
      echo -e "${name}\tPASS\tlog:$logfile\t" >> "$RESULTS_FILE"
    else
      echo -e "${name}\tFAIL\tlog:$logfile\texit=$?" >> "$RESULTS_FILE"
    fi
  }

  run_v7 "open"         open "$PLAYGROUND_URL"
  run_v7 "info"         info
  run_v7 "snapshot_i"   snapshot -i
  run_v7 "reload"       reload
  run_v7 "eval"         eval "1+1"
  run_v7 "screenshot"   screenshot "$ART_DIR/v7-smoke.png"
  run_v7 "verify-title" verify-title "QA: CLI full coverage playground"
  run_v7 "close"        close

  qa_log "mode-matrix: V7 smoke complete"
}

run_variant() {
  local v="$1"
  qa_log "============================================================"
  qa_log "mode-matrix: START variant=$v"
  configure_variant "$v" || return 1

  if [[ "$v" == "V2" || "$v" == "V4" || "$v" == "V6" ]]; then
    if ! headed_prerequisite_ok; then
      qa_log "mode-matrix: SKIP $v — no system Chrome"
      mkdir -p "$QA_DIR/cli-full-coverage/$v"
      local results="$QA_DIR/cli-full-coverage/$v/coverage-results.tsv"
      echo -e "command\tstatus\tevidence\tnote" > "$results"
      echo -e "(variant)\tN/A\t-\tQA_CHROME_BIN unavailable" >> "$results"
      return
    fi
  fi

  full_reset

  if [[ "$v" == "V5" ]]; then
    if ! start_cdp_chrome headless; then
      mkdir -p "$QA_DIR/cli-full-coverage/$v"
      echo -e "command\tstatus\tevidence\tnote" > "$QA_DIR/cli-full-coverage/$v/coverage-results.tsv"
      echo -e "(variant)\tN/A\t-\tCDP headless chrome failed to start" >> "$QA_DIR/cli-full-coverage/$v/coverage-results.tsv"
      return
    fi
  elif [[ "$v" == "V6" ]]; then
    if ! start_cdp_chrome headed; then
      mkdir -p "$QA_DIR/cli-full-coverage/$v"
      echo -e "command\tstatus\tevidence\tnote" > "$QA_DIR/cli-full-coverage/$v/coverage-results.tsv"
      echo -e "(variant)\tN/A\t-\tCDP headed chrome failed to start" >> "$QA_DIR/cli-full-coverage/$v/coverage-results.tsv"
      return
    fi
  fi

  if [[ "$v" == "V7" ]]; then
    # V7 is persistent × stealth=off. Chrome refuses to reuse a user_data_dir
    # that a stealth=on run populated (SIGTRAP on launch due to incompatible
    # flag set), so V7 gets its own scratch user_data_dir. full_reset already
    # killed any daemon; the new daemon will read BRIDGIC_BROWSER_JSON here.
    local v7_userdata="$QA_DIR/v7-userdata"
    rm -rf "$v7_userdata"
    mkdir -p "$v7_userdata"
    export BRIDGIC_BROWSER_JSON="{\"stealth\": false, \"user_data_dir\": \"$v7_userdata\"}"
    run_v7_smoke
    unset BRIDGIC_BROWSER_JSON
  else
    bash "$THIS_DIR/run-cli-full-coverage.sh" || qa_log "mode-matrix: $v script returned nonzero"
  fi

  # SDK differential pass for V1/V3/V5. The CLI run's daemon holds the
  # persistent user-data-dir lock (V1), so we reset_bridgic first to release
  # it; SDK script opens its own Browser instance.
  if [[ "$BRIDGIC_QA_SDK" == "1" ]] && [[ "$v" == "V1" || "$v" == "V3" || "$v" == "V5" ]]; then
    qa_log "mode-matrix: running SDK differential pass for $v"
    reset_bridgic
    local sdk_args=(--variant "$v")
    if [[ "$v" == "V5" ]]; then sdk_args+=(--cdp "$QA_CDP_PORT"); fi
    if ! uv run python3 "$THIS_DIR/run-sdk-coverage.py" "${sdk_args[@]}"; then
      qa_log "mode-matrix: SDK pass for $v reported failures"
    fi
  fi

  if [[ "$v" == "V5" || "$v" == "V6" ]]; then
    cleanup_cdp
  fi

  qa_log "mode-matrix: END variant=$v"
}

qa_log "mode-matrix: starting variants=($BRIDGIC_QA_VARIANTS)"
for v in $BRIDGIC_QA_VARIANTS; do
  run_variant "$v"
done

qa_log "mode-matrix: rendering aggregate report"
REPORT_OUT="$MATRIX_DIR/mode-matrix-report.md"
if python3 "$THIS_DIR/render-mode-matrix-report.py" "$QA_DIR" > "$REPORT_OUT"; then
  qa_log "mode-matrix: report written to $REPORT_OUT"
else
  qa_log "mode-matrix: WARNING report rendering failed"
fi

echo
echo "===== Mode matrix summary ====="
for v in $BRIDGIC_QA_VARIANTS; do
  local_tsv="$QA_DIR/cli-full-coverage/$v/coverage-results.tsv"
  if [[ -f "$local_tsv" ]]; then
    pass=$(awk -F'\t' 'NR>1 && $2=="PASS"' "$local_tsv" | wc -l | tr -d ' ')
    fail=$(awk -F'\t' 'NR>1 && $2=="FAIL"' "$local_tsv" | wc -l | tr -d ' ')
    na=$(awk -F'\t' 'NR>1 && $2=="N/A"' "$local_tsv" | wc -l | tr -d ' ')
    echo "$v  PASS=$pass  FAIL=$fail  N/A=$na"
  else
    echo "$v  (no results)"
  fi
done
echo "Aggregate report: $REPORT_OUT"
