#!/usr/bin/env bash
# scripts/qa/env.sh
# Source this file at the top of every QA session:
#   source scripts/qa/env.sh
#
# Exports:
#   QA_TS          : timestamp tag for this run
#   QA_DIR         : artifact output directory (/tmp/bridgic-qa-$QA_TS)
#   QA_CHROME_BIN  : absolute path to system Chrome (macOS)
#   QA_USER_DATA   : Chrome user_data dir for QA-only Chrome (CDP target)
#   QA_CDP_PORT    : default CDP remote-debugging port (9222)
#   BRIDGIC_LOG_LEVEL=DEBUG
#   BRIDGIC_DAEMON_LOG_FILE=$QA_DIR/daemon.log
#
# Provides functions:
#   reset_bridgic     : stop daemon + wipe run/ + clear snapshot/tmp
#   reset_qa_chrome   : kill QA Chrome instance(s) + wipe /tmp/chrome-qa
#   full_reset        : reset_bridgic + reset_qa_chrome
#   qa_log <msg>      : timestamped echo into $QA_DIR/run.log

set -u  # nounset, but leave errexit to the caller

if [[ -z "${QA_TS:-}" ]]; then
  export QA_TS=$(date +%Y%m%d-%H%M%S)
fi
export QA_DIR="${QA_DIR:-/tmp/bridgic-qa-$QA_TS}"
mkdir -p "$QA_DIR"

export BRIDGIC_LOG_LEVEL=DEBUG
export BRIDGIC_DAEMON_LOG_FILE="$QA_DIR/daemon.log"

export QA_USER_DATA="${QA_USER_DATA:-/tmp/chrome-qa-$QA_TS}"
export QA_CDP_PORT="${QA_CDP_PORT:-9222}"

# Chrome binary resolution (macOS)
if [[ -z "${QA_CHROME_BIN:-}" ]]; then
  for cand in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta" \
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
    if [[ -x "$cand" ]]; then
      export QA_CHROME_BIN="$cand"
      break
    fi
  done
fi
if [[ -z "${QA_CHROME_BIN:-}" ]]; then
  echo "[qa/env.sh] WARNING: no Chrome binary found; CDP cases will fail." >&2
fi

qa_log() {
  local ts; ts=$(date +%H:%M:%S)
  echo "[$ts] $*" | tee -a "$QA_DIR/run.log"
}

reset_bridgic() {
  bridgic-browser close >/dev/null 2>&1 || true
  # Give background close some time
  sleep 1
  # Kill any remaining daemon
  pkill -f "bridgic.browser daemon" 2>/dev/null || true
  sleep 1
  rm -rf "$HOME/.bridgic/bridgic-browser/run" \
         "$HOME/.bridgic/bridgic-browser/snapshot" \
         "$HOME/.bridgic/bridgic-browser/tmp"
}

reset_qa_chrome() {
  pkill -f "remote-debugging-port=$QA_CDP_PORT" 2>/dev/null || true
  sleep 1
  rm -rf "$QA_USER_DATA"
}

full_reset() {
  reset_bridgic
  reset_qa_chrome
}

qa_log "env.sh loaded  QA_DIR=$QA_DIR  Chrome=${QA_CHROME_BIN:-NONE}"
