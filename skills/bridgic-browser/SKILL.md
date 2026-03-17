---
name: bridgic-browser
description: |
  Use when tasks involve browser automation with bridgic-browser via terminal CLI (`bridgic-browser ...`) or Python SDK (`from bridgic.browser.session import Browser`, `from bridgic.browser.tools import BrowserToolSetBuilder`). Trigger for navigation, scraping, form filling, accessibility snapshot refs (`[ref=eN]`), e2e checks, stealth browsing, CLI-to-SDK migration, and generating SDK code from CLI action steps.
---

# bridgic-browser

Use progressive disclosure. Do not load every reference file by default.

## Python Environment Requirements

- **Python**: >= 3.10
- **Install package**: `pip install bridgic-browser`
- **Install browser binaries** (one-time): `playwright install chromium`
- The CLI tool (`bridgic-browser`) is installed automatically with the package.
- The SDK (`from bridgic.browser.session import Browser`) is available in the same package.

## Reference Files

Three reference files cover all use cases. Load only the one(s) relevant to the task:

| File | When to load |
|------|-------------|
| [references/cli-guide.md](references/cli-guide.md) | User wants to **directly control the browser via terminal** (`bridgic-browser open ...`, `bridgic-browser snapshot`, etc.) |
| [references/sdk-guide.md](references/sdk-guide.md) | User wants **Python automation code** (`Browser`, `BrowserToolSetBuilder`, agent integration) |
| [references/cli-sdk-api-mapping.md](references/cli-sdk-api-mapping.md) | User needs to understand **CLI ↔ SDK correspondence**: migration, comparison, or **generating SDK code from CLI action steps** |

## Reference Routing

- **Direct terminal control** → read `cli-guide.md`.
- **Python code / agent integration** → read `sdk-guide.md`.
- **CLI and SDK relationship** (migration, "what's the SDK equivalent of this command?", "convert these CLI steps to Python") → read `cli-sdk-api-mapping.md`.
- **CLI exploration + SDK code output** (user is operating via CLI but needs the final deliverable as runnable Python) → read `cli-sdk-api-mapping.md` first to translate steps, then `sdk-guide.md` for final code shape.
- For mixed tasks, load only the needed sections from each relevant reference.

## Interface Decision Rules

1. Output requested as shell commands → use CLI guide first.
2. Output requested as runnable Python (`async`, `Browser`, tool builder) → use SDK guide first.
3. Input is CLI actions but output must be SDK API code → use mapping guide first, then SDK guide for final code shape.
4. If intent is ambiguous, infer from requested artifact (`.sh` / terminal session vs `.py` script).

## Shared Invariants (CLI + SDK)

- Ref-based actions depend on the latest snapshot.
- After navigation or major DOM updates, refs can become stale; refresh snapshot before ref actions.
- CLI keeps state in a daemon session across invocations.
- SDK keeps state in the Python process/context unless a persistent `user_data_dir` is configured.
- Use exact command/method names from references; do not invent aliases.

## Bridge Workflow: CLI Actions -> SDK Code

1. Parse CLI steps in order.
2. Map each step using `references/cli-sdk-api-mapping.md`.
3. Preserve behavior details: snapshot flags, wait modes, ref normalization, and capture options.
4. Emit runnable async Python code with explicit browser lifecycle (`async with Browser(...)` preferred).
5. Call out any behavior differences that cannot be represented 1:1.

## Minimal Quality Checklist

- CLI request: return valid CLI commands/options only.
- SDK request: return executable async Python with correct imports.
- Bridge request: include mapping rationale plus final SDK code.
