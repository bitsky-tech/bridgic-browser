"""
Browser navigation, page control, and general operation tools.

These tool functions are designed to be used with BrowserToolSetBuilder,
which binds them to a Browser instance. Each function takes a Browser
as its first parameter, which will be automatically provided by the
BrowserToolSpec when the tool is called by an LLM.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional, List
from ..utils import model_to_llm_string

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)

# ==================== Navigation and Browser Control Tools ====================

async def search(browser: "Browser", query: str, engine: str = "duckduckgo") -> str:
    """Search using a search engine.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    query : str
        Search query string.
    engine : str, optional
        "duckduckgo" (default), "google", or "bing".

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f"[search] start engine={engine} query={query!r}")

        # Parameter validation
        if not query or not query.strip():
            return "Search query cannot be empty"

        query = query.strip()
        engine = engine.strip().lower() if engine else "duckduckgo"

        import urllib.parse

        # URL encode query for safety
        encoded_query = urllib.parse.quote_plus(query)

        # Build search URL based on engine
        search_engines = {
            'duckduckgo': f'https://duckduckgo.com/?q={encoded_query}',
            'google': f'https://www.google.com/search?q={encoded_query}&udm=14',
            'bing': f'https://www.bing.com/search?q={encoded_query}',
        }

        if engine not in search_engines:
            error_msg = f'Unsupported search engine: {engine}. Options: duckduckgo, google, bing'
            logger.error(f'[search] {error_msg}')
            return error_msg

        search_url = search_engines[engine]

        # Navigate to search URL using Playwright
        try:
            await browser.navigate_to(search_url)
            result = f"Searched on {engine.title()}: '{query}'"
            logger.info(f"[search] done {result}")
            return result
        except Exception as e:
            logger.error(f"[search] failed engine={engine} error={type(e).__name__}: {e}")
            error_msg = f'Search on {engine} failed for "{query}": {str(e)}'
            return error_msg
    except Exception as e:
        error_msg = f"Search failed: {str(e)}"
        logger.error(f"[search] failed error={type(e).__name__}: {error_msg}")
        return error_msg


