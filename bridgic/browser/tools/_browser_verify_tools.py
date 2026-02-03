"""
Browser verification and assertion tools.

This module provides tools for verifying page state, element visibility,
and content. Useful for testing and validation scenarios.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional, Literal

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)


async def verify_element_visible(
    browser: "Browser",
    role: str,
    accessible_name: str,
    timeout: float = 5000,
) -> str:
    """Verify that an element with the given role and name is visible.

    Check that an element matching the specified ARIA role and accessible
    name is visible on the page.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    role : str
        ARIA role of the element (e.g., "button", "link", "textbox",
        "heading", "listitem").
    accessible_name : str
        Accessible name of the element (usually its text content or
        aria-label).
    timeout : float, optional
        Maximum time to wait for the element in milliseconds.
        Default is 5000 (5 seconds).

    Returns
    -------
    str
        On success: Confirmation that the element is visible.
        On failure: Error message describing the verification failure.

    Examples
    --------
    >>> await verify_element_visible(browser, "button", "Submit")
    >>> await verify_element_visible(browser, "link", "Learn more")
    >>> await verify_element_visible(browser, "heading", "Welcome")
    """
    try:
        logger.info(f"[verify_element_visible] start role={role} name={accessible_name}")

        page = await browser.get_current_page()
        if page is None:
            return "FAIL: No active page available"

        # Use get_by_role to find element
        locator = page.get_by_role(role, name=accessible_name)

        try:
            await locator.wait_for(state="visible", timeout=timeout)
            result = f"PASS: Element with role '{role}' and name '{accessible_name}' is visible"
            logger.info(f"[verify_element_visible] {result}")
            return result
        except Exception:
            result = f"FAIL: Element with role '{role}' and name '{accessible_name}' is not visible"
            logger.warning(f"[verify_element_visible] {result}")
            return result
    except Exception as e:
        error_msg = f"FAIL: Verification error: {str(e)}"
        logger.error(f"[verify_element_visible] {error_msg}")
        return error_msg


async def verify_text_visible(
    browser: "Browser",
    text: str,
    exact: bool = False,
    timeout: float = 5000,
) -> str:
    """Verify that specific text is visible on the page.

    Check that the specified text appears and is visible on the page.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    text : str
        Text to search for on the page.
    exact : bool, optional
        Whether to match the text exactly. Default is False
        (substring match).
    timeout : float, optional
        Maximum time to wait for the text in milliseconds.
        Default is 5000 (5 seconds).

    Returns
    -------
    str
        On success: Confirmation that the text is visible.
        On failure: Error message describing the verification failure.
    """
    try:
        logger.info(f"[verify_text_visible] start text={text!r} exact={exact}")

        page = await browser.get_current_page()
        if page is None:
            return "FAIL: No active page available"

        locator = page.get_by_text(text, exact=exact)

        try:
            await locator.first.wait_for(state="visible", timeout=timeout)
            result = f"PASS: Text '{text}' is visible on the page"
            logger.info(f"[verify_text_visible] {result}")
            return result
        except Exception:
            result = f"FAIL: Text '{text}' is not visible on the page"
            logger.warning(f"[verify_text_visible] {result}")
            return result
    except Exception as e:
        error_msg = f"FAIL: Verification error: {str(e)}"
        logger.error(f"[verify_text_visible] {error_msg}")
        return error_msg


async def verify_value(
    browser: "Browser",
    ref: str,
    value: str,
    attribute: str = "value",
) -> str:
    """Verify that an element has the expected value or attribute.

    Check that an element identified by ref has the expected value in
    the specified attribute or property.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    ref : str
        Element ref obtained from snapshot refs (e.g., "e1", "e2").
    value : str
        Expected value.
    attribute : str, optional
        Attribute or property to check. Default is "value" for input
        elements. Can also be "textContent", "innerText", etc.

    Returns
    -------
    str
        On success: Confirmation with actual and expected values.
        On failure: Error message with actual vs expected values.
    """
    try:
        logger.info(f"[verify_value] start ref={ref} expected={value} attr={attribute}")

        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            return f"FAIL: Element ref {ref} is not available"

        # Get actual value
        if attribute == "value":
            actual = await locator.input_value()
        elif attribute == "textContent":
            actual = await locator.text_content()
        elif attribute == "innerText":
            actual = await locator.inner_text()
        else:
            actual = await locator.get_attribute(attribute)

        if actual is None:
            actual = ""

        if actual == value:
            result = f"PASS: Element {ref} has {attribute}='{value}'"
            logger.info(f"[verify_value] {result}")
        else:
            result = f"FAIL: Element {ref} {attribute} mismatch. Expected: '{value}', Actual: '{actual}'"
            logger.warning(f"[verify_value] {result}")

        return result
    except Exception as e:
        error_msg = f"FAIL: Verification error: {str(e)}"
        logger.error(f"[verify_value] {error_msg}")
        return error_msg


async def verify_element_state(
    browser: "Browser",
    ref: str,
    state: Literal["visible", "hidden", "enabled", "disabled", "checked", "unchecked", "editable"],
) -> str:
    """Verify that an element is in the expected state.

    Check various states of an element including visibility, enabled
    state, checked state (for checkboxes/radios), and editability.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    ref : str
        Element ref obtained from snapshot refs (e.g., "e1", "e2").
    state : str
        Expected state. Options: "visible", "hidden", "enabled",
        "disabled", "checked", "unchecked", "editable".

    Returns
    -------
    str
        On success: Confirmation of the element state.
        On failure: Error message describing the state mismatch.
    """
    try:
        logger.info(f"[verify_element_state] start ref={ref} state={state}")

        locator = await browser.get_element_by_ref(ref)
        if locator is None:
            return f"FAIL: Element ref {ref} is not available"

        result = ""
        try:
            if state == "visible":
                is_visible = await locator.is_visible()
                if is_visible:
                    result = f"PASS: Element {ref} is visible"
                else:
                    result = f"FAIL: Element {ref} is not visible"

            elif state == "hidden":
                is_hidden = await locator.is_hidden()
                if is_hidden:
                    result = f"PASS: Element {ref} is hidden"
                else:
                    result = f"FAIL: Element {ref} is not hidden"

            elif state == "enabled":
                is_enabled = await locator.is_enabled()
                if is_enabled:
                    result = f"PASS: Element {ref} is enabled"
                else:
                    result = f"FAIL: Element {ref} is not enabled"

            elif state == "disabled":
                is_disabled = await locator.is_disabled()
                if is_disabled:
                    result = f"PASS: Element {ref} is disabled"
                else:
                    result = f"FAIL: Element {ref} is not disabled"

            elif state == "checked":
                is_checked = await locator.is_checked()
                if is_checked:
                    result = f"PASS: Element {ref} is checked"
                else:
                    result = f"FAIL: Element {ref} is not checked"

            elif state == "unchecked":
                is_checked = await locator.is_checked()
                if not is_checked:
                    result = f"PASS: Element {ref} is unchecked"
                else:
                    result = f"FAIL: Element {ref} is checked (expected unchecked)"

            elif state == "editable":
                is_editable = await locator.is_editable()
                if is_editable:
                    result = f"PASS: Element {ref} is editable"
                else:
                    result = f"FAIL: Element {ref} is not editable"

            else:
                result = f"FAIL: Unknown state '{state}'"

        except Exception as e:
            result = f"FAIL: Could not check state '{state}' for element {ref}: {str(e)}"

        logger.info(f"[verify_element_state] {result}")
        return result
    except Exception as e:
        error_msg = f"FAIL: Verification error: {str(e)}"
        logger.error(f"[verify_element_state] {error_msg}")
        return error_msg


async def verify_url(
    browser: "Browser",
    expected_url: str,
    exact: bool = False,
) -> str:
    """Verify the current page URL.

    Check that the current page URL matches the expected URL.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    expected_url : str
        Expected URL or URL pattern.
    exact : bool, optional
        Whether to match exactly. Default is False (contains check).

    Returns
    -------
    str
        On success: Confirmation with the current URL.
        On failure: Error message with actual vs expected URL.
    """
    try:
        logger.info(f"[verify_url] start expected={expected_url} exact={exact}")

        page = await browser.get_current_page()
        if page is None:
            return "FAIL: No active page available"

        actual_url = page.url

        if exact:
            matches = actual_url == expected_url
        else:
            matches = expected_url in actual_url

        if matches:
            result = f"PASS: URL matches. Current: {actual_url}"
            logger.info(f"[verify_url] {result}")
        else:
            result = f"FAIL: URL mismatch. Expected: '{expected_url}', Actual: '{actual_url}'"
            logger.warning(f"[verify_url] {result}")

        return result
    except Exception as e:
        error_msg = f"FAIL: Verification error: {str(e)}"
        logger.error(f"[verify_url] {error_msg}")
        return error_msg


async def verify_title(
    browser: "Browser",
    expected_title: str,
    exact: bool = False,
) -> str:
    """Verify the current page title.

    Check that the current page title matches the expected title.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    expected_title : str
        Expected title or title pattern.
    exact : bool, optional
        Whether to match exactly. Default is False (contains check).

    Returns
    -------
    str
        On success: Confirmation with the current title.
        On failure: Error message with actual vs expected title.
    """
    try:
        logger.info(f"[verify_title] start expected={expected_title} exact={exact}")

        page = await browser.get_current_page()
        if page is None:
            return "FAIL: No active page available"

        actual_title = await page.title()

        if exact:
            matches = actual_title == expected_title
        else:
            matches = expected_title in actual_title

        if matches:
            result = f"PASS: Title matches. Current: '{actual_title}'"
            logger.info(f"[verify_title] {result}")
        else:
            result = f"FAIL: Title mismatch. Expected: '{expected_title}', Actual: '{actual_title}'"
            logger.warning(f"[verify_title] {result}")

        return result
    except Exception as e:
        error_msg = f"FAIL: Verification error: {str(e)}"
        logger.error(f"[verify_title] {error_msg}")
        return error_msg
