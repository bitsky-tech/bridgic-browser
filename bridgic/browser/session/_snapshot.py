"""
Enhanced Accessibility Snapshot Generator for AI-driven Browser Automation.

This module generates structured accessibility tree snapshots with element references (refs)
that enable deterministic element selection for browser automation tasks. The refs allow
AI agents to reliably interact with elements without fragile CSS/XPath selectors.

Key Features:
- Generates AI-friendly accessibility tree with element refs
- Filters invisible elements and viewport-only content
- Supports interactive-only mode for action-focused tasks
- Provides locator reconstruction from refs for element interaction

Example Output:
    - heading "Example Domain" [ref=e1] [level=1]
    - paragraph: Some text content
    - button "Submit" [ref=e2]
    - textbox "Email" [ref=e3]

Usage:
    from playwright.async_api import async_playwright
    from bridgic.browser.session import SnapshotGenerator, SnapshotOptions

    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto("https://example.com")

            generator = SnapshotGenerator()

            # Default: viewport-only, filter invisible elements
            snapshot = await generator.get_enhanced_snapshot_async(page)
            print(snapshot.tree)

            # Full page snapshot
            snapshot = await generator.get_enhanced_snapshot_async(
                page, SnapshotOptions(full_page=True)
            )

            # Interactive elements only (flattened list)
            snapshot = await generator.get_enhanced_snapshot_async(
                page, SnapshotOptions(interactive=True)
            )

            # Get locator from ref for interaction
            locator = generator.get_locator_from_ref_async(page, "@e2", snapshot.refs)
            if locator:
                await locator.click()

    asyncio.run(main())
"""

import re
import math
import logging
import asyncio
from typing import Dict, Optional, Set, List, Tuple
from dataclasses import dataclass
from playwright.async_api import Page as AsyncPage, Locator as AsyncLocator

logger = logging.getLogger(__name__)

@dataclass
class RefData:
    """Data structure for element reference."""
    selector: str
    role: str
    name: Optional[str] = None
    nth: Optional[int] = None


@dataclass
class EnhancedSnapshot:
    """Enhanced snapshot with tree and refs."""
    tree: str
    refs: Dict[str, RefData]


@dataclass
class SnapshotOptions:
    """Options for snapshot generation.

    Attributes
    ----------
    interactive : bool
        If True, only include interactive elements (buttons, links, etc.)
        with flattened output (no indentation). Useful for getting a quick
        list of actionable elements on the page.
    full_page : bool
        If False (default), only include elements within the viewport.
        If True, include all elements regardless of viewport position.
    filter_invisible : bool
        If True (default), filter out CSS-hidden elements (display:none,
        visibility:hidden, opacity:0, aria-hidden="true", etc.).
        If False, keep all elements regardless of visibility.
    """
    interactive: bool = False
    full_page: bool = False
    filter_invisible: bool = True


class RoleNameTracker:
    """Track role+name combinations to detect duplicates."""
    
    def __init__(self):
        self.counts: Dict[str, int] = {}
        self.refs_by_key: Dict[str, List[str]] = {}
    
    def get_key(self, role: str, name: Optional[str] = None) -> str:
        """Generate a stable key from role and name.

        Parameters
        ----------
        role : str
            ARIA role (lowercase).
        name : Optional[str], optional
            Accessible name. If None, an empty string is used.

        Returns
        -------
        str
            Key in the form ``<role>:<name>``.
        """
        return f"{role}:{name or ''}"
    
    def get_next_index(self, role: str, name: Optional[str] = None) -> int:
        """Get the next occurrence index for a role+name combination.

        Parameters
        ----------
        role : str
            ARIA role (lowercase).
        name : Optional[str], optional
            Accessible name.

        Returns
        -------
        int
            Zero-based index for this role+name pair.
        """
        key = self.get_key(role, name)
        current = self.counts.get(key, 0)
        self.counts[key] = current + 1
        return current
    
    def track_ref(self, role: str, name: Optional[str], ref: str) -> None:
        """Track a generated ref for a role+name combination.

        Parameters
        ----------
        role : str
            ARIA role (lowercase).
        name : Optional[str]
            Accessible name.
        ref : str
            Generated ref (e.g., ``e1``).
        """
        key = self.get_key(role, name)
        if key not in self.refs_by_key:
            self.refs_by_key[key] = []
        self.refs_by_key[key].append(ref)
    
    def get_duplicate_keys(self) -> Set[str]:
        """Get all role+name keys that have duplicates.

        Returns
        -------
        Set[str]
            Set of keys where more than one ref was recorded.
        """
        duplicates = set()
        for key, refs in self.refs_by_key.items():
            if len(refs) > 1:
                duplicates.add(key)
        return duplicates


