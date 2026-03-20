Contributing to Bridgic Browser
================================

We love your input! We want to make contributing to Bridgic Browser as easy and transparent as possible.

## Quick Start Guide

### Prerequisites

We use **uv** as the package and project manager. Before contributing, make sure you have uv installed.

**On macOS and Linux:**
```shell
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**On Windows:**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

For more install options, see [uv's official documentation](https://docs.astral.sh/uv/getting-started/installation/).

### Environment Setup

1. **Clone the repository:**
   ```shell
   git clone https://github.com/bitsky-tech/bridgic-browser.git
   cd bridgic-browser
   ```

2. **Initialize the development environment:**
   ```shell
   make init-dev
   ```
   This will:
   - Configure git hooks for code quality
   - Create a Python 3.10 virtual environment
   - Install all development dependencies
   - Install Playwright browsers (Chromium)

3. **Activate the virtual environment:**
   ```shell
   source .venv/bin/activate
   ```

### Running Tests

Run the test suite to verify your setup:

```shell
# Quick test (recommended during development)
make test-quick

# Full test with wheel rebuild
make test

# Integration tests only (requires browser)
make test-integration
```

**Difference between the three:**

| Target | What it does | When to use |
|--------|----------------|--------------|
| **test-quick** | Runs tests via `uv run pytest -m "not integration"` (no build). Uses the project in editable mode. **Fastest.** | Day-to-day development; quick feedback after code changes. |
| **test** | Syncs deps → builds wheel → installs wheel → runs pytest with PYTHONPATH from site-packages. Simulates the package **as installed** (e.g. namespace + deps from PyPI). | Before committing/PR; CI; verify “real install” behavior. |
| **test-integration** | Runs only tests marked `@pytest.mark.integration` (e.g. tests that need a real browser). | When you want to run only integration/E2E tests. |

### Development Workflow

1. **Create a feature branch:**
   ```shell
   git checkout -b feature/your-feature-name
   ```
   
   Branch naming convention:
   - `feature/` - New features
   - `bugfix/` - Bug fixes
   - `refactor/` - Code refactoring
   - `release/` - Release preparation (version/release docs/changelog)
   - `docs/` - Documentation updates
   
   Protected branches:
   - `main` and `dev` do not allow direct push from local branches
   - Use Pull Requests to merge into `main`/`dev`

2. **Make your changes and test:**
   ```shell
   # Run tests
   make test-quick
   
   ```

3. **Commit your changes:**
   ```shell
   git add .
   git commit -m "feat: description of your changes"
   ```

4. **Push and create a Pull Request:**
   ```shell
   git push origin feature/your-feature-name
   ```

## Building the Package

```shell
# Build the package
make build

# The built files will be in dist/
ls dist/
```

## Project Structure

```
bridgic-browser/
├── bridgic/
│   ├── __init__.py          # Namespace package
│   └── browser/
│       ├── __init__.py      # Package initialization
│       ├── session/         # Browser session management
│       │   ├── _browser.py  # Main Browser class
│       │   ├── _snapshot.py # Page snapshot generation
│       │   ├── _stealth.py  # Stealth mode configuration
│       │   └── _download.py # Download management
│       ├── tools/           # Browser automation tools
│       │   ├── __init__.py
│       │   ├── _browser_tool_set_builder.py
│       │   └── _browser_tool_spec.py
│       └── utils/           # Utility functions
├── tests/                   # Test files
├── docs/                    # Documentation
├── scripts/                 # Build and publish scripts
├── pyproject.toml           # Project configuration
├── Makefile                 # Development commands
└── README.md                # Project documentation
```

## Code Style

- We follow PEP 8 style guidelines
- Use type hints for function parameters and return values
- Write docstrings for public functions and classes (NumPy style)
- Keep functions focused and small

## Pull Request Process

1. Ensure all tests pass (`make test-quick`)
2. Update documentation if needed
3. Add tests for new functionality
4. Request review from maintainers

## Questions?

Feel free to open an issue for any questions or discussions!