async def navigate_to_url(browser: "Browser", url: str) -> str:
    """Navigate to URL in current tab. Use new_tab(url) for new tab.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    url : str
        URL to navigate to. Auto-prepends "http://" if missing protocol.

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f"[navigate_to_url] start url={url}")

        # Parameter validation
        if not url or not url.strip():
            return "URL cannot be empty"

        url = url.strip()

        # Security: Block dangerous URL schemes that could execute code or access local files
        blocked_schemes = ["javascript:", "data:", "vbscript:", "about:"]
        url_lower = url.lower()
        for scheme in blocked_schemes:
            if url_lower.startswith(scheme):
                error_msg = f"URL scheme '{scheme}' is not allowed for security reasons"
                logger.warning(f"[navigate_to_url] blocked: {error_msg}")
                return error_msg

        # Basic URL format validation
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
            # If not a complete URL, try adding http://
            if not url.startswith("/"):
                url = f"http://{url}"

        await browser.navigate_to(url)
        result = f"Navigated to: {url}"

        logger.info(f"[navigate_to_url] done {result}")
        return result
    except Exception as e:
        error_msg = f"Navigation failed: {str(e)}"
        logger.error(f"[navigate_to_url] {error_msg}")
        return error_msg


async def go_back(browser: "Browser") -> str:
    """Navigate back to previous page in history.

    Parameters
    ----------
    browser : Browser
        Browser instance.

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f"[go_back] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        # Use Playwright's go_back()
        await page.go_back()
        result = "Navigated back to previous page"
        logger.info(f"[go_back] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to navigate back: {str(e)}"
        logger.error(f"[go_back] {error_msg}")
        # Playwright's go_back() raises exception when navigation is not possible
        if "Cannot navigate" in str(e) or "no previous entry" in str(e):
            result = "Cannot navigate back: no previous page in history"
            logger.info(f"[go_back] {result}")
            return result
        return error_msg


# NOTE: wait() function has been removed - use wait_for(time=...) instead


async def go_forward(browser: "Browser") -> str:
    """Navigate forward to next page in history.

    Parameters
    ----------
    browser : Browser
        Browser instance.

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f"[go_forward] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"
        await page.go_forward()
        result = "Navigated forward to next page"
        logger.info(f"[go_forward] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to navigate forward: {str(e)}"
        logger.error(f"[go_forward] {error_msg}")
        return error_msg


async def reload_page(browser: "Browser") -> str:
    """Reload the current page.

    Parameters
    ----------
    browser : Browser
        Browser instance.

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f"[reload_page] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"
        await page.reload(wait_until="networkidle")
        result = "Page reloaded"
        logger.info(f"[reload_page] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to reload page: {str(e)}"
        logger.error(f"[reload_page] {error_msg}")
        return error_msg


async def get_current_page_info(browser: "Browser") -> str:
    """Get current page info: URL, title, viewport size, scroll position.

    Parameters
    ----------
    browser : Browser
        Browser instance.

    Returns
    -------
    str
        Page info string.
    """
    try:
        logger.info(f"[get_current_page_info] start")

        page_info = await browser.get_current_page_info()
        if page_info is None:
            error_msg = "No active page available"
            logger.error(f"[get_current_page_info] {error_msg}")
            return error_msg
        result = (
            f"url={page_info.url!r}, title={page_info.title!r}, "
            f"viewport={page_info.viewport_width}x{page_info.viewport_height}, "
            f"page={page_info.page_width}x{page_info.page_height}, "
            f"scroll=({page_info.scroll_x},{page_info.scroll_y})"
        )
        logger.info(f"[get_current_page_info] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to get current page info: {str(e)}"
        logger.error(f"[get_current_page_info] {error_msg}")
        return error_msg

# ==================== Page Element Interaction Tools ====================

# NOTE: scroll_page() function has been removed - use mouse_wheel() instead


async def press_key(browser: "Browser", key: str) -> str:
    """Press a keyboard key or combination (e.g., "Enter", "Control+A").

    Parameters
    ----------
    browser : Browser
        Browser instance.
    key : str
        Key name or combination (e.g., "Tab", "Control+C", "Shift+Tab").

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f"[press_key] start key={key}")

        # Parameter validation
        if not key or not key.strip():
            return "Key name cannot be empty"

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        # Use Playwright's page.keyboard.press()
        await page.keyboard.press(key.strip())
        result = f"Pressed key: {key}"
        logger.info(f"[press_key] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to press key: {str(e)}"
        logger.error(f"[press_key] {error_msg}")
        return error_msg


async def scroll_to_text(browser: "Browser", text: str) -> str:
    """Scroll to make the specified text visible on page.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    text : str
        Text to find and scroll to.

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f"[scroll_to_text] start text={text!r}")

        # Parameter validation
        if not text or not text.strip():
            return "Text to find cannot be empty"

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        # Use locator to find text and scroll to it
        try:
            locator = page.get_by_text(text.strip(), exact=False).first
            # Check if element exists by trying to get its bounding box
            bounding_box = await locator.bounding_box(timeout=5000)
            if bounding_box:
                await locator.scroll_into_view_if_needed()
                result = f'Scrolled to text: {text}'
                logger.info(f"[scroll_to_text] done {result}")
                return result
            else:
                result = f'Text not found: {text}'
                logger.warning(f"[scroll_to_text] done {result}")
                return result
        except Exception as e:
            # Text not found or not visible
            result = f"Text '{text}' not found or not visible"
            logger.info(f"[scroll_to_text] done {result}")
            return result
    except Exception as e:
        error_msg = f"Failed to scroll to text: {str(e)}"
        logger.error(f"[scroll_to_text] {error_msg}")
        return error_msg


# ==================== JavaScript Execution Tools ====================

async def evaluate_javascript(browser: "Browser", code: str) -> str:
    """Execute JavaScript in page context. **Only run trusted code.**

    Parameters
    ----------
    browser : Browser
        Browser instance.
    code : str
        Arrow function format, e.g., "() => document.title".

    Returns
    -------
    str
        Execution result as string.
    """
    try:
        logger.info(f"[evaluate_javascript] start code_preview={code[:100] if code and len(code) > 100 else code!r}")

        # Parameter validation
        if not code or not code.strip():
            return "JavaScript code cannot be empty"

        code = code.strip()

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        # Use Playwright's page.evaluate() to execute JavaScript
        result = await page.evaluate(code)

        # Handle different return types
        if isinstance(result, bool):
            # Convert boolean to string
            result_str = "True" if result else "False"
            logger.info(f"[evaluate_javascript] done result={result_str!r}")
            return result_str
        elif result is None:
            logger.info(f"[evaluate_javascript] done result=None")
            return "None"
        elif isinstance(result, (int, float)):
            result_str = str(result)
            logger.info(f"[evaluate_javascript] done result={result_str!r}")
            return result_str
        else:
            # Convert other types to string
            result_str = str(result)
            logger.info(f"[evaluate_javascript] done result_preview={result_str[:200]!r} result_len={len(result_str)}")
            return result_str
    except Exception as e:
        error_msg = f"Failed to execute JavaScript: {str(e)}"
        logger.error(f"[evaluate_javascript] {error_msg}")
        return error_msg


# ==================== Tab Management Tools ====================

async def new_tab(browser: "Browser", url: Optional[str] = None) -> str:
    """Create a new tab.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    url : Optional[str], optional
        URL to open. If None or empty, creates a blank tab.

    Returns
    -------
    str
        Operation result message.
    """
    try:
        logger.info(f"[new_tab] start url={url}")

        # Handle URL: treat empty string as None
        if url is not None:
            url = url.strip()
            if not url:
                url = None

        # Validate and fix URL format if provided
        if url:
            if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
                if not url.startswith("/"):
                    url = f"http://{url}"

        await browser.new_page(url)
        if url:
            result = f"Opened new tab with URL: {url}"
        else:
            result = "Created new blank tab"
        logger.info(f"[new_tab] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to create new tab: {str(e)}"
        logger.error(f"[new_tab] {error_msg}")
        return error_msg


async def get_tabs(browser: "Browser") -> str:
    """Get information about all open tabs.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.

    Returns
    -------
    str
        List of JSON strings with tab info (page_id, url, title), or error message.
    """
    try:
        logger.info(f"[get_tabs] start")

        page_descs = await browser.get_all_page_descs()
        tabs_info = [model_to_llm_string(page_desc) for page_desc in page_descs]
        logger.info(f"[get_tabs] done tabs={len(tabs_info)}")
        return "\n".join(tabs_info)
    except Exception as e:
        error_msg = f"Failed to get tabs info: {str(e)}"
        logger.error(f"[get_tabs] {error_msg}")
        return error_msg


async def switch_tab(browser: "Browser", page_id: str) -> str:
    """Switch to specified tab.

    Switch the active tab to the tab identified by the given page_id.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    page_id : str
        Target tab's page_id, format: "page_xxxx".

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.

    Notes
    -----
    The page_id format is "page_xxxx" where xxxx is a unique identifier.
    Use get_tabs() to retrieve available page_ids.
    """
    try:
        logger.info(f"[switch_tab] start page_id={page_id}")

        # Get all pages and switch to target page using Playwright
        success, result = await browser.switch_to_page(page_id)
        if not success:
            logger.error(f"[switch_tab] {result}")
            return result
        logger.info(f"[switch_tab] done page_id={page_id}")
        return result
    except Exception as e:
        error_msg = f"Failed to switch tab: {str(e)}"
        logger.error(f"[switch_tab] {error_msg}")
        return error_msg


async def close_tab(browser: "Browser", page_id: Optional[str] = None) -> str:
    """Close a tab.

    Close the specified tab by page_id, or close the current tab if
    page_id is None.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    page_id : Optional[str], optional
        page_id of the tab to close. If None, closes the current tab.
        Format: "page_xxxx".

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.

    Notes
    -----
    If the closed tab is the current tab, the browser will automatically
    switch to another open tab if available.
    """
    try:
        logger.info(f"[close_tab] start page_id={page_id}")

        result = ""
        # Close current tab
        if page_id is None:
            page = await browser.get_current_page()
            if page is None:
                return "No active page available"
            success, closed_result = await browser.close_page(page)
            if not success:
                logger.error(f"[close_tab] {closed_result}")
                return closed_result
            result = closed_result
        else:
            success, closed_result = await browser.close_page(page_id)
            if not success:
                logger.error(f"[close_tab] {closed_result}")
                return closed_result
            result = closed_result

        logger.info(f"[close_tab] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to close tab: {str(e)}"
        logger.error(f"[close_tab] {error_msg}")
        return error_msg


# ==================== Browser Control Tools ====================

async def browser_close(browser: "Browser") -> str:
    """Close the browser.

    Close the browser and clean up all resources. This will close all
    tabs and the browser window.

    Parameters
    ----------
    browser : Browser
        Browser instance to close.

    Returns
    -------
    str
        Operation result message.
    """
    try:
        logger.info("[browser_close] start")

        await browser.kill()

        result = "Browser closed successfully"
        logger.info(f"[browser_close] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to close browser: {str(e)}"
        logger.error(f"[browser_close] {error_msg}")
        return error_msg


async def browser_resize(
    browser: "Browser",
    width: int,
    height: int,
) -> str:
    """Resize the browser viewport.

    Change the browser viewport size to the specified dimensions.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    width : int
        New viewport width in pixels.
    height : int
        New viewport height in pixels.

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message with the new dimensions. On failure, returns an error message.
    """
    try:
        logger.info(f"[browser_resize] start width={width} height={height}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.set_viewport_size({"width": width, "height": height})

        result = f"Browser viewport resized to {width}x{height}"
        logger.info(f"[browser_resize] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to resize browser: {str(e)}"
        logger.error(f"[browser_resize] {error_msg}")
        return error_msg


async def wait_for(
    browser: "Browser",
    time_seconds: Optional[float] = None,
    text: Optional[str] = None,
    text_gone: Optional[str] = None,
    selector: Optional[str] = None,
    state: str = "visible",
    timeout_ms: float = 30000,
) -> str:
    """Wait for a condition: time delay, text appearance/disappearance, or element state.

    **Priority**: Only ONE condition is used: time_seconds > text > text_gone > selector.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    time_seconds : float, optional
        Fixed delay in SECONDS (e.g., 2.5 = 2.5 seconds, max 60).
        If provided, ignores all other parameters.
    text : str, optional
        Wait until this text appears and is visible on the page.
    text_gone : str, optional
        Wait until this text disappears from the page.
    selector : str, optional
        CSS selector to wait for (e.g., "#submit-btn", ".loading-spinner").
    state : str, optional
        Element state when using selector: "visible" (default), "hidden",
        "attached", "detached".
    timeout_ms : float, optional
        Maximum wait time in MILLISECONDS for text/selector conditions.
        Default is 30000 (30 seconds). Does not apply to time_seconds.

    Returns
    -------
    str
        Success: "Waited for X seconds" or "Text 'X' appeared on the page"
        Failure: "Wait condition not met: {error}"

    Examples
    --------
    wait_for(time_seconds=3)  # Wait 3 seconds
    wait_for(text="Success")  # Wait for "Success" to appear
    wait_for(text_gone="Loading...")  # Wait for loading text to disappear
    wait_for(selector=".modal", state="visible")  # Wait for modal
    """
    try:
        logger.info(f"[wait_for] start time_seconds={time_seconds} text={text} text_gone={text_gone} selector={selector}")

        # Wait for time
        if time_seconds is not None:
            import asyncio
            actual_seconds = min(max(float(time_seconds), 0), 60)
            await asyncio.sleep(actual_seconds)
            result = f"Waited for {actual_seconds} seconds"
            logger.info(f"[wait_for] done {result}")
            return result

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        # Wait for text to appear
        if text is not None:
            locator = page.get_by_text(text, exact=False)
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
            result = f"Text '{text}' appeared on the page"
            logger.info(f"[wait_for] done {result}")
            return result

        # Wait for text to disappear
        if text_gone is not None:
            locator = page.get_by_text(text_gone, exact=False)
            await locator.first.wait_for(state="hidden", timeout=timeout_ms)
            result = f"Text '{text_gone}' disappeared from the page"
            logger.info(f"[wait_for] done {result}")
            return result

        # Wait for selector
        if selector is not None:
            locator = page.locator(selector)
            await locator.first.wait_for(state=state, timeout=timeout_ms)
            result = f"Selector '{selector}' reached state '{state}'"
            logger.info(f"[wait_for] done {result}")
            return result

        return "No wait condition specified"
    except Exception as e:
        error_msg = f"Wait condition not met: {str(e)}"
        logger.error(f"[wait_for] {error_msg}")
        return error_msg


# NOTE: manage_tabs() is commented out - use new_tab(), get_tabs(), switch_tab(), close_tab() instead
# async def manage_tabs(
#     browser: "Browser",
#     action: str,
#     index: Optional[int] = None,
#     url: Optional[str] = None,
# ) -> str:
#     """Unified tab management.
#
#     Perform various tab operations: list, create, switch, close.
#
#     Parameters
#     ----------
#     browser : Browser
#         Browser instance to use.
#     action : str
#         Action to perform. Options: "list", "new", "switch", "close".
#     index : Optional[int], optional
#         Tab index for switch/close actions (0-based).
#     url : Optional[str], optional
#         URL to open when creating a new tab.
#
#     Returns
#     -------
#     str
#         Operation result message or tab list for "list" action.
#     """
#     try:
#         logger.info(f"[manage_tabs] start action={action} index={index} url={url}")
#
#         if action == "list":
#             # List all tabs
#             page_descs = await browser.get_all_page_descs()
#             tabs_info = []
#             for i, desc in enumerate(page_descs):
#                 tabs_info.append(f"{i}: {desc.title} ({desc.url})")
#             result = "\n".join(tabs_info) if tabs_info else "No tabs open"
#             logger.info(f"[manage_tabs] done listed {len(page_descs)} tabs")
#             return result
#
#         elif action == "new":
#             # Create new tab
#             await browser.new_page(url)
#             result = f"Created new tab" + (f" with URL: {url}" if url else "")
#             logger.info(f"[manage_tabs] done {result}")
#             return result
#
#         elif action == "switch":
#             # Switch to tab by index
#             if index is None:
#                 return "Tab index required for switch action"
#             page_descs = await browser.get_all_page_descs()
#             if index < 0 or index >= len(page_descs):
#                 return f"Invalid tab index: {index}. Available: 0-{len(page_descs)-1}"
#             page_id = page_descs[index].page_id
#             _, result = await browser.switch_to_page(page_id)
#             logger.info(f"[manage_tabs] done {result}")
#             return result
#
#         elif action == "close":
#             # Close tab by index
#             if index is None:
#                 # Close current tab
#                 page = await browser.get_current_page()
#                 if page is None:
#                     return "No active page to close"
#                 _, result = await browser.close_page(page)
#             else:
#                 page_descs = await browser.get_all_page_descs()
#                 if index < 0 or index >= len(page_descs):
#                     return f"Invalid tab index: {index}. Available: 0-{len(page_descs)-1}"
#                 page_id = page_descs[index].page_id
#                 _, result = await browser.close_page(page_id)
#             logger.info(f"[manage_tabs] done {result}")
#             return result
#
#         else:
#             return f"Unknown action: {action}. Options: list, new, switch, close"
#
#     except Exception as e:
#         error_msg = f"Tab management failed: {str(e)}"
#         logger.error(f"[manage_tabs] {error_msg}")
#         return error_msg


# async def run_playwright_code(browser: "Browser", code: str) -> str:
#     """Execute arbitrary Playwright code.

#     Execute custom Playwright code with access to the current page.
#     The code should be a Python async function body that uses 'page'
#     variable.

#     Parameters
#     ----------
#     browser : Browser
#         Browser instance to use.
#     code : str
#         Python code to execute. Has access to 'page' (current Page object).
#         Must be an async code block.

#     Returns
#     -------
#     str
#         Result of the code execution, or error message on failure.

#     Warnings
#     --------
#     This function executes arbitrary code and should be used with caution.
#     Only use with trusted input.

#     Examples
#     --------
#     >>> await run_playwright_code(browser, '''
#     ... # Get all links on the page
#     ... links = await page.locator('a').all()
#     ... result = [await link.get_attribute('href') for link in links[:5]]
#     ... return str(result)
#     ... ''')
#     """
#     try:
#         logger.info(f"[run_playwright_code] start code_len={len(code)}")

#         page = await browser.get_current_page()
#         if page is None:
#             return "No active page available"

#         # Create async function from code
#         func_code = f"""
# async def _playwright_func(page):
# {chr(10).join('    ' + line for line in code.strip().split(chr(10)))}
# """
#         # Execute the function
#         local_vars = {}
#         exec(func_code, {"__builtins__": __builtins__}, local_vars)
#         result = await local_vars["_playwright_func"](page)

#         result_str = str(result) if result is not None else "Code executed successfully"
#         logger.info(f"[run_playwright_code] done result_len={len(result_str)}")
#         return result_str
#     except Exception as e:
#         error_msg = f"Failed to execute Playwright code: {str(e)}"
#         logger.error(f"[run_playwright_code] {error_msg}")
#         return error_msg
