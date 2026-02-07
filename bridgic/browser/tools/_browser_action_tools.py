"""
Browser interaction tools based on element refs.

This module provides tools for interacting with browser elements using
element references (refs) obtained from page snapshots.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..session._browser import Browser


async def input_text_by_ref(
    browser: "Browser",
    ref: str,
    text: str,
    clear: bool = True,
    is_secret: bool = False,
    slowly: bool = False,
    submit: bool = False,
) -> str:
    """Input text into an element by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1", "e2").
    text : str
        Text to input.
    clear : bool, optional
        Clear field first. Default True.
    is_secret : bool, optional
        Hide text in result message. Default False.
    slowly : bool, optional
        Type with delays (simulates real typing). Default False.
    submit : bool, optional
        Press Enter after typing. Default False.

    Returns
    -------
    str
        Result message.
    """
    try:
        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[input_text_by_ref] {msg}')
            return msg

        # Input text
        if clear:
            await locator.clear()

        if slowly:
            # Use type() for realistic typing simulation
            await locator.focus()
            await locator.type(text, delay=100)
        else:
            # Use fill() for fast input
            await locator.fill(text)

        # Submit if requested
        if submit:
            page = await browser.get_current_page()
            if page:
                await page.keyboard.press("Enter")

        msg = f"Input text '{text}'"
        if is_secret:
            msg = "Successfully input sensitive information"
        if submit:
            msg += " and submitted"

        logger.info(f'[input_text_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[input_text_by_ref] Failed to input text: {type(e).__name__}: {e}')
        error_msg = f'Failed to input text to element {ref}: {e}'
        return error_msg


async def click_element_by_ref(browser: "Browser", ref: str) -> str:
    """Click an element by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1", "e2").

    Returns
    -------
    str
        Result message.
    """
    try:
        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[click_element_by_ref] {msg}')
            return msg

        # Click element
        await locator.click()

        msg = f'Clicked element {ref}'
        logger.info(f'[click_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[click_element_by_ref] Failed to click element: {type(e).__name__}: {e}')
        error_msg = f'Failed to click element {ref}: {str(e)}'
        return error_msg


async def get_dropdown_options_by_ref(browser: "Browser", ref: str) -> str:
    """Get all options from a dropdown/select element.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").

    Returns
    -------
    str
        Numbered list: "1. Option Text (value: val)"
    """
    try:
        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[get_dropdown_options_by_ref] {msg}')
            return msg

        # Get dropdown options
        options = await locator.locator("option").all()
        if not options:
            return 'This dropdown has no options'

        option_texts = []
        for i, option in enumerate(options):
            text = await option.text_content()
            value = await option.get_attribute("value")
            if text:
                option_texts.append(f"{i + 1}. {text.strip()}" + (f" (value: {value})" if value else ""))
        
        result = '\n'.join(option_texts) if option_texts else 'Unable to get dropdown options'
        logger.info(f'[get_dropdown_options_by_ref] Retrieved dropdown options')
        return result

    except Exception as e:
        logger.error(f'[get_dropdown_options_by_ref] Failed to get dropdown options: {type(e).__name__}: {e}')
        error_msg = f'Failed to get dropdown options for element {ref}: {str(e)}'
        return error_msg


async def select_dropdown_option_by_ref(browser: "Browser", ref: str, text: str) -> str:
    """Select an option from a dropdown by visible text or value.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").
    text : str
        Option text or value to select.

    Returns
    -------
    str
        Result message.
    """
    try:
        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[select_dropdown_option_by_ref] {msg}')
            return msg

        # Select dropdown option
        await locator.select_option(text)
        
        msg = f'Selected option: {text}'
        logger.info(f'[select_dropdown_option_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[select_dropdown_option_by_ref] Failed to select dropdown option: {type(e).__name__}: {e}')
        error_msg = f'Failed to select dropdown option "{text}" for element {ref}: {str(e)}'
        return error_msg


async def hover_element_by_ref(browser: "Browser", ref: str) -> str:
    """Hover mouse over an element by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").

    Returns
    -------
    str
        Result message.
    """
    try:
        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[hover_element_by_ref] {msg}')
            return msg

        # Hover element
        await locator.hover()
        
        msg = f'Hovered over element ref {ref}'
        logger.info(f'[hover_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[hover_element_by_ref] Failed to hover element: {type(e).__name__}: {e}')
        error_msg = f'Failed to hover element {ref}: {str(e)}'
        return error_msg


async def focus_element_by_ref(browser: "Browser", ref: str) -> str:
    """Focus an element by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").

    Returns
    -------
    str
        Result message.
    """
    try:
        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[focus_element_by_ref] {msg}')
            return msg

        # Focus element
        await locator.focus()
        
        msg = f'Focused element ref {ref}'
        logger.info(f'[focus_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[focus_element_by_ref] Failed to focus element: {type(e).__name__}: {e}')
        error_msg = f'Failed to focus element {ref}: {str(e)}'
        return error_msg


async def evaluate_javascript_on_ref(browser: "Browser", ref: str, code: str) -> str:
    """Execute JavaScript on an element. `this` refers to the element.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").
    code : str
        Arrow function, e.g., "() => this.textContent".

    Returns
    -------
    str
        Execution result as string.
    """
    try:
        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[evaluate_javascript_on_ref] {msg}')
            return msg

        # Execute JavaScript
        result = await locator.evaluate(code)
        
        # Convert result to string
        if result is None:
            result_str = "null"
        elif isinstance(result, str):
            result_str = result
        else:
            result_str = str(result)
        
        logger.info(f'[evaluate_javascript_on_ref] Execution successful, result length: {len(result_str)}')
        return result_str

    except Exception as e:
        logger.error(f'[evaluate_javascript_on_ref] Failed to execute JavaScript: {type(e).__name__}: {e}')
        error_msg = f'Failed to execute JavaScript on element {ref}: {str(e)}'
        return error_msg


async def upload_file_by_ref(browser: "Browser", ref: str, file_path: str) -> str:
    """Upload a file to a file input element by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").
    file_path : str
        Path to the file to upload.

    Returns
    -------
    str
        Result message.
    """
    try:
        import os

        # Check if file exists
        if not os.path.exists(file_path):
            msg = f'File {file_path} does not exist'
            logger.error(f'[upload_file_by_ref] {msg}')
            return msg

        # Get Locator by ref
        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[upload_file_by_ref] {msg}')
            return msg

        # Check element type
        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
        if tag_name != "input":
            msg = f'Element ref {ref} is not a file input element (tag: {tag_name})'
            logger.error(f'[upload_file_by_ref] {msg}')
            return msg

        input_type = await locator.get_attribute("type")
        if input_type != "file":
            msg = f'Element ref {ref} is not a file input element (type: {input_type})'
            logger.error(f'[upload_file_by_ref] {msg}')
            return msg

        # Upload file
        await locator.set_input_files(file_path)

        msg = f'Successfully uploaded file to element ref {ref}'
        logger.info(f'[upload_file_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[upload_file_by_ref] Failed to upload file: {type(e).__name__}: {e}')
        error_msg = f'Failed to upload file to element {ref}: {str(e)}'
        return error_msg


async def drag_element_by_ref(
    browser: "Browser",
    start_ref: str,
    end_ref: str,
) -> str:
    """Drag element from start_ref and drop on end_ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    start_ref : str
        Element ref to drag (e.g., "e1").
    end_ref : str
        Element ref of drop target (e.g., "e2").

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f'[drag_element_by_ref] start start_ref={start_ref} end_ref={end_ref}')

        # Get source locator
        source_locator = await browser.get_element_by_ref(start_ref)
        if source_locator is None:
            msg = f'Source element ref {start_ref} is not available - page may have changed.'
            logger.warning(f'[drag_element_by_ref] {msg}')
            return msg

        # Get target locator
        target_locator = await browser.get_element_by_ref(end_ref)
        if target_locator is None:
            msg = f'Target element ref {end_ref} is not available - page may have changed.'
            logger.warning(f'[drag_element_by_ref] {msg}')
            return msg

        # Perform drag and drop
        await source_locator.drag_to(target_locator)

        msg = f'Dragged element {start_ref} to {end_ref}'
        logger.info(f'[drag_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[drag_element_by_ref] Failed to drag element: {type(e).__name__}: {e}')
        error_msg = f'Failed to drag element from {start_ref} to {end_ref}: {str(e)}'
        return error_msg