class SnapshotGenerator:
    """Generate enhanced snapshots with element references.

    IMPORTANT USAGE NOTES:

    1. Thread/Coroutine Safety:
       This class uses instance-level state (_ref_counter) that is reset at the
       start of each snapshot generation. Do NOT share a single SnapshotGenerator
       instance across concurrent coroutines. Create a new instance per concurrent
       operation, or ensure sequential access.

    2. Regex Handling:
       The line parsing regex uses `(?:[^"\\]|\\.)*` to correctly handle escaped quotes
       in element names (e.g., `"Type \"hello\" to verify:"`).

    3. nth Index Accuracy:
       The nth index computed for duplicate role+name combinations is based on
       snapshot text order, not DOM order. This can cause locator mismatches if
       the DOM has been modified or contains filtered elements.
    """

    # Roles that are interactive and should get refs
    # Reference: W3C WAI-ARIA 1.2 Widget Roles (https://www.w3.org/TR/wai-aria/#widget_roles)
    # and browser-use's clickable_elements.py
    INTERACTIVE_ROLES: Set[str] = {
        # Widget roles (WAI-ARIA 1.2 Section 5.3.2)
        'button', 'link', 'textbox', 'checkbox', 'radio',
        'menuitem', 'menuitemcheckbox', 'menuitemradio',
        'option', 'searchbox', 'slider', 'spinbutton', 'switch',
        'tab', 'treeitem',
        'gridcell',      # Editable/selectable grid cell
        'progressbar',   # Progress indicator (user monitors)
        'scrollbar',     # Scrollbar control (user operates)
        # Composite widget roles (container widgets that manage focus)
        'combobox', 'listbox',
        # Landmark role that's also interactive
        'search',
    }

    # Roles that provide structure/context (get refs only when named)
    # NOTE: Some roles like 'cell' also appear in ALWAYS_REF_ROLES for DOM lookup purposes.
    # The overlap is intentional - CONTENT_ROLES controls "named = get ref" logic,
    # while ALWAYS_REF_ROLES forces refs regardless of name.
    # NOTE: 'gridcell' moved to INTERACTIVE_ROLES as it's a widget role per WAI-ARIA 1.2
    CONTENT_ROLES: Set[str] = {
        'heading', 'cell', 'columnheader', 'rowheader',
        'article', 'region', 'main', 'navigation'
    }

    # Roles that should always get refs for DOM reverse-lookup.
    # These roles need refs even when unnamed, for data extraction and interaction.
    # NOTE: Some roles overlap with CONTENT_ROLES - this is intentional.
    ALWAYS_REF_ROLES: Set[str] = {
        'listitem',      # List items need refs for interaction/selection
        'cell',          # Table cells need refs for data extraction/interaction
        'gridcell',      # Grid cells
        'columnheader',  # Table column headers
        'rowheader',     # Table row headers
        'row',           # Table rows
        'option',        # Select options (also in INTERACTIVE, but explicit here)
    }

    # Landmark roles that provide semantic structure (always keep)
    LANDMARK_ROLES: Set[str] = {
        'banner', 'contentinfo', 'complementary', 'form', 'search',
        'main', 'navigation', 'region'
    }

    # Semantic roles that convey meaning (always keep)
    # Reference: W3C WAI-ARIA 1.2 Document Structure & Live Region & Window Roles
    SEMANTIC_ROLES: Set[str] = {
        # Document structure roles
        'img', 'figure', 'paragraph', 'blockquote', 'code', 'emphasis',
        'strong', 'deletion', 'insertion', 'subscript', 'superscript',
        'term', 'definition', 'note', 'math', 'time', 'tooltip',
        'document', 'application', 'feed', 'text',
        'caption',       # Table/figure caption (WAI-ARIA 1.2)
        'meter',         # Scalar measurement within known range (WAI-ARIA 1.2)
        # Live region roles (WAI-ARIA 1.2 Section 5.3.5)
        'status', 'alert', 'log', 'marquee', 'timer',
        # Window roles (WAI-ARIA 1.2 Section 5.3.6)
        'dialog', 'alertdialog',
    }

    # Roles that are purely structural noise (filter when unnamed)
    STRUCTURAL_NOISE_ROLES: Set[str] = {
        'generic', 'group', 'none', 'presentation'
    }

    # Container structural roles (keep for tree structure)
    # Includes composite widget containers (WAI-ARIA 1.2 Section 5.3.3)
    # These manage focus for their child widgets
    STRUCTURAL_ROLES: Set[str] = {
        # Document structure containers
        'list', 'table', 'row', 'rowgroup',
        # Composite widget containers (focus management)
        'grid', 'treegrid', 'menu', 'menubar', 'toolbar',
        'tablist', 'tree', 'radiogroup', 'tabpanel',
        # Deprecated but still in use
        'directory',
    }

    def __init__(self):
        """Initialize the snapshot generator."""
        self._ref_counter = 0
    
    def _reset_refs(self) -> None:
        """Reset ref counter (call at start of each snapshot)."""
        self._ref_counter = 0
    
    def _next_ref(self) -> str:
        """Generate next ref ID."""
        self._ref_counter += 1
        return f"e{self._ref_counter}"
    
    def _build_selector(self, role: str, name: Optional[str] = None) -> str:
        """Build a selector string for storing in the ref map.

        Parameters
        ----------
        role : str
            ARIA role (lowercase).
        name : Optional[str], optional
            Accessible name.

        Returns
        -------
        str
            A Playwright selector expression string (stored for debugging / reverse lookup).
        """
        if name:
            # Escape all double quotes (matching TS version: name.replace(/"/g, '\\"'))
            escaped_name = name.replace('"', '\\"')
            return f"get_by_role('{role}', name=\"{escaped_name}\", exact=True)"
        return f"get_by_role('{role}')"
    
    def _get_indent_level(self, line: str) -> int:
        """Get indentation level (number of spaces / 2).

        Parameters
        ----------
        line : str
            A single line of snapshot text.

        Returns
        -------
        int
            Indent level in units of 2 spaces.
        """
        match = re.match(r'^(\s*)', line)
        return math.floor(len(match.group(1)) / 2) if match else 0

    def _remove_nth_from_non_duplicates(
        self,
        refs: Dict[str, RefData],
        tracker: RoleNameTracker
    ) -> None:
        """Remove `nth` from refs that are not duplicates.

        Parameters
        ----------
        refs : Dict[str, RefData]
            Ref mapping to update in-place.
        tracker : RoleNameTracker
            Tracker that can report duplicate keys.
        """
        duplicate_keys = tracker.get_duplicate_keys()
        
        for ref, data in refs.items():
            key = tracker.get_key(data.role, data.name)
            if key not in duplicate_keys:
                # Not a duplicate, remove nth to keep locator simple
                refs[ref].nth = None

    async def page_snapshot_for_ai(self, page: AsyncPage) -> str:
        """Get Playwright internal snapshot for AI.

        WARNING: This method uses Playwright's private/internal API (_impl_obj, _channel).
        These APIs are not part of Playwright's public contract and may change or break
        in future Playwright versions without notice. Monitor Playwright releases and
        test thoroughly after upgrades.

        The 'snapshotForAI' command is an internal Playwright feature that returns
        a structured accessibility tree optimized for AI consumption.

        Parameters
        ----------
        page : playwright.async_api.Page
            Target Playwright page.

        Returns
        -------
        str
            Raw snapshot string returned by Playwright `snapshotForAI`.
            Returns None if the snapshot fails or is empty.
        """
        # ACCESS PRIVATE API - May break in future Playwright versions
        page_impl = page._impl_obj
        channel = page_impl._channel
        result = await channel.send_return_as_dict(
            "snapshotForAI",
            page_impl._timeout_settings.timeout,
            {"track": None, "timeout": 30000},
            is_internal=True
        )
        full_data = result.get('full')
        return full_data

    def _process_page_snapshot_for_ai(
        self,
        raw_snapshot: str,
        refs: Dict[str, RefData],
        options: SnapshotOptions
    ) -> str:
        """Process `page_snapshot_for_ai` output into a streamlined tree.

        This is the CORE PROCESSING METHOD that transforms raw Playwright snapshots
        into AI-friendly trees with element references.

        Processing Pipeline:
        1. Parse each line to extract role, name, and attributes
        2. Determine if element should be kept based on role classification
        3. Assign refs to elements that need them for interaction
        4. Collapse indentation when wrapper elements are filtered
        5. Preserve inline text content even from filtered elements

        Key Design Decisions:
        - INTERACTIVE_ROLES always get refs (button, link, textbox, etc.)
        - STRUCTURAL_NOISE_ROLES (generic, group) are filtered unless named
        - Indentation is recalculated when elements are filtered to maintain
          valid tree structure for AI parsing
        - Inline text content (": text") from filtered elements is preserved
          as standalone text nodes

        Parameters
        ----------
        raw_snapshot : str
            Raw output from `page_snapshot_for_ai()`.
        refs : Dict[str, RefData]
            Mapping to populate with ``ref -> RefData``.
        options : SnapshotOptions
            Snapshot options (interactive only, for flattened output).

        Returns
        -------
        str
            Processed snapshot tree string.
        """
        lines = raw_snapshot.split('\n')
        result: List[str] = []
        tracker = RoleNameTracker()

        # Track the stack of (original_depth, kept, effective_depth)
        # - original_depth: the depth in the original tree
        # - kept: whether this element was kept in output
        # - effective_depth: the depth in the output tree
        depth_stack: List[Tuple[int, bool, int]] = []

        # Regex to match snapshot lines
        # Note: (?:[^"\\]|\\.)* handles escaped quotes like \"
        line_pattern = re.compile(
            r'^(\s*-\s*)'              # prefix with indentation
            r'(\w+)'                   # role
            r'(?:\s+"((?:[^"\\]|\\.)*)")?'  # optional name in quotes (handles escaped quotes)
            r'(.*)$'                   # suffix (attributes, colon, etc.)
        )

        # Regex patterns for cleaning
        ref_pattern = re.compile(r'\s*\[ref=e\d+\]')
        # Note: We keep [cursor=pointer] as it indicates interactive elements

        def get_effective_depth(original_depth: int) -> int:
            """Calculate effective depth based on kept parents."""
            # Find the nearest kept parent
            effective = 0
            for orig_d, kept, eff_d in depth_stack:
                if orig_d < original_depth and kept:
                    effective = eff_d + 1
            return effective

        for line in lines:
            # Skip empty lines
            if not line.strip():
                continue

            # Skip empty text nodes (e.g., "- text: " or "- text:")
            stripped_line = line.strip()
            # Match "- text:" with optional whitespace/non-printable chars after
            text_content_match = re.match(r'^-\s*text:\s*(.*)$', stripped_line)
            if text_content_match:
                content = text_content_match.group(1)
                # Skip if content is empty or only contains non-printable characters
                if not content or not any(c.isprintable() and not c.isspace() for c in content):
                    continue

            original_depth = self._get_indent_level(line)

            # Pop elements from stack that are at same or deeper level
            while depth_stack and depth_stack[-1][0] >= original_depth:
                depth_stack.pop()

            match = line_pattern.match(line)

            if not match:
                # Non-standard line (text content, metadata like /url:)
                # Find if there's a kept parent
                has_kept_parent = any(kept for _, kept, _ in depth_stack)
                if has_kept_parent or not depth_stack:
                    # Calculate effective depth and re-indent
                    eff_depth = get_effective_depth(original_depth)
                    # Extract content after the "- " prefix
                    stripped = line.lstrip()
                    if stripped.startswith('- '):
                        content = stripped[2:]
                        new_line = '  ' * eff_depth + '- ' + content
                    else:
                        # Text content or other format
                        new_line = '  ' * eff_depth + stripped
                    result.append(new_line)
                continue

            _, role, name, suffix = match.groups()
            role_lower = role.lower()

            # Handle metadata lines (like /url:, /placeholder:)
            if role.startswith('/'):
                has_kept_parent = any(kept for _, kept, _ in depth_stack)
                if has_kept_parent or not depth_stack:
                    eff_depth = get_effective_depth(original_depth)
                    new_line = '  ' * eff_depth + f'- {role}{suffix}'
                    result.append(new_line)
                continue

            is_interactive = role_lower in self.INTERACTIVE_ROLES
            is_content = role_lower in self.CONTENT_ROLES
            is_always_ref = role_lower in self.ALWAYS_REF_ROLES
            is_landmark = role_lower in self.LANDMARK_ROLES
            is_semantic = role_lower in self.SEMANTIC_ROLES
            is_structural = role_lower in self.STRUCTURAL_ROLES
            is_noise = role_lower in self.STRUCTURAL_NOISE_ROLES

            # =================================================================
            # Browser-use style: Check disabled state FIRST (exclude from interactive)
            # Reference: Section 4 - "禁用/隐藏 → 直接排除"
            # =================================================================
            is_disabled = suffix and '[disabled]' in suffix

            # =================================================================
            # Browser-use style: Check for cursor=pointer (fallback rule)
            # Reference: Section 5.7 - "cursor:pointer 兜底"
            # =================================================================
            has_cursor_pointer = suffix and '[cursor=pointer]' in suffix

            # =================================================================
            # Browser-use style: Check ARIA state attributes (indicate interactivity)
            # Reference: Section 5.4 - "ARIA 状态/属性"
            # Elements with these states are likely interactive widgets
            # =================================================================
            has_aria_state = suffix and any(
                attr in suffix for attr in [
                    '[pressed]', '[expanded]', '[checked]', '[selected]',
                    '[pressed=', '[expanded=', '[checked=', '[selected=',
                ]
            )

            # Determine if this element should be kept
            should_keep = False
            should_have_ref = False

            # Disabled elements: keep them visible but don't treat as fully interactive
            # (they still get refs for status checking, but won't be primary action targets)
            if is_disabled:
                # Keep disabled elements in output (user needs to see them)
                # but they are still interactive role elements
                if is_interactive or has_aria_state:
                    should_keep = True
                    should_have_ref = True
            elif is_interactive or has_cursor_pointer or has_aria_state:
                should_keep = True
                should_have_ref = True
            elif is_always_ref:
                # Roles like listitem that always need refs for DOM lookup
                should_keep = True
                should_have_ref = True
            elif is_content:
                should_keep = True
                should_have_ref = bool(name)
            elif is_landmark:
                should_keep = True
                should_have_ref = bool(name)
            elif is_semantic:
                should_keep = True
                should_have_ref = bool(name)
            elif is_structural:
                should_keep = True
                should_have_ref = bool(name)
            elif is_noise:
                # Filter noise elements (generic, group, none, presentation)
                should_keep = bool(name)
                should_have_ref = bool(name)
            else:
                # Unknown role - keep it, ref if named
                should_keep = True
                should_have_ref = bool(name)

            # In interactive-only mode, only keep interactive elements
            # cursor=pointer and ARIA state elements are also considered interactive
            if options.interactive:
                is_effectively_interactive = (
                    is_interactive or has_cursor_pointer or has_aria_state
                )
                if not is_effectively_interactive:
                    should_keep = False

            # Calculate effective depth for this element
            effective_depth = get_effective_depth(original_depth)

            # Track this element in the stack
            depth_stack.append((original_depth, should_keep, effective_depth))

            if not should_keep:
                # Check if this filtered element has inline text content
                # e.g., "- generic [ref=e369]: Recommended" has text after the colon
                if suffix and ':' in suffix:
                    # Extract text after the last colon (non-whitespace content only)
                    text_match = re.search(r':\s*(\S.*)$', suffix)
                    if text_match:
                        inline_text = text_match.group(1).strip()
                        # Only emit if text has printable content
                        if inline_text and any(c.isprintable() and not c.isspace() for c in inline_text):
                            indent = '  ' * effective_depth
                            result.append(f"{indent}- text: {inline_text}")
                continue

            # Clean the suffix - remove existing ref (we keep style attributes like cursor)
            clean_suffix = ref_pattern.sub('', suffix)
            clean_suffix = clean_suffix.strip()

            # Build enhanced line with correct indentation
            if options.interactive:
                enhanced = f"- {role}"
            else:
                indent = '  ' * effective_depth
                enhanced = f"{indent}- {role}"

            if name:
                enhanced += f' "{name}"'

            if should_have_ref:
                ref = self._next_ref()
                nth = tracker.get_next_index(role_lower, name)
                tracker.track_ref(role_lower, name, ref)

                refs[ref] = RefData(
                    selector=self._build_selector(role_lower, name),
                    role=role_lower,
                    name=name,
                    nth=nth
                )

                enhanced += f" [ref={ref}]"
                # Only show nth for named elements with duplicates
                # For unnamed elements, ref alone is sufficient for identification
                if nth > 0 and name:
                    enhanced += f" [nth={nth}]"

            # Re-add clean suffix (like [level=1] for headings, or trailing colon)
            if clean_suffix:
                if clean_suffix == ':':
                    enhanced += ':'
                elif clean_suffix.startswith(':'):
                    # Inline content like ": some text" - append directly without extra space
                    enhanced += clean_suffix
                elif clean_suffix.endswith(':'):
                    enhanced += f" {clean_suffix[:-1]}:"
                else:
                    enhanced += f" {clean_suffix}"

            result.append(enhanced)

        # Post-process: remove nth from refs that don't have duplicates
        self._remove_nth_from_non_duplicates(refs, tracker)

        return '\n'.join(result)

    def _extract_original_refs_from_raw(self, raw_snapshot: str) -> Dict[str, Tuple[str, Optional[str], int]]:
        """Extract original refs from a raw snapshot.

        IMPORTANT: The nth_index computed here is based on the ORDER OF APPEARANCE
        in the snapshot text, NOT the actual DOM order. This can cause issues when:
        1. The snapshot has already filtered some elements (invisible, out-of-viewport)
        2. The DOM contains more elements than the snapshot shows
        3. Using get_by_role().nth(nth_index) may locate a different element

        For reliable element location, prefer using named elements where possible,
        or use Playwright's original ref mechanism if available.

        Parameters
        ----------
        raw_snapshot : str
            Raw output from `page_snapshot_for_ai()`.

        Returns
        -------
        Dict[str, Tuple[str, Optional[str], int]]
            Mapping of ``ref -> (role, name, nth_index)``.
        """
        refs_info: Dict[str, Tuple[str, Optional[str], int]] = {}
        role_name_counts: Dict[str, int] = {}

        # Pattern to match lines with refs
        # Note: (?:[^"\\]|\\.)* correctly handles escaped quotes like \"
        line_pattern = re.compile(
            r'^\s*-\s*(\w+)'           # role
            r'(?:\s+"((?:[^"\\]|\\.)*)")?'  # optional name (handles escaped quotes)
            r'.*\[ref=(e\d+)\]'        # ref
        )

        for line in raw_snapshot.split('\n'):
            match = line_pattern.match(line)
            if match:
                role, name, ref = match.groups()
                role_lower = role.lower()

                # Track nth index for role+name combination
                key = f"{role_lower}:{name or ''}"
                nth = role_name_counts.get(key, 0)
                role_name_counts[key] = nth + 1

                refs_info[ref] = (role_lower, name, nth)

        return refs_info

    async def _check_element_should_include(
        self,
        page: AsyncPage,
        role: str,
        name: Optional[str],
        nth: int,
        filter_invisible: bool,
        check_viewport: bool,
        viewport_width: Optional[int] = None,
        viewport_height: Optional[int] = None
    ) -> bool:
        """Check whether an element should be included based on visibility and viewport.

        This method determines element inclusion using Playwright locators.

        Locator Strategy:
        1. Build locator using get_by_role(role, name=name, exact=True)
        2. Apply nth index for disambiguation when multiple matches exist
        3. Check CSS visibility using is_visible() if filter_invisible is enabled
        4. Check viewport bounds if check_viewport is enabled

        CRITICAL LIMITATION:
        The nth index is based on snapshot text order, which may not match DOM order.
        For unnamed elements (especially structural roles like 'generic'), this can
        cause the locator to target the wrong element. This is why we skip visibility
        checks for STRUCTURAL_NOISE_ROLES without names - they cannot be reliably located.

        Parameters
        ----------
        page : playwright.async_api.Page
            Playwright page object.
        role : str
            ARIA role of the element.
        name : Optional[str]
            Accessible name of the element.
        nth : int
            Index for elements with same role+name (based on snapshot order, NOT DOM order).
        filter_invisible : bool
            Whether to filter CSS-hidden elements.
        check_viewport : bool
            Whether to filter elements outside the viewport.
        viewport_width : Optional[int], optional
            Viewport width (pre-fetched for efficiency).
        viewport_height : Optional[int], optional
            Viewport height (pre-fetched for efficiency).

        Returns
        -------
        bool
            True if element should be included, otherwise False.
        """
        # Skip visibility check for structural noise roles (generic, group, etc.)
        # These are layout containers that:
        # 1. Cannot be reliably located via get_by_role without a name
        # 2. May cause entire subtrees to be incorrectly filtered
        # 3. Their visibility is determined by their children, not themselves
        if role in self.STRUCTURAL_NOISE_ROLES and not name:
            return True

        try:
            # Build locator
            if name:
                locator = page.get_by_role(role, name=name, exact=True)
            else:
                locator = page.get_by_role(role)

            # Apply nth index
            if nth > 0:
                locator = locator.nth(nth)
            else:
                locator = locator.first

            # Check CSS visibility using is_visible() if filter_invisible is enabled
            if filter_invisible:
                is_visible = await locator.is_visible()
                if not is_visible:
                    return False

            # Check viewport bounds if needed
            if check_viewport and viewport_width is not None and viewport_height is not None:
                # Get element bounding box
                bounding_box = await locator.bounding_box()
                if bounding_box:
                    # Check if element overlaps with viewport (partial overlap counts)
                    elem_right = bounding_box['x'] + bounding_box['width']
                    elem_bottom = bounding_box['y'] + bounding_box['height']

                    is_in_viewport = not (
                        elem_right < 0 or bounding_box['x'] > viewport_width or
                        elem_bottom < 0 or bounding_box['y'] > viewport_height
                    )
                    if not is_in_viewport:
                        return False
                else:
                    # No bounding box means element is not rendered, exclude it
                    return False

            return True

        except Exception as e:
            # If we can't check (element not found, etc.), assume it should be included
            logger.debug(f"Failed to check element {role} '{name}': {e}")
            return True

    async def _pre_filter_raw_snapshot(
        self,
        raw_snapshot: str,
        page: AsyncPage,
        options: SnapshotOptions
    ) -> str:
        """Pre-filter raw snapshot to remove invisible/out-of-viewport elements.

        This method runs BEFORE the main processing to filter out elements that
        don't need to be processed at all, improving performance significantly.

        Filter Strategy:
        1. Extract all refs from raw snapshot with their role/name/nth info
        2. Run parallel visibility checks on all ref'd elements
        3. Build a set of visible refs
        4. Filter snapshot lines, skipping invisible elements and their children

        IMPORTANT: When an element is marked invisible, ALL its children are also
        skipped, regardless of their individual visibility. This matches the
        expectation that content inside a hidden container is not accessible.

        Performance Note:
        Visibility checks are run in parallel using asyncio.gather() for better
        performance on pages with many elements.

        Parameters
        ----------
        raw_snapshot : str
            Raw output from `page_snapshot_for_ai()`.
        page : playwright.async_api.Page
            Playwright page object for visibility checking.
        options : SnapshotOptions
            Snapshot options with `full_page` and `filter_invisible` settings.

        Returns
        -------
        str
            Filtered raw snapshot string.
        """
        # If no filtering needed, return as-is
        if options.full_page and not options.filter_invisible:
            return raw_snapshot

        # Extract all original refs with their role/name/nth
        refs_info = self._extract_original_refs_from_raw(raw_snapshot)

        if not refs_info:
            return raw_snapshot

        # Pre-fetch viewport size once for efficiency
        viewport = page.viewport_size
        viewport_width = viewport['width'] if viewport else None
        viewport_height = viewport['height'] if viewport else None
        check_viewport = not options.full_page and viewport_width is not None

        # Define coroutine for checking each ref
        async def check_ref(ref: str, role: str, name: Optional[str], nth: int) -> Tuple[str, bool]:
            should_include = await self._check_element_should_include(
                page, role, name, nth,
                filter_invisible=options.filter_invisible,
                check_viewport=check_viewport,
                viewport_width=viewport_width,
                viewport_height=viewport_height
            )
            return (ref, should_include)

        # Run all visibility checks in parallel for better performance
        tasks = [
            check_ref(ref, role, name, nth)
            for ref, (role, name, nth) in refs_info.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect visible refs
        visible_refs: Set[str] = set()
        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"Visibility check exception: {result}")
                continue
            ref, should_include = result
            if should_include:
                visible_refs.add(ref)

        logger.debug(f"Pre-filter: {len(visible_refs)}/{len(refs_info)} refs visible/in-viewport")

        # If all refs are visible, return as-is
        if len(visible_refs) == len(refs_info):
            return raw_snapshot

        # Filter the raw snapshot
        lines = raw_snapshot.split('\n')
        result: List[str] = []

        # Track invisible parent depths to skip children
        invisible_depth: Optional[int] = None

        # Pattern to extract ref from line
        ref_pattern = re.compile(r'\[ref=(e\d+)\]')

        for line in lines:
            if not line.strip():
                result.append(line)
                continue

            depth = self._get_indent_level(line)

            # If we're inside an invisible parent's subtree, check if we've exited
            if invisible_depth is not None:
                if depth > invisible_depth:
                    # Still inside invisible subtree, skip this line
                    continue
                else:
                    # Exited the invisible subtree
                    invisible_depth = None

            # Check if this line has a ref
            ref_match = ref_pattern.search(line)
            if ref_match:
                ref = ref_match.group(1)
                if ref not in visible_refs:
                    # This element is invisible/out-of-viewport, mark depth to skip children
                    invisible_depth = depth
                    continue

            result.append(line)

        return '\n'.join(result)

    async def _generate_snapshot(
        self,
        page: AsyncPage,
        refs: Dict[str, RefData],
        options: SnapshotOptions
    ) -> EnhancedSnapshot:
        """Internal method to generate enhanced snapshot.

        Parameters
        ----------
        page : AsyncPage
            Playwright page object.
        refs : Dict[str, RefData]
            Ref mapping to populate.
        options : SnapshotOptions
            Snapshot generation options.

        Returns
        -------
        EnhancedSnapshot
            Generated snapshot with tree and refs.
        """
        raw_snapshot = await self.page_snapshot_for_ai(page)

        if not raw_snapshot:
            return EnhancedSnapshot(tree='(empty)', refs={})

        # Pre-filter invisible/out-of-viewport elements before processing
        filtered_snapshot = await self._pre_filter_raw_snapshot(raw_snapshot, page, options)
        enhanced_tree = self._process_page_snapshot_for_ai(filtered_snapshot, refs, options)

        return EnhancedSnapshot(tree=enhanced_tree, refs=refs)

    async def get_enhanced_snapshot_async(
        self,
        page: AsyncPage,
        options: Optional[SnapshotOptions] = None
    ) -> EnhancedSnapshot:
        """Get enhanced snapshot with refs and optional filtering.

        This is the MAIN ENTRY POINT for generating snapshots.

        Processing Flow:
        1. Call page_snapshot_for_ai() to get raw Playwright snapshot
        2. Pre-filter invisible/out-of-viewport elements
        3. Process and enhance the tree with element refs
        4. Return EnhancedSnapshot with tree text and refs dictionary

        Options:
        - interactive: Only include interactive elements (flattened output)
        - filter_invisible: Filter CSS-hidden elements (default: True)
        - full_page: Include all elements regardless of viewport (default: False)

        Filtering Behavior:
        | filter_invisible | full_page | Behavior                              |
        |------------------|-----------|---------------------------------------|
        | True             | False     | Visible + in viewport only (default)  |
        | True             | True      | Visible elements (entire page)        |
        | False            | False     | In viewport only (include hidden)     |
        | False            | True      | No filtering (all elements)           |

        Parameters
        ----------
        page : AsyncPage
            Playwright page object.
        options : Optional[SnapshotOptions]
            Snapshot generation options.

        Returns
        -------
        EnhancedSnapshot
            Snapshot with tree string and refs dictionary.
        """
        if options is None:
            options = SnapshotOptions()

        self._reset_refs()
        refs: Dict[str, RefData] = {}

        return await self._generate_snapshot(page, refs, options)

    @staticmethod
    def parse_ref(arg: str) -> Optional[str]:
        """Parse a ref string (e.g., ``@e1`` -> ``e1``).

        Parameters
        ----------
        arg : str
            Reference string in various formats (``@e1``, ``ref=e1``, ``e1``).

        Returns
        -------
        Optional[str]
            Parsed ref ID, or None if invalid.
        """
        arg = arg.strip()
        if arg.startswith('@'):
            return arg[1:]
        if arg.startswith('ref='):
            return arg[4:]
        if arg.isdigit():
            return f"e{arg}"
        if re.match(r'^e\d+$', arg):
            return arg
        return None
    
    def get_locator_from_ref_async(
        self,
        page: AsyncPage,
        ref_arg: str,
        refs: Dict[str, RefData]
    ) -> Optional[AsyncLocator]:
        """Get a Playwright locator from a ref string.

        This method reconstructs a Playwright locator from stored RefData.
        The locator can then be used for interactions (click, fill, etc.).

        Locator Construction:
        1. Parse ref string to extract ref ID (e.g., "@e1" -> "e1")
        2. Look up RefData from the refs dictionary
        3. Build locator using get_by_role(role, name=name, exact=True)
        4. Apply nth() if disambiguation is needed for duplicate role+name

        IMPORTANT: The returned locator is NOT guaranteed to match the original
        element if the DOM has changed since the snapshot was taken. Always use
        the snapshot and locators within the same navigation context.

        Parameters
        ----------
        page : playwright.async_api.Page
            Playwright page object (async_api).
        ref_arg : str
            Reference string (``e1``, ``@e1``, ``ref=e1``).
        refs : Dict[str, RefData]
            Ref map dictionary from snapshot.

        Returns
        -------
        Optional[playwright.async_api.Locator]
            Locator if the ref exists; otherwise None.
        """
        ref = self.parse_ref(ref_arg)
        if not ref:
            return None
        
        ref_data = refs.get(ref)
        if not ref_data:
            return None
        
        # Build locator with exact=True to avoid substring matches
        if ref_data.name:
            locator = page.get_by_role(ref_data.role, name=ref_data.name, exact=True)
        else:
            locator = page.get_by_role(ref_data.role)
        
        # If an nth index is stored (for disambiguation), use it
        if ref_data.nth is not None:
            locator = locator.nth(ref_data.nth)
        
        return locator
    
    def get_snapshot_stats(
        self,
        tree: str,
        refs: Dict[str, RefData]
    ) -> Dict[str, int]:
        """Get snapshot statistics.

        Parameters
        ----------
        tree : str
            Snapshot tree string.
        refs : Dict[str, RefData]
            Ref map dictionary.

        Returns
        -------
        Dict[str, int]
            Dictionary with stats (lines, chars, tokens, refs, interactive).
        """
        interactive = sum(
            1 for r in refs.values()
            if r.role in self.INTERACTIVE_ROLES
        )
        
        return {
            'lines': len(tree.split('\n')),
            'chars': len(tree),
            'tokens': (len(tree) + 3) // 4,  # Approximate token count
            'refs': len(refs),
            'interactive': interactive
        }
