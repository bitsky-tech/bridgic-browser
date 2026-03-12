# W3C + Playwright Priority Reference

Purpose: standards baseline for the LLM tool layer and CLI-driven browser control in Bridgic.

Audience: contributors working on snapshot generation, ref-to-locator reconstruction, and action tools.

Last reviewed: 2026-03-09

## Priority-Ordered Rules

### P0 (Must) - Accessibility tree inclusion/exclusion must be respected

- Use accessibility-tree semantics as the source of truth for what the LLM can "see".
- Apply Core-AAM include/exclude rules consistently, especially hidden/excluded cases.
- Treat `role="none"` / `role="presentation"` with conflict-resolution rules (focusable/global ARIA cases).

Why this matters here:

- Snapshot filtering errors create false positives/false negatives for refs.
- If iframe/container lines are dropped aggressively, frame-local indexing can drift and break later locator reconstruction.

## P0 (Must) - Accessible name computation must stay aligned with ACCNAME/HTML-AAM

- Prefer host-language naming signals where applicable (associated labels, `alt`, input value for button-like inputs, title, placeholder/aria-placeholder where applicable).
- Do not treat mutable user value as the stable accessible name for text inputs.
- Use name normalization (whitespace collapse) consistently between snapshot parsing and locator matching.

Why this matters here:

- If snapshot names and runtime names diverge, pre-filter and re-location can silently miss valid elements.

## P0 (Must) - Locator strategy should follow Playwright guidance

- Prefer semantic locators (`getByRole(..., { name })`) when available.
- Use `getByText` fallback only when role semantics are weak or pseudo-role based.
- Respect locator strictness: ambiguous matches should not silently degrade into unrelated targets.

Why this matters here:

- Silent `first`/`nth` fallbacks can execute the wrong action while returning success.

## P1 (Should) - Actionability bypasses need guardrails

- Playwright actionability checks exist to prevent non-user-like interactions.
- `dispatchEvent`/force paths should be constrained to known edge cases (shadow DOM, proxy controls) and not become the default path.

Why this matters here:

- Overusing bypass paths can hide real page issues and produce unstable automation behavior.

## P1 (Should) - Iframe handling must preserve frame scope integrity

- Nested iframes require stable frame-path reconstruction (`frame_locator(...).nth(...)` chaining).
- When filtering viewport content, preserve enough iframe structure to keep local frame indices stable.

Why this matters here:

- The project can support nested iframes functionally, but pre-filter/index drift can still break specific viewport-mode cases.

## Review Checklist (for this project)

- Snapshot pipeline:
  - Are refs extracted for both main-frame and Playwright internal frame-style ids?
  - Can unresolved refs in viewport mode cause accidental data loss?
  - Is iframe line preservation sufficient for stable frame-path indexing?
- Locator pipeline:
  - Is role+name preferred over text fallback where valid?
  - Does ambiguous locator handling return deterministic disambiguation or explicit error?
- Action tools:
  - Are overlay/shadow fallbacks constrained and observable?
  - Are checkbox/radio native vs custom widgets handled separately?
- CLI/daemon:
  - Are failures machine-readable (`success`, `error_code`) for LLM decision loops?

## Source Links

- W3C Core-AAM 1.2: https://www.w3.org/TR/core-aam-1.2/
- W3C Accessible Name and Description (ACCNAME) 1.2: https://www.w3.org/TR/accname-1.2/
- WAI-ARIA 1.2: https://www.w3.org/TR/wai-aria-1.2/
- HTML Accessibility API Mappings (HTML-AAM) 1.0: https://www.w3.org/TR/html-aam-1.0/
- Playwright locators: https://playwright.dev/docs/locators
- Playwright locator strictness: https://playwright.dev/docs/locators#strictness
- Playwright actionability: https://playwright.dev/docs/actionability
