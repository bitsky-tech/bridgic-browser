# Release Guide

This document describes the release process for bridgic-browser.

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/) (SemVer) and [PEP 440](https://peps.python.org/pep-0440/).

### Version Format

```
MAJOR.MINOR.PATCH[.devN | aN | bN | rcN]
```

- **MAJOR**: Incompatible API changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)
- **devN**: Development release (e.g., `0.1.0.dev1`)
- **aN**: Alpha release (e.g., `0.1.0a1`)
- **bN**: Beta release (e.g., `0.1.0b1`)
- **rcN**: Release candidate (e.g., `0.1.0rc1`)

### Version → Repository Mapping

| Version Type | Repository | Example |
|--------------|------------|---------|
| Development (`*.dev*`) | btsk (private) | `0.1.0.dev1` |
| Alpha/Beta/RC | testpypi or pypi | `0.1.0a1`, `0.1.0b1` |
| Stable (`X.Y.Z`) | pypi | `0.1.0`, `1.0.0` |

## Release Process

### Prerequisites

1. All tests pass
2. CHANGELOG.md is updated
3. Version in `pyproject.toml` is correct
4. You have publishing credentials configured

### Step 1: Update Version

Edit `pyproject.toml`:

```toml
[project]
version = "X.Y.Z"  # Update this
```

### Step 2: Update CHANGELOG

Add release notes to `CHANGELOG.md`:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New feature description

### Changed
- Changed behavior description

### Fixed
- Bug fix description
```

### Step 3: Run Tests

```bash
# Run all tests
make test

# Or quick test during development
make test-quick
```

### Step 4: Build Package

```bash
make build
```

Verify the build artifacts in `dist/`:
- `bridgic_browser-X.Y.Z.tar.gz` (source distribution)
- `bridgic_browser-X.Y.Z-py3-none-any.whl` (wheel)

### Step 5: Publish

#### To Test PyPI (recommended for pre-releases)

```bash
make publish repo=testpypi
```

Verify installation:
```bash
pip install -i https://test.pypi.org/simple/ bridgic-browser==X.Y.Z
```

#### To PyPI (production)

```bash
make publish repo=pypi
```

### Step 6: Create Git Tag

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

### Step 7: Create GitHub Release

1. Go to GitHub Releases page
2. Click "Create a new release"
3. Select the tag `vX.Y.Z`
4. Title: `vX.Y.Z`
5. Copy release notes from CHANGELOG.md
6. Publish release

## Release Checklist

Before releasing, verify:

- [ ] All tests pass (`make test`)
- [ ] No TODO/FIXME in production code
- [ ] Version updated in `pyproject.toml`
- [ ] CHANGELOG.md updated with release notes
- [ ] README.md is up to date
- [ ] No debug/logging code that writes files
- [ ] Dependencies are up to date and pinned appropriately

## Credentials Setup

### Environment Variables

Set these environment variables for publishing:

```bash
# For PyPI
export UV_PUBLISH_USERNAME=__token__
export UV_PUBLISH_PASSWORD=pypi-xxxxxxxxxxxxx

# For Test PyPI
export UV_PUBLISH_USERNAME=__token__
export UV_PUBLISH_PASSWORD=pypi-xxxxxxxxxxxxx
```

### Using API Tokens (Recommended)

1. Go to PyPI → Account Settings → API tokens
2. Create a token with "Entire account" or project-specific scope
3. Use `__token__` as username and the token as password

### Interactive Mode

If environment variables are not set, you'll be prompted:

```bash
make publish repo=pypi
# Enter username: __token__
# Enter password: pypi-xxxxxxxxxxxxx
```

## Troubleshooting

### Version Already Exists

```
HTTPError: 400 Bad Request - File already exists
```

Solution: Increment the version number.

### Authentication Failed

```
HTTPError: 403 Forbidden - Invalid or non-existent authentication
```

Solution: Check your API token and credentials.

### Package Not Found After Publishing

PyPI indexing can take a few minutes. Wait and try again.

## Automated Releases (CI/CD)

For automated releases via GitHub Actions, see `.github/workflows/publish.yml`.

The workflow is triggered by:
- Pushing a version tag (e.g., `v0.1.0`)
- Manual workflow dispatch

## Rollback

If a release has issues:

1. **Yank the release** (PyPI allows yanking, not deletion):
   ```bash
   # Via PyPI web interface or API
   ```

2. **Release a patch version** with the fix:
   ```bash
   # Update version to X.Y.Z+1
   # Fix the issue
   # Release new version
   ```

## Contact

For release-related questions, contact the maintainers or open an issue.
