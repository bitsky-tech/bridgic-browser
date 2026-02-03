"""
Browser dialog handling tools.

This module provides tools for handling JavaScript dialogs (alert, confirm,
prompt) and file chooser dialogs.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)

# Storage for pending dialog handlers
_dialog_handlers: dict = {}


def _get_page_key(page) -> str:
    """Get a unique key for a page to store data."""
    return str(id(page))


async def setup_dialog_handler(
    browser: "Browser",
    default_action: str = "accept",
    default_prompt_text: Optional[str] = None,
) -> str:
    """Set up automatic dialog handling for all future dialogs.

    Configure automatic handling of JavaScript dialogs (alert, confirm,
    prompt). Once set up, ALL dialogs on the current page will be
    automatically handled until remove_dialog_handler is called.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    default_action : str, optional
        Action to take on dialogs: "accept" or "dismiss". Default is "accept".
    default_prompt_text : str, optional
        Text to enter for prompt() dialogs. Default is empty string.

    Returns
    -------
    str
        Confirmation message with the configured action.

    Notes
    -----
    - Handler stays active until remove_dialog_handler is called
    - Only one handler per page; calling again replaces the previous
    - Handler is page-specific; navigating to new page may require re-setup
    - For one-time handling, use handle_dialog instead
    """
    try:
        logger.info(f"[setup_dialog_handler] start action={default_action}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)

        async def handle_dialog(dialog):
            dialog_type = dialog.type
            message = dialog.message
            logger.info(f"[dialog_handler] type={dialog_type} message={message}")

            if default_action == "accept":
                if dialog_type == "prompt" and default_prompt_text is not None:
                    await dialog.accept(default_prompt_text)
                else:
                    await dialog.accept()
            else:
                await dialog.dismiss()

        # Remove existing handler if any
        if page_key in _dialog_handlers:
            page.remove_listener("dialog", _dialog_handlers[page_key])

        # Store and add new handler
        _dialog_handlers[page_key] = handle_dialog
        page.on("dialog", handle_dialog)

        result = f"Dialog handler set up with default action: {default_action}"
        logger.info(f"[setup_dialog_handler] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to setup dialog handler: {str(e)}"
        logger.error(f"[setup_dialog_handler] {error_msg}")
        return error_msg


async def handle_dialog(
    browser: "Browser",
    accept: bool,
    prompt_text: Optional[str] = None,
) -> str:
    """Handle the next dialog that appears.

    Set up a one-time handler for the next JavaScript dialog that appears.
    The handler will accept or dismiss the dialog based on the parameters.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    accept : bool
        Whether to accept (True) or dismiss (False) the dialog.
    prompt_text : Optional[str], optional
        Text to enter for prompt dialogs. Only used when accept is True.
        Default is None.

    Returns
    -------
    str
        Operation result message.

    Notes
    -----
    This sets up a one-time handler that will handle the very next dialog
    and then remove itself. Use setup_dialog_handler for persistent
    automatic handling.
    """
    try:
        logger.info(f"[handle_dialog] start accept={accept} prompt_text={prompt_text}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        handled = {"done": False, "type": None, "message": None}

        async def one_time_handler(dialog):
            if handled["done"]:
                return

            handled["done"] = True
            handled["type"] = dialog.type
            handled["message"] = dialog.message

            if accept:
                if dialog.type == "prompt" and prompt_text is not None:
                    await dialog.accept(prompt_text)
                else:
                    await dialog.accept()
            else:
                await dialog.dismiss()

        page.once("dialog", one_time_handler)

        action = "accept" if accept else "dismiss"
        result = f"Dialog handler ready to {action} the next dialog"
        logger.info(f"[handle_dialog] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to set up dialog handler: {str(e)}"
        logger.error(f"[handle_dialog] {error_msg}")
        return error_msg


async def remove_dialog_handler(browser: "Browser") -> str:
    """Remove the automatic dialog handler.

    Remove any previously set up automatic dialog handler for the current
    page. After this, dialogs will not be handled automatically.

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
        logger.info("[remove_dialog_handler] start")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        page_key = _get_page_key(page)

        if page_key in _dialog_handlers:
            page.remove_listener("dialog", _dialog_handlers[page_key])
            del _dialog_handlers[page_key]
            result = "Dialog handler removed"
        else:
            result = "No dialog handler was set up"

        logger.info(f"[remove_dialog_handler] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to remove dialog handler: {str(e)}"
        logger.error(f"[remove_dialog_handler] {error_msg}")
        return error_msg