async def check_element_by_ref(browser: "Browser", ref: str) -> str:
    """Check a checkbox or radio button by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f'[check_element_by_ref] start ref={ref}')

        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[check_element_by_ref] {msg}')
            return msg

        await locator.check()

        msg = f'Checked element {ref}'
        logger.info(f'[check_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[check_element_by_ref] Failed to check element: {type(e).__name__}: {e}')
        error_msg = f'Failed to check element {ref}: {str(e)}'
        return error_msg


async def uncheck_element_by_ref(browser: "Browser", ref: str) -> str:
    """Uncheck a checkbox by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f'[uncheck_element_by_ref] start ref={ref}')

        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[uncheck_element_by_ref] {msg}')
            return msg

        await locator.uncheck()

        msg = f'Unchecked element {ref}'
        logger.info(f'[uncheck_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[uncheck_element_by_ref] Failed to uncheck element: {type(e).__name__}: {e}')
        error_msg = f'Failed to uncheck element {ref}: {str(e)}'
        return error_msg


async def double_click_element_by_ref(browser: "Browser", ref: str) -> str:
    """Double-click an element by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f'[double_click_element_by_ref] start ref={ref}')

        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[double_click_element_by_ref] {msg}')
            return msg

        await locator.dblclick()

        msg = f'Double-clicked element {ref}'
        logger.info(f'[double_click_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[double_click_element_by_ref] Failed to double-click element: {type(e).__name__}: {e}')
        error_msg = f'Failed to double-click element {ref}: {str(e)}'
        return error_msg


async def scroll_element_into_view_by_ref(browser: "Browser", ref: str) -> str:
    """Scroll page to make element visible in viewport.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").

    Returns
    -------
    str
        Result message.
    """
    try:
        logger.info(f'[scroll_element_into_view_by_ref] start ref={ref}')

        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
            logger.warning(f'[scroll_element_into_view_by_ref] {msg}')
            return msg

        await locator.scroll_into_view_if_needed()

        msg = f'Scrolled element {ref} into view'
        logger.info(f'[scroll_element_into_view_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[scroll_element_into_view_by_ref] Failed to scroll element into view: {type(e).__name__}: {e}')
        error_msg = f'Failed to scroll element {ref} into view: {str(e)}'
        return error_msg
