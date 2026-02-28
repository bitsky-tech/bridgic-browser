"""
Browser tool specification for binding browser tools to Browser instances.
"""
from typing import Optional, Callable, Dict, Any, TYPE_CHECKING
from typing_extensions import override
from functools import partial

from bridgic.core.agentic.tool_specs import ToolSpec
from bridgic.core.model.types import Tool
from bridgic.core.automa.worker import Worker, CallableWorker
from bridgic.core.utils._json_schema import create_func_params_json_schema
from bridgic.core.utils._inspect_tools import get_tool_description_from

if TYPE_CHECKING:
    from ..session._browser import Browser


class BrowserToolSpec(ToolSpec):
    """
    A tool specification that represents a browser tool bound to a Browser instance.

    This class provides a bridge between browser tools and the Bridgic framework,
    allowing browser tools to be used seamlessly within Bridgic agentic systems.

    The key difference from FunctionToolSpec is that BrowserToolSpec:
    1. Holds a reference to a Browser instance
    2. Automatically excludes the `browser` parameter from the tool schema
    3. Creates workers that have the browser pre-bound via functools.partial

    Examples
    --------
    Create a BrowserToolSpec from a tool function:

    >>> async def search(browser: Browser, query: str) -> str:
    ...     await browser.navigate_to(f"https://google.com/search?q={query}")
    ...     return f"Searched for: {query}"
    >>>
    >>> browser = Browser(name="my_browser")
    >>> tool_spec = BrowserToolSpec.from_raw(search, browser)
    >>> # The resulting tool schema only has `query` parameter, not `browser`
    """

    _func: Callable
    """The original tool function (first param is browser: Browser)."""

    _browser: "Browser"
    """The Browser instance this tool is bound to."""

    def __init__(
        self,
        func: Callable,
        browser: "Browser",
        tool_name: Optional[str] = None,
        tool_description: Optional[str] = None,
        tool_parameters: Optional[Dict[str, Any]] = None,
        from_builder: bool = False,
    ):
        """
        Initialize a BrowserToolSpec.

        Parameters
        ----------
        func : Callable
            The tool function. First parameter must be `browser: Browser`.
        browser : Browser
            The Browser instance to bind to.
        tool_name : Optional[str]
            The name of the tool. If not provided, uses function name.
        tool_description : Optional[str]
            The description of the tool. If not provided, uses function docstring.
        tool_parameters : Optional[Dict[str, Any]]
            The JSON schema of the tool's parameters.
        from_builder : bool
            Whether this ToolSpec was created from a ToolSetBuilder.
        """
        super().__init__(
            tool_name=tool_name,
            tool_description=tool_description,
            tool_parameters=tool_parameters,
            from_builder=from_builder,
        )
        self._func = func
        self._browser = browser

    @classmethod
    def from_raw(
        cls,
        func: Callable,
        browser: "Browser",
        tool_name: Optional[str] = None,
        tool_description: Optional[str] = None,
        tool_parameters: Optional[Dict[str, Any]] = None,
    ) -> "BrowserToolSpec":
        """
        Create a BrowserToolSpec from a tool function and Browser instance.

        This factory method automatically extracts the tool name, description,
        and parameters schema from the function, excluding the `browser` parameter
        from the schema so that the LLM doesn't need to provide it.

        Parameters
        ----------
        func : Callable
            The tool function. First parameter must be `browser: Browser`.
        browser : Browser
            The Browser instance to bind to.
        tool_name : Optional[str]
            Custom tool name. Defaults to function name.
        tool_description : Optional[str]
            Custom description. Defaults to function docstring.
        tool_parameters : Optional[Dict[str, Any]]
            Custom parameters schema. Defaults to auto-generated schema
            (excluding the `browser` parameter).

        Returns
        -------
        BrowserToolSpec
            A new BrowserToolSpec instance.

        Examples
        --------
        >>> tool_spec = BrowserToolSpec.from_raw(search, browser)
        >>> tool = tool_spec.to_tool()
        >>> print(tool.parameters)  # browser param is excluded
        """
        if not tool_name:
            tool_name = func.__name__

        if not tool_description:
            tool_description = get_tool_description_from(func, tool_name)

        if not tool_parameters:
            # Key: exclude browser parameter when generating schema
            tool_parameters = create_func_params_json_schema(
                func,
                ignore_params=["self", "cls", "browser"]
            )

        return cls(
            func=func,
            browser=browser,
            tool_name=tool_name,
            tool_description=tool_description,
            tool_parameters=tool_parameters,
        )

    @property
    def browser(self) -> "Browser":
        """Get the bound Browser instance."""
        return self._browser

    @property
    def func(self) -> Callable:
        """Get the original tool function."""
        return self._func

    @override
    def to_tool(self) -> Tool:
        """
        Transform this BrowserToolSpec to a Tool object used by LLM.

        Returns
        -------
        Tool
            A Tool object that can be used by LLM for tool selection.
            The browser parameter is not included in the schema.
        """
        return Tool(
            name=self._tool_name,
            description=self._tool_description,
            parameters=self._tool_parameters,
        )

    @override
    def create_worker(self) -> Worker:
        """
        Create a Worker that executes this tool with the bound browser.

        The worker wraps the original function with the browser pre-bound
        using functools.partial, so the LLM only needs to provide the
        remaining parameters.

        Returns
        -------
        Worker
            A CallableWorker that executes the tool with the bound browser.
        """
        # Use partial to bind the browser parameter
        bound_func = partial(self._func, self._browser)
        return CallableWorker(bound_func)

    @override
    def dump_to_dict(self) -> Dict[str, Any]:
        """
        Serialize this BrowserToolSpec to a dictionary.

        Note: The browser instance is not fully serialized, only its name
        is stored. Deserialization requires a browser instance to be provided.
        """
        state_dict = super().dump_to_dict()
        state_dict["func"] = self._func.__module__ + "." + self._func.__qualname__
        state_dict["browser_name"] = self._browser.name
        return state_dict

    @override
    def load_from_dict(self, state_dict: Dict[str, Any]) -> None:
        """
        Deserialize from a dictionary.

        Note: This method is not fully implemented because browser instances
        cannot be serialized. Use BrowserToolSetBuilder to recreate tool specs.
        """
        raise NotImplementedError(
            "BrowserToolSpec deserialization requires a browser instance. "
            "Use BrowserToolSetBuilder to recreate tool specs with a browser."
        )

    def __repr__(self) -> str:
        return (
            f"<BrowserToolSpec("
            f"tool_name={self._tool_name!r}, "
            f")>"
        )
