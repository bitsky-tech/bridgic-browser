#!/usr/bin/env bash
# scripts/qa/setup-chrome.sh [PORT] [URL1 URL2 ...]
#
# Launches a QA-only Chrome with --remote-debugging-port and a scratch
# --user-data-dir. Waits until DevTools HTTP endpoint is live.
# Prints: PID on stdout. Exits nonzero if Chrome failed to come up.
#
# Opens any URLs given as args as pre-existing tabs (so bridgic attaches
# to already-loaded pages).
#
# Env: QA_CHROME_BIN, QA_USER_DATA, QA_CDP_PORT (see env.sh)

set -euo pipefail
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$THIS_DIR/env.sh"

PORT="${1:-$QA_CDP_PORT}"
shift 2>/dev/null || true
URLS=("$@")

if [[ -z "${QA_CHROME_BIN:-}" || ! -x "$QA_CHROME_BIN" ]]; then
  echo "[qa/setup-chrome.sh] Chrome not found: $QA_CHROME_BIN" >&2
  exit 1
fi

mkdir -p "$QA_USER_DATA"

# Common flags for a clean QA Chrome: no restore prompt, no first-run wizard,
# no default-browser nag, no translate bar, silent-login.
FLAGS=(
  --remote-debugging-port="$PORT"
  --user-data-dir="$QA_USER_DATA"
  --no-first-run
  --no-default-browser-check
  --disable-session-crashed-bubble
  --disable-features=TranslateUI
  --password-store=basic
  --use-mock-keychain
)

# Pass at least one URL so Chrome opens a real window (no start-page grid that
# can grab focus and confuse CDP). If the caller didn't provide URLs, use
# about:blank.
if [[ ${#URLS[@]} -eq 0 ]]; then
  URLS=("about:blank")
fi

qa_log "setup-chrome: launching $QA_CHROME_BIN port=$PORT urls=${URLS[*]}"
"$QA_CHROME_BIN" "${FLAGS[@]}" "${URLS[@]}" >"$QA_DIR/chrome-$PORT.log" 2>&1 &
CHROME_PID=$!

# Wait for DevTools HTTP endpoint
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    qa_log "setup-chrome: port $PORT ready (pid=$CHROME_PID, after ${i}0ms)"
    echo "$CHROME_PID"
    exit 0
  fi
  sleep 0.1
done

kill "$CHROME_PID" 2>/dev/null || true
echo "[qa/setup-chrome.sh] Chrome did not open DevTools on port $PORT within 3s" >&2
exit 1
