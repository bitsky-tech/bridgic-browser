#!/usr/bin/env bash
# scripts/qa/collect-artifacts.sh [TAG]
#
# Snapshots everything useful into $QA_DIR, then tars it up.
#
# Captures:
#   - daemon.log (already being written there)
#   - close-report.json (if exists)
#   - any *.webm / *.zip trace in ~/.bridgic/bridgic-browser/logs
#   - ps snapshot of chrome / bridgic
#   - version banners
#
# Usage:
#   source scripts/qa/env.sh
#   bash scripts/qa/collect-artifacts.sh phase1
# Produces:
#   $QA_DIR/artifacts-<TAG>/ + $QA_DIR/bridgic-qa-<TS>-<TAG>.tar.gz

set -uo pipefail
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$THIS_DIR/env.sh"

TAG="${1:-run}"
DEST="$QA_DIR/artifacts-$TAG"
mkdir -p "$DEST"

qa_log "collect-artifacts: tag=$TAG -> $DEST"

# 1. Daemon log (copy, don't move; daemon may still be writing)
if [[ -f "$BRIDGIC_DAEMON_LOG_FILE" ]]; then
  cp "$BRIDGIC_DAEMON_LOG_FILE" "$DEST/daemon.log" 2>/dev/null || true
fi

# 2. Everything under ~/.bridgic/bridgic-browser/run and tmp and logs
for d in run tmp logs; do
  src="$HOME/.bridgic/bridgic-browser/$d"
  if [[ -d "$src" ]]; then
    mkdir -p "$DEST/bridgic-$d"
    (cd "$src" && find . -type f -size -100M -print0 2>/dev/null | \
      xargs -0 -I{} cp --parents {} "$DEST/bridgic-$d/" 2>/dev/null) || true
    # macOS cp doesn't have --parents; do a plain recursive copy as fallback.
    cp -R "$src" "$DEST/bridgic-$d.full" 2>/dev/null || true
  fi
done

# 3. Process snapshot
{
  echo "### $(date) ###"
  echo "## ps (bridgic + chrome) ##"
  ps -ef | grep -iE "bridgic|remote-debugging|chromium|chrome" | grep -v grep
  echo
  echo "## netstat (9222/9223) ##"
  lsof -nP -iTCP:9222 -iTCP:9223 2>/dev/null
  echo
  echo "## ls run/ ##"
  ls -la "$HOME/.bridgic/bridgic-browser/run" 2>/dev/null
} > "$DEST/process-snapshot.txt" 2>&1

# 4. Versions
{
  echo "os: $(uname -a)"
  echo "python: $(python3 --version 2>&1)"
  echo "bridgic-browser: $(bridgic-browser --version 2>&1)"
  echo "chrome: $("$QA_CHROME_BIN" --version 2>&1)"
  echo "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
  echo "playwright: $(uv run playwright --version 2>&1 || true)"
} > "$DEST/versions.txt"

# 5. Tar it up — exclude webm by default (they're big), keep .webm summary
TAR="$QA_DIR/bridgic-qa-$QA_TS-$TAG.tar.gz"
(cd "$QA_DIR" && tar --exclude='*.webm' -czf "$TAR" "artifacts-$TAG") 2>/dev/null || \
  (cd "$QA_DIR" && tar -czf "$TAR" "artifacts-$TAG")

qa_log "collect-artifacts: wrote $TAR ($(du -h "$TAR" 2>/dev/null | awk '{print $1}'))"
ls -la "$TAR"
