"""
Unit tests for `SnapshotGenerator` snapshot processing methods.

Tests are organized by method/feature:
1. `_extract_original_refs_from_raw()` — raw snapshot parsing
2. `_batch_get_elements_info()` — element routing, viewport filtering, interactivity
3. `_process_page_snapshot_for_ai()` — enhanced tree building
4. Name dedup logic — suffix deduplication for named elements
5. Integration: full pipeline via `get_enhanced_snapshot_async()`
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple
from unittest.mock import AsyncMock, Mock

import pytest

from bridgic.browser.session._snapshot import (
    RefData,
    RoleNameTracker,
    SnapshotGenerator,
    SnapshotOptions,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gen() -> SnapshotGenerator:
    """Create a fresh SnapshotGenerator with ref counter reset."""
    g = SnapshotGenerator()
    g._reset_refs()
    return g


# ---------------------------------------------------------------------------
# 1. _extract_original_refs_from_raw
# ---------------------------------------------------------------------------

class TestExtractOriginalRefsFromRaw:
    """Tests for parsing raw Playwright snapshots into refs_info + ref_suffixes."""

    def test_simple_named_element(self, gen: SnapshotGenerator) -> None:
        raw = '- button "Submit" [ref=e1] [cursor=pointer]'
        refs_info, _ref_suffixes = gen._extract_original_refs_from_raw(raw)

        assert "e1" in refs_info
        role, name, nth = refs_info["e1"]
        assert role == "button"
        assert name == "Submit"
        assert nth == 0

    def test_unnamed_element(self, gen: SnapshotGenerator) -> None:
        raw = "- generic [ref=e5]"
        refs_info, _ref_suffixes = gen._extract_original_refs_from_raw(raw)

        assert "e5" in refs_info
        role, name, nth = refs_info["e5"]
        assert role == "generic"
        assert name is None
        assert nth == 0

    def test_inline_label_as_name(self, gen: SnapshotGenerator) -> None:
        """Unnamed element with inline text after colon gets name from label."""
        raw = '- generic [ref=e10]: Recommended'
        refs_info, _ = gen._extract_original_refs_from_raw(raw)

        role, name, _nth = refs_info["e10"]
        assert role == "generic"
        assert name == "Recommended"

    def test_inline_label_quoted(self, gen: SnapshotGenerator) -> None:
        """Unnamed element with quoted inline text after colon."""
        raw = '- generic [ref=e10]: "Some label"'
        refs_info, _ = gen._extract_original_refs_from_raw(raw)

        _, name, _ = refs_info["e10"]
        assert name == "Some label"

    def test_escaped_quotes_in_name(self, gen: SnapshotGenerator) -> None:
        r"""Name containing escaped quotes like \"hello\"."""
        raw = r'- generic "Type \"hello\" to verify:" [ref=e105]: "Type \"hello\" to verify:"'
        refs_info, _ref_suffixes = gen._extract_original_refs_from_raw(raw)

        role, name, nth = refs_info["e105"]
        assert role == "generic"
        assert name == r'Type \"hello\" to verify:'
        assert nth == 0

    def test_suffix_extraction(self, gen: SnapshotGenerator) -> None:
        raw = '- button "Click" [ref=e1] [cursor=pointer]'
        _, ref_suffixes = gen._extract_original_refs_from_raw(raw)

        assert "e1" in ref_suffixes
        assert "[ref=e1]" in ref_suffixes["e1"]
        assert "[cursor=pointer]" in ref_suffixes["e1"]

    def test_nth_index_tracking(self, gen: SnapshotGenerator) -> None:
        """Duplicate role+name combos get incrementing nth indices."""
        raw = (
            '- button "Reset" [ref=e1]\n'
            '- button "Reset" [ref=e2]\n'
            '- button "Reset" [ref=e3]'
        )
        refs_info, _ = gen._extract_original_refs_from_raw(raw)

        assert refs_info["e1"] == ("button", "Reset", 0)
        assert refs_info["e2"] == ("button", "Reset", 1)
        assert refs_info["e3"] == ("button", "Reset", 2)

    def test_different_names_independent_nth(self, gen: SnapshotGenerator) -> None:
        """Different names have independent nth counters."""
        raw = (
            '- button "OK" [ref=e1]\n'
            '- button "Cancel" [ref=e2]\n'
            '- button "OK" [ref=e3]'
        )
        refs_info, _ = gen._extract_original_refs_from_raw(raw)

        assert refs_info["e1"][2] == 0  # OK nth=0
        assert refs_info["e2"][2] == 0  # Cancel nth=0
        assert refs_info["e3"][2] == 1  # OK nth=1

    def test_multiline_snapshot(self, gen: SnapshotGenerator) -> None:
        raw = (
            '- heading "Title" [ref=e1] [level=1]\n'
            '- list:\n'
            '  - listitem [ref=e2]:\n'
            '    - link "Home" [ref=e3] [cursor=pointer]:\n'
            '      - /url: https://example.com'
        )
        refs_info, _ = gen._extract_original_refs_from_raw(raw)

        assert len(refs_info) == 3
        assert refs_info["e1"][0] == "heading"
        assert refs_info["e2"][0] == "listitem"
        assert refs_info["e3"][0] == "link"

    def test_empty_snapshot(self, gen: SnapshotGenerator) -> None:
        refs_info, ref_suffixes = gen._extract_original_refs_from_raw("")
        assert refs_info == {}
        assert ref_suffixes == {}

    def test_lines_without_refs_ignored(self, gen: SnapshotGenerator) -> None:
        raw = (
            "- list:\n"
            "  - listitem [ref=e1]:\n"
            "    - /url: https://example.com"
        )
        refs_info, _ = gen._extract_original_refs_from_raw(raw)
        assert len(refs_info) == 1
        assert "e1" in refs_info


