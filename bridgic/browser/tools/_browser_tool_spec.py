"""
Browser tool specification for binding browser tools to Browser instances.
"""
import inspect
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

    This class supports two usage patterns:

    1. **Bound method** (preferred): Pass a bound Browser method directly.
       The method already has ``self`` bound, so no browser param is needed.

       >>> spec = BrowserToolSpec.from_raw(browser.click_element_by_ref)

    2. **Custom function**: Pass a custom async function whose first parameter
       is ``browser: Browser``, along with a Browser instance.

       >>> spec = BrowserToolSpec.from_raw(my_custom_tool, browser)

    In both cases, the ``browser``/``self`` parameter is excluded from the
    tool schema so the LLM only sees the remaining parameters.
    """

    _func: Callable
    """The original tool function or bound method."""

    _browser: Optional["Browser"]
    """The Browser instance (None when func is already a bound method)."""

    def __init__(
        self,
        func: Callable,
        browser: Optional["Browser"] = None,
        tool_name: Optional[str] = None,
        tool_description: Optional[str] = None,
        tool_parameters: Optional[Dict[str, Any]] = None,
        from_builder: bool = False,
    ):
        super().__init__(
            tool_name=tool_name,
            tool_description=tool_description,
            tool_parameters=tool_parameters,
            from_builder=from_builder,
        )
        self._func = func
        # If func is a bound method the browser is accessible via __self__
        self._browser = browser or (getattr(func, "__self__", None))

    @classmethod
    def from_raw(
        cls,
        func: Callable,
        browser: Optional["Browser"] = None,
        tool_name: Optional[str] = None,
        tool_description: Optional[str] = None,
        tool_parameters: Optional[Dict[str, Any]] = None,
    ) -> "BrowserToolSpec":
        """
        Create a BrowserToolSpec from a tool function (or bound method) and optionally
        a Browser instance.

        Parameters
        ----------
        func : Callable
            A bound Browser method *or* a standalone tool function whose first
            parameter is ``browser: Browser``.
        browser : Optional[Browser]
            Required when ``func`` is a standalone function.  Not required (and
            ignored) when ``func`` is already a bound method.
        tool_name : Optional[str]
            Custom tool name. Defaults to function name.
        tool_description : Optional[str]
            Custom description. Defaults to function docstring.
        tool_parameters : Optional[Dict[str, Any]]
            Custom parameters schema. Auto-generated when omitted (``browser``
            and ``self`` are always excluded from the schema).

        Returns
        -------
        BrowserToolSpec

        Examples
        --------
        Mode 1 — bound method (recommended), browser is implicit via ``__self__``::

            spec = BrowserToolSpec.from_raw(browser.click_element_by_ref)

        Mode 2 — custom function with browser as first arg, must pass browser explicitly::

            async def my_custom_tool(browser: Browser, url: str) -> str:
                ...
            spec = BrowserToolSpec.from_raw(my_custom_tool, browser=browser)
        """
        if not tool_name:
            tool_name = getattr(func, "__name__", None) or getattr(func, "__func__", func).__name__

        if not tool_description:
            tool_description = get_tool_description_from(func, tool_name)

        if not tool_parameters:
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
    def browser(self) -> Optional["Browser"]:
        """Get the bound Browser instance."""
        return self._browser

    @property
    def func(self) -> Callable:
        """Get the original tool function or bound method."""
        return self._func

    @override
    def to_tool(self) -> Tool:
        """
        Transform this BrowserToolSpec to a Tool object used by LLM.

        Returns
        -------
        Tool
            The browser/self parameter is not included in the schema.
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

        For bound methods, the worker calls the method directly.
        For standalone functions, the browser is pre-bound via functools.partial.

        Returns
        -------
        Worker
            A CallableWorker that executes the tool.
        """
        if inspect.ismethod(self._func):
            # Already a bound method — no partial needed
            return CallableWorker(self._func)
        else:
            # Standalone function — bind browser as first arg
            bound_func = partial(self._func, self._browser)
            return CallableWorker(bound_func)

    @override
    def dump_to_dict(self) -> Dict[str, Any]:
        state_dict = super().dump_to_dict()
        state_dict["func"] = self._func.__module__ + "." + self._func.__qualname__
        if self._browser is not None:
            state_dict["browser_name"] = getattr(self._browser, "name", self._browser.__class__.__name__)
            state_dict["browser_id"] = str(id(self._browser))
        return state_dict

    @override
    def load_from_dict(self, state_dict: Dict[str, Any]) -> None:
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
