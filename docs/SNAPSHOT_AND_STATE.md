# Snapshot and Page State for LLM

This document describes how page snapshots and the LLM-facing page state work in Bridgic Browser: options, data structures, and the typical flow from snapshot to element interaction.

For standards-level constraints and priority rules (W3C accessibility tree + Playwright locator/actionability), see [W3C_PLAYWRIGHT_PRIORITY_REFERENCE.md](W3C_PLAYWRIGHT_PRIORITY_REFERENCE.md).

## Overview

- **Snapshot** (programmatic): `Browser.get_snapshot()` returns an `EnhancedSnapshot` with a tree string and a refs map. Used when you need structured access to both the tree and ref metadata.
- **Page state for LLM** (tool): `get_llm_repr(browser, ...)` returns a single string (the same tree, possibly truncated with pagination). Use this from tools/agents so the LLM can read the page and choose refs to interact with.
- **Element by ref**: `Browser.get_element_by_ref(ref)` returns a Playwright `Locator` for a given ref, using the **last** snapshot’s refs. So the flow is: get snapshot or get_llm_repr → parse refs from the tree → get_element_by_ref(ref) → click/fill/etc.

## SnapshotOptions

Options for how the snapshot is generated (used by both `get_snapshot` and `get_llm_repr`).

| Option         | Type | Default | Description |
|----------------|------|---------|-------------|
| `interactive`  | bool | False   | If True, only include interactive elements (buttons, links, inputs, checkboxes, elements with cursor:pointer, etc.) and output a flattened list (no indentation). Best for “what can I click/type?”. |
| `full_page`    | bool | True    | If True (default), include all elements regardless of viewport position. If False, only include elements within the viewport. |

Example:

```python
from bridgic.browser.session import Browser, SnapshotOptions

# Full page, all elements (default)
snapshot = await browser.get_snapshot()

# Interactive elements only, flattened
snapshot = await browser.get_snapshot(interactive=True)

# Viewport-only
snapshot = await browser.get_snapshot(full_page=False)
```

## EnhancedSnapshot

Returned by `Browser.get_snapshot()`. Exposed from `bridgic.browser.session`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `tree`    | str  | Accessibility tree as a string. Lines look like `- role "name" [ref=e1]`. |
| `refs`    | Dict[str, RefData] | Map from ref id (e.g. `"e1"`) to `RefData` used to resolve the element. |

## RefData

Stored in `EnhancedSnapshot.refs`. Used internally to build a Playwright locator from a ref.

| Field           | Type   | Description |
|-----------------|--------|-------------|
| `selector`      | str    | CSS selector (or other selector) for the element. |
| `role`          | str    | ARIA role (e.g. button, textbox). |
| `name`          | str, optional | Accessible name. |
| `nth`           | int, optional | Occurrence index for disambiguation. |
| `text_content`  | str, optional | Text content snippet. |
| `parent_ref`    | str, optional | Ref of the nearest ancestor element that has a ref. |
| `frame_path`    | List[int], optional | Per-level local iframe indices for nested iframes. `None` = main frame; `[0]` = 1st top-level iframe; `[0, 0]` = 1st iframe inside the 1st iframe. Used to build the `frame_locator(...).nth(n)` chain in `get_element_by_ref`. |

You normally do not need to use `RefData` directly; `get_element_by_ref(ref)` uses it under the hood.

## SnapshotGenerator

Low-level snapshot generator that works on a raw Playwright `Page`. `Browser` uses it internally.

```python
from bridgic.browser.session import SnapshotGenerator, SnapshotOptions

generator = SnapshotGenerator()

# With a Playwright page
snapshot = await generator.get_enhanced_snapshot_async(page, SnapshotOptions(interactive=False, full_page=False))

# Get a locator from a ref (requires the same page and the snapshot’s refs)
locator = generator.get_locator_from_ref_async(page, "e2", snapshot.refs)
if locator:
    await locator.click()
```

