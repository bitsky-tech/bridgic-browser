"""
Browser keyboard tools.

This module provides tools for keyboard operations, including typing text,
pressing keys, and key combinations.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, List, Dict, Optional

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)


# NOTE: type_text_by_ref has been merged into input_text_by_ref (in _browser_action_tools.py)
# Use input_text_by_ref(browser, ref, text, slowly=True) for the same functionality


async def press_sequentially(
    browser: "Browser",
    text: str,
    submit: bool = False,
) -> str:
    """Type text sequentially using keyboard press events.

    Type text character by character using keyboard press events on the
    currently focused element. This is useful when you need to trigger
    individual key events for each character.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    text : str
        Text to type character by character.
    submit : bool, optional
        Whether to press Enter after typing to submit. Default is False.

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[press_sequentially] start text_len={len(text)} submit={submit}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        # Type each character
        for char in text:
            await page.keyboard.press(char)

        if submit:
            await page.keyboard.press("Enter")

        submit_msg = " and submitted" if submit else ""
        result = f"Typed {len(text)} characters sequentially{submit_msg}"
        logger.info(f"[press_sequentially] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to type sequentially: {str(e)}"
        logger.error(f"[press_sequentially] {error_msg}")
        return error_msg


async def key_down(browser: "Browser", key: str) -> str:
    """Press and hold a key.

    Press and hold the specified key without releasing it.
    Use key_up() to release the key.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    key : str
        Key name to press. Examples: "Shift", "Control", "Alt", "a", "Enter".

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.

    Notes
    -----
    This is useful for key combinations where you need to hold a modifier
    key (like Shift or Control) while pressing other keys.
    """
    try:
        logger.info(f"[key_down] start key={key}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.keyboard.down(key)
        result = f"Key '{key}' pressed down"
        logger.info(f"[key_down] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to press key down: {str(e)}"
        logger.error(f"[key_down] {error_msg}")
        return error_msg


async def key_up(browser: "Browser", key: str) -> str:
    """Release a held key.

    Release a key that was previously pressed with key_down().

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    key : str
        Key name to release. Examples: "Shift", "Control", "Alt", "a", "Enter".

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[key_up] start key={key}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.keyboard.up(key)
        result = f"Key '{key}' released"
        logger.info(f"[key_up] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to release key: {str(e)}"
        logger.error(f"[key_up] {error_msg}")
        return error_msg


async def fill_form(
    browser: "Browser",
    fields: List[Dict[str, str]],
    submit: bool = False,
) -> str:
    """Fill multiple form fields at once.

    Fill multiple form fields by their refs in a single operation.
    Each field is specified as a dict with 'ref' and 'value' keys.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    fields : List[Dict[str, str]]
        List of field specifications. Each dict should have:
        - 'ref': Element ref from snapshot (e.g., "e1")
        - 'value': Value to fill into the field
    submit : bool, optional
        Whether to press Enter after filling the last field. Default is False.

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message with the number of fields filled. On failure, returns
        an error message.

    Examples
    --------
    >>> await fill_form(browser, [
    ...     {"ref": "e1", "value": "john@example.com"},
    ...     {"ref": "e2", "value": "password123"},
    ... ], submit=True)
    """
    try:
        logger.info(f"[fill_form] start fields_count={len(fields)} submit={submit}")

        if not fields:
            return "No fields provided to fill"

        filled_count = 0
        errors = []

        for field in fields:
            ref = field.get("ref")
            value = field.get("value", "")

            if not ref:
                errors.append("Field missing 'ref' key")
                continue

            locator = await browser.get_element_by_ref(ref)
            if locator is None:
                errors.append(f"Element ref {ref} not available")
                continue

            try:
                await locator.fill(value)
                filled_count += 1
            except Exception as e:
                errors.append(f"Failed to fill {ref}: {str(e)}")

        if submit and filled_count > 0:
            page = await browser.get_current_page()
            if page:
                await page.keyboard.press("Enter")

        submit_msg = " and submitted" if submit else ""
        if errors:
            error_details = "; ".join(errors)
            result = f"Filled {filled_count}/{len(fields)} fields{submit_msg}. Errors: {error_details}"
        else:
            result = f"Filled {filled_count} form fields{submit_msg}"

        logger.info(f"[fill_form] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to fill form: {str(e)}"
        logger.error(f"[fill_form] {error_msg}")
        return error_msg


async def insert_text(browser: "Browser", text: str) -> str:
    """Insert text at the current cursor position.

    Insert text into the currently focused element without triggering
    individual key events. This is faster than typing but may not
    trigger certain event handlers.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    text : str
        Text to insert at cursor position.

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[insert_text] start text_len={len(text)}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.keyboard.insert_text(text)
        result = f"Inserted text ({len(text)} characters)"
        logger.info(f"[insert_text] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to insert text: {str(e)}"
        logger.error(f"[insert_text] {error_msg}")
        return error_msg
