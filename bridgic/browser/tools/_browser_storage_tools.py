"""
Browser storage state tools.

This module provides tools for saving and restoring browser storage state
(cookies, localStorage, sessionStorage).
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)


async def save_storage_state(
    browser: "Browser",
    filename: Optional[str] = None,
) -> str:
    """Save the browser's storage state to a file.

    Save cookies, localStorage, and sessionStorage from the browser context
    to a JSON file. This state can be restored later with restore_storage_state.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    filename : Optional[str], optional
        Path to save the storage state. If not provided, saves to a
        temporary file and returns the path.

    Returns
    -------
    str
        On success: Returns the file path where state was saved.
        On failure: Returns an error message.

    Notes
    -----
    The storage state includes:
    - Cookies for all domains visited
    - Origins with their localStorage data

    This is useful for maintaining login sessions across browser restarts
    or sharing authenticated sessions.
    """
    try:
        logger.info(f"[save_storage_state] start filename={filename}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context

        # Determine output path
        if filename:
            if not filename.lower().endswith(".json"):
                filename = f"{filename}.json"
            output_path = filename

            # Create directory if needed
            dirname = os.path.dirname(filename)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        else:
            # Generate temporary file
            fd, output_path = tempfile.mkstemp(suffix=".json", prefix="browser_state_")
            os.close(fd)

        # Save storage state
        await context.storage_state(path=output_path)

        result = f"Storage state saved to: {output_path}"
        logger.info(f"[save_storage_state] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to save storage state: {str(e)}"
        logger.error(f"[save_storage_state] {error_msg}")
        return error_msg


async def restore_storage_state(
    browser: "Browser",
    filename: str,
) -> str:
    """Restore browser storage state from a file.

    Restore cookies and localStorage from a previously saved state file.
    This applies the storage state to the current browser context.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    filename : str
        Path to the storage state JSON file.

    Returns
    -------
    str
        On success: Returns a confirmation message.
        On failure: Returns an error message.

    Notes
    -----
    For full state restoration (including starting a fresh context with
    the saved state), consider using Browser(storage_state=filename)
    when creating the browser.

    This function adds cookies and localStorage to an existing context,
    which may not perfectly restore all state if the context already has
    conflicting data.
    """
    try:
        logger.info(f"[restore_storage_state] start filename={filename}")

        if not os.path.exists(filename):
            return f"Storage state file not found: {filename}"

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context

        # Load storage state
        with open(filename, "r") as f:
            state = json.load(f)

        # Add cookies
        cookies = state.get("cookies", [])
        if cookies:
            await context.add_cookies(cookies)

        # Handle localStorage by navigating to each origin and setting values
        origins = state.get("origins", [])
        for origin_data in origins:
            origin = origin_data.get("origin", "")
            local_storage = origin_data.get("localStorage", [])

            if local_storage and origin:
                # We need to navigate to the origin to set localStorage
                # This is a limitation - we'll set it via JavaScript
                current_url = page.url
                for item in local_storage:
                    name = item.get("name", "")
                    value = item.get("value", "")
                    if name:
                        await page.evaluate(
                            f"localStorage.setItem({json.dumps(name)}, {json.dumps(value)})"
                        )

        result = f"Storage state restored from: {filename} ({len(cookies)} cookies)"
        logger.info(f"[restore_storage_state] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to restore storage state: {str(e)}"
        logger.error(f"[restore_storage_state] {error_msg}")
        return error_msg


async def clear_cookies(browser: "Browser") -> str:
    """Clear all cookies from the browser context.

    Remove all cookies from the current browser context.

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
        logger.info("[clear_cookies] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context
        await context.clear_cookies()

        result = "All cookies cleared"
        logger.info(f"[clear_cookies] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to clear cookies: {str(e)}"
        logger.error(f"[clear_cookies] {error_msg}")
        return error_msg


async def get_cookies(
    browser: "Browser",
    urls: Optional[list] = None,
) -> str:
    """Get cookies from the browser context.

    Retrieve cookies for specified URLs or all cookies if no URLs provided.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    urls : Optional[list], optional
        List of URLs to get cookies for. If not provided, returns all cookies.

    Returns
    -------
    str
        JSON string containing the cookies.
    """
    try:
        logger.info(f"[get_cookies] start urls={urls}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context

        if urls:
            cookies = await context.cookies(urls)
        else:
            cookies = await context.cookies()

        result = json.dumps(cookies, indent=2)
        logger.info(f"[get_cookies] done count={len(cookies)}")
        return result
    except Exception as e:
        error_msg = f"Failed to get cookies: {str(e)}"
        logger.error(f"[get_cookies] {error_msg}")
        return error_msg


async def set_cookie(
    browser: "Browser",
    name: str,
    value: str,
    url: Optional[str] = None,
    domain: Optional[str] = None,
    path: str = "/",
    expires: Optional[float] = None,
    http_only: bool = False,
    secure: bool = False,
    same_site: Optional[str] = None,
) -> str:
    """Set a cookie in the browser context.

    Add or update a cookie with the specified properties.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    name : str
        Cookie name.
    value : str
        Cookie value.
    url : Optional[str], optional
        URL to associate the cookie with. Either url or domain must be specified.
    domain : Optional[str], optional
        Cookie domain. Either url or domain must be specified.
    path : str, optional
        Cookie path. Default is "/".
    expires : Optional[float], optional
        Unix timestamp when the cookie expires. Default is session cookie.
    http_only : bool, optional
        Whether the cookie is HTTP only. Default is False.
    secure : bool, optional
        Whether the cookie requires HTTPS. Default is False.
    same_site : Optional[str], optional
        SameSite attribute. Options: "Strict", "Lax", "None".

    Returns
    -------
    str
        Operation result message.
    """
    try:
        logger.info(f"[set_cookie] start name={name}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        if not url and not domain:
            return "Either url or domain must be specified"

        context = page.context

        cookie = {
            "name": name,
            "value": value,
            "path": path,
            "httpOnly": http_only,
            "secure": secure,
        }

        if url:
            cookie["url"] = url
        if domain:
            cookie["domain"] = domain
        if expires:
            cookie["expires"] = expires
        if same_site:
            cookie["sameSite"] = same_site

        await context.add_cookies([cookie])

        result = f"Cookie '{name}' set successfully"
        logger.info(f"[set_cookie] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to set cookie: {str(e)}"
        logger.error(f"[set_cookie] {error_msg}")
        return error_msg
