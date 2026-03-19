SHELL := /bin/bash
ROOT_DIR := $(shell git rev-parse --show-toplevel)
VERSION_CHECK := $(ROOT_DIR)/scripts/version_check.py
SET_CREDENTIALS := $(ROOT_DIR)/scripts/set_publish_credentials.sh

.PHONY: init-dev test build publish clean download-extensions

package_name := bridgic-browser
repo ?= btsk

# Install Playwright browser binaries used by tests.
# Note: Playwright may use a separate `chromium_headless_shell` executable in headless mode,
# so we install both `chromium` and `chromium-headless-shell`.
.PHONY: playwright-install
playwright-install:
	@echo "\n==> Installing Playwright browsers (chromium + headless shell)..."
	@.venv/bin/python -m playwright install chromium chromium-headless-shell

# Initialize development environment
init-dev:
	@test -d .git || (echo "Not a git repo"; exit 1)
	@git config --local core.hooksPath .githooks
	@echo "\n==> Successfully installed git hooks."
	@echo "\n==> Preparing virtual environment for project."
	@if [ -d .venv ]; then echo ".venv already exists, removing..."; rm -rf .venv; fi
	@uv venv --python=python3.11 .venv && echo ".venv created."
	@echo "\n==> Installing development dependencies for the project..."
	@source .venv/bin/activate && uv sync --group dev
	@$(MAKE) playwright-install
	@echo "\n==> Development environment ready!"

# Run tests
# Uses wheel install + PYTHONPATH so bridgic namespace (browser + core + llms) is used from site-packages.
test:
	@uv sync --group dev
	@rm -f dist/bridgic_browser-*.whl dist/bridgic_browser-*.tar.gz
	@uv build --out-dir dist
	@uv pip install dist/bridgic_browser-*.whl --force-reinstall -q
	@SITE=$$(.venv/bin/python -c "import site; print(site.getsitepackages()[0])"); \
	PYTHONPATH="$$SITE" .venv/bin/pytest tests/ -v

# Run tests without wheel rebuild (faster for development)
test-quick:
	@uv run pytest tests/ -v -m "not integration"

# Run integration tests only
test-integration:
	@uv run pytest tests/ -v -m integration

# Build package
build:
	@mkdir -p dist
	@rm -rf dist/*
	@uv build --out-dir dist
	@echo "==> Built package: $(package_name)"
	@ls -la dist/

# Clean build artifacts
clean:
	@rm -rf dist/ build/ *.egg-info .pytest_cache .coverage htmlcov/
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "==> Cleaned build artifacts"

# Publish package
publish: build
	@source $(SET_CREDENTIALS) && \
	version=$$(uv run python -c "import re, pathlib; m=re.search(r'version\s*=\s*\"([^\"]+)\"', pathlib.Path('pyproject.toml').read_text(encoding='utf-8')); print(m.group(1))") && \
	uv run python $(VERSION_CHECK) --version "$$version" --repo "$(repo)" --package "$(package_name)" && \
	$(MAKE) _publish_$(repo)

_publish_btsk:
	@uv publish dist/* --index btsk-repo --config-file $(ROOT_DIR)/uv.toml

_publish_testpypi:
	@uv publish dist/* --index test-pypi --config-file $(ROOT_DIR)/uv.toml

_publish_pypi:
	@uv publish dist/* --config-file $(ROOT_DIR)/uv.toml

download-extensions:
	@echo "==> Downloading stealth extensions into bridgic/browser/extensions/ ..."
	@uv run python scripts/download_extensions.py
	@echo "==> Commit bridgic/browser/extensions/extensions.zip to include it in the package."

# Show help
help:
	@echo "Available targets:"
	@echo "  init-dev        - Initialize development environment"
	@echo "  test            - Run tests with wheel install"
	@echo "  test-quick      - Run tests quickly (no wheel rebuild)"
	@echo "  test-integration - Run integration tests only"
	@echo "  build           - Build package"
	@echo "  clean           - Clean build artifacts"
	@echo "  publish         - Publish package (repo=btsk|testpypi|pypi)"
	@echo ""
	@echo "Examples:"
	@echo "  make init-dev"
	@echo "  make test"
	@echo "  make build"
	@echo "  make publish repo=testpypi"
