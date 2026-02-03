"""
Browser mouse coordinate-based tools.

This module provides tools for mouse operations using screen coordinates,
complementing the ref-based tools in _browser_action_tools.py.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)


async def mouse_move(browser: "Browser", x: float, y: float) -> str:
    """Move the mouse to specific coordinates.

    Move the mouse cursor to the specified X and Y screen coordinates.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    x : float
        X coordinate (horizontal position from left).
    y : float
        Y coordinate (vertical position from top).

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[mouse_move] start x={x} y={y}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.mouse.move(x, y)
        result = f"Moved mouse to coordinates ({x}, {y})"
        logger.info(f"[mouse_move] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to move mouse: {str(e)}"
        logger.error(f"[mouse_move] {error_msg}")
        return error_msg


async def mouse_click(
    browser: "Browser",
    x: float,
    y: float,
    button: Literal["left", "right", "middle"] = "left",
    click_count: int = 1,
) -> str:
    """Click the mouse at specific coordinates.

    Move to the specified coordinates and perform a mouse click.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    x : float
        X coordinate (horizontal position from left).
    y : float
        Y coordinate (vertical position from top).
    button : {"left", "right", "middle"}, optional
        Mouse button to click. Default is "left".
    click_count : int, optional
        Number of clicks. Default is 1. Use 2 for double-click.

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[mouse_click] start x={x} y={y} button={button} click_count={click_count}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.mouse.click(x, y, button=button, click_count=click_count)

        click_type = "double-clicked" if click_count == 2 else "clicked"
        result = f"Mouse {click_type} at ({x}, {y}) with {button} button"
        logger.info(f"[mouse_click] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to click mouse: {str(e)}"
        logger.error(f"[mouse_click] {error_msg}")
        return error_msg


async def mouse_drag(
    browser: "Browser",
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
) -> str:
    """Drag the mouse from one position to another.

    Perform a mouse drag operation from start coordinates to end coordinates.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    start_x : float
        Starting X coordinate.
    start_y : float
        Starting Y coordinate.
    end_x : float
        Ending X coordinate.
    end_y : float
        Ending Y coordinate.

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[mouse_drag] start from=({start_x}, {start_y}) to=({end_x}, {end_y})")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        # Move to start position, press, move to end, release
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(end_x, end_y)
        await page.mouse.up()

        result = f"Dragged mouse from ({start_x}, {start_y}) to ({end_x}, {end_y})"
        logger.info(f"[mouse_drag] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to drag mouse: {str(e)}"
        logger.error(f"[mouse_drag] {error_msg}")
        return error_msg


async def mouse_down(
    browser: "Browser",
    button: Literal["left", "right", "middle"] = "left",
) -> str:
    """Press and hold a mouse button.

    Press and hold the specified mouse button at the current cursor position.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    button : {"left", "right", "middle"}, optional
        Mouse button to press. Default is "left".

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[mouse_down] start button={button}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.mouse.down(button=button)
        result = f"Mouse {button} button pressed down"
        logger.info(f"[mouse_down] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to press mouse button: {str(e)}"
        logger.error(f"[mouse_down] {error_msg}")
        return error_msg


async def mouse_up(
    browser: "Browser",
    button: Literal["left", "right", "middle"] = "left",
) -> str:
    """Release a mouse button.

    Release the specified mouse button at the current cursor position.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    button : {"left", "right", "middle"}, optional
        Mouse button to release. Default is "left".

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[mouse_up] start button={button}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.mouse.up(button=button)
        result = f"Mouse {button} button released"
        logger.info(f"[mouse_up] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to release mouse button: {str(e)}"
        logger.error(f"[mouse_up] {error_msg}")
        return error_msg


async def mouse_wheel(
    browser: "Browser",
    delta_x: float = 0,
    delta_y: float = 0,
) -> str:
    """Scroll the mouse wheel.

    Scroll the page using the mouse wheel. Positive delta_y scrolls down,
    negative delta_y scrolls up. Positive delta_x scrolls right,
    negative delta_x scrolls left.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    delta_x : float, optional
        Horizontal scroll amount. Default is 0.
    delta_y : float, optional
        Vertical scroll amount. Default is 0.

    Returns
    -------
    str
        Operation result message. On success, returns a confirmation
        message. On failure, returns an error message.
    """
    try:
        logger.info(f"[mouse_wheel] start delta_x={delta_x} delta_y={delta_y}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        await page.mouse.wheel(delta_x=delta_x, delta_y=delta_y)
        result = f"Scrolled mouse wheel: delta_x={delta_x}, delta_y={delta_y}"
        logger.info(f"[mouse_wheel] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to scroll mouse wheel: {str(e)}"
        logger.error(f"[mouse_wheel] {error_msg}")
        return error_msg
