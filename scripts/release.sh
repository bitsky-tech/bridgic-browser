#!/bin/bash
# release.sh — Automated release script for bridgic-browser
#
# Usage:
#   ./scripts/release.sh <version> <repo> [--username <user>] [--password <pass>]
#
# Examples:
#   ./scripts/release.sh 0.0.1 testpypi
#   ./scripts/release.sh 0.0.1 pypi --username __token__ --password pypi-xxx
#   UV_PUBLISH_USERNAME=__token__ UV_PUBLISH_PASSWORD=pypi-xxx ./scripts/release.sh 0.0.1 pypi
#
# The script will:
#   1. Update version in pyproject.toml
#   2. Run unit tests
#   3. Build the package
#   4. Publish to the target repository

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────────
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
CYAN="\033[36m"
RESET="\033[0m"

info()  { echo -e "${GREEN}==> $*${RESET}"; }
warn()  { echo -e "${YELLOW}==> $*${RESET}"; }
error() { echo -e "${RED}==> $*${RESET}" >&2; }

# ── Parse arguments ──────────────────────────────────────────────────────
VERSION=""
REPO=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --username) UV_PUBLISH_USERNAME="$2"; shift 2 ;;
        --password) UV_PUBLISH_PASSWORD="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 <version> <repo> [--username <user>] [--password <pass>]"
            echo ""
            echo "Arguments:"
            echo "  version    Version to release (e.g. 0.0.1, 0.1.0a1)"
            echo "  repo       Target repository: btsk | testpypi | pypi"
            echo ""
            echo "Options:"
            echo "  --username  Publish username (or set UV_PUBLISH_USERNAME)"
            echo "  --password  Publish password/token (or set UV_PUBLISH_PASSWORD)"
            echo "  -h, --help  Show this help"
            exit 0
            ;;
        *)
            if [[ -z "$VERSION" ]]; then
                VERSION="$1"
            elif [[ -z "$REPO" ]]; then
                REPO="$1"
            else
                error "Unexpected argument: $1"
                exit 1
            fi
            shift
            ;;
    esac
done

if [[ -z "$VERSION" || -z "$REPO" ]]; then
    error "Usage: $0 <version> <repo> [--username <user>] [--password <pass>]"
    exit 1
fi

# ── Resolve project root ─────────────────────────────────────────────────
ROOT_DIR="$(git rev-parse --show-toplevel)"
cd "$ROOT_DIR"

PACKAGE_NAME="bridgic-browser"

# ── Step 1: Version check ────────────────────────────────────────────────
info "Checking version compatibility: ${CYAN}${VERSION}${GREEN} → ${YELLOW}${REPO}${RESET}"
uv run python scripts/version_check.py --version "$VERSION" --repo "$REPO" --package "$PACKAGE_NAME"

# ── Step 2: Update version in pyproject.toml ─────────────────────────────
CURRENT_VERSION=$(uv run python -c "
import re, pathlib
m = re.search(r'version\s*=\s*\"([^\"]+)\"', pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))
print(m.group(1))
")

if [[ "$CURRENT_VERSION" != "$VERSION" ]]; then
    info "Updating version: ${CYAN}${CURRENT_VERSION}${GREEN} → ${CYAN}${VERSION}${RESET}"
    # Use Python for cross-platform safe replacement (version passed via env var, not shell expansion)
    RELEASE_VERSION="$VERSION" uv run python -c "
import os, pathlib, re
v = os.environ['RELEASE_VERSION']
p = pathlib.Path('pyproject.toml')
text = p.read_text(encoding='utf-8')
text = re.sub(r'(version\s*=\s*\")([^\"]+)(\")', r'\g<1>' + v + r'\3', text, count=1)
p.write_text(text, encoding='utf-8')
"
else
    info "Version already set to ${CYAN}${VERSION}${RESET}"
fi

# ── Step 3: Run tests ────────────────────────────────────────────────────
info "Running unit tests..."
if ! uv run pytest tests/ -m "not integration" -q; then
    error "Tests failed. Aborting release."
    exit 1
fi
info "All tests passed."

# ── Step 4: Build ─────────────────────────────────────────────────────────
info "Building package..."
rm -rf dist/*
uv build --out-dir dist
echo ""
ls -la dist/

# ── Step 5: Credentials ──────────────────────────────────────────────────
if [[ -z "${UV_PUBLISH_USERNAME:-}" ]]; then
    read -p "Username: " UV_PUBLISH_USERNAME
fi
if [[ -z "${UV_PUBLISH_PASSWORD:-}" ]]; then
    read -sp "Password: " UV_PUBLISH_PASSWORD
    echo ""
fi
export UV_PUBLISH_USERNAME UV_PUBLISH_PASSWORD

# ── Step 6: Publish ──────────────────────────────────────────────────────
info "Publishing ${CYAN}${PACKAGE_NAME}==${VERSION}${GREEN} to ${YELLOW}${REPO}${RESET} ..."

case "$REPO" in
    btsk)
        uv publish dist/* --index btsk-repo --config-file "$ROOT_DIR/uv.toml"
        ;;
    testpypi)
        uv publish dist/* --index test-pypi --config-file "$ROOT_DIR/uv.toml"
        ;;
    pypi)
        uv publish dist/* --config-file "$ROOT_DIR/uv.toml"
        ;;
    *)
        error "Unknown repo: $REPO (expected: btsk | testpypi | pypi)"
        exit 1
        ;;
esac

# ── Done ──────────────────────────────────────────────────────────────────
echo ""
info "Published ${CYAN}${PACKAGE_NAME}==${VERSION}${GREEN} to ${YELLOW}${REPO}${GREEN} successfully!"

if [[ "$REPO" == "testpypi" ]]; then
    echo ""
    warn "Verify with:"
    echo "  pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ ${PACKAGE_NAME}==${VERSION}"
    echo ""
    warn "Then publish to pypi:"
    echo "  ./scripts/release.sh ${VERSION} pypi"
elif [[ "$REPO" == "pypi" ]]; then
    echo ""
    warn "Next steps:"
    echo "  git tag -a v${VERSION} -m \"Release v${VERSION}\""
    echo "  git push origin v${VERSION}"
fi
