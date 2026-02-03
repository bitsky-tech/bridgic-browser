"""
Browser screenshot and PDF tools.

This module provides tools for capturing screenshots and saving pages as PDFs.
"""
from __future__ import annotations
import base64
import logging
import os
import tempfile
from typing import TYPE_CHECKING, Optional, Literal

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)


async def take_screenshot(
    browser: "Browser",
    filename: Optional[str] = None,
    ref: Optional[str] = None,
    full_page: bool = False,
    type: Literal["png", "jpeg"] = "png",
    quality: Optional[int] = None,
) -> str:
    """Take a screenshot of the page or a specific element.

    Capture a screenshot of the current page viewport, full page, or a
    specific element identified by ref.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    filename : Optional[str], optional
        Path to save the screenshot. If not provided, returns base64-encoded
        image data. If the path doesn't have an extension, the appropriate
        extension will be added based on the type parameter.
    ref : Optional[str], optional
        Element ref from snapshot to screenshot. If provided, captures only
        that element. If not provided, captures the page.
    full_page : bool, optional
        Whether to capture the full scrollable page. Default is False.
        Ignored if ref is provided.
    type : {"png", "jpeg"}, optional
        Image format. Default is "png".
    quality : Optional[int], optional
        Quality for JPEG images (0-100). Only applies when type is "jpeg".

    Returns
    -------
    str
        On success:
        - With filename: "Screenshot saved to: /path/to/file.png"
        - Without filename: Base64 data URL "data:image/png;base64,iVBORw0..."
        On failure: Error message starting with "Failed to".

    Notes
    -----
    The base64 data URL can be directly used in HTML img tags or
    processed by image analysis tools.
    """
    try:
        logger.info(f"[take_screenshot] start filename={filename} ref={ref} full_page={full_page} type={type}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        screenshot_options = {
            "type": type,
            "full_page": full_page if ref is None else False,
        }

        if type == "jpeg" and quality is not None:
            screenshot_options["quality"] = quality

        # Determine the target (page or element)
        if ref is not None:
            locator = await browser.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed.'
                logger.warning(f'[take_screenshot] {msg}')
                return msg
            target = locator
        else:
            target = page

        # Take screenshot
        if filename:
            # Ensure proper extension
            if not filename.lower().endswith(f".{type}"):
                filename = f"{filename}.{type}"

            # Create directory if needed
            dirname = os.path.dirname(filename)
            if dirname:
                os.makedirs(dirname, exist_ok=True)

            screenshot_options["path"] = filename
            await target.screenshot(**screenshot_options)
            result = f"Screenshot saved to: {filename}"
        else:
            # Return base64-encoded data
            screenshot_bytes = await target.screenshot(**screenshot_options)
            b64_data = base64.b64encode(screenshot_bytes).decode("utf-8")
            result = f"data:image/{type};base64,{b64_data}"

        logger.info(f"[take_screenshot] done")
        return result
    except Exception as e:
        error_msg = f"Failed to take screenshot: {str(e)}"
        logger.error(f"[take_screenshot] {error_msg}")
        return error_msg


async def save_pdf(
    browser: "Browser",
    filename: Optional[str] = None,
    display_header_footer: bool = False,
    print_background: bool = True,
    scale: float = 1.0,
    paper_width: Optional[str] = None,
    paper_height: Optional[str] = None,
    margin_top: Optional[str] = None,
    margin_bottom: Optional[str] = None,
    margin_left: Optional[str] = None,
    margin_right: Optional[str] = None,
    landscape: bool = False,
) -> str:
    """Save the current page as a PDF file.

    Generate a PDF of the current page. This only works in headless mode
    for Chromium-based browsers.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    filename : Optional[str], optional
        Path to save the PDF. If not provided, saves to a temporary file
        and returns the path.
    display_header_footer : bool, optional
        Whether to display header and footer. Default is False.
    print_background : bool, optional
        Whether to print background graphics. Default is True.
    scale : float, optional
        Scale of the webpage rendering. Default is 1.0.
    paper_width : Optional[str], optional
        Paper width with units (e.g., "8.5in", "21cm"). Default is letter size.
    paper_height : Optional[str], optional
        Paper height with units (e.g., "11in", "29.7cm"). Default is letter size.
    margin_top : Optional[str], optional
        Top margin with units. Default is "0".
    margin_bottom : Optional[str], optional
        Bottom margin with units. Default is "0".
    margin_left : Optional[str], optional
        Left margin with units. Default is "0".
    margin_right : Optional[str], optional
        Right margin with units. Default is "0".
    landscape : bool, optional
        Whether to use landscape orientation. Default is False.

    Returns
    -------
    str
        On success: Returns the file path where PDF was saved.
        On failure: Returns an error message.

    Notes
    -----
    PDF generation only works in headless mode for Chromium browsers.
    In headed mode, this will fail.
    """
    try:
        logger.info(f"[save_pdf] start filename={filename}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        pdf_options = {
            "display_header_footer": display_header_footer,
            "print_background": print_background,
            "scale": scale,
            "landscape": landscape,
        }

        # Add optional parameters
        if paper_width:
            pdf_options["width"] = paper_width
        if paper_height:
            pdf_options["height"] = paper_height
        if margin_top:
            pdf_options["margin"] = pdf_options.get("margin", {})
            pdf_options["margin"]["top"] = margin_top
        if margin_bottom:
            pdf_options["margin"] = pdf_options.get("margin", {})
            pdf_options["margin"]["bottom"] = margin_bottom
        if margin_left:
            pdf_options["margin"] = pdf_options.get("margin", {})
            pdf_options["margin"]["left"] = margin_left
        if margin_right:
            pdf_options["margin"] = pdf_options.get("margin", {})
            pdf_options["margin"]["right"] = margin_right

        # Determine output path
        if filename:
            if not filename.lower().endswith(".pdf"):
                filename = f"{filename}.pdf"
            output_path = filename

            # Create directory if needed
            dirname = os.path.dirname(filename)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        else:
            # Generate temporary file
            fd, output_path = tempfile.mkstemp(suffix=".pdf", prefix="browser_page_")
            os.close(fd)

        pdf_options["path"] = output_path
        await page.pdf(**pdf_options)

        result = f"PDF saved to: {output_path}"
        logger.info(f"[save_pdf] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to save PDF: {str(e)}"
        logger.error(f"[save_pdf] {error_msg}")
        return error_msg
