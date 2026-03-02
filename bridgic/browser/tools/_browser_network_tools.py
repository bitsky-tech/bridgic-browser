"""
Browser console and network monitoring tools.

This module provides tools for capturing console messages and monitoring
network requests.

Note: Uses module-level storage keyed by page ID. Call stop_console_capture()
or stop_network_capture() when done to prevent memory leaks.
"""
import json
import logging
from typing import TYPE_CHECKING, List, Optional, Literal

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)

# Storage for console messages and network requests per page
# Keys are page IDs (str(id(page))), values are message/request lists
_console_messages: dict = {}
_network_requests: dict = {}

# Track registered handlers for cleanup (page_key -> handler_func)
_console_handlers: dict = {}
_network_handlers: dict = {}


def _get_page_key(page) -> str:
    """Get a unique key for a page to store data."""
    return str(id(page))


def _cleanup_page_data(page_key: str) -> None:
    """Clean up stored data for a page (internal helper)."""
    _console_messages.pop(page_key, None)
    _network_requests.pop(page_key, None)
    _console_handlers.pop(page_key, None)
    _network_handlers.pop(page_key, None)


async def start_console_capture(browser: "Browser") -> str:
    """Start capturing console messages from the current page.

    Messages are stored until retrieved with get_console_messages().
    Call stop_console_capture() when done to free memory.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.

    Returns
    -------
    str
        "Console message capture started" or error message.

    Notes
    -----
    - Only one capture session per page; calling again resets the capture
    - Capture is page-specific; navigation to new page requires re-starting
    - Use get_console_messages() to retrieve and optionally clear messages
    """
    try:
        logger.info("[start_console_capture] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        
        # Remove existing handler if any
        if page_key in _console_handlers:
            try:
                page.remove_listener("console", _console_handlers[page_key])
            except Exception:
                pass
        
        _console_messages[page_key] = []

        def handle_console(msg):
            if page_key in _console_messages:
                _console_messages[page_key].append({
                    "type": msg.type,
                    "text": msg.text,
                    "location": str(msg.location) if msg.location else None,
                })

        page.on("console", handle_console)
        _console_handlers[page_key] = handle_console

        result = "Console message capture started"
        logger.info(f"[start_console_capture] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to start console capture: {str(e)}"
        logger.error(f"[start_console_capture] {error_msg}")
        return error_msg


async def get_console_messages(
    browser: "Browser",
    type_filter: Optional[Literal["log", "debug", "info", "error", "warning", "dir", "trace"]] = None,
    clear: bool = True,
) -> str:
    """Get captured console messages.

    Retrieve console messages that have been captured since the last call
    or since capture was started.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    type_filter : Optional[str], optional
        Filter messages by type. Options: "log", "debug", "info", "error",
        "warning", "dir", "trace". Default is None (all types).
    clear : bool, optional
        Whether to clear messages after retrieving. Default is True.

    Returns
    -------
    str
        JSON string containing the captured console messages.

    Notes
    -----
    Console capture must be started first with start_console_capture().
    """
    try:
        logger.info(f"[get_console_messages] start type_filter={type_filter} clear={clear}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        messages = _console_messages.get(page_key, [])

        # Filter by type if specified
        if type_filter:
            messages = [m for m in messages if m["type"] == type_filter]

        # Clear if requested
        if clear and page_key in _console_messages:
            _console_messages[page_key] = []

        result = json.dumps(messages, indent=2)
        logger.info(f"[get_console_messages] done count={len(messages)}")
        return result
    except Exception as e:
        error_msg = f"Failed to get console messages: {str(e)}"
        logger.error(f"[get_console_messages] {error_msg}")
        return error_msg


async def stop_console_capture(browser: "Browser") -> str:
    """Stop capturing console messages and clean up resources.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.

    Returns
    -------
    str
        Operation result message.
    """
    try:
        logger.info("[stop_console_capture] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        
        if page_key in _console_handlers:
            try:
                page.remove_listener("console", _console_handlers[page_key])
            except Exception:
                pass
            del _console_handlers[page_key]
        
        _console_messages.pop(page_key, None)

        result = "Console capture stopped"
        logger.info(f"[stop_console_capture] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to stop console capture: {str(e)}"
        logger.error(f"[stop_console_capture] {error_msg}")
        return error_msg


async def start_network_capture(browser: "Browser") -> str:
    """Start capturing network requests from the current page.

    Requests are stored until retrieved with get_network_requests().
    Call stop_network_capture() when done to free memory.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.

    Returns
    -------
    str
        "Network request capture started" or error message.

    Notes
    -----
    - Call BEFORE navigation to capture all requests from page load
    - POST data is truncated to first 500 characters
    - Capture is page-specific; navigation to new page requires re-starting
    - Use get_network_requests(include_static=False) to filter out images/CSS/JS
    """
    try:
        logger.info("[start_network_capture] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        
        # Remove existing handler if any
        if page_key in _network_handlers:
            try:
                page.remove_listener("request", _network_handlers[page_key])
            except Exception:
                pass
        
        _network_requests[page_key] = []

        def handle_request(request):
            if page_key in _network_requests:
                _network_requests[page_key].append({
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "headers": dict(request.headers) if request.headers else {},
                    "post_data": request.post_data[:500] if request.post_data else None,
                })

        page.on("request", handle_request)
        _network_handlers[page_key] = handle_request

        result = "Network request capture started"
        logger.info(f"[start_network_capture] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to start network capture: {str(e)}"
        logger.error(f"[start_network_capture] {error_msg}")
        return error_msg


async def stop_network_capture(browser: "Browser") -> str:
    """Stop capturing network requests and clean up resources.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.

    Returns
    -------
    str
        Operation result message.
    """
    try:
        logger.info("[stop_network_capture] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        
        if page_key in _network_handlers:
            try:
                page.remove_listener("request", _network_handlers[page_key])
            except Exception:
                pass
            del _network_handlers[page_key]
        
        _network_requests.pop(page_key, None)

        result = "Network capture stopped"
        logger.info(f"[stop_network_capture] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to stop network capture: {str(e)}"
        logger.error(f"[stop_network_capture] {error_msg}")
        return error_msg


async def get_network_requests(
    browser: "Browser",
    include_static: bool = False,
    clear: bool = True,
) -> str:
    """Get captured network requests.

    Retrieve network requests that have been captured since the last call
    or since capture was started.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    include_static : bool, optional
        Whether to include static resources (images, stylesheets, scripts,
        fonts). Default is False (only document, xhr, fetch requests).
    clear : bool, optional
        Whether to clear requests after retrieving. Default is True.

    Returns
    -------
    str
        JSON string containing the captured network requests.

    Notes
    -----
    Network capture must be started first with start_network_capture().
    """
    try:
        logger.info(f"[get_network_requests] start include_static={include_static} clear={clear}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        requests = _network_requests.get(page_key, [])

        # Filter out static resources if not requested
        if not include_static:
            static_types = {"image", "stylesheet", "script", "font", "media"}
            requests = [r for r in requests if r["resource_type"] not in static_types]

        # Clear if requested
        if clear and page_key in _network_requests:
            _network_requests[page_key] = []

        result = json.dumps(requests, indent=2)
        logger.info(f"[get_network_requests] done count={len(requests)}")
        return result
    except Exception as e:
        error_msg = f"Failed to get network requests: {str(e)}"
        logger.error(f"[get_network_requests] {error_msg}")
        return error_msg


async def wait_for_network_idle(
    browser: "Browser",
    timeout: float = 30000,
) -> str:
    """Wait for network to become idle.

    Wait until there are no network connections for at least 500ms.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    timeout : float, optional
        Maximum time to wait in milliseconds. Default is 30000 (30 seconds).

    Returns
    -------
    str
        Operation result message.
    """
    try:
        logger.info(f"[wait_for_network_idle] start timeout={timeout}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.wait_for_load_state("networkidle", timeout=timeout)

        result = "Network is idle"
        logger.info(f"[wait_for_network_idle] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to wait for network idle: {str(e)}"
        logger.error(f"[wait_for_network_idle] {error_msg}")
        return error_msg
