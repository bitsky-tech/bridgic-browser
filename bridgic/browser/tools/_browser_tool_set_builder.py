"""
Builder for creating browser tool specifications bound to Browser instances.

This module provides a flexible, scenario-based approach to tool selection,
making it easy for developers to get exactly the tools they need.
"""
from typing import List, Callable, Dict, Any, Set, TYPE_CHECKING
from typing_extensions import override

from bridgic.core.agentic.tool_specs import ToolSetBuilder, ToolSetResponse
from .._constants import ToolPreset
from .._cli_catalog import CLI_TOOL_CATEGORIES, CLI_PRESET_TOOL_METHODS
from ._browser_tool_spec import BrowserToolSpec

if TYPE_CHECKING:
    from ..session._browser import Browser

class BrowserToolSetBuilder(ToolSetBuilder):
    """
    A flexible builder for creating browser tool sets.

    Provides multiple ways to select tools:
    1. **Presets**: Pre-defined tool sets for common scenarios
    2. **Categories**: Select by tool category
    3. **Individual**: Cherry-pick specific tools by function or name
    4. **Composable**: Combine any of the above

    Examples
    --------
    Quick start with presets:

    >>> # Minimal tools for simple automation
    >>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)
    >>> tools = builder.build()["tool_specs"]

    >>> # Form filling scenario
    >>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)
    >>> tools = builder.build()["tool_specs"]

    >>> # E2E testing with verification
    >>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)
    >>> tools = builder.build()["tool_specs"]

    Category-based selection:

    >>> builder = BrowserToolSetBuilder.for_categories(
    ...     browser,
    ...     "navigation", "element_interaction", "capture"
    ... )

    Name-based selection:

    >>> builder = BrowserToolSetBuilder.for_tool_names(
    ...     browser,
    ...     "search",
    ...     "navigate_to_url",
    ...     "click_element_by_ref",
    ... )
    """

    # Tool categories and presets are derived from the shared CLI catalog.
    _CATEGORIES: Dict[str, List[str]] = {
        category: list(tool_names) for category, tool_names in CLI_TOOL_CATEGORIES.items()
    }
    _PRESET_TOOL_NAMES: Dict[ToolPreset, List[str]] = {
        preset: list(tool_names) for preset, tool_names in CLI_PRESET_TOOL_METHODS.items()
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

    # ==================== Preset-based Factory Methods ====================

    @classmethod
    def for_preset(
        cls,
        browser: "Browser",
        preset: ToolPreset = ToolPreset.MINIMAL,
    ) -> "BrowserToolSetBuilder":
        """
        Create a builder preconfigured for a preset scenario.

        This is the recommended entry point for most use cases.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        preset : ToolPreset
            The preset scenario. Default is MINIMAL.

        Returns
        -------
        BrowserToolSetBuilder
            Builder configured for the preset scenario.

        Examples
        --------
        >>> # Simple web scraping
        >>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.SCRAPING)
        >>> tools = builder.build()["tool_specs"]

        >>> # Form automation
        >>> builder = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)
        >>> tools = builder.build()["tool_specs"]
        """
        builder = cls(browser)
        builder._add_preset(preset)
        return builder

    @classmethod
    def for_categories(
        cls,
        browser: "Browser",
        *categories: str,
    ) -> "BrowserToolSetBuilder":
        """
        Create a builder with tools from specific categories.

        Available categories: navigation, snapshot, element_interaction, tabs,
        evaluate, keyboard, mouse, wait, capture, network, dialog, storage,
        verify, developer, lifecycle

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *categories : str
            Category names to include.

        Returns
        -------
        BrowserToolSetBuilder
            Builder configured with the specified categories.

        Examples
        --------
        >>> builder = BrowserToolSetBuilder.for_categories(
        ...     browser, "navigation", "element_interaction", "capture"
        ... )
        >>> tools = builder.build()["tool_specs"]
        """
        builder = cls(browser)
        for cat in categories:
            builder._add_category(cat)
        return builder

    @classmethod
    def for_funcs(
        cls,
        browser: "Browser",
        *funcs: Callable,
    ) -> "BrowserToolSetBuilder":
        """
        Create a builder configured from specific tool functions.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *funcs : Callable
            Tool functions to include.

        Returns
        -------
        BrowserToolSetBuilder
            Builder configured for the specified functions.

        Examples
        --------
        >>> builder = BrowserToolSetBuilder.for_funcs(
        ...     browser, browser.search, browser.navigate_to_url, browser.click_element_by_ref
        ... )
        >>> tools = builder.build()["tool_specs"]
        """
        builder = cls(browser)
        builder._add_funcs(*funcs)
        return builder

    @classmethod
    def for_tool_names(
        cls,
        browser: "Browser",
        *tool_names: str,
        strict: bool = False,
    ) -> "BrowserToolSetBuilder":
        """
        Create a builder configured from specific tool function names.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *tool_names : str
            Tool function names to include.
        strict : bool
            When True, raise ValueError if any name is unknown or not available
            on the provided browser instance. When False (default), unknown
            names are ignored.

        Returns
        -------
        BrowserToolSetBuilder
            Builder configured for the specified tool names.

        Examples
        --------
        >>> builder = BrowserToolSetBuilder.for_tool_names(
        ...     browser,
        ...     "search",
        ...     "navigate_to_url",
        ...     "click_element_by_ref",
        ... )
        >>> tools = builder.build()["tool_specs"]
        """
        builder = cls(browser)
        builder._add_tool_names(*tool_names, strict=strict)
        return builder

    # ==================== Selection Internals ====================

    def _add_preset(self, preset: ToolPreset) -> None:
        """Add all tools from a preset (preset maps directly to method names)."""
        tool_names = self._PRESET_TOOL_NAMES.get(preset, [])
        self._selected_tools.update(tool_names)

    def _add_category(self, category: str) -> None:
        """Add all tools from a category."""
        tool_names = self._CATEGORIES.get(category, [])
        self._selected_tools.update(tool_names)

    def _add_funcs(self, *funcs: Callable) -> None:
        """Add specific tools by function reference."""
        for func in funcs:
            if not callable(func):
                raise TypeError(
                    f"for_funcs expects callable tool methods, got {type(func).__name__}: {func!r}"
                )
            self._selected_tools.add(func.__name__)

    def _add_tool_names(
        self,
        *tool_names: str,
        strict: bool = False,
    ) -> None:
        """
        Add specific tools by function names.

        Parameters
        ----------
        *tool_names : str
            Tool function names to add.
        strict : bool
            When True, raise ValueError if any name is unknown or not available
            on the provided browser instance. When False (default), unknown
            names are ignored.

        """
        self._selected_tools.update(tool_names)

        if strict:
            all_tool_names = self._get_all_tool_names()
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

    # ==================== Build Methods ====================

    @override
    def build(self) -> ToolSetResponse:
        """
        Build and return the complete ToolSetResponse.

        Returns
        -------
        ToolSetResponse
            Response containing tool_specs list.
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
        # If nothing selected, use minimal preset
        if not self._selected_tools:
            self._add_preset(ToolPreset.MINIMAL)

        result = []
        for name in self._get_stable_selected_tool_names():
            method = getattr(self._browser, name, None)
            if method is not None and callable(method):
                result.append(method)

        return result

    def _get_stable_selected_tool_names(self) -> List[str]:
        """
        Return selected tool names in a deterministic order.

        Known built-in tools follow category declaration order.
        Unknown/custom names are appended in lexical order.
        """
        ordered: List[str] = []
        seen: Set[str] = set()

        for category_tool_names in self._CATEGORIES.values():
            for name in category_tool_names:
                if name in self._selected_tools and name not in seen:
                    ordered.append(name)
                    seen.add(name)

        all_tool_names = self._get_all_tool_names()
        custom_names = sorted(
            name for name in self._selected_tools if name not in all_tool_names
        )
        for name in custom_names:
            if name not in seen:
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
    def list_presets(cls) -> Dict[str, str]:
        """
        List available presets with descriptions.

        Returns
        -------
        Dict[str, str]
            Preset name -> description mapping.
        """
        return {
            "MINIMAL": f"Absolute minimum: navigate, click, input, snapshot ({len(cls._PRESET_TOOL_NAMES[ToolPreset.MINIMAL])} tools)",
            "NAVIGATION": f"Navigation only: search, navigate, back/forward ({len(cls._PRESET_TOOL_NAMES[ToolPreset.NAVIGATION])} tools)",
            "SCRAPING": f"Web scraping: navigation + snapshot + scroll ({len(cls._PRESET_TOOL_NAMES[ToolPreset.SCRAPING])} tools)",
            "FORM_FILLING": f"Form automation: navigation + input + dropdown + checkbox ({len(cls._PRESET_TOOL_NAMES[ToolPreset.FORM_FILLING])} tools)",
            "TESTING": f"E2E testing: form filling + verification + screenshot ({len(cls._PRESET_TOOL_NAMES[ToolPreset.TESTING])} tools)",
            "INTERACTIVE": f"Full interaction: all action tools + mouse + keyboard ({len(cls._PRESET_TOOL_NAMES[ToolPreset.INTERACTIVE])} tools)",
            "DEVELOPER": f"Developer tools: network + console + tracing ({len(cls._PRESET_TOOL_NAMES[ToolPreset.DEVELOPER])} tools)",
            "COMPLETE": f"All available tools ({len(cls._PRESET_TOOL_NAMES[ToolPreset.COMPLETE])} tools)",
        }

    @classmethod
    def list_categories(cls) -> Dict[str, int]:
        """
        List available categories with tool counts.

        Returns
        -------
        Dict[str, int]
            Category name -> tool count mapping.
        """
        return {cat: len(tools) for cat, tools in cls._CATEGORIES.items()}

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
