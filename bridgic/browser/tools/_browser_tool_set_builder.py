"""
Builder for creating browser tool specifications bound to Browser instances.

This module provides a flexible, scenario-based approach to tool selection,
making it easy for developers to get exactly the tools they need.
"""
from enum import Enum
from typing import List, Callable, Dict, Any, Set, TYPE_CHECKING, Union
from typing_extensions import override

from bridgic.core.agentic.tool_specs import ToolSetBuilder, ToolSetResponse
from ._browser_tool_spec import BrowserToolSpec

if TYPE_CHECKING:
    from ..session._browser import Browser


class ToolPreset(str, Enum):
    """Pre-defined tool presets for common use cases.

    Each preset is designed for a specific scenario, containing only
    the tools necessary for that use case.
    """

    # Core presets (minimal sets)
    MINIMAL = "minimal"
    """Absolute minimum: navigate, click, input, snapshot (11 tools)"""

    NAVIGATION = "navigation"
    """Navigation only: search, navigate, back/forward (4 tools)"""

    # Scenario presets
    SCRAPING = "scraping"
    """Web scraping: navigation + snapshot + scroll (14 tools)"""

    FORM_FILLING = "form_filling"
    """Form automation: navigation + input + dropdown + checkbox (20 tools)"""

    TESTING = "testing"
    """E2E testing: form_filling + verification + screenshot (28 tools)"""

    INTERACTIVE = "interactive"
    """Full interaction: all action tools + mouse + keyboard (39 tools)"""

    # Special presets
    DEVELOPER = "developer"
    """Developer tools: network + console + tracing (22 tools)"""

    COMPLETE = "complete"
    """All available tools (69 tools) - use sparingly"""


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
    >>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.MINIMAL)

    >>> # Form filling scenario
    >>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)

    >>> # E2E testing with verification
    >>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.TESTING)

    Fluent builder for custom selection:

    >>> tools = (BrowserToolSetBuilder(browser)
    ...     .with_preset(ToolPreset.MINIMAL)
    ...     .with_category("screenshot")
    ...     .with_tools(verify_url, verify_title)
    ...     .build_specs())

    Category-based selection:

    >>> tools = BrowserToolSetBuilder.for_categories(
    ...     browser,
    ...     "navigation", "action", "screenshot"
    ... )

    Name-based selection:

    >>> tools = BrowserToolSetBuilder.from_tool_names(
    ...     browser,
    ...     "search",
    ...     "navigate_to_url",
    ...     "click_element_by_ref",
    ... )
    """

    # Tool category definitions
    _CATEGORIES: Dict[str, List[str]] = {
        "navigation": [
            "search", "navigate_to_url", "go_back", "go_forward",
        ],
        "page": [
            "reload_page", "scroll_to_text", "press_key", "evaluate_javascript",
            "get_current_page_info", "new_tab", "get_tabs", "switch_tab", "close_tab",
        ],
        "action": [
            "input_text_by_ref", "click_element_by_ref", "hover_element_by_ref",
            "focus_element_by_ref", "double_click_element_by_ref",
            "scroll_element_into_view_by_ref",
        ],
        "form": [
            "input_text_by_ref", "get_dropdown_options_by_ref",
            "select_dropdown_option_by_ref", "check_element_by_ref",
            "uncheck_element_by_ref", "upload_file_by_ref", "fill_form",
        ],
        "mouse": [
            "mouse_move", "mouse_click", "mouse_drag",
            "mouse_down", "mouse_up", "mouse_wheel",
        ],
        "keyboard": [
            "press_sequentially", "key_down", "key_up", "insert_text",
        ],
        "screenshot": [
            "take_screenshot", "save_pdf",
        ],
        "network": [
            "start_console_capture", "stop_console_capture", "get_console_messages",
            "start_network_capture", "stop_network_capture", "get_network_requests", "wait_for_network_idle",
        ],
        "dialog": [
            "setup_dialog_handler", "handle_dialog", "remove_dialog_handler",
        ],
        "storage": [
            "save_storage_state", "restore_storage_state",
            "clear_cookies", "get_cookies", "set_cookie",
        ],
        "verify": [
            "verify_element_visible", "verify_text_visible", "verify_value",
            "verify_element_state", "verify_url", "verify_title",
        ],
        "devtools": [
            "start_tracing", "stop_tracing", "start_video", "stop_video", "add_trace_chunk",
        ],
        "control": [
            "browser_close", "browser_resize", "wait_for",
        ],
        "state": [
            "get_llm_repr",
        ],
        "advanced": [
            "evaluate_javascript_on_ref", "drag_element_by_ref",
        ],
    }

    # Preset definitions (which categories to include)
    _PRESETS: Dict[ToolPreset, List[str]] = {
        ToolPreset.MINIMAL: ["state", "navigation", "action"],
        ToolPreset.NAVIGATION: ["navigation"],
        ToolPreset.SCRAPING: ["state", "navigation", "page"],
        ToolPreset.FORM_FILLING: ["state", "navigation", "action", "form", "control"],
        ToolPreset.TESTING: ["state", "navigation", "action", "form", "verify", "screenshot", "control"],
        ToolPreset.INTERACTIVE: ["state", "navigation", "page", "action", "form", "mouse", "keyboard", "control"],
        ToolPreset.DEVELOPER: ["state", "navigation", "network", "devtools", "screenshot", "control"],
        ToolPreset.COMPLETE: list(_CATEGORIES.keys()),
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
    ) -> List[BrowserToolSpec]:
        """
        Get tools for a specific preset scenario.

        This is the recommended entry point for most use cases.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        preset : ToolPreset
            The preset scenario. Default is MINIMAL.

        Returns
        -------
        List[BrowserToolSpec]
            Tools configured for the preset scenario.

        Examples
        --------
        >>> # Simple web scraping
        >>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.SCRAPING)

        >>> # Form automation
        >>> tools = BrowserToolSetBuilder.for_preset(browser, ToolPreset.FORM_FILLING)
        """
        return cls(browser).with_preset(preset).build_specs()

    @classmethod
    def for_categories(
        cls,
        browser: "Browser",
        *categories: str,
    ) -> List[BrowserToolSpec]:
        """
        Get tools from specific categories.

        Available categories: navigation, page, action, form, mouse, keyboard,
        screenshot, network, dialog, storage, verify, devtools, control, state, advanced

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *categories : str
            Category names to include.

        Returns
        -------
        List[BrowserToolSpec]
            Tools from the specified categories.

        Examples
        --------
        >>> tools = BrowserToolSetBuilder.for_categories(
        ...     browser, "navigation", "action", "screenshot"
        ... )
        """
        builder = cls(browser)
        for cat in categories:
            builder.with_category(cat)
        return builder.build_specs()

    @classmethod
    def from_funcs(
        cls,
        browser: "Browser",
        *funcs: Callable,
    ) -> List[BrowserToolSpec]:
        """
        Build tool specs from specific functions.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *funcs : Callable
            Tool functions to include.

        Returns
        -------
        List[BrowserToolSpec]
            Tool specs for the specified functions.

        Examples
        --------
        >>> from bridgic.browser.tools import search, navigate_to_url, click_element_by_ref
        >>> tools = BrowserToolSetBuilder.from_funcs(browser, search, navigate_to_url, click_element_by_ref)
        """
        return cls(browser).with_tools(*funcs).build_specs()

    @classmethod
    def from_tool_names(
        cls,
        browser: "Browser",
        *tool_names: str,
        strict: bool = False,
    ) -> List[BrowserToolSpec]:
        """
        Build tool specs from specific tool function names.

        Parameters
        ----------
        browser : Browser
            The Browser instance to bind tools to.
        *tool_names : str
            Tool function names to include.
        strict : bool
            When True, raise ValueError if any name is unknown.
            When False (default), unknown names are ignored.

        Returns
        -------
        List[BrowserToolSpec]
            Tool specs for the specified tool names.

        Examples
        --------
        >>> tools = BrowserToolSetBuilder.from_tool_names(
        ...     browser,
        ...     "search",
        ...     "navigate_to_url",
        ...     "click_element_by_ref",
        ... )
        """
        return cls(browser).with_tool_names(*tool_names, strict=strict).build_specs()

    # ==================== Legacy API (backward compatible) ====================

    @classmethod
    def basic_tools(cls, browser: "Browser") -> List[BrowserToolSpec]:
        """
        Get a basic set of browser tools (legacy API).

        Equivalent to `for_preset(browser, ToolPreset.FORM_FILLING)`.

        .. deprecated::
            Use `for_preset()` instead for more control.
        """
        return cls.for_preset(browser, ToolPreset.FORM_FILLING)

    # ==================== Fluent Builder Methods ====================

    def with_preset(self, preset: ToolPreset) -> "BrowserToolSetBuilder":
        """
        Add all tools from a preset.

        Parameters
        ----------
        preset : ToolPreset
            The preset to add.

        Returns
        -------
        BrowserToolSetBuilder
            Self for chaining.
        """
        categories = self._PRESETS.get(preset, [])
        for cat in categories:
            self.with_category(cat)
        return self

    def with_category(self, category: str) -> "BrowserToolSetBuilder":
        """
        Add all tools from a category.

        Parameters
        ----------
        category : str
            Category name (navigation, page, action, form, mouse, keyboard,
            screenshot, network, dialog, storage, verify, devtools, control, state, advanced).

        Returns
        -------
        BrowserToolSetBuilder
            Self for chaining.
        """
        tool_names = self._CATEGORIES.get(category, [])
        self._selected_tools.update(tool_names)
        return self

    def with_tools(self, *tools: Union[Callable, str]) -> "BrowserToolSetBuilder":
        """
        Add specific tools by function or name.

        Parameters
        ----------
        *tools : Union[Callable, str]
            Tool functions or tool names to add.

        Returns
        -------
        BrowserToolSetBuilder
            Self for chaining.
        """
        for tool in tools:
            if callable(tool):
                self._selected_tools.add(tool.__name__)
            else:
                self._selected_tools.add(tool)
        return self

    def with_tool_names(
        self,
        *tool_names: str,
        strict: bool = False,
    ) -> "BrowserToolSetBuilder":
        """
        Add specific tools by function names.

        Parameters
        ----------
        *tool_names : str
            Tool function names to add.
        strict : bool
            When True, raise ValueError if any name is unknown.
            When False (default), unknown names are ignored.

        Returns
        -------
        BrowserToolSetBuilder
            Self for chaining.
        """
        self._selected_tools.update(tool_names)

        if strict:
            unknown_names = sorted(
                name for name in tool_names if name not in self._get_all_tool_funcs()
            )
            if unknown_names:
                raise ValueError(
                    f"Unknown tool name(s): {', '.join(unknown_names)}"
                )

        return self

    def without_tools(self, *tools: Union[Callable, str]) -> "BrowserToolSetBuilder":
        """
        Remove specific tools.

        Parameters
        ----------
        *tools : Union[Callable, str]
            Tool functions or names to remove.

        Returns
        -------
        BrowserToolSetBuilder
            Self for chaining.
        """
        for tool in tools:
            name = tool.__name__ if callable(tool) else tool
            self._selected_tools.discard(name)
        return self

    # ==================== Build Methods ====================

    def build_specs(self) -> List[BrowserToolSpec]:
        """
        Build and return the tool specifications.

        Returns
        -------
        List[BrowserToolSpec]
            List of configured tool specifications.
        """
        return self.build()["tool_specs"]

    @override
    def build(self) -> ToolSetResponse:
        """
        Build and return the complete ToolSetResponse.

        Returns
        -------
        ToolSetResponse
            Response containing tool_specs list.
        """
        tool_funcs = self._resolve_tool_funcs()
        tool_specs: List[BrowserToolSpec] = []

        for func in tool_funcs:
            tool_spec = BrowserToolSpec.from_raw(
                func=func,
                browser=self._browser,
            )
            tool_spec._from_builder = True
            tool_specs.append(tool_spec)

        return ToolSetResponse(tool_specs=tool_specs)

    def _resolve_tool_funcs(self) -> List[Callable]:
        """Resolve selected tool names to actual functions."""
        # Import all tool functions
        all_tools = self._get_all_tool_funcs()

        # If nothing selected, use minimal preset
        if not self._selected_tools:
            self.with_preset(ToolPreset.MINIMAL)

        # Resolve names to functions
        result = []
        for name in self._selected_tools:
            if name in all_tools:
                result.append(all_tools[name])

        return result

    def _get_all_tool_funcs(self) -> Dict[str, Callable]:
        """Get a mapping of all available tool functions."""
        from ._browser_tools import (
            search, navigate_to_url, go_back, go_forward,
            reload_page, scroll_to_text, press_key, evaluate_javascript,
            new_tab, get_tabs, switch_tab, close_tab, get_current_page_info,
            browser_close, browser_resize, wait_for,
        )
        from ._browser_state_tools import get_llm_repr
        from ._browser_action_tools import (
            input_text_by_ref, click_element_by_ref, get_dropdown_options_by_ref,
            select_dropdown_option_by_ref, hover_element_by_ref, focus_element_by_ref,
            evaluate_javascript_on_ref, upload_file_by_ref, drag_element_by_ref,
            check_element_by_ref, uncheck_element_by_ref, double_click_element_by_ref,
            scroll_element_into_view_by_ref,
        )
        from ._browser_mouse_tools import (
            mouse_move, mouse_click, mouse_drag, mouse_down, mouse_up, mouse_wheel,
        )
        from ._browser_keyboard_tools import (
            press_sequentially, key_down, key_up, fill_form, insert_text,
        )
        from ._browser_screenshot_tools import take_screenshot, save_pdf
        from ._browser_network_tools import (
            start_console_capture, stop_console_capture, get_console_messages,
            start_network_capture, stop_network_capture, get_network_requests, wait_for_network_idle,
        )
        from ._browser_dialog_tools import (
            setup_dialog_handler, handle_dialog, remove_dialog_handler,
        )
        from ._browser_storage_tools import (
            save_storage_state, restore_storage_state,
            clear_cookies, get_cookies, set_cookie,
        )
        from ._browser_verify_tools import (
            verify_element_visible, verify_text_visible, verify_value,
            verify_element_state, verify_url, verify_title,
        )
        from ._browser_devtools import (
            start_tracing, stop_tracing, start_video, stop_video, add_trace_chunk,
        )

        # Build name -> function mapping
        funcs = [
            # Navigation
            search, navigate_to_url, go_back, go_forward,
            # Page
            reload_page, scroll_to_text, press_key, evaluate_javascript,
            new_tab, get_tabs, switch_tab, close_tab, get_current_page_info,
            # Control
            browser_close, browser_resize, wait_for,
            # State
            get_llm_repr,
            # Action
            input_text_by_ref, click_element_by_ref, get_dropdown_options_by_ref,
            select_dropdown_option_by_ref, hover_element_by_ref, focus_element_by_ref,
            evaluate_javascript_on_ref, upload_file_by_ref, drag_element_by_ref,
            check_element_by_ref, uncheck_element_by_ref, double_click_element_by_ref,
            scroll_element_into_view_by_ref,
            # Mouse
            mouse_move, mouse_click, mouse_drag, mouse_down, mouse_up, mouse_wheel,
            # Keyboard
            press_sequentially, key_down, key_up, fill_form, insert_text,
            # Screenshot
            take_screenshot, save_pdf,
            # Network
            start_console_capture, stop_console_capture, get_console_messages,
            start_network_capture, stop_network_capture, get_network_requests, wait_for_network_idle,
            # Dialog
            setup_dialog_handler, handle_dialog, remove_dialog_handler,
            # Storage
            save_storage_state, restore_storage_state,
            clear_cookies, get_cookies, set_cookie,
            # Verify
            verify_element_visible, verify_text_visible, verify_value,
            verify_element_state, verify_url, verify_title,
            # DevTools
            start_tracing, stop_tracing, start_video, stop_video, add_trace_chunk,
        ]

        return {f.__name__: f for f in funcs}

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
            "MINIMAL": "Absolute minimum: navigate, click, input, snapshot (11 tools)",
            "NAVIGATION": "Navigation only: search, navigate, back/forward (4 tools)",
            "SCRAPING": "Web scraping: navigation + snapshot + scroll (14 tools)",
            "FORM_FILLING": "Form automation: navigation + input + dropdown + checkbox (20 tools)",
            "TESTING": "E2E testing: form_filling + verification + screenshot (28 tools)",
            "INTERACTIVE": "Full interaction: all action tools + mouse + keyboard (39 tools)",
            "DEVELOPER": "Developer tools: network + console + tracing (22 tools)",
            "COMPLETE": "All available tools (69 tools)",
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
        return {"selected_tools": list(self._selected_tools)}

    @override
    def load_from_dict(self, state_dict: Dict[str, Any]) -> None:
        """Deserialize from a dictionary."""
        raise NotImplementedError(
            "BrowserToolSetBuilder deserialization requires a browser instance. "
            "Create a new builder instead."
        )

    def __repr__(self) -> str:
        return f"<BrowserToolSetBuilder(selected={len(self._selected_tools)} tools)>"
