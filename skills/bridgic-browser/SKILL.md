---
name: bridgic-browser
description: |
  Use when tasks involve browser automation with bridgic-browser via terminal CLI (`bridgic-browser ...`) or Python SDK (`from bridgic.browser.session import Browser`, `from bridgic.browser.tools import BrowserToolSetBuilder`). Trigger for navigation, scraping, form filling, accessibility snapshot refs, e2e checks, stealth browsing, CLI-SDK mapping/migration, and generating SDK code from CLI action steps.
---

# bridgic-browser

Use progressive disclosure. Do not load every reference file by default.

## Python Environment Requirements

- **Python**: >= 3.10
- **Install package**: `pip install bridgic-browser`
- **Install browser binaries** (one-time): `playwright install chromium`
- **Dev mode** (repo): `make init-dev`

The CLI tool (`bridgic-browser`) and the Python SDK (`from bridgic.browser.session import Browser`) come from the **same package** — installing one installs both.

## Reference Files

Reference files cover all use cases. Load only the one(s) relevant to the task:

| Scenario | Interface | Load |
|---|---|---|
| Directly control browser from terminal | CLI | [cli-guide.md](references/cli-guide.md) |
| Write Python code about browser automation | Python | [sdk-guide.md](references/sdk-guide.md) |
| Write shell script about browser automation | CLI | [cli-guide.md](references/cli-guide.md) |
| Explore via CLI, then generate Python code | CLI → Python | [cli-sdk-api-mapping.md](references/cli-sdk-api-mapping.md) + [sdk-guide.md](references/sdk-guide.md) |
| Migrate / compare / explain CLI ↔ SDK | Both | [cli-sdk-api-mapping.md](references/cli-sdk-api-mapping.md) |
| Configure env vars or login state persistence | Either | [env-vars.md](references/env-vars.md) |

## Interface Decision Rules

1. Output requested as shell commands or scripts → use CLI guide first (`references/cli-guide.md`).
2. Output requested as runnable Python code (`async`, `Browser`, tool builder) → use SDK guide first (`references/sdk-guide.md`).
3. Input is CLI outputs or actions but output needs to be Python code → use mapping guide first (`references/cli-sdk-api-mapping.md`), then SDK guide for final code generation (``references/sdk-guide.md``).
4. If intent is ambiguous, infer from requested artifacts (`.sh` / terminal session vs `.py` script).

## Common Usage (CLI + SDK)

- Ref-based actions depend on the latest snapshot.
- After navigation or major DOM updates, refs can become stale; refresh snapshot before ref actions.
- CLI keeps state in a daemon session across invocations.
- SDK keeps state in the Python process/context unless a persistent `user_data_dir` is configured.
- Use exact command/method names from references; do not invent aliases.

## Bridge Workflow: CLI Actions -> Python Code

1. Parse CLI steps in order.
2. Map each step using `references/cli-sdk-api-mapping.md`.
3. Preserve behavior details: refs, options, arguments, configuration, etc.
4. Emit runnable async Python code with explicit browser lifecycle (`async with Browser(...)` preferred).
5. Call out any behavior differences that cannot be represented 1:1.

## Minimal Quality Checklist

- CLI request: return valid CLI commands/options only.
- SDK request: return executable async Python with correct imports.
- Bridge request: include mapping rationale plus final SDK code.
