"""
Browser interaction tools based on element refs.

This module provides tools for interacting with browser elements using
element references (refs) obtained from page snapshots.

Several tools include targeted fallbacks for real-world UI edge cases that are
common in modern web apps but awkward for Playwright's normal actionability
checks:

- Shadow DOM / slotted custom elements that have screen geometry but report
  `is_visible() == False` to Playwright.
- Elements covered by overlays or proxy controls where the center point belongs
  to another clickable element.
- Portalized dropdown menus whose options are rendered outside the trigger's
  subtree (for example React-Select / Ant Design).
- Hidden file inputs exposed via a visible label or wrapper container.
- Custom checkbox/radio widgets that reflect state with `aria-checked` instead
  of only the native `.checked` property.
- JS-driven inputs and contenteditable nodes that need `input` / `change`
  events after direct DOM value updates.

The general policy is: prefer standard Playwright interactions when they are
reliable, and only fall back to targeted JS / DOM-event based behavior when it
avoids long retry loops or matches how these custom widgets actually work.
"""
from __future__ import annotations
import logging
import os
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..session._browser import Browser


def _css_attr_equals(name: str, value: str) -> str:
    """Build a CSS attribute selector with basic quote escaping."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"[{name}='{escaped}']"


async def _prefer_visible_locators(locators: list) -> list:
    """Keep only visible locators when possible, otherwise preserve original order."""
    visible = []
    for locator in locators:
        try:
            if await locator.is_visible():
                visible.append(locator)
        except Exception:
            continue
    return visible or locators


async def _get_dropdown_option_locators(browser: "Browser", locator) -> list:
    """Resolve option locators for native, embedded, and portalized dropdowns."""
    options = await locator.locator("option").all()
    if options:
        return options

    options = await locator.locator("[role='option']").all()
    if options:
        return await _prefer_visible_locators(options)

    page = await browser.get_current_page()
    if page is None:
        return []

    # Portalized dropdowns often link the trigger to the listbox via aria-controls
    # or aria-owns. Prefer that container before scanning the whole page.
    controlled_ids = []
    for attr_name in ("aria-controls", "aria-owns"):
        attr_value = await locator.get_attribute(attr_name)
        if attr_value:
            controlled_ids.extend(part for part in attr_value.split() if part)

    for controlled_id in controlled_ids:
        container = page.locator(_css_attr_equals("id", controlled_id))
        if await container.count() > 0:
            options = await container.locator("option, [role='option']").all()
            if options:
                return await _prefer_visible_locators(options)

    # Conservative fallback: if exactly one visible listbox is open, use it.
    # This avoids accidentally mixing options across unrelated dropdowns.
    listboxes = await page.locator("[role='listbox']").all()
    visible_listboxes = await _prefer_visible_locators(listboxes)
    if len(visible_listboxes) == 1:
        options = await visible_listboxes[0].locator("option, [role='option']").all()
        if options:
            return await _prefer_visible_locators(options)

    return []


async def _is_native_checkbox_or_radio(locator) -> bool:
    """Return True when locator points to <input type=checkbox|radio>."""
    try:
        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        return False
    if tag_name != "input":
        return False
    input_type = (await locator.get_attribute("type") or "").strip().lower()
    return input_type in {"checkbox", "radio"}


async def _is_checked(locator) -> bool:
    """Check both native .checked and aria-checked state."""
    return bool(
        await locator.evaluate(
            "el => el.checked === true || el.getAttribute('aria-checked') === 'true'"
        )
    )


async def _click_checkable_target(browser: "Browser", locator, bbox) -> None:
    """Click a checkable target with overlay handling and shadow DOM fallback."""
    if bbox is not None:
        cx = bbox["x"] + bbox["width"] / 2
        cy = bbox["y"] + bbox["height"] / 2
        if not await locator.is_visible():
            await locator.dispatch_event("click")
            return

        covered = await locator.evaluate(
            f"(el) => {{ if (window.frameElement !== null) return false; "
            f"const t = document.elementFromPoint({cx}, {cy}); "
            f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
        )
        if covered:
            page = await browser.get_current_page()
            if page:
                await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
            else:
                await locator.dispatch_event("click")
        else:
            await locator.click()
        return

    if await locator.is_visible():
        await locator.click()
    else:
        await locator.dispatch_event("click")


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

        # For shadow-DOM / slotted inputs (e.g. Stripe Elements, Google Pay, custom
        # web-component inputs), locator.clear() / locator.fill() / locator.focus()
        # all perform actionability (visibility) checks and will hang.
        # Detect this upfront and use JS to set the value directly instead.
        is_vis = await locator.is_visible()

        # JS snippet for setting/appending value with proper event dispatch.
        # When clear=False, appends to existing value instead of overwriting.
        _js_set_value = (
            "(el, v) => {"
            "  if ('value' in el) {"
            f"    el.value = {'el.value + v' if not clear else 'v'};"
            "    el.dispatchEvent(new Event('input', {bubbles: true}));"
            "    el.dispatchEvent(new Event('change', {bubbles: true}));"
            "  } else if (el.isContentEditable) {"
            f"    el.textContent = {'el.textContent + v' if not clear else 'v'};"
            "    el.dispatchEvent(new Event('input', {bubbles: true}));"
            "  }"
            "}"
        )

        # Input text
        if clear:
            if is_vis:
                await locator.clear()
            else:
                logger.debug("[input_text_by_ref] is_visible()=False; clearing via JS")
                await locator.evaluate(
                    "(el) => { if ('value' in el) el.value = ''; "
                    "else if (el.isContentEditable) el.textContent = ''; }"
                )

        if slowly:
            # Use type() for realistic typing simulation.
            # type() appends naturally (unlike fill()), so clear=False works correctly.
            if is_vis:
                await locator.focus()
                await locator.type(text, delay=100)
            else:
                logger.debug("[input_text_by_ref] is_visible()=False; setting value via JS (slowly mode unavailable)")
                await locator.evaluate("el => el.focus()")
                await locator.evaluate(_js_set_value, text)
        else:
            if is_vis and clear:
                # fill() is fastest for the common case (clear + visible).
                await locator.fill(text)
            else:
                # fill() always clears the field first, which contradicts clear=False.
                # Shadow-DOM elements also cannot use fill() (actionability hang).
                # Use JS to set/append the value directly in both cases.
                if not is_vis:
                    logger.debug("[input_text_by_ref] is_visible()=False; setting value via JS")
                await locator.evaluate(_js_set_value, text)

        # Submit if requested
        if submit:
            if not is_vis:
                # After JS-based fill, document.activeElement is not this element.
                # dispatch_event("focus") only fires the event without updating
                # activeElement. el.focus() via evaluate() actually moves focus so
                # the subsequent keyboard.press("Enter") reaches this element.
                await locator.evaluate("el => el.focus()")
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

        # Pre-check: if another element sits on top of the target at its center point,
        # click that intercepting element directly. This avoids Playwright's 30-second
        # retry loop which only gives up after the full timeout has elapsed.
        # (Common pattern: Stripe accordion buttons covering radio inputs.)
        bbox = await locator.bounding_box()
        if bbox is not None:
            cx = bbox["x"] + bbox["width"] / 2
            cy = bbox["y"] + bbox["height"] / 2

            # Fast-path for shadow-DOM / slotted elements (e.g. gmp-advanced-marker).
            # If the element has geometry but Playwright's CSS visibility check fails
            # (common for custom elements rendered via slot projection), dispatch the
            # click event directly to avoid the 30-second actionability retry loop.
            if not await locator.is_visible():
                logger.debug(
                    "[click_element_by_ref] element has bbox but is_visible()=False "
                    "(likely shadow-DOM slot); using dispatch_event click"
                )
                await locator.dispatch_event("click")
            else:
                covered = await locator.evaluate(
                    f"(el) => {{ if (window.frameElement !== null) return false; "
                    f"const t = document.elementFromPoint({cx}, {cy}); "
                    f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                )
                if covered:
                    logger.debug("[click_element_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                    page = await browser.get_current_page()
                    if page:
                        await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
                    else:
                        await locator.evaluate("el => el.click()")
                else:
                    await locator.click()
        else:
            # bbox is None: element may be off-screen (Playwright will scroll it in)
            # or permanently invisible (display:none). Avoid the 30-second hang for
            # the latter by checking visibility and falling back to dispatch_event.
            if not await locator.is_visible():
                logger.debug("[click_element_by_ref] bbox=None and is_visible()=False; using dispatch_event click")
                await locator.dispatch_event("click")
            else:
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

        # Resolve native <select> options, embedded role=option nodes, or options
        # rendered into a portal/listbox controlled by the trigger element.
        options = await _get_dropdown_option_locators(browser, locator)
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

        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")

        if tag_name == "select":
            # Playwright's select_option(string) matches by value attribute only.
            # Try by value first, then fall back to visible label (text shown to user).
            try:
                await locator.select_option(value=text)
            except Exception:
                await locator.select_option(label=text)
        else:
            normalized_target = text.strip()
            options = await _get_dropdown_option_locators(browser, locator)

            # If no option list is currently present, try opening the trigger first and retry.
            if not options:
                if await locator.is_visible():
                    await locator.click()
                else:
                    await locator.dispatch_event("click")
                options = await _get_dropdown_option_locators(browser, locator)

            if not options:
                return f'Failed to find dropdown options for element {ref}'

            chosen_option = None
            for option in options:
                option_text = (await option.text_content() or "").strip()
                option_value = (await option.get_attribute("value") or "").strip()
                if option_text == normalized_target or option_value == normalized_target:
                    chosen_option = option
                    break

            if chosen_option is None:
                lowered_target = normalized_target.lower()
                for option in options:
                    option_text = (await option.text_content() or "").strip()
                    option_value = (await option.get_attribute("value") or "").strip()
                    if option_text.lower() == lowered_target or option_value.lower() == lowered_target:
                        chosen_option = option
                        break

            if chosen_option is None:
                return f'Failed to find dropdown option "{text}" for element {ref}'

            if await chosen_option.is_visible():
                await chosen_option.click()
            else:
                await chosen_option.dispatch_event("click")

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

        # If element is covered, move the mouse to those coordinates so the visible
        # overlay receives the hover events (tooltips, dropdowns, etc.).
        bbox = await locator.bounding_box()
        if bbox is not None:
            cx = bbox["x"] + bbox["width"] / 2
            cy = bbox["y"] + bbox["height"] / 2

            # Fast-path for shadow-DOM / slotted elements: move mouse to coordinates
            # directly to avoid the 30-second actionability retry loop.
            if not await locator.is_visible():
                logger.debug(
                    "[hover_element_by_ref] element has bbox but is_visible()=False "
                    "(likely shadow-DOM slot); moving mouse to coordinates directly"
                )
                page = await browser.get_current_page()
                if page:
                    await page.mouse.move(cx, cy)
                else:
                    await locator.hover(force=True)
            else:
                covered = await locator.evaluate(
                    f"(el) => {{ if (window.frameElement !== null) return false; "
                    f"const t = document.elementFromPoint({cx}, {cy}); "
                    f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                )
                if covered:
                    logger.debug("[hover_element_by_ref] covered at (%.1f, %.1f), moving mouse to coordinates", cx, cy)
                    page = await browser.get_current_page()
                    if page:
                        await page.mouse.move(cx, cy)
                    else:
                        await locator.hover(force=True)
                else:
                    await locator.hover()
        else:
            if not await locator.is_visible():
                msg = (
                    f'Could not hover element {ref}: element is not visible and has '
                    'no screen coordinates'
                )
                logger.warning(f'[hover_element_by_ref] {msg}')
                return msg
            else:
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

        # Focus element.
        # For shadow-DOM / slotted elements, locator.focus() performs visibility
        # checks and will hang. dispatch_event("focus") bypasses actionability.
        if await locator.is_visible():
            await locator.focus()
        else:
            logger.debug(
                "[focus_element_by_ref] is_visible()=False (likely shadow-DOM slot); "
                "using el.focus() via evaluate to properly update document.activeElement"
            )
            # dispatch_event("focus") only fires the event; it does NOT update
            # document.activeElement. el.focus() via JS does both without triggering
            # Playwright's actionability checks.
            await locator.evaluate("el => el.focus()")

        msg = f'Focused element ref {ref}'
        logger.info(f'[focus_element_by_ref] {msg}')
        return msg

    except Exception as e:
        logger.error(f'[focus_element_by_ref] Failed to focus element: {type(e).__name__}: {e}')
        error_msg = f'Failed to focus element {ref}: {str(e)}'
        return error_msg


async def evaluate_javascript_on_ref(browser: "Browser", ref: str, code: str) -> str:
    """Execute JavaScript on an element.

    The element is passed as the first argument to the function.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    ref : str
        Element ref from snapshot (e.g., "e1").
    code : str
        Arrow function receiving the element as first arg, e.g., "el => el.textContent".

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

        # Check element type.
        # Hidden <input type="file"> elements are often invisible in the snapshot;
        # the LLM may instead pass a <label> or wrapper element. If the ref is not
        # directly a file input, try to find a nested input[type=file] within it.
        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
        input_type = await locator.get_attribute("type") if tag_name == "input" else None
        if tag_name != "input" or input_type != "file":
            nested = locator.locator("input[type='file']")
            if await nested.count() > 0:
                logger.debug(
                    "[upload_file_by_ref] ref %s (%s) is not a file input; "
                    "found nested input[type=file], retargeting",
                    ref, tag_name,
                )
                locator = nested.first
            else:
                msg = f'Element ref {ref} is not a file input element (tag: {tag_name}, type: {input_type})'
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

        is_native = await _is_native_checkbox_or_radio(locator)
        already_checked = await _is_checked(locator)
        if already_checked:
            msg = f'Checked element {ref}'
            logger.info(f'[check_element_by_ref] {msg} (already checked)')
            return msg

        bbox = await locator.bounding_box()
        if is_native:
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                # Fast-path for shadow-DOM / slotted custom checkboxes.
                if not await locator.is_visible():
                    logger.debug(
                        "[check_element_by_ref] native input has bbox but is_visible()=False; "
                        "using dispatch_event click"
                    )
                    await locator.dispatch_event("click")
                else:
                    covered = await locator.evaluate(
                        f"(el) => {{ if (window.frameElement !== null) return false; "
                        f"const t = document.elementFromPoint({cx}, {cy}); "
                        f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                    )
                    if covered:
                        logger.debug("[check_element_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                        page = await browser.get_current_page()
                        if page:
                            await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
                        else:
                            await locator.check(force=True)
                    else:
                        await locator.check()
            else:
                if not await locator.is_visible():
                    logger.debug("[check_element_by_ref] native input bbox=None and is_visible()=False; using dispatch_event click")
                    await locator.dispatch_event("click")
                else:
                    await locator.check()
        else:
            # Custom role=checkbox/radio widgets don't support locator.check().
            await _click_checkable_target(browser, locator, bbox)

        if not await _is_checked(locator):
            msg = f'Failed to check element {ref}: element state did not become checked'
            logger.warning(f'[check_element_by_ref] {msg}')
            return msg

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

        is_native = await _is_native_checkbox_or_radio(locator)
        already_checked = await _is_checked(locator)
        if not already_checked:
            msg = f'Unchecked element {ref}'
            logger.info(f'[uncheck_element_by_ref] {msg} (already unchecked)')
            return msg

        bbox = await locator.bounding_box()
        if is_native:
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                # Fast-path for shadow-DOM / slotted custom checkboxes.
                if not await locator.is_visible():
                    logger.debug(
                        "[uncheck_element_by_ref] native input has bbox but is_visible()=False; "
                        "using dispatch_event click"
                    )
                    await locator.dispatch_event("click")
                else:
                    covered = await locator.evaluate(
                        f"(el) => {{ if (window.frameElement !== null) return false; "
                        f"const t = document.elementFromPoint({cx}, {cy}); "
                        f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                    )
                    if covered:
                        logger.debug("[uncheck_element_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                        page = await browser.get_current_page()
                        if page:
                            await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
                        else:
                            await locator.uncheck(force=True)
                    else:
                        await locator.uncheck()
            else:
                if not await locator.is_visible():
                    logger.debug("[uncheck_element_by_ref] native input bbox=None and is_visible()=False; using dispatch_event click")
                    await locator.dispatch_event("click")
                else:
                    await locator.uncheck()
        else:
            # Custom role=checkbox/radio widgets don't support locator.uncheck().
            await _click_checkable_target(browser, locator, bbox)

        # Native radio inputs cannot be unchecked by clicking themselves;
        # clicking a checked radio does not change its state.  Skip the
        # post-condition check for radios so callers get a success message
        # (the click was dispatched as intended, even if state is unchanged).
        is_native_radio = is_native and (await locator.get_attribute("type") or "").strip().lower() == "radio"
        if not is_native_radio and await _is_checked(locator):
            msg = f'Failed to uncheck element {ref}: element state remained checked'
            logger.warning(f'[uncheck_element_by_ref] {msg}')
            return msg

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

        bbox = await locator.bounding_box()
        if bbox is not None:
            cx = bbox["x"] + bbox["width"] / 2
            cy = bbox["y"] + bbox["height"] / 2

            # Fast-path for shadow-DOM / slotted elements.
            if not await locator.is_visible():
                logger.debug(
                    "[double_click_element_by_ref] element has bbox but is_visible()=False "
                    "(likely shadow-DOM slot); using dispatch_event dblclick"
                )
                await locator.dispatch_event("dblclick")
            else:
                covered = await locator.evaluate(
                    f"(el) => {{ if (window.frameElement !== null) return false; "
                    f"const t = document.elementFromPoint({cx}, {cy}); "
                    f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                )
                if covered:
                    logger.debug("[double_click_element_by_ref] covered at (%.1f, %.1f), dispatching dblclick on intercepting element", cx, cy)
                    page = await browser.get_current_page()
                    if page:
                        await page.evaluate(
                            f"(function(){{"
                            f"const el=document.elementFromPoint({cx},{cy});"
                            f"if(el)el.dispatchEvent(new MouseEvent('dblclick',{{bubbles:true,cancelable:true,view:window}}));"
                            f"}})()"
                        )
                    else:
                        await locator.dblclick(force=True)
                else:
                    await locator.dblclick()
        else:
            if not await locator.is_visible():
                logger.debug("[double_click_element_by_ref] bbox=None and is_visible()=False; using dispatch_event dblclick")
                await locator.dispatch_event("dblclick")
            else:
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
