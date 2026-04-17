"""
Download manager for handling browser file downloads.

Based on browser-use's download handling implementation.
Ensures files are saved with correct filenames instead of hash values.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from playwright.async_api import Download, Page, BrowserContext

logger = logging.getLogger(__name__)


@dataclass
class DownloadedFile:
    """Information about a downloaded file."""

    url: str
    path: str
    file_name: str
    file_size: int
    file_type: Optional[str] = None  # e.g., 'pdf', 'zip', 'docx'
    mime_type: Optional[str] = None  # e.g., 'application/pdf'
    suggested_filename: Optional[str] = None  # Original suggested name


@dataclass
class DownloadManagerConfig:
    """Configuration for the download manager.

    Parameters
    ----------
    downloads_path : Path | str
        Directory where downloads will be saved.
    auto_save : bool
        Whether to automatically save downloads with correct filenames.
        Default True.
    overwrite : bool
        Whether to overwrite existing files. If False, will generate
        unique names like "file (1).pdf". Default False.
    on_download_start : Callable
        Callback when download starts. Receives Download object.
    on_download_complete : Callable
        Callback when download completes. Receives DownloadedFile object.
    """

    downloads_path: Path = field(default_factory=lambda: Path.home() / "Downloads")
    auto_save: bool = True
    overwrite: bool = False
    on_download_start: Optional[Callable[["Download"], Any]] = None
    on_download_complete: Optional[Callable[[DownloadedFile], Any]] = None

    def __post_init__(self):
        if isinstance(self.downloads_path, str):
            self.downloads_path = Path(self.downloads_path)
        self.downloads_path = self.downloads_path.expanduser()


class DownloadManager:
    """Manages browser downloads with correct filename handling.

    This class solves the problem where Playwright saves files with
    hash/UUID names instead of their original filenames.

    Usage
    -----
    >>> manager = DownloadManager(downloads_path="~/Downloads")
    >>> manager.attach_to_context(browser_context)
    >>>
    >>> # Downloads will now be saved with correct names
    >>> await page.click("a[download]")
    >>>
    >>> # Get list of downloaded files
    >>> files = manager.downloaded_files

    How it works
    ------------
    1. Listens to 'download' events on all pages in the context
    2. When a download starts, waits for it to complete
    3. Uses `download.suggested_filename` to get the original name
    4. Saves the file using `download.save_as()` with the correct name
    """

    def __init__(
        self,
        downloads_path: Optional[Path | str] = None,
        config: Optional[DownloadManagerConfig] = None,
    ):
        """Initialize the download manager.

        Parameters
        ----------
        downloads_path : Path | str, optional
            Directory for downloads. Defaults to ~/Downloads.
        config : DownloadManagerConfig, optional
            Full configuration object. If provided, downloads_path is ignored.
        """
        if config:
            self._config = config
        else:
            self._config = DownloadManagerConfig(
                downloads_path=Path(downloads_path).expanduser()
                if downloads_path
                else Path.home() / "Downloads"
            )

        # Ensure downloads directory exists
        self._config.downloads_path.mkdir(parents=True, exist_ok=True)

        # Track downloaded files
        self._downloaded_files: List[DownloadedFile] = []
        self._pending_downloads: Dict[str, "Download"] = {}
        self._attached_contexts: List["BrowserContext"] = []
        # Track handlers for cleanup
        self._page_handlers: Dict[str, Callable] = {}
        self._context_handlers: Dict[str, Callable] = {}
        # Track in-flight per-page download handler tasks so detach/close
        # can cancel them and avoid writing files after teardown.
        self._page_download_tasks: Dict[str, set[asyncio.Task[None]]] = {}

    @property
    def downloads_path(self) -> Path:
        """Get the downloads directory path."""
        return self._config.downloads_path

    @property
    def downloaded_files(self) -> List[DownloadedFile]:
        """Get list of all downloaded files in this session."""
        return self._downloaded_files.copy()

    def attach_to_context(self, context: "BrowserContext") -> None:
        """Attach download handler to a browser context.

        This will handle downloads for all pages in the context,
        including pages created after this call.

        Parameters
        ----------
        context : BrowserContext
            The Playwright browser context to attach to.
        """
        if context in self._attached_contexts:
            logger.debug("Download manager already attached to this context")
            return

        # Handle downloads on all existing pages
        for page in context.pages:
            self._attach_to_page(page)

        # Handle downloads on new pages
        handler = lambda page: self._attach_to_page(page)
        context.on("page", handler)
        self._context_handlers[str(id(context))] = handler

        self._attached_contexts.append(context)
        logger.info(f"Download manager attached, saving to: {self._config.downloads_path}")

    def detach_from_context(self, context: "BrowserContext") -> None:
        """Detach download handler from a browser context and its pages."""
        if context not in self._attached_contexts:
            return

        # Remove context-level page listener
        context_key = str(id(context))
        handler = self._context_handlers.pop(context_key, None)
        if handler:
            try:
                context.remove_listener("page", handler)
            except Exception:
                pass

        # Detach from all pages in this context
        for page in context.pages:
            self._detach_from_page(page)

        try:
            self._attached_contexts.remove(context)
        except ValueError:
            pass

    def attach_to_page(self, page: "Page") -> None:
        """Attach download handler to a specific page.

        Parameters
        ----------
        page : Page
            The Playwright page to attach to.
        """
        self._attach_to_page(page)

    def detach_from_page(self, page: "Page") -> None:
        """Detach download handler from a specific page (no-op if not attached).

        Counterpart to :meth:`attach_to_page`. Use when the handler was
        registered page-scoped (e.g. CDP borrowed-context mode where attaching
        to the whole context would hijack the user's other tabs).
        """
        self._detach_from_page(page)

    def _attach_to_page(self, page: "Page") -> None:
        """Internal method to attach download handler to a page."""
        page_key = str(id(page))

        # Remove old handler if exists
        self._detach_from_page(page)

        def handle_download(download):
            task: asyncio.Task[None] = asyncio.create_task(
                self._handle_download(download)
            )
            self._page_download_tasks.setdefault(page_key, set()).add(task)

            def _on_done(t: asyncio.Task[None]) -> None:
                tasks = self._page_download_tasks.get(page_key)
                if tasks is not None:
                    tasks.discard(t)
                    if not tasks:
                        self._page_download_tasks.pop(page_key, None)
                try:
                    t.result()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"Download task failed: {e}")

            task.add_done_callback(_on_done)

        page.on("download", handle_download)
        self._page_handlers[page_key] = handle_download
        logger.debug(f"Download handler attached to page: {page.url}")

    def _detach_from_page(self, page: "Page") -> None:
        """Internal method to detach download handler from a page."""
        page_key = str(id(page))
        handler = self._page_handlers.pop(page_key, None)
        if handler:
            try:
                page.remove_listener("download", handler)
            except Exception:
                pass

        # Cancel any in-flight download processing tasks started by this
        # page-scoped handler.
        tasks = self._page_download_tasks.pop(page_key, set())
        for t in tasks:
            t.cancel()

    async def _handle_download(self, download: "Download") -> None:
        """Handle a download event.

        Parameters
        ----------
        download : Download
            The Playwright Download object.
        """
        url = download.url
        suggested_filename = download.suggested_filename

        logger.info(f"Download started: {suggested_filename} from {url}")

        # Call start callback if configured
        if self._config.on_download_start:
            try:
                result = self._config.on_download_start(download)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Download start callback error: {e}")

        # Track pending download
        self._pending_downloads[url] = download

        try:
            if not self._config.auto_save:
                # Just wait for download without saving
                path = await download.path()
                if path:
                    logger.info(f"Download completed (temp): {path}")
                return

            # Sanitise filename to prevent path traversal and Windows-illegal chars.
            safe_filename = self._sanitize_filename(suggested_filename)

            # Generate unique filename if needed
            target_filename = self._get_unique_filename(
                self._config.downloads_path,
                safe_filename,
                overwrite=self._config.overwrite,
            )
            target_path = self._config.downloads_path / target_filename

            # Final path-traversal guard: resolved path must stay inside downloads_path.
            if not target_path.resolve().is_relative_to(
                self._config.downloads_path.resolve()
            ):
                logger.warning(
                    f"Download filename resolved outside downloads_path, "
                    f"using fallback: {suggested_filename!r}"
                )
                target_filename = "download"
                target_path = self._config.downloads_path / target_filename

            # Save with correct filename
            await download.save_as(str(target_path))

            # Get file info
            file_size = target_path.stat().st_size if target_path.exists() else 0
            file_type = self._get_file_type(target_filename)

            # Create download record
            downloaded_file = DownloadedFile(
                url=url,
                path=str(target_path),
                file_name=target_filename,
                file_size=file_size,
                file_type=file_type,
                suggested_filename=suggested_filename,
            )

            self._downloaded_files.append(downloaded_file)
            logger.info(f"Download saved: {target_path} ({file_size} bytes)")

            # Call complete callback if configured
            if self._config.on_download_complete:
                try:
                    result = self._config.on_download_complete(downloaded_file)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.warning(f"Download complete callback error: {e}")

        except asyncio.CancelledError:
            # Cancellation is part of detach/close lifecycle. Do not treat
            # it as a download failure, and do not attempt download.failure().
            raise
        except Exception as e:
            logger.error(f"Download failed: {suggested_filename} - {e}")
            # Try to get failure reason
            failure = await download.failure()
            if failure:
                logger.error(f"Download failure reason: {failure}")

        finally:
            # Remove from pending
            self._pending_downloads.pop(url, None)

    @staticmethod
    def _get_unique_filename(
        directory: Path,
        filename: str,
        overwrite: bool = False,
    ) -> str:
        """Generate a unique filename if file already exists.

        Parameters
        ----------
        directory : Path
            Target directory.
        filename : str
            Desired filename.
        overwrite : bool
            If True, return filename as-is even if exists.

        Returns
        -------
        str
            Unique filename (may have counter appended).
        """
        if overwrite:
            return filename

        target_path = directory / filename
        if not target_path.exists():
            return filename

        # Generate unique name: "file (1).pdf", "file (2).pdf", etc.
        base, ext = os.path.splitext(filename)
        counter = 1

        while counter <= 9999:
            new_filename = f"{base} ({counter}){ext}"
            if not (directory / new_filename).exists():
                return new_filename
            counter += 1
        # Extremely unlikely: 10000 collisions.  Return a name that is
        # guaranteed unique by including the counter.
        return f"{base} ({counter}){ext}"

    # Characters illegal in Windows filenames (also covers / and \ for traversal).
    _UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitise a server-suggested filename for safe local storage.

        Strips path separators (preventing traversal), replaces Windows-illegal
        characters, and collapses leading/trailing dots/spaces (reserved on
        Windows).  Falls back to ``"download"`` if the result is empty.
        """
        # Use only the basename (strip any directory components).
        filename = os.path.basename(filename)

        # Replace characters illegal on Windows (and dangerous on all platforms).
        filename = DownloadManager._UNSAFE_FILENAME_RE.sub("_", filename)

        # Strip leading/trailing dots and spaces (Windows reserved).
        filename = filename.strip(". ")

        return filename or "download"

    @staticmethod
    def _get_file_type(filename: str) -> Optional[str]:
        """Extract file type from filename.

        Parameters
        ----------
        filename : str
            The filename to extract type from.

        Returns
        -------
        Optional[str]
            File extension without dot, or None.
        """
        _, ext = os.path.splitext(filename)
        return ext[1:].lower() if ext else None

    async def wait_for_download(
        self,
        page: "Page",
        action: Callable,
        timeout: float = 30000,
    ) -> Optional[DownloadedFile]:
        """Wait for a download triggered by an action.

        Parameters
        ----------
        page : Page
            The page where download will be triggered.
        action : Callable
            Async function that triggers the download (e.g., clicking a button).
        timeout : float
            Maximum time to wait in milliseconds.

        Returns
        -------
        Optional[DownloadedFile]
            The downloaded file info, or None if failed.

        Example
        -------
        >>> file = await manager.wait_for_download(
        ...     page,
        ...     lambda: page.click("a.download-btn"),
        ... )
        >>> print(f"Downloaded: {file.file_name}")
        """
        downloaded = None
        download_event = asyncio.Event()

        original_callback = self._config.on_download_complete

        def on_complete(file: DownloadedFile):
            nonlocal downloaded
            downloaded = file
            download_event.set()
            if original_callback:
                result = original_callback(file)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)

        self._config.on_download_complete = on_complete

        try:
            # Start waiting for download
            async with page.expect_download(timeout=timeout) as download_info:
                # Perform the action that triggers download
                action_result = action()
                if asyncio.iscoroutine(action_result):
                    await action_result

            # Wait for our handler to process it
            await asyncio.wait_for(
                download_event.wait(),
                timeout=timeout / 1000,
            )

            return downloaded

        except asyncio.TimeoutError:
            logger.warning("Download wait timed out")
            return None
        finally:
            self._config.on_download_complete = original_callback

    def clear_history(self) -> None:
        """Clear the download history."""
        self._downloaded_files.clear()

    def get_downloads_by_type(self, file_type: str) -> List[DownloadedFile]:
        """Get all downloads of a specific file type.

        Parameters
        ----------
        file_type : str
            File extension without dot (e.g., "pdf", "xlsx").

        Returns
        -------
        List[DownloadedFile]
            List of matching downloads.
        """
        return [f for f in self._downloaded_files if f.file_type == file_type.lower()]


__all__ = ["DownloadManager", "DownloadManagerConfig", "DownloadedFile"]
