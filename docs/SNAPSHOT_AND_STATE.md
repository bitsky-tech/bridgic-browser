# Snapshot and Page State for LLM

This document describes how page snapshots and the LLM-facing page state work in Bridgic Browser: options, data structures, and the typical flow from snapshot to element interaction.

## Overview

- **Snapshot** (programmatic): `Browser.get_snapshot()` returns an `EnhancedSnapshot` with a tree string and a refs map. Used when you need structured access to both the tree and ref metadata.
- **Page state for LLM** (tool): `browser.get_snapshot_text(...)` returns a single string (the same tree; when content exceeds `limit`, full snapshot is saved to a file and only a notice with the file path is returned). Use this from tools/agents so the LLM can read the page and choose refs to interact with.
- **Element by ref**: `Browser.get_element_by_ref(ref)` returns a Playwright `Locator` for a given ref, using the **last** snapshot’s refs. So the flow is: get snapshot or get_snapshot_text → parse refs from the tree → get_element_by_ref(ref) → click/fill/etc.

## SnapshotOptions

Options for how the snapshot is generated (used by both `get_snapshot` and `get_snapshot_text`).

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
| `tree`    | str  | Accessibility tree as a string. Lines look like `- role "name" [ref=1f79fe5e]`. |
| `refs`    | Dict[str, RefData] | Map from ref id (e.g. `"1f79fe5e"`) to `RefData` used to resolve the element. |

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
locator = generator.get_locator_from_ref_async(page, "8d4b03a9", snapshot.refs)
if locator:
    await locator.click()
```

When using `Browser`, you typically use `browser.get_snapshot()` and `browser.get_element_by_ref(ref)` instead, which delegate to the same generator and keep the “last snapshot” in sync.

## get_snapshot_text

Browser method used to supply the page state to an LLM. It calls `browser.get_snapshot(interactive=..., full_page=...)` and returns the tree string. When content exceeds the limit or `file` is explicitly provided, the full snapshot is saved to a file and only a notice with the file path is returned.

- **Signature**: `await browser.get_snapshot_text(limit=10000, interactive=False, full_page=True, file=None) -> str`
- **Returns**: The accessibility tree string. When the full tree exceeds `limit` or `file` is explicitly provided, full content is saved to a file and a `[notice]` with the file path is returned instead of the snapshot content.

### Parameters

| Parameter     | Type        | Default | Description |
|---------------|-------------|---------|-------------|
| `limit`       | int         | 10000   | Maximum characters to return. Must be `>= 1`. |
| `interactive` | bool        | False   | Same as `SnapshotOptions.interactive`: only interactive elements, flattened. |
| `full_page`   | bool        | True    | Same as `SnapshotOptions.full_page`: include all elements regardless of viewport position. |
| `file`        | str or None | None    | File path to save the full snapshot. When provided, snapshot is always saved to this file and only a notice is returned. When `None`, file is only written if content exceeds `limit` (auto-generated under `~/.bridgic/bridgic-browser/snapshot/`). Raises `InvalidInputError` if the path is empty/whitespace-only, contains null bytes, or points to an existing directory. |

### Overflow behavior

When the full tree is longer than `limit`, or when `file` is explicitly provided, the full snapshot is written to a file and a notice is returned instead of the snapshot content:

```
[notice] Page snapshot (45000 characters) saved to: /Users/you/.bridgic/bridgic-browser/snapshot/snapshot-20260330-143025-a7b2.txt
```

Read the file to get the complete snapshot content.

### Relation to get_snapshot

- `get_snapshot_text` calls `browser.get_snapshot(interactive=interactive, full_page=full_page)` once per invocation.
- So the browser’s “last snapshot” (and thus `get_element_by_ref`) is updated to that snapshot. The LLM can safely use refs from the returned string with action tools.

## get_element_by_ref

- **Usage**: `locator = await browser.get_element_by_ref(ref)` (e.g. `ref="1f79fe5e"`).
- **Returns**: A Playwright `Locator` or `None` if the ref is invalid or the element is not found.
- **Depends on**: The **last** snapshot. You must call `get_snapshot()` or `get_snapshot_text()` (which triggers a snapshot) before using refs.

Typical flow:

1. Navigate: `await browser.navigate_to(url)`.
2. Get state: `state = await browser.get_snapshot_text()` (or `snapshot = await browser.get_snapshot()`).
3. LLM (or your code) reads the tree and picks a ref, e.g. `8d4b03a9`.
4. Interact: use tools like `await browser.click_element_by_ref("8d4b03a9")`.

If the page changes (e.g. after navigation or dynamic update), take a new snapshot or call `get_snapshot_text` again so refs stay valid.

## CLI: bridgic-browser snapshot

The `snapshot` command is the CLI equivalent of `browser.get_snapshot_text()`. It shares the same parameters and delegates to the same implementation.

```
bridgic-browser snapshot [OPTIONS]

Options:
  -i, --interactive   Only show clickable/editable elements.
  -f, --full-page     Include elements outside the viewport (default).
  -F, --no-full-page  Limit to viewport-only elements.
  -l, --limit INT     Max characters to return. Default: 10000.
  -s, --file PATH     File path to save full snapshot. When provided, snapshot is
                      always saved to this file. Default: auto-generated in
                      ~/.bridgic/bridgic-browser/snapshot/ (only when over limit).
```

Examples:

```bash
bridgic-browser snapshot                     # full tree
bridgic-browser snapshot -i                  # interactive elements only
bridgic-browser snapshot -F                  # viewport-only
bridgic-browser snapshot -l 5000             # custom limit
bridgic-browser snapshot -s /tmp/snap.txt    # save overflow to specific file
bridgic-browser snapshot -i -F -l 5000       # combined
```

### Environment variables

See `skills/bridgic-browser/references/env-vars.md` for environment variable details.