When using `Browser`, you typically use `browser.get_snapshot()` and `browser.get_element_by_ref(ref)` instead, which delegate to the same generator and keep the “last snapshot” in sync.

## get_llm_repr

Tool function used to supply the page state to an LLM. It calls `browser.get_snapshot(interactive=..., full_page=...)` and returns the tree string, with optional truncation and pagination.

- **Signature**: `get_llm_repr(browser, start_from_char=0, interactive=False, full_page=True) -> str`
- **Returns**: The accessibility tree string. May be truncated at ~30,000 characters; if so, a `[notice]` at the end explains how to continue (see below).

### Parameters

| Parameter         | Type | Default | Description |
|-------------------|------|---------|-------------|
| `start_from_char` | int  | 0       | Character offset for pagination. Use the `next_start_char` from the truncation notice to get the next segment. |
| `interactive`     | bool | False   | Same as `SnapshotOptions.interactive`: only interactive elements, flattened. |
| `full_page`       | bool | True    | Same as `SnapshotOptions.full_page`: include all elements regardless of viewport position. |

### Truncation and pagination

When the full tree is longer than the limit (default 30,000 characters), the returned string is cut at a natural break (e.g. paragraph or sentence) and a notice is appended, for example:

```
[notice] Current page state text is too long, returned portion starting from character 0 (this segment length 30000 / total length 45000 characters). To continue getting subsequent content, use start_from_char=30000 to call get_llm_repr again.
```

Use the given `start_from_char` (e.g. `30000`) in the next call to get the rest.

The character limit can be adjusted via the `BRIDGIC_MAX_CHARS` environment variable (default `30000`).

### Relation to get_snapshot

- `get_llm_repr` calls `browser.get_snapshot(interactive=interactive, full_page=full_page)` once per invocation.
- So the browser’s “last snapshot” (and thus `get_element_by_ref`) is updated to that snapshot. The LLM can safely use refs from the returned string with action tools.

## get_element_by_ref

- **Usage**: `locator = await browser.get_element_by_ref(ref)` (e.g. `ref="e1"`).
- **Returns**: A Playwright `Locator` or `None` if the ref is invalid or the element is not found.
- **Depends on**: The **last** snapshot. You must call `get_snapshot()` or use a tool that calls `get_llm_repr()` (which triggers a snapshot) before using refs.

Typical flow:

1. Navigate: `await browser.navigate_to(url)`.
2. Get state: `state = await get_llm_repr(browser)` (or `snapshot = await browser.get_snapshot()`).
3. LLM (or your code) reads the tree and picks a ref, e.g. `e5`.
4. Interact: `el = await browser.get_element_by_ref("e5")` then e.g. `await el.click()` or use tools like `click_element_by_ref(browser, "e5")`.

If the page changes (e.g. after navigation or dynamic update), take a new snapshot or call `get_llm_repr` again so refs stay valid.

## CLI: bridgic-browser snapshot

The `snapshot` command is the CLI equivalent of `get_llm_repr`. It shares the same parameters and delegates to the same implementation (truncation, pagination, and all).

```
bridgic-browser snapshot [OPTIONS]

Options:
  -i, --interactive          Only show clickable/editable elements.
  -f, --full-page            Include elements outside the viewport (default).
  -F, --no-full-page         Limit to viewport-only elements.
  -s, --start-from-char INT  Pagination offset (use next_start_char from the
                             truncation notice). Default: 0.
```

Examples:

```bash
bridgic-browser snapshot                  # full tree
bridgic-browser snapshot -i               # interactive elements only
bridgic-browser snapshot -F               # viewport-only
bridgic-browser snapshot -s 30000         # page 2 of a long snapshot
bridgic-browser snapshot -i -F -s 10000  # combined
```

### Environment variables

| Variable           | Default | Description |
|--------------------|---------|-------------|
| `BRIDGIC_MAX_CHARS` | `30000` | Max characters returned per `snapshot`/`get_llm_repr` call before pagination kicks in. |