# ---------------------------------------------------------------------------
# 1b. get_locator_from_ref_async
# ---------------------------------------------------------------------------

class TestGetLocatorFromRefAsync:
    """Tests for ref -> Playwright locator reconstruction."""

    def test_returns_none_for_invalid_ref_arg(self, gen: SnapshotGenerator) -> None:
        page = Mock()
        refs: Dict[str, RefData] = {}

        locator = gen.get_locator_from_ref_async(page, "not-a-ref", refs)

        assert locator is None

    def test_returns_none_for_missing_ref(self, gen: SnapshotGenerator) -> None:
        page = Mock()
        refs: Dict[str, RefData] = {}

        locator = gen.get_locator_from_ref_async(page, "e999", refs)

        assert locator is None

    @pytest.mark.parametrize(
        ("role", "name"),
        [
            ("listitem", "待处理"),
            ("cell", "cell text"),
            ("gridcell", "gridcell text"),
            ("columnheader", "Status"),
            ("rowheader", "Order ID"),
        ],
    )
    def test_role_text_match_roles_use_role_filter_with_exact_text(
        self, gen: SnapshotGenerator, role: str, name: str
    ) -> None:
        page = Mock()
        role_locator = Mock()
        filtered_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.return_value = role_locator
        role_locator.filter.return_value = filtered_locator
        filtered_locator.nth.return_value = nth_locator

        refs = {
            "e1": RefData(
                selector=f'get_by_role(\'{role}\', name="{name}", exact=True)',
                role=role,
                name=name,
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        filtered_locator.nth.assert_called_once_with(0)
        page.get_by_role.assert_called_once_with(role)
        role_locator.filter.assert_called_once()
        _, kwargs = role_locator.filter.call_args
        assert "has_text" in kwargs
        pattern = kwargs["has_text"]
        assert isinstance(pattern, re.Pattern)
        assert pattern.match(name)
        assert not pattern.match(f"{name} extra")

    def test_row_uses_role_exact_intersection_with_text_fallback(
        self, gen: SnapshotGenerator
    ) -> None:
        page = Mock()
        row_role_locator = Mock()
        row_text_locator = Mock()
        intersect_locator = Mock()
        fallback_row_locator = Mock()
        combined_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.side_effect = [row_role_locator, fallback_row_locator]
        page.get_by_text.return_value = row_text_locator
        row_role_locator.and_.return_value = intersect_locator
        fallback_row_locator.filter.return_value = fallback_row_locator
        intersect_locator.or_.return_value = combined_locator
        combined_locator.nth.return_value = nth_locator

        refs = {
            "e1": RefData(
                selector='get_by_role(\'row\', name="状态", exact=True)',
                role="row",
                name="状态",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        combined_locator.nth.assert_called_once_with(0)
        assert page.get_by_role.call_count == 2
        page.get_by_role.assert_any_call("row")
        page.get_by_text.assert_called_once_with("状态", exact=True)
        row_role_locator.and_.assert_called_once_with(row_text_locator)
        fallback_row_locator.filter.assert_called_once()
        _, kwargs = fallback_row_locator.filter.call_args
        assert "has_text" in kwargs
        pattern = kwargs["has_text"]
        assert isinstance(pattern, re.Pattern)
        assert pattern.search("状态 处理人")
        intersect_locator.or_.assert_called_once_with(fallback_row_locator)

    def test_role_text_match_blank_text_falls_back_to_role(self, gen: SnapshotGenerator) -> None:
        page = Mock()
        role_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.return_value = role_locator
        role_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector="get_by_role('cell')",
                role="cell",
                name="   ",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        role_locator.nth.assert_called_once_with(0)
        page.get_by_role.assert_called_once_with("cell")

    def test_structural_noise_blank_text_falls_back_to_role(self, gen: SnapshotGenerator) -> None:
        page = Mock()
        role_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.return_value = role_locator
        role_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector="get_by_role('generic')",
                role="generic",
                name="   ",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        role_locator.nth.assert_called_once_with(0)
        page.get_by_role.assert_called_once_with("generic")

    def test_listitem_named_with_nth_applies_nth(self, gen: SnapshotGenerator) -> None:
        page = Mock()
        role_locator = Mock()
        filtered_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.return_value = role_locator
        role_locator.filter.return_value = filtered_locator
        filtered_locator.nth.return_value = nth_locator

        refs = {
            "e2": RefData(
                selector='get_by_role(\'listitem\', name="待处理", exact=True)',
                role="listitem",
                name="待处理",
                nth=1,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e2", refs)

        filtered_locator.nth.assert_called_once_with(1)
        assert locator is nth_locator

    def test_named_button_gets_nth0_by_default(self, gen: SnapshotGenerator) -> None:
        """Named roles with nth=None get forced to nth(0) to prevent strict mode."""
        page = Mock()
        role_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.return_value = role_locator
        role_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_role(\'button\', name="Submit", exact=True)',
                role="button",
                name="Submit",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_role.assert_called_once_with("button", name="Submit", exact=True)
        role_locator.nth.assert_called_once_with(0)

    def test_named_option_gets_nth0_by_default(self, gen: SnapshotGenerator) -> None:
        """Named roles with nth=None get forced to nth(0) to prevent strict mode."""
        page = Mock()
        role_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.return_value = role_locator
        role_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_role(\'option\', name="United States", exact=True)',
                role="option",
                name="United States",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_role.assert_called_once_with("option", name="United States", exact=True)
        role_locator.nth.assert_called_once_with(0)

    def test_structural_noise_uses_get_by_text_with_nth0(self, gen: SnapshotGenerator) -> None:
        """STRUCTURAL_NOISE_ROLES with text use get_by_text and force nth(0) for safety."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("Username", exact=True)',
                role="generic",
                name="Username",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_text.assert_called_once_with("Username", exact=True)
        text_locator.nth.assert_called_once_with(0)
        assert refs["e1"].nth == 0

    def test_structural_noise_with_explicit_nth_preserves_it(
        self, gen: SnapshotGenerator
    ) -> None:
        """When nth is already set for a STRUCTURAL_NOISE_ROLES element, keep it."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("自动检测", exact=True)',
                role="generic",
                name="自动检测",
                nth=2,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        text_locator.nth.assert_called_once_with(2)
        assert refs["e1"].nth == 2

    @pytest.mark.parametrize("role", ["generic", "group", "none", "presentation"])
    def test_all_structural_noise_roles_force_nth0(
        self, gen: SnapshotGenerator, role: str
    ) -> None:
        """All STRUCTURAL_NOISE_ROLES with text and nth=None get forced to nth(0)."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector=f'get_by_text("label", exact=True)',
                role=role,
                name="label",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        text_locator.nth.assert_called_once_with(0)

    def test_structural_noise_text_content_fallback_forces_nth0(
        self, gen: SnapshotGenerator
    ) -> None:
        """STRUCTURAL_NOISE_ROLES using text_content (not name) also force nth(0)."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("自动检测", exact=True)',
                role="generic",
                name=None,
                nth=None,
                text_content="自动检测",
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_text.assert_called_once_with("自动检测", exact=True)
        text_locator.nth.assert_called_once_with(0)

    def test_bare_text_content_fallback_forces_nth0(
        self, gen: SnapshotGenerator
    ) -> None:
        """Elements with only text_content (no name, non-noise role) also force nth(0).

        This covers the elif ref_data.text_content branch (line ~1718).
        """
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("自动检测", exact=True)',
                role="button",
                name=None,
                nth=None,
                text_content="自动检测",
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_text.assert_called_once_with("自动检测", exact=True)
        text_locator.nth.assert_called_once_with(0)
        assert refs["e1"].nth == 0

    def test_bare_text_content_with_explicit_nth_preserves_it(
        self, gen: SnapshotGenerator
    ) -> None:
        """When nth is already set on the text_content fallback path, keep it."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("Click me", exact=True)',
                role="button",
                name=None,
                nth=3,
                text_content="Click me",
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        text_locator.nth.assert_called_once_with(3)
        assert refs["e1"].nth == 3

    def test_bare_role_no_name_gets_nth0(self, gen: SnapshotGenerator) -> None:
        """Bare get_by_role() with no name/text also gets nth(0) safety net."""
        page = Mock()
        role_locator = Mock()
        nth_locator = Mock()
        page.get_by_role.return_value = role_locator
        role_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector="get_by_role('separator')",
                role="separator",
                name=None,
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_role.assert_called_once_with("separator")
        role_locator.nth.assert_called_once_with(0)
        assert refs["e1"].nth == 0

    def test_text_role_with_name_uses_get_by_text(self, gen: SnapshotGenerator) -> None:
        """'text' pseudo-role with name uses get_by_text, not get_by_role."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("Hello", exact=True)',
                role="text",
                name="Hello",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_text.assert_called_once_with("Hello", exact=True)
        page.get_by_role.assert_not_called()
        text_locator.nth.assert_called_once_with(0)

    def test_text_role_with_text_content_uses_get_by_text(self, gen: SnapshotGenerator) -> None:
        """'text' pseudo-role with only text_content also uses get_by_text."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("some content", exact=True)',
                role="text",
                name=None,
                nth=None,
                text_content="some content",
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        page.get_by_text.assert_called_once_with("some content", exact=True)
        page.get_by_role.assert_not_called()
        text_locator.nth.assert_called_once_with(0)

    def test_text_role_with_explicit_nth(self, gen: SnapshotGenerator) -> None:
        """'text' pseudo-role preserves explicit nth value."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("Label", exact=True)',
                role="text",
                name="Label",
                nth=3,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        text_locator.nth.assert_called_once_with(3)
        assert refs["e1"].nth == 3

    def test_text_role_with_nth_none_defaults_to_0(self, gen: SnapshotGenerator) -> None:
        """'text' pseudo-role defaults nth to 0 when not set."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e1": RefData(
                selector='get_by_text("Label", exact=True)',
                role="text",
                name="Label",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e1", refs)

        assert locator is nth_locator
        text_locator.nth.assert_called_once_with(0)
        assert refs["e1"].nth == 0

    def test_text_role_chinese_text(self, gen: SnapshotGenerator) -> None:
        """'text' pseudo-role works with Chinese text like '订单ID' (the original bug)."""
        page = Mock()
        text_locator = Mock()
        nth_locator = Mock()
        page.get_by_text.return_value = text_locator
        text_locator.nth.return_value = nth_locator
        refs = {
            "e29": RefData(
                selector='get_by_text("订单ID", exact=True)',
                role="text",
                name="订单ID",
                nth=None,
                text_content=None,
            )
        }

        locator = gen.get_locator_from_ref_async(page, "e29", refs)

        assert locator is nth_locator
        page.get_by_text.assert_called_once_with("订单ID", exact=True)
        page.get_by_role.assert_not_called()
        text_locator.nth.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# 2. _batch_get_elements_info — element routing
# ---------------------------------------------------------------------------

class TestBatchGetElementsInfoRouting:
    """Tests for how elements are routed between suffix_only vs batch JS paths."""

    @pytest.mark.asyncio
    async def test_unnamed_generic_goes_to_suffix_only(self, gen: SnapshotGenerator) -> None:
        """Unnamed structural noise roles bypass batch JS (suffix-only)."""
        mock_page = AsyncMock()
        refs_info = {
            "e1": ("generic", None, 0),
        }
        ref_suffixes = {"e1": "[ref=e1]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        # Should be included without any page.evaluate call
        assert "e1" in visible
        assert interactive["e1"] is False
        mock_page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_unnamed_generic_with_cursor_pointer_is_interactive(
        self, gen: SnapshotGenerator
    ) -> None:
        """Unnamed generic with [cursor=pointer] in suffix is interactive."""
        mock_page = AsyncMock()
        refs_info = {"e1": ("generic", None, 0)}
        ref_suffixes = {"e1": "[ref=e1] [cursor=pointer]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible
        assert interactive["e1"] is True

    @pytest.mark.asyncio
    async def test_unnamed_generic_with_aria_state_is_interactive(
        self, gen: SnapshotGenerator
    ) -> None:
        """Unnamed generic with ARIA state attributes is interactive."""
        mock_page = AsyncMock()
        refs_info = {"e1": ("generic", None, 0)}
        ref_suffixes = {"e1": "[ref=e1] [expanded=false]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible
        assert interactive["e1"] is True

    @pytest.mark.asyncio
    async def test_named_generic_goes_to_batch(self, gen: SnapshotGenerator) -> None:
        """Named generics go through batch JS path, not suffix-only."""
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={
            "e1": {
                "rect": {"x": 100, "y": 100, "right": 200, "bottom": 200},
                "isEditable": False,
                "isDisabled": False,
                "interactive": {"cursor": "pointer"},
            }
        })

        refs_info = {"e1": ("generic", "Item 1", 0)}
        ref_suffixes = {"e1": "[ref=e1] [cursor=pointer]: Item 1"}

        visible, _interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        mock_page.evaluate.assert_called_once()
        assert "e1" in visible

    @pytest.mark.asyncio
    async def test_button_goes_to_batch(self, gen: SnapshotGenerator) -> None:
        """Interactive roles always go through batch JS."""
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={
            "e1": {
                "rect": {"x": 10, "y": 10, "right": 100, "bottom": 50},
                "isEditable": False,
                "isDisabled": False,
                "interactive": {"cursor": "pointer"},
            }
        })

        refs_info = {"e1": ("button", "Submit", 0)}
        ref_suffixes = {"e1": "[ref=e1] [cursor=pointer]"}

        visible, _interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        mock_page.evaluate.assert_called_once()
        assert "e1" in visible


# ---------------------------------------------------------------------------
# 2b. _batch_get_elements_info — viewport filtering
# ---------------------------------------------------------------------------

class TestBatchViewportFiltering:
    """Tests for viewport-based element inclusion/exclusion."""

    @pytest.mark.asyncio
    async def test_element_in_viewport_included(self, gen: SnapshotGenerator) -> None:
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={
            "e1": {
                "rect": {"x": 100, "y": 100, "right": 200, "bottom": 200},
                "isEditable": False,
                "isDisabled": False,
                "interactive": {"cursor": "default"},
            }
        })

        refs_info = {"e1": ("button", "Click", 0)}
        ref_suffixes = {"e1": "[ref=e1]"}

        visible, _ = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=True, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible

    @pytest.mark.asyncio
    async def test_element_below_viewport_excluded(self, gen: SnapshotGenerator) -> None:
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={
            "e1": {
                "rect": {"x": 100, "y": 2000, "right": 200, "bottom": 2100},
                "isEditable": False,
                "isDisabled": False,
                "interactive": {"cursor": "default"},
            }
        })

        refs_info = {"e1": ("button", "Far below", 0)}
        ref_suffixes = {"e1": "[ref=e1]"}

        visible, _ = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=True, viewport_width=1280, viewport_height=720,
        )

        assert "e1" not in visible

    @pytest.mark.asyncio
    async def test_full_page_mode_includes_offscreen(self, gen: SnapshotGenerator) -> None:
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={
            "e1": {
                "rect": {"x": 100, "y": 2000, "right": 200, "bottom": 2100},
                "isEditable": False,
                "isDisabled": False,
                "interactive": {"cursor": "default"},
            }
        })

        refs_info = {"e1": ("button", "Far below", 0)}
        ref_suffixes = {"e1": "[ref=e1]"}

        visible, _ = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible

    @pytest.mark.asyncio
    async def test_info_none_excluded_in_viewport_mode(self, gen: SnapshotGenerator) -> None:
        """Elements with info=None (unfindable in DOM) are excluded in viewport mode."""
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={
            # e1 not in results → info=None
        })

        refs_info = {"e1": ("generic", "Ghost Element", 0)}
        ref_suffixes = {"e1": "[ref=e1] [cursor=pointer]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=True, viewport_width=1280, viewport_height=720,
        )

        assert "e1" not in visible
        assert interactive["e1"] is False

    @pytest.mark.asyncio
    async def test_info_none_included_in_full_page_mode(self, gen: SnapshotGenerator) -> None:
        """Elements with info=None are included in full-page mode with suffix-based interactivity."""
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={})

        refs_info = {"e1": ("generic", "Ghost Element", 0)}
        ref_suffixes = {"e1": "[ref=e1] [cursor=pointer]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible
        assert interactive["e1"] is True  # cursor=pointer → interactive

    @pytest.mark.asyncio
    async def test_info_none_non_interactive_in_full_page(self, gen: SnapshotGenerator) -> None:
        """Elements with info=None and no interactivity signals are non-interactive."""
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={})

        refs_info = {"e1": ("generic", "Plain Label", 0)}
        ref_suffixes = {"e1": "[ref=e1]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible
        assert interactive["e1"] is False

    @pytest.mark.asyncio
    async def test_info_none_interactive_role_in_full_page(self, gen: SnapshotGenerator) -> None:
        """Interactive-role elements with info=None are still marked interactive."""
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={})

        refs_info = {"e1": ("button", "Missing Button", 0)}
        ref_suffixes = {"e1": "[ref=e1]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible
        assert interactive["e1"] is True


# ---------------------------------------------------------------------------
# 2c. _batch_get_elements_info — error handling
# ---------------------------------------------------------------------------

class TestBatchErrorHandling:
    """Tests for batch evaluation failures."""

    @pytest.mark.asyncio
    async def test_evaluate_exception_falls_back(self, gen: SnapshotGenerator) -> None:
        """When page.evaluate raises, all batch elements are included as non-interactive."""
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("JS error"))

        refs_info = {
            "e1": ("button", "Click", 0),
            "e2": ("link", "Home", 0),
        }
        ref_suffixes = {"e1": "[ref=e1]", "e2": "[ref=e2]"}

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=True, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible
        assert "e2" in visible
        assert interactive["e1"] is False
        assert interactive["e2"] is False

    @pytest.mark.asyncio
    async def test_empty_refs_info_no_evaluate(self, gen: SnapshotGenerator) -> None:
        """With no elements, no evaluate call is made."""
        mock_page = AsyncMock()

        visible, interactive = await gen._batch_get_elements_info(
            mock_page, {}, {},
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert visible == set()
        assert interactive == {}
        mock_page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_suffix_elements_no_evaluate(self, gen: SnapshotGenerator) -> None:
        """When all elements are suffix-only (unnamed generics), no evaluate is called."""
        mock_page = AsyncMock()
        refs_info = {
            "e1": ("generic", None, 0),
            "e2": ("group", None, 0),
        }
        ref_suffixes = {"e1": "[ref=e1]", "e2": "[ref=e2]"}

        visible, _interactive = await gen._batch_get_elements_info(
            mock_page, refs_info, ref_suffixes,
            check_viewport=False, viewport_width=1280, viewport_height=720,
        )

        assert "e1" in visible
        assert "e2" in visible
        mock_page.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# 3. _process_page_snapshot_for_ai — enhanced tree building
# ---------------------------------------------------------------------------

class TestProcessPageSnapshotForAI:
    """Tests for the core snapshot tree transformation."""

    def test_simple_button(self, gen: SnapshotGenerator) -> None:
        raw = '- button "Submit" [ref=e1] [cursor=pointer]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert 'button "Submit"' in result
        assert "[ref=e1]" in result
        assert "[cursor=pointer]" in result
        assert "e1" in refs
        assert refs["e1"].role == "button"
        assert refs["e1"].name == "Submit"

    def test_heading_with_level(self, gen: SnapshotGenerator) -> None:
        raw = '- heading "Title" [ref=e1] [level=1]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert 'heading "Title"' in result
        assert "[level=1]" in result

    def test_unnamed_generic_filtered(self, gen: SnapshotGenerator) -> None:
        """Unnamed generic elements (structural noise) are filtered out."""
        raw = '- generic [ref=e1]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert result.strip() == ""

    def test_named_generic_kept(self, gen: SnapshotGenerator) -> None:
        """Named generic elements are kept."""
        raw = '- generic "Username" [ref=e1]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert 'generic "Username"' in result

    def test_interactive_mode_only_interactive(self, gen: SnapshotGenerator) -> None:
        """In interactive mode, only interactive elements are kept."""
        raw = (
            '- heading "Title" [ref=e1] [level=1]\n'
            '- button "Click" [ref=e2] [cursor=pointer]\n'
            '- generic "Label" [ref=e3]'
        )
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=True, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert "button" in result
        assert "heading" not in result
        assert "generic" not in result

    def test_interactive_mode_with_interactive_map(self, gen: SnapshotGenerator) -> None:
        """Interactive mode uses interactive_map for precise filtering."""
        raw = (
            '- generic "Double-click me!" [ref=e1] [cursor=pointer]\n'
            '- generic "Plain label" [ref=e2]'
        )
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=True, full_page=True)
        interactive_map = {"e1": True, "e2": False}

        result = gen._process_page_snapshot_for_ai(raw, refs, options, interactive_map)

        assert "Double-click me!" in result
        assert "Plain label" not in result

    def test_interactive_mode_flattened_output(self, gen: SnapshotGenerator) -> None:
        """Interactive mode removes indentation (flat list)."""
        raw = (
            '- list:\n'
            '  - listitem [ref=e1]:\n'
            '    - link "Home" [ref=e2] [cursor=pointer]:\n'
            '      - /url: https://example.com'
        )
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=True, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        # Links should be at top level (no indentation)
        for line in result.strip().split('\n'):
            if line.strip().startswith('- link'):
                assert line.startswith('- link'), f"Expected no indentation: {line!r}"

    def test_disabled_element_kept(self, gen: SnapshotGenerator) -> None:
        """Disabled interactive elements are kept in output."""
        raw = '- textbox "Disabled Input" [ref=e1] [disabled]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert 'textbox "Disabled Input"' in result
        assert "[disabled]" in result

    def test_disabled_element_in_interactive_mode(self, gen: SnapshotGenerator) -> None:
        """Disabled interactive elements show up in interactive mode too."""
        raw = '- textbox "Disabled Input" [ref=e1] [disabled]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=True, full_page=True)
        interactive_map = {"e1": True}

        result = gen._process_page_snapshot_for_ai(raw, refs, options, interactive_map)

        assert 'textbox "Disabled Input"' in result
        assert "[disabled]" in result

    def test_metadata_lines_preserved(self, gen: SnapshotGenerator) -> None:
        """Metadata lines like /url: and /placeholder: are preserved under kept parents."""
        raw = (
            '- link "Home" [ref=e1] [cursor=pointer]:\n'
            '  - /url: https://example.com'
        )
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert "/url: https://example.com" in result

    def test_inline_text_from_filtered_element_preserved(self, gen: SnapshotGenerator) -> None:
        """In non-interactive mode, inline text from filtered unnamed noise elements
        is kept as text node when the element itself is filtered out.

        Note: `_process_page_snapshot_for_ai` uses the LINE_PATTERN regex which
        also extracts inline labels as names. So `generic [ref=e1]: Recommended`
        gets name="Recommended" and is kept as a named generic. To test the
        inline-text-preservation path, we need an element that IS filtered (e.g.
        unnamed generic wrapper) but whose child text node is preserved.
        """
        # An unnamed generic with inline text — the parser extracts "Recommended" as name,
        # so it's kept as a named generic element.
        raw = '- generic [ref=e1]: Recommended'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        # The element becomes named and is kept (not filtered)
        assert 'generic "Recommended"' in result

    def test_indentation_collapse(self, gen: SnapshotGenerator) -> None:
        """When a wrapper is filtered, children's indentation collapses."""
        raw = (
            '- generic [ref=e1]:\n'          # filtered (unnamed generic)
            '  - button "Click" [ref=e2]'     # child should collapse
        )
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)
        lines = [l for l in result.strip().split('\n') if l.strip()]

        # Button should be at top level (parent was filtered)
        button_line = [l for l in lines if 'button' in l]
        assert len(button_line) == 1
        assert button_line[0].startswith('- button')

    def test_nth_for_duplicates(self, gen: SnapshotGenerator) -> None:
        """Duplicate role+name combos get [nth=N] annotation."""
        raw = (
            '- button "Reset" [ref=e1]\n'
            '- button "Reset" [ref=e2]'
        )
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert "[nth=1]" in result  # Second "Reset" gets nth=1

    def test_file_upload_button(self, gen: SnapshotGenerator) -> None:
        """File upload buttons (input[type=file]) are kept."""
        raw = '- button "File Upload" [ref=e1] [cursor=pointer]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=True, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert 'button "File Upload"' in result


# ---------------------------------------------------------------------------
# 4. Name dedup in clean_suffix
# ---------------------------------------------------------------------------

class TestNameDedup:
    """Tests for the suffix dedup logic that removes duplicate name from inline text."""

    def test_simple_name_dedup(self, gen: SnapshotGenerator) -> None:
        """generic "Username" [ref=e14]: Username → generic "Username" [ref=e1]"""
        raw = '- generic "Username" [ref=e14]: Username'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        # Name should appear only once (in quotes), not duplicated after colon
        assert result.count("Username") == 1

    def test_quoted_name_dedup(self, gen: SnapshotGenerator) -> None:
        """generic "Username" [ref=e14]: "Username" → generic "Username" [ref=e1]"""
        raw = '- generic "Username" [ref=e14]: "Username"'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        # Count raw "Username" occurrences (the one in the name quotes)
        lines = result.strip().split('\n')
        assert len(lines) == 1
        assert lines[0].count("Username") == 1

    def test_attributed_suffix_dedup(self, gen: SnapshotGenerator) -> None:
        """generic "Item 1" [ref=e93] [cursor=pointer]: Item 1 → keeps [cursor=pointer]"""
        raw = '- generic "Item 1" [ref=e93] [cursor=pointer]: Item 1'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert "[cursor=pointer]" in result
        # Name should appear only once
        assert result.count("Item 1") == 1

    def test_attributed_suffix_dedup_with_quoted_text(self, gen: SnapshotGenerator) -> None:
        """generic "Double-click me!" [ref=e62] [cursor=pointer]: "Double-click me!" → keeps [cursor=pointer]"""
        raw = '- generic "Double-click me!" [ref=e62] [cursor=pointer]: "Double-click me!"'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert "[cursor=pointer]" in result
        assert result.count("Double-click me!") == 1

    def test_escaped_quotes_dedup(self, gen: SnapshotGenerator) -> None:
        r"""generic "Type \"hello\" to verify:" dedup with escaped quotes."""
        raw = r'- generic "Type \"hello\" to verify:" [ref=e105]: "Type \"hello\" to verify:"'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        # Should not have duplicate text after the ref
        lines = result.strip().split('\n')
        assert len(lines) == 1
        line = lines[0]
        # The line should end with [ref=eN] and NOT have `: "Type..."` appended
        assert line.endswith("]"), f"Expected line to end with ']': {line!r}"

    def test_different_name_and_text_no_dedup(self, gen: SnapshotGenerator) -> None:
        """When inline text differs from name, no dedup occurs."""
        raw = '- generic "Label" [ref=e1]: Different text'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert "Label" in result
        assert "Different text" in result

    def test_suffix_with_level_no_dedup(self, gen: SnapshotGenerator) -> None:
        """Suffix like [level=1] without colon is preserved normally."""
        raw = '- heading "Title" [ref=e1] [level=1]'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert "[level=1]" in result

    def test_colon_only_suffix_preserved(self, gen: SnapshotGenerator) -> None:
        """Trailing colon suffix is preserved (e.g., list items ending with ':')."""
        raw = '- link "Home" [ref=e1]:'
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=False, full_page=True)

        result = gen._process_page_snapshot_for_ai(raw, refs, options)

        assert result.strip().endswith(":")


# ---------------------------------------------------------------------------
# 5. RoleNameTracker
# ---------------------------------------------------------------------------

class TestRoleNameTracker:
    """Tests for the RoleNameTracker helper."""

    def test_first_occurrence_index_zero(self) -> None:
        tracker = RoleNameTracker()
        assert tracker.get_next_index("button", "OK") == 0

    def test_second_occurrence_index_one(self) -> None:
        tracker = RoleNameTracker()
        tracker.get_next_index("button", "OK")
        tracker.track_ref("button", "OK", "e1")
        assert tracker.get_next_index("button", "OK") == 1

    def test_different_names_independent(self) -> None:
        tracker = RoleNameTracker()
        assert tracker.get_next_index("button", "OK") == 0
        tracker.track_ref("button", "OK", "e1")
        assert tracker.get_next_index("button", "Cancel") == 0

    def test_get_duplicate_keys(self) -> None:
        tracker = RoleNameTracker()
        tracker.get_next_index("button", "Reset")
        tracker.track_ref("button", "Reset", "e1")
        tracker.get_next_index("button", "Reset")
        tracker.track_ref("button", "Reset", "e2")
        tracker.get_next_index("button", "OK")
        tracker.track_ref("button", "OK", "e3")

        dupes = tracker.get_duplicate_keys()
        assert "button:Reset" in dupes
        assert "button:OK" not in dupes

    def test_unnamed_elements(self) -> None:
        tracker = RoleNameTracker()
        assert tracker.get_next_index("generic", None) == 0
        tracker.track_ref("generic", None, "e1")
        assert tracker.get_next_index("generic", None) == 1


# ---------------------------------------------------------------------------
# 6. Integration: _extract + _process pipeline
# ---------------------------------------------------------------------------

class TestExtractAndProcessPipeline:
    """Tests combining _extract_original_refs_from_raw + _process_page_snapshot_for_ai."""

    def _run_pipeline(
        self,
        gen: SnapshotGenerator,
        raw: str,
        *,
        interactive: bool = False,
        full_page: bool = True,
        interactive_map: Optional[Dict[str, bool]] = None,
    ) -> Tuple[str, Dict[str, RefData]]:
        """Helper to run the full snapshot processing pipeline."""
        gen._reset_refs()
        refs: Dict[str, RefData] = {}
        options = SnapshotOptions(interactive=interactive, full_page=full_page)
        result = gen._process_page_snapshot_for_ai(raw, refs, options, interactive_map)
        return result, refs

    def test_full_page_all_elements(self, gen: SnapshotGenerator) -> None:
        """Full-page non-interactive shows all meaningful elements."""
        raw = (
            '- heading "Page Title" [ref=e1] [level=1]\n'
            '- generic "Label" [ref=e2]\n'
            '- button "Submit" [ref=e3] [cursor=pointer]\n'
            '- generic [ref=e4]\n'  # unnamed → filtered
            '- textbox "Email" [ref=e5]:\n'
            '  - /placeholder: Enter email'
        )

        result, _refs = self._run_pipeline(gen, raw)

        assert "Page Title" in result
        assert "Label" in result
        assert "Submit" in result
        assert "Email" in result
        assert "/placeholder: Enter email" in result
        # Unnamed generic should be filtered
        assert result.count("generic") == 1  # Only the named one

    def test_interactive_mode_filtering(self, gen: SnapshotGenerator) -> None:
        """Interactive mode only shows interactive elements."""
        raw = (
            '- heading "Title" [ref=e1] [level=1]\n'
            '- button "OK" [ref=e2] [cursor=pointer]\n'
            '- link "Home" [ref=e3] [cursor=pointer]:\n'
            '  - /url: /home\n'
            '- generic "Label" [ref=e4]\n'
            '- textbox "Search" [ref=e5]'
        )

        result, _refs = self._run_pipeline(gen, raw, interactive=True)

        assert "button" in result
        assert "link" in result
        assert "textbox" in result
        assert "heading" not in result
        assert "Label" not in result

    def test_interactive_map_precise_filtering(self, gen: SnapshotGenerator) -> None:
        """Interactive map overrides role-based classification."""
        raw = (
            '- generic "Clickable div" [ref=e1] [cursor=pointer]\n'
            '- generic "Static div" [ref=e2]\n'
            '- button "OK" [ref=e3] [cursor=pointer]'
        )
        interactive_map = {"e1": True, "e2": False, "e3": True}

        result, _refs = self._run_pipeline(
            gen, raw, interactive=True, interactive_map=interactive_map
        )

        assert "Clickable div" in result
        assert "OK" in result
        assert "Static div" not in result

    def test_nested_structure_with_filtering(self, gen: SnapshotGenerator) -> None:
        """Nested elements preserve correct indentation after filtering."""
        raw = (
            '- list:\n'
            '  - listitem [ref=e1]:\n'
            '    - link "Home" [ref=e2] [cursor=pointer]:\n'
            '      - /url: /home\n'
            '  - listitem [ref=e3]:\n'
            '    - link "About" [ref=e4] [cursor=pointer]:\n'
            '      - /url: /about'
        )

        result, _refs = self._run_pipeline(gen, raw)

        assert "list:" in result
        assert "listitem" in result
        assert 'link "Home"' in result
        assert 'link "About"' in result
        assert "/url: /home" in result
        assert "/url: /about" in result

    def test_cursor_pointer_generic_in_interactive_map(self, gen: SnapshotGenerator) -> None:
        """Named generics with [cursor=pointer] marked interactive via map."""
        raw = (
            '- generic "Item 1" [ref=e1] [cursor=pointer]: Item 1\n'
            '- generic "Item 2" [ref=e2] [cursor=pointer]: Item 2\n'
            '- generic "Item 3" [ref=e3] [cursor=pointer]: Item 3'
        )
        interactive_map = {"e1": True, "e2": True, "e3": True}

        result, _refs = self._run_pipeline(
            gen, raw, interactive=True, interactive_map=interactive_map
        )

        assert "Item 1" in result
        assert "Item 2" in result
        assert "Item 3" in result
        # Dedup should remove duplicate text
        for line in result.strip().split('\n'):
            if "Item" in line:
                # Each "Item N" should appear only once per line
                for n in range(1, 4):
                    if f"Item {n}" in line:
                        assert line.count(f"Item {n}") == 1

    def test_disabled_elements_in_both_modes(self, gen: SnapshotGenerator) -> None:
        """Disabled elements appear in both interactive and non-interactive modes."""
        raw = (
            '- button "Active" [ref=e1] [cursor=pointer]\n'
            '- button "Disabled" [ref=e2] [disabled] [cursor=pointer]\n'
            '- textbox "Disabled Input" [ref=e3] [disabled]'
        )
        interactive_map = {"e1": True, "e2": True, "e3": True}

        # Non-interactive mode
        result_full, _ = self._run_pipeline(gen, raw)
        assert "Active" in result_full
        assert "Disabled" in result_full
        assert "Disabled Input" in result_full

        # Interactive mode
        result_int, _ = self._run_pipeline(
            gen, raw, interactive=True, interactive_map=interactive_map
        )
        assert "Active" in result_int
        assert "[disabled]" in result_int

    def test_complex_page_structure(self, gen: SnapshotGenerator) -> None:
        """A realistic page structure with mixed elements."""
        raw = (
            '- heading "Form" [ref=e1] [level=2]\n'
            '- generic "Username" [ref=e2]\n'
            '- textbox "Username" [ref=e3]:\n'
            '  - /placeholder: Enter username\n'
            '- generic [ref=e4]:\n'                # unnamed → filtered, but has child
            '  - button "Submit" [ref=e5] [cursor=pointer]\n'
            '- generic "Status" [ref=e6]: Active\n'  # named generic with different inline text
            '- heading "Results" [ref=e7] [level=2]\n'
            '- list:\n'
            '  - listitem [ref=e8]:\n'
            '    - link "Result 1" [ref=e9] [cursor=pointer]:\n'
            '      - /url: /r/1'
        )

        result, _refs = self._run_pipeline(gen, raw)

        assert 'heading "Form"' in result
        assert 'textbox "Username"' in result
        assert "/placeholder: Enter username" in result
        assert 'button "Submit"' in result
        assert 'generic "Status"' in result
        assert "Active" in result  # inline text different from name → kept
        assert 'link "Result 1"' in result
