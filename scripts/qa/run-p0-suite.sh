#!/usr/bin/env bash
# scripts/qa/run-p0-suite.sh
#
# Thin orchestrator reference for the P0 execution plan
# (see ~/.claude/plans/task-md-cached-dragon.md for authoritative sequence).
#
# This is intentionally a reference rather than a full automation:
# several P0 cases (§2 handheld attach, §3 video visual verify, §7 real
# page tools) require eyeballing intermediate state. Prefer running each
# phase interactively and capturing artifacts via collect-artifacts.sh.
#
# Usage:
#   source scripts/qa/env.sh
#   bash scripts/qa/run-p0-suite.sh phase1   # smoke only
#   bash scripts/qa/run-p0-suite.sh phase3   # collect artifacts
#
# Exit code is OR of sub-phase exit codes.

set -uo pipefail
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$THIS_DIR/env.sh"

PHASE="${1:-help}"
RC=0

case "$PHASE" in
  phase0)
    qa_log "phase0: reset + sanity boot"
    full_reset
    t0=$(date +%s)
    bridgic-browser open https://example.com || RC=$?
    bridgic-browser snapshot -i >/dev/null || RC=$?
    bridgic-browser close || RC=$?
    t1=$(date +%s)
    qa_log "phase0: REG-01 elapsed $((t1-t0))s (budget: 15s)"
    ;;
  phase1)
    qa_log "phase1: REG-02 make test-quick"
    (cd "$(dirname "$THIS_DIR")/.." && make test-quick) || RC=$?
    qa_log "phase1: REG-03 make test-integration"
    (cd "$(dirname "$THIS_DIR")/.." && make test-integration) || RC=$?
    ;;
  phase3)
    bash "$THIS_DIR/collect-artifacts.sh" "${2:-final}" || RC=$?
    ;;
  help|--help|-h|*)
    cat <<'EOF'
Usage: run-p0-suite.sh <phase>

  phase0   Reset + REG-01 sanity boot
  phase1   REG-02 (test-quick) + REG-03 (test-integration)
  phase3   Invoke collect-artifacts.sh

Phase 2 is interactive; see the plan file for the per-section sequence.
EOF
    ;;
esac

qa_log "$PHASE: done rc=$RC"
exit $RC
