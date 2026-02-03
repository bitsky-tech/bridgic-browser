"""
Browser DevTools tools (tracing and video recording).

This module provides tools for browser tracing and video recording,
useful for debugging and test documentation.
"""
from __future__ import annotations
import logging
import os
import tempfile
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)

# Track tracing and video state per context
_tracing_state: dict = {}
_video_state: dict = {}


def _get_context_key(context) -> str:
    """Get a unique key for a context to store data."""
    return str(id(context))


async def start_tracing(
    browser: "Browser",
    screenshots: bool = True,
    snapshots: bool = True,
    sources: bool = False,
) -> str:
    """Start browser tracing.

    Start collecting trace information including screenshots, DOM snapshots,
    and optionally source files. The trace can be stopped and saved with
    stop_tracing.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    screenshots : bool, optional
        Whether to capture screenshots during trace. Default is True.
    snapshots : bool, optional
        Whether to capture DOM snapshots. Default is True.
    sources : bool, optional
        Whether to include source files. Default is False.

    Returns
    -------
    str
        Operation result message.

    Notes
    -----
    Only one trace can be active at a time per browser context.
    Call stop_tracing to save and end the current trace before
    starting a new one.
    """
    try:
        logger.info(f"[start_tracing] start screenshots={screenshots} snapshots={snapshots}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context
        context_key = _get_context_key(context)

        if context_key in _tracing_state and _tracing_state[context_key]:
            return "Tracing is already active. Stop the current trace first."

        await context.tracing.start(
            screenshots=screenshots,
            snapshots=snapshots,
            sources=sources,
        )

        _tracing_state[context_key] = True

        result = "Tracing started"
        logger.info(f"[start_tracing] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to start tracing: {str(e)}"
        logger.error(f"[start_tracing] {error_msg}")
        return error_msg


async def stop_tracing(
    browser: "Browser",
    filename: Optional[str] = None,
) -> str:
    """Stop browser tracing and save the trace file.

    Stop the current trace and save it to a file. The trace file can be
    viewed in Playwright Trace Viewer (npx playwright show-trace trace.zip).

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    filename : Optional[str], optional
        Path to save the trace file. If not provided, saves to a
        temporary file. File extension should be .zip.

    Returns
    -------
    str
        On success: Returns the file path where trace was saved.
        On failure: Returns an error message.
    """
    try:
        logger.info(f"[stop_tracing] start filename={filename}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context
        context_key = _get_context_key(context)

        if context_key not in _tracing_state or not _tracing_state[context_key]:
            return "No active tracing to stop. Start tracing first."

        # Determine output path
        if filename:
            if not filename.lower().endswith(".zip"):
                filename = f"{filename}.zip"
            output_path = filename

            # Create directory if needed
            dirname = os.path.dirname(filename)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        else:
            # Generate temporary file
            fd, output_path = tempfile.mkstemp(suffix=".zip", prefix="browser_trace_")
            os.close(fd)

        await context.tracing.stop(path=output_path)
        _tracing_state[context_key] = False

        result = f"Trace saved to: {output_path}"
        logger.info(f"[stop_tracing] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to stop tracing: {str(e)}"
        logger.error(f"[stop_tracing] {error_msg}")
        return error_msg


async def start_video(
    browser: "Browser",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> str:
    """Start video recording for new pages.

    Configure video recording for pages created after this call.
    Videos are saved when pages are closed.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    width : Optional[int], optional
        Video width. Default uses viewport width.
    height : Optional[int], optional
        Video height. Default uses viewport height.

    Returns
    -------
    str
        Operation result message.

    Notes
    -----
    Video recording must be configured at context creation time in
    Playwright. This function provides a simplified interface but
    has limitations.

    For full video recording support, create the browser with:
    Browser(record_video_dir="./videos", record_video_size={"width": 1280, "height": 720})
    """
    try:
        logger.info(f"[start_video] start width={width} height={height}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context
        context_key = _get_context_key(context)

        # Check if video was configured at context creation
        # If so, we can get the video from pages
        if page.video:
            _video_state[context_key] = True
            result = "Video recording is available (configured at browser creation)"
            logger.info(f"[start_video] done {result}")
            return result
        else:
            # Video recording requires context configuration at creation time
            result = (
                "Video recording requires configuration at browser creation time. "
                "Create browser with: Browser(record_video_dir='./videos')"
            )
            logger.warning(f"[start_video] {result}")
            return result
    except Exception as e:
        error_msg = f"Failed to start video: {str(e)}"
        logger.error(f"[start_video] {error_msg}")
        return error_msg


async def stop_video(
    browser: "Browser",
    filename: Optional[str] = None,
) -> str:
    """Stop video recording and save the video file.

    Save the video recording for the current page. The video is
    automatically recorded if the browser was started with video
    recording enabled.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    filename : Optional[str], optional
        Path to save/rename the video file. If not provided, returns
        the path where Playwright saved the video.

    Returns
    -------
    str
        On success: Returns the file path where video was saved.
        On failure: Returns an error message.

    Notes
    -----
    The video file is only finalized when the page is closed or
    the browser context is closed. This function returns the path
    but the file may not be complete until the page is closed.
    """
    try:
        logger.info(f"[stop_video] start filename={filename}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        if not page.video:
            return "No video recording available for this page"

        # Get the video path
        video_path = await page.video.path()

        if filename:
            # Copy/rename to desired location
            import shutil

            if not filename.lower().endswith(".webm"):
                filename = f"{filename}.webm"

            # Create directory if needed
            dirname = os.path.dirname(filename)
            if dirname:
                os.makedirs(dirname, exist_ok=True)

            # Note: The file may still be being written
            # For complete file, page should be closed first
            try:
                shutil.copy(video_path, filename)
                result = f"Video copied to: {filename} (original at: {video_path})"
            except Exception:
                result = f"Video path: {video_path} (close page to finalize)"
        else:
            result = f"Video path: {video_path} (close page to finalize)"

        logger.info(f"[stop_video] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to stop video: {str(e)}"
        logger.error(f"[stop_video] {error_msg}")
        return error_msg


async def add_trace_chunk(
    browser: "Browser",
    title: Optional[str] = None,
) -> str:
    """Add a new chunk to the trace.

    Start a new chunk in the trace with an optional title. This is useful
    for organizing traces into logical sections.

    Parameters
    ----------
    browser : Browser
        Browser instance to use.
    title : Optional[str], optional
        Title for the new trace chunk.

    Returns
    -------
    str
        Operation result message.
    """
    try:
        logger.info(f"[add_trace_chunk] start title={title}")

        page = await browser.get_current_page()
        if page is None:
            return "No active page available"

        context = page.context
        context_key = _get_context_key(context)

        if context_key not in _tracing_state or not _tracing_state[context_key]:
            return "No active tracing. Start tracing first."

        await context.tracing.start_chunk(title=title)

        result = f"New trace chunk started" + (f": {title}" if title else "")
        logger.info(f"[add_trace_chunk] done {result}")
        return result
    except Exception as e:
        error_msg = f"Failed to add trace chunk: {str(e)}"
        logger.error(f"[add_trace_chunk] {error_msg}")
        return error_msg
