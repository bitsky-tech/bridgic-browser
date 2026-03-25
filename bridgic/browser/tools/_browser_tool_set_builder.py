"""
Builder for creating browser tool specifications bound to Browser instances.

This module provides a flexible, category-based approach to tool selection,
making it easy for developers to get exactly the tools they need.
"""
from typing import List, Callable, Dict, Any, Set, TYPE_CHECKING
from typing_extensions import override

from bridgic.core.agentic.tool_specs import ToolSetBuilder, ToolSetResponse
from .._constants import ToolCategory
from .._cli_catalog import CLI_TOOL_CATEGORIES
from ._browser_tool_spec import BrowserToolSpec

if TYPE_CHECKING:
    from ..session._browser import Browser

class BrowserToolSetBuilder(ToolSetBuilder):
    """
    A flexible builder for creating browser tool sets.

    Provides multiple ways to select tools:
    1. **Categories**: Select by ``ToolCategory`` (pass ``ToolCategory.ALL`` for everything)
    2. **Individual**: Cherry-pick specific CLI-mapped tools by method name
    3. **Composable**: Combine any of the above

    Examples
    --------
    Category-based selection:

    >>> builder = BrowserToolSetBuilder.for_categories(
    ...     browser,
    ...     ToolCategory.NAVIGATION, ToolCategory.ELEMENT_INTERACTION, ToolCategory.CAPTURE,
    ... )
    >>> tools = builder.build()["tool_specs"]

    All tools:

    >>> builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.ALL)
    >>> tools = builder.build()["tool_specs"]

    Name-based selection:

    >>> builder = BrowserToolSetBuilder.for_tool_names(
    ...     browser,
    ...     "search",
    ...     "navigate_to",
    ...     "click_element_by_ref",
    ... )
    >>> tools = builder.build()["tool_specs"]
    """

    # Tool categories are derived from the shared CLI catalog.
    _CATEGORIES: Dict[ToolCategory, List[str]] = {
        category: list(tool_names) for category, tool_names in CLI_TOOL_CATEGORIES.items()
    }
    _CATEGORY_ALIASES: Dict[str, ToolCategory] = {
        "navigation": ToolCategory.NAVIGATION,
        "snapshot": ToolCategory.SNAPSHOT,
        "state": ToolCategory.SNAPSHOT,
        "element_interaction": ToolCategory.ELEMENT_INTERACTION,
        "element interaction": ToolCategory.ELEMENT_INTERACTION,
        "action": ToolCategory.ELEMENT_INTERACTION,
        "tabs": ToolCategory.TABS,
        "evaluate": ToolCategory.EVALUATE,
        "keyboard": ToolCategory.KEYBOARD,
        "mouse": ToolCategory.MOUSE,
        "wait": ToolCategory.WAIT,
        "capture": ToolCategory.CAPTURE,
        "network": ToolCategory.NETWORK,
        "dialog": ToolCategory.DIALOG,
        "storage": ToolCategory.STORAGE,
        "verify": ToolCategory.VERIFY,
        "developer": ToolCategory.DEVELOPER,
        "lifecycle": ToolCategory.LIFECYCLE,
        "all": ToolCategory.ALL,
    }

    _browser: "Browser"
    _selected_tools: Set[str]

    def __init__(self, browser: "Browser"):
        """
        Initialize the builder with a browser instance.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        """
        self._browser = browser
        self._selected_tools = set()

    # ==================== Factory Methods ====================

    @classmethod
    def for_categories(
        cls,
        browser: "Browser",
        *categories: ToolCategory | str,
    ) -> "BrowserToolSetBuilder":
        """
        Create a builder with tools from specific categories.

        Pass ``ToolCategory.ALL`` to include every tool from all categories.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *categories : ToolCategory | str
            Categories to include. Strings are accepted (e.g. ``"navigation"``,
            ``"action"``, ``"capture"``). Use ``ToolCategory.ALL`` or ``"all"``
            to include every tool.

        Returns
        -------
        BrowserToolSetBuilder
            Builder configured with the specified categories.

        Examples
        --------
        >>> builder = BrowserToolSetBuilder.for_categories(
        ...     browser, ToolCategory.NAVIGATION, ToolCategory.ELEMENT_INTERACTION,
        ... )
        >>> tools = builder.build()["tool_specs"]

        >>> builder = BrowserToolSetBuilder.for_categories(browser, ToolCategory.ALL)
        >>> tools = builder.build()["tool_specs"]
        """
        builder = cls(browser)
        parsed = [cls._coerce_category(category) for category in categories]
        if ToolCategory.ALL in parsed:
            for cat in cls._CATEGORIES:
                builder._add_category(cat)
        else:
            for cat in parsed:
                builder._add_category(cat)
        return builder

    @classmethod
    def for_tool_names(
        cls,
        browser: "Browser",
        *tool_names: str,
        strict: bool = True,
    ) -> "BrowserToolSetBuilder":
        """
        Create a builder configured from specific tool function names.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *tool_names : str
            Tool function names to include.
        strict : bool, optional
            If True (default), unknown names and missing browser methods raise
            ``ValueError``. If False, unavailable names are silently ignored.
        Returns
        -------
        BrowserToolSetBuilder
            Builder configured for the specified tool names.

        Examples
        --------
        >>> builder = BrowserToolSetBuilder.for_tool_names(
        ...     browser,
        ...     "search",
        ...     "navigate_to",
        ...     "click_element_by_ref",
        ... )
        >>> tools = builder.build()["tool_specs"]
        """
        builder = cls(browser)
        builder._add_tool_names(*tool_names, strict=strict)
        return builder

    # ==================== Selection Internals ====================

    def _add_category(self, category: ToolCategory) -> None:
        """Add all tools from a category."""
        tool_names = self._CATEGORIES.get(category, [])
        self._selected_tools.update(tool_names)

    def _add_tool_names(
        self,
        *tool_names: str,
        strict: bool = True,
    ) -> None:
        """
        Add specific tools by function names.

        Parameters
        ----------
        *tool_names : str
            Tool function names to add.
        """
        all_tool_names = self._get_all_tool_names()
        if strict:
            unknown_names = sorted(
                name for name in tool_names if name not in all_tool_names
            )
            missing_on_browser = sorted(
                name
                for name in tool_names
                if name in all_tool_names and not callable(getattr(self._browser, name, None))
            )
            errors: List[str] = []
            if unknown_names:
                errors.append(f"Unknown tool name(s): {', '.join(unknown_names)}")
            if missing_on_browser:
                errors.append(
                    f"Tool name(s) not available on browser instance: {', '.join(missing_on_browser)}"
                )
            if errors:
                raise ValueError("; ".join(errors))

        for name in tool_names:
            if name not in all_tool_names:
                continue
            if not callable(getattr(self._browser, name, None)):
                continue
            self._selected_tools.add(name)

    # ==================== Build Methods ====================

    @override
    def build(self) -> ToolSetResponse:
        """
        Build and return the complete ToolSetResponse.

        Returns
        -------
        ToolSetResponse
            Response containing tool_specs list.

        Raises
        ------
        ValueError
            If no tools have been selected.
        """
        bound_methods = self._resolve_tool_methods()
        tool_specs: List[BrowserToolSpec] = []

        for method in bound_methods:
            tool_spec = BrowserToolSpec.from_raw(func=method)
            tool_spec._from_builder = True
            tool_specs.append(tool_spec)

        return ToolSetResponse(tool_specs=tool_specs)

    def _resolve_tool_methods(self) -> List[Callable]:
        """Resolve selected tool names to bound Browser methods."""
        if not self._selected_tools:
            raise ValueError(
                "No tools selected. Use for_categories() or for_tool_names() "
                "to select tools before calling build()."
            )

        result = []
        for name in self._get_stable_selected_tool_names():
            method = getattr(self._browser, name, None)
            if method is not None and callable(method):
                result.append(method)

        return result

    def _get_stable_selected_tool_names(self) -> List[str]:
        """
        Return selected tool names in a deterministic order.

        Tool names follow category declaration order from the CLI catalog.
        """
        ordered: List[str] = []
        seen: Set[str] = set()

        for category_tool_names in self._CATEGORIES.values():
            for name in category_tool_names:
                if name in self._selected_tools and name not in seen:
                    ordered.append(name)
                    seen.add(name)

        return ordered

    def _get_all_tool_names(self) -> set:
        """Get the set of all available tool method names."""
        all_names: set = set()
        for names in self._CATEGORIES.values():
            all_names.update(names)
        return all_names

    # ==================== Utility Methods ====================

    @classmethod
    def list_categories(cls) -> Dict[ToolCategory, int]:
        """
        List available categories with tool counts.

        Returns
        -------
        Dict[ToolCategory, int]
            Category -> tool count mapping.
        """
        return {cat: len(tools) for cat, tools in cls._CATEGORIES.items()}

    @classmethod
    def _coerce_category(cls, category: ToolCategory | str) -> ToolCategory:
        """Normalize category enum/string input."""
        if isinstance(category, ToolCategory):
            if category == ToolCategory.ALL or category in cls._CATEGORIES:
                return category
            raise ValueError(f"Tool category has no mapped tools: {category.name.lower()}")

        normalized = category.strip().replace("-", "_").replace("/", "_").lower()
        by_alias = cls._CATEGORY_ALIASES.get(normalized)
        if by_alias is not None:
            return by_alias

        for member in ToolCategory:
            if normalized == member.name.lower() or normalized == member.value.lower():
                if member == ToolCategory.ALL or member in cls._CATEGORIES:
                    return member
                raise ValueError(f"Tool category has no mapped tools: {member.name.lower()}")

        raise ValueError(f"Unknown tool category: {category}")

    @override
    def dump_to_dict(self) -> Dict[str, Any]:
        """Serialize the builder configuration."""
        return {"selected_tools": sorted(self._selected_tools)}

    @override
    def load_from_dict(self, state_dict: Dict[str, Any]) -> None:
        """Deserialize from a dictionary."""
        raise NotImplementedError(
            "BrowserToolSetBuilder deserialization requires a browser instance. "
            "Create a new builder instead."
        )

    def __repr__(self) -> str:
        return f"<BrowserToolSetBuilder(selected={len(self._selected_tools)} tools)>"
