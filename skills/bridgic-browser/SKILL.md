---
name: bridgic-browser
description: |
  Use when tasks involve browser automation with bridgic-browser via terminal CLI (`bridgic-browser ...`) or Python SDK (`from bridgic.browser.session import Browser`, `from bridgic.browser.tools import BrowserToolSetBuilder`). Trigger for navigation, scraping, form filling, accessibility snapshot refs, e2e checks, stealth browsing, CLI-SDK mapping/migration, and generating SDK code from CLI action steps.
---

## Dependencies

A bridgic-browser project requires the following packages:

| Package | Description |
|---------|-------------|
| `bridgic-browser` | Browser automation CLI + Python SDK (installing one installs both) |

Additionally, browser binaries must be installed once: `uv run playwright install chromium`.

**Installation**: Run the install script to set up all dependencies:

```bash
bash "skills/bridgic-browser/scripts/install-deps.sh" "$PWD"
```

The script checks uv availability, initializes a uv project if needed, installs missing packages, and ensures Playwright chromium is available.

## Strategies & Guidelines (Important!!)

When writing browser automation or web scraping code, **ALWAYS follow this "explore first, then coding" strategy**:
- First, use the `bridgic-browser` CLI tools to explore the page structure. It is recommended to use headed mode with the command `bridgic-browser open --headed <url>` during exploration.
- Then, use the `bridgic-browser` Python SDK to write the code.

Notes:
- Whenever invoking the `bridgic-browser` CLI, you must call it using `uv run`.
- If the user clearly specifies exact steps that must be followed, try to perform the exploration according to those steps. If loops or branches appear during exploration, decide the best exploration path autonomously.
- If you think you may need to return to the original page after clicking into a new page, try opening the new page in a new browser tab instead of using a “click then go back” approach. This is especially important when the original page already has interaction state (such as filled forms or applied filters); otherwise, that state may be lost after navigating back. Be sure to close the new tab promptly after finishing the related actions.
- If exploration involves repeatedly clicking items in a list, you do not need to traverse every item (especially when the list is large).
- If login, verification, or authorization is required during exploration, pause and ask the user to complete it manually, unless the user explicitly provides instructions in the task.
- To avoid operating on websites too frequently, maintain human-like access intervals during both exploration and coding. You may simulate random wait times to reduce the risk of being blocked. Note: the `bridgic-browser wait` command parameter is in **seconds**, not milliseconds; for example, `bridgic-browser wait 2` or `bridgic-browser wait 3.2`.
- After finishing exploration and code writing, automatically run testing/validation.

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
- SDK keeps state in the Python process/context. By default, browser profile (cookies, session) is persisted to `~/.bridgic/bridgic-browser/user_data/`; pass `clear_user_data=True` to `Browser()` for an ephemeral session.
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
