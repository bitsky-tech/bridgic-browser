"""
Browser console and network monitoring tools.

This module provides tools for capturing console messages and monitoring
network requests.
"""
from __future__ import annotations
import json
import logging
from typing import TYPE_CHECKING, List, Optional, Literal

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)

# Storage for console messages and network requests per page
_console_messages: dict = {}
_network_requests: dict = {}


def _get_page_key(page) -> str:
    """Get a unique key for a page to store data."""
    return str(id(page))


async def start_console_capture(browser: "Browser") -> str:
    """Start capturing console messages.

    Begin capturing console messages from the current page. Messages will
    be stored until retrieved with get_console_messages.

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
        logger.info("[start_console_capture] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        _console_messages[page_key] = []

        def handle_console(msg):
            _console_messages[page_key].append({
                "type": msg.type,
                "text": msg.text,
                "location": str(msg.location) if msg.location else None,
            })

        page.on("console", handle_console)

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


async def start_network_capture(browser: "Browser") -> str:
    """Start capturing network requests.

    Begin capturing network requests from the current page. Requests will
    be stored until retrieved with get_network_requests.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.

    Returns
    -------
    str
        Confirmation message that capture has started.

    Notes
    -----
    - Call before navigation to capture all requests
    - POST data is truncated to first 500 characters
    - Use get_network_requests to retrieve and optionally clear captured data
    - Capture is page-specific; navigation may require re-starting capture
    """
    try:
        logger.info("[start_network_capture] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)
        _network_requests[page_key] = []

        def handle_request(request):
            _network_requests[page_key].append({
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "headers": dict(request.headers) if request.headers else {},
                "post_data": request.post_data[:500] if request.post_data else None,
            })

        page.on("request", handle_request)

        result = "Network request capture started"
        logger.info(f"[start_network_capture] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to start network capture: {str(e)}"
        logger.error(f"[start_network_capture] {error_msg}")
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
