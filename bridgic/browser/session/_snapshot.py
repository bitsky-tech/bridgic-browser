"""
Enhanced Accessibility Snapshot Generator for AI-driven Browser Automation.

This module generates structured accessibility tree snapshots with element references (refs)
that enable deterministic element selection for browser automation tasks. The refs allow
AI agents to reliably interact with elements without fragile CSS/XPath selectors.

Key Features:
- Generates AI-friendly accessibility tree with element refs
- Filters viewport-only content
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

            # Default: viewport-only
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
import time
from typing import Dict, Optional, Set, List, Tuple, Any
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
    text_content: Optional[str] = None


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
        If True (default), include all elements regardless of viewport position.
        If False, only include elements within the viewport.
    """
    interactive: bool = False
    full_page: bool = True


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

    # =========================================================================
    # Interactive Tags (W3C HTML5 Specification)
    # Reference: Section 5.1 of INTERACTIVE_ELEMENTS.md
    # These HTML tags are inherently interactive by specification
    # =========================================================================
    INTERACTIVE_TAGS: Set[str] = {
        'button', 'input', 'select', 'textarea', 'a',
        'details', 'summary', 'option', 'optgroup'
    }

    # =========================================================================
    # Search-related Keywords
    # Reference: Section 5.3 of INTERACTIVE_ELEMENTS.md
    # Used to detect search-related elements by class/id/data-* attributes
    # =========================================================================
    SEARCH_KEYWORDS: Set[str] = {
        'search', 'magnify', 'glass', 'lookup', 'find', 'query',
        'search-icon', 'search-btn', 'search-button', 'searchbox'
    }

    # =========================================================================
    # Interactive Roles (WAI-ARIA 1.2 Specification)
    # Reference: https://www.w3.org/TR/wai-aria-1.2/#widget_roles
    # =========================================================================

    # Basic Widget Roles - Independent interactive components
    WIDGET_ROLES: Set[str] = {
        'button', 'checkbox', 'gridcell', 'link',
        'menuitem', 'menuitemcheckbox', 'menuitemradio',
        'option', 'progressbar', 'radio', 'scrollbar', 'searchbox',
        'slider', 'spinbutton', 'switch',
        'tab', 'tabpanel', 'textbox', 'treeitem',
        # Note: 'separator' is only interactive when focusable (tabindex >= 0)
        # This is handled specially in _is_element_interactive()
    }

    # Composite Widget Roles - Containers that manage child components
    COMPOSITE_WIDGET_ROLES: Set[str] = {
        'combobox', 'grid', 'listbox', 'menu', 'menubar',
        'radiogroup', 'tablist', 'tree', 'treegrid',
    }

    # Window Roles - Windows requiring user interaction
    WINDOW_ROLES: Set[str] = {
        'alertdialog', 'dialog',
    }

    # Other Interactive Roles
    OTHER_INTERACTIVE_ROLES: Set[str] = {
        'application',  # Contains focusable elements
        'search',       # Search landmark (usually contains interactive elements)
    }

    # Combined Interactive Roles set
    INTERACTIVE_ROLES: Set[str] = (
        WIDGET_ROLES |
        COMPOSITE_WIDGET_ROLES |
        WINDOW_ROLES |
        OTHER_INTERACTIVE_ROLES
    )

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

    # Roles where snapshot "name" can come from visible text while Playwright
    # role-name matching relies on accessible name semantics.
    # For these roles, prefer role-constrained exact text matching.
    ROLE_TEXT_MATCH_ROLES: Set[str] = {
        'listitem',
        'row',
        'cell',
        'gridcell',
        'columnheader',
        'rowheader',
    }

    # Landmark roles that provide semantic structure (always keep)
    # Note: 'search' is also in OTHER_INTERACTIVE_ROLES
    LANDMARK_ROLES: Set[str] = {
        'banner', 'contentinfo', 'complementary', 'form', 'search',
        'main', 'navigation', 'region'
    }

    # Semantic roles that convey meaning (always keep)
    # Reference: W3C WAI-ARIA 1.2 Document Structure & Live Region Roles
    # Note: Window roles (dialog, alertdialog) moved to WINDOW_ROLES
    # Note: 'application' moved to OTHER_INTERACTIVE_ROLES
    SEMANTIC_ROLES: Set[str] = {
        # Document structure roles
        'img', 'figure', 'paragraph', 'blockquote', 'code', 'emphasis',
        'strong', 'deletion', 'insertion', 'subscript', 'superscript',
        'term', 'definition', 'note', 'math', 'time', 'tooltip',
        'document', 'feed', 'text',
        'caption',       # Table/figure caption (WAI-ARIA 1.2)
        'meter',         # Scalar measurement within known range (WAI-ARIA 1.2)
        # Live region roles (WAI-ARIA 1.2 Section 5.3.5)
        'status', 'alert', 'log', 'marquee', 'timer',
    }

    # Roles that are purely structural noise (filter when unnamed)
    STRUCTURAL_NOISE_ROLES: Set[str] = {
        'generic', 'group', 'none', 'presentation'
    }

    # Pre-compiled regex patterns (avoid recompilation per call)
    # Pattern for _process_page_snapshot_for_ai: matches any snapshot line
    _LINE_PATTERN = re.compile(
        r'^(\s*-\s*)'              # prefix with indentation
        r'(\w+)'                   # role
        r'(?:\s+"((?:[^"\\]|\\.)*)")?'  # optional name in quotes (handles escaped quotes)
        r'(.*)$'                   # suffix (attributes, colon, etc.)
    )
    # Pattern for cleaning existing refs from suffix
    _REF_CLEAN_PATTERN = re.compile(r'\s*\[ref=e\d+\]')
    # Pattern to extract ref ID from a line or suffix
    _REF_EXTRACT_PATTERN = re.compile(r'\[ref=(e\d+)\]')
    # Pattern for _extract_original_refs_from_raw: matches lines with refs
    _REF_LINE_PATTERN = re.compile(
        r'^\s*-\s*(\w+)'           # role
        r'(?:\s+"((?:[^"\\]|\\.)*)")?'  # optional name (handles escaped quotes)
        r'(.*\[ref=(e\d+)\].*)$'   # suffix containing ref
    )

    # Container structural roles (keep for tree structure)
    # Note: Composite widget containers moved to COMPOSITE_WIDGET_ROLES
    STRUCTURAL_ROLES: Set[str] = {
        # Document structure containers
        'list', 'table', 'row', 'rowgroup',
        'toolbar',       # Toolbar is a container, not an interaction target
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
    
    def _build_selector(self, role: str, name: Optional[str] = None, text_content: Optional[str] = None) -> str:
        """Build a selector string for storing in the ref map.

        Parameters
        ----------
        role : str
            ARIA role (lowercase).
        name : Optional[str], optional
            Accessible name.
        text_content : Optional[str], optional
            Inline text content for elements without an accessible name.

        Returns
        -------
        str
            A Playwright selector expression string (stored for debugging / reverse lookup).
        """
        if name:
            # Escape all double quotes (matching TS version: name.replace(/"/g, '\\"'))
            escaped_name = name.replace('"', '\\"')
            return f"get_by_role('{role}', name=\"{escaped_name}\", exact=True)"
        if text_content:
            escaped_text = text_content.replace('"', '\\"')
            return f"get_by_text(\"{escaped_text}\", exact=True)"
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

    async def _get_element_interactive_info(
        self,
        locator: AsyncLocator
    ) -> Dict[str, Any]:
        """Get element's interactive judgment info via a single evaluate call.

        Based on Section 2 of INTERACTIVE_ELEMENTS.md, this method retrieves
        all input data needed for interactive element judgment.

        Parameters
        ----------
        locator : AsyncLocator
            Playwright locator for the target element.

        Returns
        -------
        Dict[str, Any]
            Dictionary containing:
            - tagName: str - Tag name (lowercase)
            - cursor: str - Computed style cursor
            - width: int - Element width
            - height: int - Element height
            - hasEventHandler: bool - Has event handlers (onclick, etc.)
            - tabindex: str | None - tabindex attribute
            - classAndId: str - Combined class and id (for search keyword check)
            - dataAction: str | None - data-action attribute (for icon detection)
            - ariaRequired: bool - Has aria-required
            - ariaAutocomplete: str | None - aria-autocomplete value
            - ariaKeyshortcuts: str | None - aria-keyshortcuts value
            - ariaHidden: bool - Has aria-hidden="true"
            - ariaDisabled: bool - Has aria-disabled="true"
            - isContentEditable: bool - Is content editable
            - role: str | None - Explicit role attribute
            - isEditable: bool - Playwright's is_editable result
            - isDisabled: bool - Playwright's is_disabled result
        """
        try:
            start_time = time.time()
            info = await locator.evaluate("""el => {
                const computed = window.getComputedStyle(el);
                const tag = el.tagName.toLowerCase();

                // Check event handlers (Section 5.2)
                const hasEventHandler = !!(
                    el.onclick || el.onmousedown || el.onmouseup ||
                    el.onkeydown || el.onkeyup || el.onkeypress ||
                    el.onmouseenter || el.onmouseleave ||
                    el.ondblclick || el.onfocus || el.onblur
                );

                // Get class, id, and data-* attributes for search keyword check (Section 5.3)
                let classAndId = '';
                if (el.className && typeof el.className === 'string') {
                    classAndId += el.className + ' ';
                }
                if (el.id) {
                    classAndId += el.id + ' ';
                }

                // Collect all data-* attribute values
                let dataAction = null;
                for (let attr of el.attributes) {
                    if (attr.name.startsWith('data-')) {
                        classAndId += attr.value + ' ';
                        if (attr.name === 'data-action') {
                            dataAction = attr.value;
                        }
                    }
                }

                // Check aria-hidden (Section 4 - Disabled/Hidden exclusion)
                const ariaHidden = el.getAttribute('aria-hidden') === 'true';

                return {
                    tagName: tag,
                    cursor: computed.cursor,
                    display: computed.display,
                    visibility: computed.visibility,
                    opacity: parseFloat(computed.opacity),
                    width: el.offsetWidth,
                    height: el.offsetHeight,
                    hasEventHandler: hasEventHandler,
                    tabindex: el.getAttribute('tabindex'),
                    classAndId: classAndId.toLowerCase().trim(),
                    dataAction: dataAction,
                    ariaRequired: el.hasAttribute('aria-required'),
                    ariaAutocomplete: el.getAttribute('aria-autocomplete'),
                    ariaKeyshortcuts: el.getAttribute('aria-keyshortcuts'),
                    ariaHidden: ariaHidden,
                    ariaDisabled: el.getAttribute('aria-disabled') === 'true',
                    isContentEditable: el.isContentEditable,
                    role: el.getAttribute('role'),
                    isEditable: el.isContentEditable ||
                        (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName.toUpperCase()) && !el.disabled && !el.readOnly),
                    isDisabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                };
            }""")
            end_time = time.time()
            logger.info(f"_get_element_interactive_info Time taken: {end_time - start_time} seconds")

            return info
        except Exception as e:
            logger.debug(f"Failed to get element interactive info: {e}")
            return {}

    async def _batch_get_elements_info(
        self,
        page: AsyncPage,
        refs_info: Dict[str, Tuple[str, Optional[str], int]],
        ref_suffixes: Dict[str, str],
        check_viewport: bool,
        viewport_width: Optional[int],
        viewport_height: Optional[int],
    ) -> Tuple[Set[str], Dict[str, bool]]:
        """Batch check visibility + interactivity for all refs in one JS call.

        Reduces browser IPC from 4N calls to 1 by evaluating all element
        lookups, bounding boxes, and interactive info in a single
        ``page.evaluate()`` call.

        Parameters
        ----------
        page : AsyncPage
            Playwright page object.
        refs_info : Dict[str, Tuple[str, Optional[str], int]]
            Mapping of ``ref -> (role, name, nth_index)``.
        ref_suffixes : Dict[str, str]
            Mapping of ``ref -> suffix``.
        check_viewport : bool
            Whether to filter elements outside the viewport.
        viewport_width : Optional[int]
            Viewport width.
        viewport_height : Optional[int]
            Viewport height.

        Returns
        -------
        Tuple[Set[str], Dict[str, bool]]
            ``(visible_refs, interactive_map)``
        """
        # Separate structural noise without name (handle from suffix only)
        suffix_only_refs: Dict[str, str] = {}
        batch_elements: list[Dict[str, Any]] = []

        for ref, (role, name, nth) in refs_info.items():
            if role in self.STRUCTURAL_NOISE_ROLES and not name:
                # Unnamed structural noise roles (generic, group, etc.) bypass batch JS
                # because they have implicit roles that can't be matched via CSS selectors.
                # Named generics go through batch for viewport filtering.
                suffix_only_refs[ref] = ref_suffixes.get(ref, '')
            else:
                batch_elements.append({
                    'ref': ref,
                    'role': role,
                    'name': name,
                    'nth': nth,
                })

        # Process suffix-only refs (no IPC needed)
        visible_refs: Set[str] = set()
        interactive_map: Dict[str, bool] = {}

        for ref, suffix in suffix_only_refs.items():
            visible_refs.add(ref)
            is_interactive = False
            if '[cursor=pointer]' in suffix:
                is_interactive = True
            elif any(state in suffix for state in ['[pressed', '[expanded', '[checked', '[selected']):
                is_interactive = True
            interactive_map[ref] = is_interactive

        if not batch_elements:
            return visible_refs, interactive_map

        # Single page.evaluate for all remaining elements
        try:
            start_time = time.time()
            batch_results = await page.evaluate("""(args) => {
                const { elements, viewportWidth, viewportHeight, checkViewport } = args;

                const IMPLICIT_ROLE_SELECTORS = {
                    'button': 'button, input[type="button"], input[type="submit"], input[type="reset"], input[type="file"], [role="button"]',
                    'link': 'a[href], area[href], [role="link"]',
                    'textbox': 'input:not([type]), input[type="text"], input[type="email"], input[type="password"], input[type="search"], input[type="url"], input[type="tel"], textarea, [role="textbox"], [contenteditable="true"], [contenteditable=""]',
                    'checkbox': 'input[type="checkbox"], [role="checkbox"]',
                    'radio': 'input[type="radio"], [role="radio"]',
                    'combobox': 'select, [role="combobox"]',
                    'option': 'option, [role="option"]',
                    'heading': 'h1, h2, h3, h4, h5, h6, [role="heading"]',
                    'listitem': 'li, [role="listitem"]',
                    'list': 'ul, ol, [role="list"]',
                    'img': 'img[alt], [role="img"]',
                    'row': 'tr, [role="row"]',
                    'cell': 'td, [role="cell"]',
                    'columnheader': 'th, [role="columnheader"]',
                    'navigation': 'nav, [role="navigation"]',
                    'main': 'main, [role="main"]',
                    'banner': 'header, [role="banner"]',
                    'contentinfo': 'footer, [role="contentinfo"]',
                    'table': 'table, [role="table"]',
                    'menuitem': '[role="menuitem"]',
                    'menuitemcheckbox': '[role="menuitemcheckbox"]',
                    'menuitemradio': '[role="menuitemradio"]',
                    'tab': '[role="tab"]',
                    'tabpanel': '[role="tabpanel"]',
                    'treeitem': '[role="treeitem"]',
                    'switch': '[role="switch"]',
                    'slider': 'input[type="range"], [role="slider"]',
                    'spinbutton': 'input[type="number"], [role="spinbutton"]',
                    'searchbox': 'input[type="search"], [role="searchbox"]',
                    'progressbar': 'progress, [role="progressbar"]',
                    'scrollbar': '[role="scrollbar"]',
                    'separator': 'hr, [role="separator"]',
                    'gridcell': '[role="gridcell"]',
                    'grid': '[role="grid"]',
                    'listbox': '[role="listbox"]',
                    'menu': '[role="menu"]',
                    'menubar': '[role="menubar"]',
                    'radiogroup': '[role="radiogroup"]',
                    'tablist': '[role="tablist"]',
                    'tree': '[role="tree"]',
                    'treegrid': '[role="treegrid"]',
                    'alertdialog': '[role="alertdialog"]',
                    'dialog': 'dialog, [role="dialog"]',
                    'application': '[role="application"]',
                    'search': '[role="search"]',
                    'article': 'article, [role="article"]',
                    'region': 'section[aria-label], section[aria-labelledby], [role="region"]',
                    'rowheader': 'th[scope="row"], [role="rowheader"]',
                    'rowgroup': 'thead, tbody, tfoot, [role="rowgroup"]',
                    'toolbar': '[role="toolbar"]',
                    'status': '[role="status"]',
                    'alert': '[role="alert"]',
                    'log': '[role="log"]',
                    'marquee': '[role="marquee"]',
                    'timer': '[role="timer"]',
                    'tooltip': '[role="tooltip"]',
                    'figure': 'figure, [role="figure"]',
                    'paragraph': 'p, [role="paragraph"]',
                    'blockquote': 'blockquote, [role="blockquote"]',
                    'code': 'code, [role="code"]',
                    'emphasis': 'em, [role="emphasis"]',
                    'strong': 'strong, [role="strong"]',
                    'deletion': 'del, [role="deletion"]',
                    'insertion': 'ins, [role="insertion"]',
                    'subscript': 'sub, [role="subscript"]',
                    'superscript': 'sup, [role="superscript"]',
                    'term': 'dfn, [role="term"]',
                    'definition': 'dd, [role="definition"]',
                    'note': '[role="note"]',
                    'math': 'math, [role="math"]',
                    'time': 'time, [role="time"]',
                    'complementary': 'aside, [role="complementary"]',
                    'form': 'form[aria-label], form[aria-labelledby], [role="form"]',
                    'feed': '[role="feed"]',
                    'document': '[role="document"]',
                    'caption': 'caption, figcaption, [role="caption"]',
                    'meter': 'meter, [role="meter"]',
                    'summary': 'summary',
                    'details': 'details',
                    'generic': 'div, span, [role="generic"]',
                };

                function getAccessibleName(el) {
                    const ariaLabel = el.getAttribute('aria-label');
                    if (ariaLabel) return ariaLabel.trim();

                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const parts = labelledBy.split(/\\s+/).map(id => {
                            const ref = document.getElementById(id);
                            return ref ? ref.textContent.trim() : '';
                        }).filter(Boolean);
                        if (parts.length) return parts.join(' ');
                    }

                    if (el.id) {
                        const label = document.querySelector('label[for="' + el.id + '"]');
                        if (label) return label.textContent.trim();
                    }

                    const title = el.getAttribute('title');
                    if (title) return title.trim();

                    // For inputs: check value/placeholder as accessible name fallbacks
                    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) {
                        if (el.value) return el.value;
                        if (el.placeholder) return el.placeholder;
                    }

                    return el.textContent ? el.textContent.trim() : '';
                }

                function normalizeText(value) {
                    if (!value) return '';
                    return String(value).replace(/\\s+/g, ' ').trim();
                }

                function findElement(role, name, nth) {
                    const selector = IMPLICIT_ROLE_SELECTORS[role] || '[role="' + role + '"]';
                    const all = Array.from(document.querySelectorAll(selector));
                    if (!name) return all[nth] || null;

                    const normalizedName = normalizeText(name);
                    if (!normalizedName) return all[nth] || null;

                    const roleTextMatchRoles = new Set([
                        'listitem', 'row', 'cell', 'gridcell', 'columnheader', 'rowheader'
                    ]);

                    let matching = [];
                    if (roleTextMatchRoles.has(role)) {
                        if (role === 'row') {
                            matching = all.filter(el => {
                                const rowText = normalizeText(el.innerText || el.textContent || '');
                                if (rowText === normalizedName) return true;

                                // Row names can map to a single cell/header text. Check descendants.
                                const descendants = [el, ...Array.from(el.querySelectorAll('*'))];
                                return descendants.some(node =>
                                    normalizeText(node.innerText || node.textContent || '') === normalizedName
                                );
                            });
                        } else {
                            matching = all.filter(el =>
                                normalizeText(el.innerText || el.textContent || '') === normalizedName
                            );
                        }
                    } else {
                        matching = all.filter(
                            el => normalizeText(getAccessibleName(el)) === normalizedName
                        );
                    }

                    return matching[nth] || null;
                }

                function getElementInfo(el) {
                    if (!el) return null;
                    const computed = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const tag = el.tagName.toLowerCase();

                    const hasEventHandler = !!(
                        el.onclick || el.onmousedown || el.onmouseup ||
                        el.onkeydown || el.onkeyup || el.onkeypress ||
                        el.onmouseenter || el.onmouseleave ||
                        el.ondblclick || el.onfocus || el.onblur
                    );

                    let classAndId = '';
                    if (el.className && typeof el.className === 'string') classAndId += el.className + ' ';
                    if (el.id) classAndId += el.id + ' ';
                    let dataAction = null;
                    for (let attr of el.attributes) {
                        if (attr.name.startsWith('data-')) {
                            classAndId += attr.value + ' ';
                            if (attr.name === 'data-action') dataAction = attr.value;
                        }
                    }

                    return {
                        rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                                right: rect.right, bottom: rect.bottom },
                        tagName: tag,
                        cursor: computed.cursor,
                        width: el.offsetWidth,
                        height: el.offsetHeight,
                        hasEventHandler: hasEventHandler,
                        tabindex: el.getAttribute('tabindex'),
                        classAndId: classAndId.toLowerCase().trim(),
                        dataAction: dataAction,
                        ariaRequired: el.hasAttribute('aria-required'),
                        ariaAutocomplete: el.getAttribute('aria-autocomplete'),
                        ariaKeyshortcuts: el.getAttribute('aria-keyshortcuts'),
                        ariaHidden: el.getAttribute('aria-hidden') === 'true',
                        ariaDisabled: el.getAttribute('aria-disabled') === 'true',
                        isContentEditable: el.isContentEditable,
                        role: el.getAttribute('role'),
                        isEditable: el.isContentEditable ||
                            (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName.toUpperCase()) && !el.disabled && !el.readOnly),
                        isDisabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
                    };
                }

                const results = {};
                for (const item of elements) {
                    const el = findElement(item.role, item.name, item.nth);
                    results[item.ref] = getElementInfo(el);
                }
                return results;
            }""", {
                'elements': batch_elements,
                'viewportWidth': viewport_width,
                'viewportHeight': viewport_height,
                'checkViewport': check_viewport,
            })
            end_time = time.time()
            logger.info(f"_batch_get_elements_info Time taken: {end_time - start_time:.3f}s for {len(batch_elements)} elements")
        except Exception as e:
            logger.debug(f"Batch element info failed: {e}")
            # Fallback: include all elements
            for item in batch_elements:
                visible_refs.add(item['ref'])
                interactive_map[item['ref']] = False
            return visible_refs, interactive_map

        # Process batch results
        for item in batch_elements:
            ref = item['ref']
            info = batch_results.get(ref)

            if info is None:
                if check_viewport:
                    # Can't verify viewport position — exclude from viewport-only modes
                    interactive_map[ref] = False
                    continue
                # Full-page mode: include with suffix-based interactivity
                visible_refs.add(ref)
                suffix = ref_suffixes.get(ref, '')
                role = refs_info[ref][0]
                is_interactive = role.lower() in self.INTERACTIVE_ROLES
                if not is_interactive and '[cursor=pointer]' in suffix:
                    is_interactive = True
                elif not is_interactive and any(
                    state in suffix for state in ['[pressed', '[expanded', '[checked', '[selected']
                ):
                    is_interactive = True
                interactive_map[ref] = is_interactive
                continue

            # Viewport check
            if check_viewport:
                rect = info['rect']
                in_viewport = not (
                    rect['right'] < 0 or rect['x'] > viewport_width or
                    rect['bottom'] < 0 or rect['y'] > viewport_height
                )
                if not in_viewport:
                    interactive_map[ref] = False
                    continue

            visible_refs.add(ref)

            # Interactive check
            role = refs_info[ref][0]
            suffix = ref_suffixes.get(ref, '')
            is_interactive = self._is_element_interactive(role, info, suffix)

            # Disabled interactive roles still included
            if not is_interactive and role.lower() in self.INTERACTIVE_ROLES:
                if info.get('isDisabled') or info.get('ariaDisabled'):
                    is_interactive = True

            interactive_map[ref] = is_interactive

        return visible_refs, interactive_map

    def _is_element_interactive(
        self,
        role: str,
        info: Dict[str, Any],
        snapshot_suffix: str = ""
    ) -> bool:
        """Determine if an element is interactive.

        Based on Section 5 of INTERACTIVE_ELEMENTS.md:
        Any single rule being True means the element is interactive.

        Also references WAI-ARIA 1.2 specification (docs/INTERACTIVE_ELEMENTS.md).

        Parameters
        ----------
        role : str
            ARIA role of the element.
        info : Dict[str, Any]
            Element info from _get_element_interactive_info().
        snapshot_suffix : str, optional
            Suffix from snapshot line (for ARIA state detection).

        Returns
        -------
        bool
            True if element is interactive, False otherwise.
        """
        role_lower = role.lower() if role else ''

        # 0. Disabled/Hidden check - Directly exclude (Section 4)
        if info.get('isDisabled'):
            # Note: Disabled elements are still shown in output but not "interactive"
            return False

        # Check aria-hidden
        if info.get('ariaHidden'):
            return False

        # 1. Tag check (Section 5.1)
        if info.get('tagName') in self.INTERACTIVE_TAGS:
            return True

        # 2. Event attribute check (Section 5.2)
        if info.get('hasEventHandler'):
            return True

        # 3. tabindex check - Focusable (Section 5.2)
        tabindex = info.get('tabindex')
        is_focusable = False
        if tabindex is not None:
            try:
                if int(tabindex) >= 0:
                    is_focusable = True
            except ValueError:
                pass

        # Directly interactive via tabindex
        if is_focusable:
            return True

        # 4. Search-related check (Section 5.3)
        class_and_id = info.get('classAndId', '')
        if any(keyword in class_and_id for keyword in self.SEARCH_KEYWORDS):
            return True

        # 5. ARIA role check (Section 5.4 + WAI-ARIA 1.2)
        # Special handling for separator: only interactive when focusable
        if role_lower == 'separator':
            return is_focusable

        if role_lower in self.INTERACTIVE_ROLES:
            return True

        # 6. ARIA state check (Section 5.5)
        # These state attributes indicate interactive widget controls
        if snapshot_suffix:
            aria_states = ['[pressed', '[expanded', '[checked', '[selected']
            if any(state in snapshot_suffix for state in aria_states):
                return True

        # 7. focusable/editable/settable check (Section 5.5)
        if info.get('isEditable') or info.get('isContentEditable'):
            return True

        # 8. Control attribute check (Section 5.5)
        if info.get('ariaRequired') or info.get('ariaAutocomplete') or info.get('ariaKeyshortcuts'):
            return True

        # 9. Small icon check (Section 5.7)
        # Size within 10-50px range + has semantic attributes
        width = info.get('width', 0)
        height = info.get('height', 0)
        if 10 <= width <= 50 and 10 <= height <= 50:
            # Check for semantic attributes: class, role, data-action, aria-label
            # Note: hasEventHandler already checked in rule 2, dataAction implies interactivity
            has_semantic = (
                info.get('classAndId') or  # Contains class and id
                info.get('dataAction') or  # data-action
                '[aria-label' in (snapshot_suffix or '')  # aria-label
            )
            if has_semantic:
                return True

        # 10. cursor=pointer fallback (Section 5.8)
        if info.get('cursor') == 'pointer':
            return True
        # Also check cursor=pointer in snapshot
        if '[cursor=pointer]' in (snapshot_suffix or ''):
            return True

        return False

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
        options: SnapshotOptions,
        interactive_map: Optional[Dict[str, bool]] = None
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
        interactive_map : Optional[Dict[str, bool]]
            Mapping of ref -> is_interactive from _pre_filter_raw_snapshot().
            Used for precise filtering in interactive mode.

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

        line_pattern = self._LINE_PATTERN
        ref_pattern = self._REF_CLEAN_PATTERN
        ref_extract_pattern = self._REF_EXTRACT_PATTERN

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
                stripped = line.lstrip()

                # In interactive-only mode, skip text nodes but keep metadata
                if options.interactive:
                    # Only keep metadata lines (like /url:, /placeholder:) for kept parents
                    if stripped.startswith('- /'):
                        has_kept_parent = any(kept for _, kept, _ in depth_stack)
                        if has_kept_parent:
                            eff_depth = get_effective_depth(original_depth)
                            content = stripped[2:]
                            new_line = '  ' * eff_depth + '- ' + content
                            result.append(new_line)
                    # Skip all other non-standard lines (text nodes) in interactive mode
                    continue

                # Non-interactive mode: keep text and metadata if there's a kept parent
                has_kept_parent = any(kept for _, kept, _ in depth_stack)
                if has_kept_parent or not depth_stack:
                    # Calculate effective depth and re-indent
                    eff_depth = get_effective_depth(original_depth)
                    # Extract content after the "- " prefix
                    if stripped.startswith('- '):
                        content = stripped[2:]
                        new_line = '  ' * eff_depth + '- ' + content
                    else:
                        # Text content or other format
                        new_line = '  ' * eff_depth + stripped
                    result.append(new_line)
                continue

            _, role, name, suffix = match.groups()
            # Use inline label (after colon) as name when no quoted name — consistent with _extract_original_refs_from_raw
            if not name and suffix and ':' in suffix:
                inline_label_match = re.search(
                    r':\s*(?:"((?:[^"\\]|\\.)*)"|([^\n]+))\s*$', suffix
                )
                if inline_label_match:
                    name = (inline_label_match.group(1) or inline_label_match.group(2) or '').strip() or None
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
            # Use interactive_map for precise filtering when available
            if options.interactive:
                # Try to get ref from suffix for interactive_map lookup
                ref_match_in_suffix = ref_extract_pattern.search(suffix) if suffix else None
                original_ref = ref_match_in_suffix.group(1) if ref_match_in_suffix else None

                if interactive_map and original_ref:
                    # Use the pre-computed interactive_map for precise filtering
                    is_effectively_interactive = interactive_map.get(original_ref, False)
                else:
                    # Fallback to basic role/attribute checks
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
                # In interactive mode, skip filtered elements entirely (no inline text)
                # In non-interactive mode, preserve inline text from filtered elements
                if not options.interactive:
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

                # Extract inline text content for unnamed elements
                text_content = None
                if not name and clean_suffix and ':' in clean_suffix:
                    text_match = re.search(r':\s*"?([^"]+)"?\s*$', clean_suffix)
                    if text_match:
                        text_content = text_match.group(1).strip()

                refs[ref] = RefData(
                    selector=self._build_selector(role_lower, name, text_content),
                    role=role_lower,
                    name=name,
                    nth=nth,
                    text_content=text_content,
                )

                enhanced += f" [ref={ref}]"
                # Only show nth for named elements with duplicates
                # For unnamed elements, ref alone is sufficient for identification
                if nth > 0 and name:
                    enhanced += f" [nth={nth}]"

            # Re-add clean suffix (like [level=1] for headings, or trailing colon)
            if clean_suffix:
                # If name matches inline text, suppress duplication to save tokens
                # e.g. generic "Username" [ref=e14]: Username → generic "Username" [ref=e14]
                # e.g. generic "Item 1" [ref=eNN] [cursor=pointer]: Item 1 → [cursor=pointer]
                if name and ':' in clean_suffix:
                    colon_match = re.search(r':\s*(.+?)\s*$', clean_suffix)
                    if colon_match:
                        raw_text = colon_match.group(1).strip()
                        # Strip outer quotes to get inline text
                        if raw_text.startswith('"') and raw_text.endswith('"'):
                            raw_text = raw_text[1:-1]
                        # Compare with name (both may contain escaped quotes like \")
                        if raw_text == name:
                            clean_suffix = clean_suffix[:colon_match.start()].rstrip()

                if clean_suffix == ':':
                    enhanced += ':'
                elif clean_suffix.startswith(':'):
                    # Inline content like ": some text" - append directly without extra space
                    enhanced += clean_suffix
                elif clean_suffix.endswith(':'):
                    enhanced += f" {clean_suffix[:-1]}:"
                elif clean_suffix:
                    enhanced += f" {clean_suffix}"

            result.append(enhanced)

        # Post-process: remove nth from refs that don't have duplicates
        self._remove_nth_from_non_duplicates(refs, tracker)

        return '\n'.join(result)

    def _extract_original_refs_from_raw(
        self, raw_snapshot: str
    ) -> Tuple[Dict[str, Tuple[str, Optional[str], int]], Dict[str, str]]:
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
        Tuple[Dict[str, Tuple[str, Optional[str], int]], Dict[str, str]]
            - refs_info: Mapping of ``ref -> (role, name, nth_index)``
            - ref_suffixes: Mapping of ``ref -> suffix`` (from [ref=...] to end of line)
        """
        refs_info: Dict[str, Tuple[str, Optional[str], int]] = {}
        ref_suffixes: Dict[str, str] = {}
        role_name_counts: Dict[str, int] = {}

        line_pattern = self._REF_LINE_PATTERN

        for line in raw_snapshot.split('\n'):
            match = line_pattern.match(line)
            if match:
                groups = match.groups()
                role = groups[0]
                name = groups[1]  # quoted name before ref (e.g. "Go to Form Section")
                suffix = groups[2]  # everything from [ref=...] to end of line
                ref = groups[3]

                # Extract inline label from suffix if no quoted name before ref
                if not name and suffix and ':' in suffix:
                    inline_label_match = re.search(
                        r':\s*(?:"((?:[^"\\]|\\.)*)"|([^\n]+))\s*$', suffix
                    )
                    if inline_label_match:
                        name = (inline_label_match.group(1) or inline_label_match.group(2) or '').strip() or None

                role_lower = role.lower()

                # Track nth index for role+name combination
                key = f"{role_lower}:{name or ''}"
                nth = role_name_counts.get(key, 0)
                role_name_counts[key] = nth + 1

                refs_info[ref] = (role_lower, name if name else None, nth)
                ref_suffixes[ref] = suffix or ''

        return refs_info, ref_suffixes

    async def _pre_filter_raw_snapshot(
        self,
        raw_snapshot: str,
        page: AsyncPage,
        options: SnapshotOptions
    ) -> Tuple[str, Dict[str, bool]]:
        """Pre-filter raw snapshot and check interactivity of elements.

        This method runs BEFORE the main processing to filter out elements that
        don't need to be processed at all, improving performance significantly.

        Filter Strategy:
        1. Extract all refs from raw snapshot with their role/name/nth info
        2. Run parallel visibility and interactivity checks on all ref'd elements
        3. Build a set of visible refs and an interactive map
        4. Filter snapshot lines, skipping out-of-viewport elements and their children

        IMPORTANT: When an element is marked invisible, ALL its children are also
        skipped, regardless of their individual visibility. This matches the
        expectation that content inside a hidden container is not accessible.

        Performance Note:
        Visibility and interactivity checks are run in parallel using asyncio.gather()
        for better performance on pages with many elements.

        Parameters
        ----------
        raw_snapshot : str
            Raw output from `page_snapshot_for_ai()`.
        page : playwright.async_api.Page
            Playwright page object for visibility and interactivity checking.
        options : SnapshotOptions
            Snapshot options with `full_page` and `interactive` settings.

        Returns
        -------
        Tuple[str, Dict[str, bool]]
            - Filtered raw snapshot string
            - ref -> is_interactive mapping (only populated when options.interactive is True)
        """
        interactive_map: Dict[str, bool] = {}

        # If no filtering needed and not interactive mode, return as-is
        if options.full_page and not options.interactive:
            return raw_snapshot, interactive_map

        # Extract all original refs with their role/name/nth and suffixes in one pass
        refs_info, ref_suffixes = self._extract_original_refs_from_raw(raw_snapshot)

        if not refs_info:
            return raw_snapshot, interactive_map

        # Pre-fetch viewport size once for efficiency
        viewport = page.viewport_size
        viewport_width = viewport['width'] if viewport else None
        viewport_height = viewport['height'] if viewport else None
        check_viewport = not options.full_page and viewport_width is not None

        # Batch check all elements in a single page.evaluate() call
        visible_refs, interactive_map = await self._batch_get_elements_info(
            page, refs_info, ref_suffixes,
            check_viewport=check_viewport,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )

        logger.debug(f"Pre-filter: {len(visible_refs)}/{len(refs_info)} refs visible/in-viewport")
        if options.interactive:
            interactive_count = sum(1 for v in interactive_map.values() if v)
            logger.debug(f"Interactive check: {interactive_count}/{len(interactive_map)} refs interactive")

        # If all refs are in-viewport (or no viewport filtering is active), return as-is
        if len(visible_refs) == len(refs_info):
            return raw_snapshot, interactive_map

        # Filter the raw snapshot
        lines = raw_snapshot.split('\n')
        result: List[str] = []

        # Track invisible parent depths to skip children
        invisible_depth: Optional[int] = None

        ref_extract_pattern = self._REF_EXTRACT_PATTERN

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
            ref_match = ref_extract_pattern.search(line)
            if ref_match:
                ref = ref_match.group(1)
                if ref not in visible_refs:
                    # This element is invisible/out-of-viewport, mark depth to skip children
                    invisible_depth = depth
                    continue

            result.append(line)

        return '\n'.join(result), interactive_map

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

        logger.debug("Raw snapshot length: %d chars", len(raw_snapshot))

        # Pre-filter out-of-viewport elements and get interactive map
        filtered_snapshot, interactive_map = await self._pre_filter_raw_snapshot(
            raw_snapshot, page, options
        )

        logger.debug("Filtered snapshot length: %d chars", len(filtered_snapshot))

        enhanced_tree = self._process_page_snapshot_for_ai(
            filtered_snapshot, refs, options, interactive_map
        )

        logger.debug("Enhanced tree length: %d chars", len(enhanced_tree))

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
        2. Pre-filter out-of-viewport elements (when full_page=False)
        3. Process and enhance the tree with element refs
        4. Return EnhancedSnapshot with tree text and refs dictionary

        Options:
        - interactive: Only include interactive elements (flattened output)
        - full_page: Include all elements regardless of viewport (default: True)

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
        if ref_data.role in self.STRUCTURAL_NOISE_ROLES and (ref_data.name or ref_data.text_content):
            # get_by_role('generic') doesn't match plain <div> elements in Playwright,
            # so use get_by_text for structural noise roles (generic, group, etc.)
            text = ref_data.name or ref_data.text_content
            if text and text.strip():
                locator = page.get_by_text(text.strip(), exact=True)
            else:
                # Empty/whitespace text would over-match with get_by_text(""), fallback conservatively.
                locator = page.get_by_role(ref_data.role)
        elif ref_data.role in self.ROLE_TEXT_MATCH_ROLES and (ref_data.name or ref_data.text_content):
            # Some structural roles can expose text in snapshot names that does not
            # reliably map to Playwright role accessible-name matching.
            # Use role-constrained text filtering to avoid broad get_by_text.
            text = ref_data.name or ref_data.text_content
            if not text or not text.strip():
                locator = page.get_by_role(ref_data.role)
            elif ref_data.role == 'row':
                normalized_text = text.strip()
                # Rows are composite containers: snapshot name may come from a child
                # cell/header, not the row's full text. Keep both primary and fallback
                # locators constrained to role='row' to avoid matching child elements.
                row_locator = page.get_by_role('row').and_(
                    page.get_by_text(normalized_text, exact=True)
                )
                row_text_pattern = re.compile(re.escape(normalized_text))
                locator = row_locator.or_(
                    page.get_by_role('row').filter(has_text=row_text_pattern)
                )
            else:
                normalized_text = text.strip()
                text_pattern = re.compile(rf'^\s*{re.escape(normalized_text)}\s*$')
                locator = page.get_by_role(ref_data.role).filter(has_text=text_pattern)
        elif ref_data.name:
            locator = page.get_by_role(ref_data.role, name=ref_data.name, exact=True)
        elif ref_data.text_content:
            locator = page.get_by_text(ref_data.text_content, exact=True)
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
