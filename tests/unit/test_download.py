"""
Unit tests for the Download module.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from bridgic.browser.session import DownloadManager, DownloadManagerConfig, DownloadedFile


class TestDownloadedFile:
    """Tests for DownloadedFile dataclass."""

    def test_create_downloaded_file(self):
        """Test creating a DownloadedFile instance."""
        file = DownloadedFile(
            url="https://example.com/file.pdf",
            path="/downloads/file.pdf",
            file_name="file.pdf",
            file_size=1024,
            file_type="pdf",
        )

        assert file.url == "https://example.com/file.pdf"
        assert file.path == "/downloads/file.pdf"
        assert file.file_name == "file.pdf"
        assert file.file_size == 1024
        assert file.file_type == "pdf"

    def test_downloaded_file_optional_fields(self):
        """Test DownloadedFile with optional fields."""
        file = DownloadedFile(
            url="https://example.com/file.pdf",
            path="/downloads/file.pdf",
            file_name="file.pdf",
            file_size=1024,
            mime_type="application/pdf",
            suggested_filename="original.pdf",
        )

        assert file.mime_type == "application/pdf"
        assert file.suggested_filename == "original.pdf"
        assert file.file_type is None  # Not set


class TestDownloadManagerConfig:
    """Tests for DownloadManagerConfig dataclass."""

    def test_default_config(self):
        """Test default download manager configuration."""
        config = DownloadManagerConfig()

        assert config.downloads_path == Path.home() / "Downloads"
        assert config.auto_save is True
        assert config.overwrite is False
        assert config.on_download_start is None
        assert config.on_download_complete is None

    def test_custom_config(self):
        """Test custom download manager configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = DownloadManagerConfig(
                downloads_path=Path(tmpdir),
                auto_save=True,
                overwrite=True,
            )

            assert config.downloads_path == Path(tmpdir)
            assert config.overwrite is True

    def test_string_path_conversion(self):
        """Test that string paths are converted to Path objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = DownloadManagerConfig(downloads_path=tmpdir)

            assert isinstance(config.downloads_path, Path)
            assert config.downloads_path == Path(tmpdir)

    def test_path_expansion(self):
        """Test that ~ in path is expanded."""
        config = DownloadManagerConfig(downloads_path="~/Downloads/test")

        assert config.downloads_path == Path.home() / "Downloads" / "test"

    def test_callbacks(self):
        """Test setting callback functions."""
        start_callback = MagicMock()
        complete_callback = MagicMock()

        config = DownloadManagerConfig(
            on_download_start=start_callback,
            on_download_complete=complete_callback,
        )

        assert config.on_download_start is start_callback
        assert config.on_download_complete is complete_callback


class TestDownloadManager:
    """Tests for DownloadManager class."""

    def test_init_with_path(self, temp_downloads_dir):
        """Test initializing DownloadManager with path."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        assert manager.downloads_path == temp_downloads_dir
        assert manager.downloaded_files == []

    def test_init_with_config(self, temp_downloads_dir):
        """Test initializing DownloadManager with config."""
        config = DownloadManagerConfig(downloads_path=temp_downloads_dir)
        manager = DownloadManager(config=config)

        assert manager.downloads_path == temp_downloads_dir

    def test_init_creates_directory(self, temp_dir):
        """Test that init creates downloads directory if it doesn't exist."""
        downloads_path = temp_dir / "new_downloads"
        assert not downloads_path.exists()

        manager = DownloadManager(downloads_path=downloads_path)

        assert downloads_path.exists()

    def test_downloaded_files_initially_empty(self, temp_downloads_dir):
        """Test that downloaded_files is initially empty."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        assert manager.downloaded_files == []

    def test_downloaded_files_returns_copy(self, temp_downloads_dir):
        """Test that downloaded_files returns a copy."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)
        files1 = manager.downloaded_files
        files2 = manager.downloaded_files

        # Should be different list objects
        assert files1 is not files2

    def test_clear_history(self, temp_downloads_dir):
        """Test clearing download history."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        # Add some fake history
        manager._downloaded_files.append(
            DownloadedFile(
                url="https://example.com/file.pdf",
                path="/test/file.pdf",
                file_name="file.pdf",
                file_size=1024,
            )
        )

        assert len(manager.downloaded_files) == 1

        manager.clear_history()

        assert len(manager.downloaded_files) == 0

    def test_get_downloads_by_type(self, temp_downloads_dir):
        """Test filtering downloads by file type."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        # Add various file types
        manager._downloaded_files.extend([
            DownloadedFile(
                url="https://example.com/doc.pdf",
                path="/test/doc.pdf",
                file_name="doc.pdf",
                file_size=1024,
                file_type="pdf",
            ),
            DownloadedFile(
                url="https://example.com/image.png",
                path="/test/image.png",
                file_name="image.png",
                file_size=2048,
                file_type="png",
            ),
            DownloadedFile(
                url="https://example.com/doc2.pdf",
                path="/test/doc2.pdf",
                file_name="doc2.pdf",
                file_size=3072,
                file_type="pdf",
            ),
        ])

        pdfs = manager.get_downloads_by_type("pdf")
        pngs = manager.get_downloads_by_type("png")
        zips = manager.get_downloads_by_type("zip")

        assert len(pdfs) == 2
        assert len(pngs) == 1
        assert len(zips) == 0


class TestDownloadManagerAttach:
    """Tests for DownloadManager attachment methods."""

    def test_attach_to_page(self, temp_downloads_dir, mock_page):
        """Test attaching download handler to a page."""
        from unittest.mock import ANY

        manager = DownloadManager(downloads_path=temp_downloads_dir)

        manager.attach_to_page(mock_page)

        # Verify on() was called with "download" event and a callable handler
        mock_page.on.assert_called_once()
        call_args = mock_page.on.call_args
        assert call_args[0][0] == "download"
        assert callable(call_args[0][1])

    def test_attach_to_context(self, temp_downloads_dir, mock_context, mock_page):
        """Test attaching download handler to a context."""
        from unittest.mock import ANY

        manager = DownloadManager(downloads_path=temp_downloads_dir)

        manager.attach_to_context(mock_context)

        # Should attach to existing pages and listen for new pages
        mock_context.on.assert_called_once()
        call_args = mock_context.on.call_args
        assert call_args[0][0] == "page"
        assert callable(call_args[0][1])

    def test_attach_to_context_twice(self, temp_downloads_dir, mock_context):
        """Test that attaching twice doesn't duplicate handlers."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        manager.attach_to_context(mock_context)
        manager.attach_to_context(mock_context)  # Second call

        # Should only be in attached list once
        assert len(manager._attached_contexts) == 1

    def test_detach_from_context(self, temp_downloads_dir, mock_context, mock_page):
        """Test detaching download handler from a context."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        # Attach first
        manager.attach_to_context(mock_context)

        # Capture the handler that was registered on the context
        context_handler = mock_context.on.call_args[0][1]

        # Detach
        manager.detach_from_context(mock_context)

        # Should remove context-level listener
        mock_context.remove_listener.assert_called_once_with("page", context_handler)

        # Should attempt to remove page-level listener
        mock_page.remove_listener.assert_called()

    @pytest.mark.asyncio
    async def test_detach_from_page_cancels_in_flight_download_tasks(
        self, temp_downloads_dir
    ):
        """Detach must cancel in-flight download processing tasks.

        Without this, detach/close could let the background task finish and
        write files after the caller thought the handler was gone.
        """
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        page = MagicMock()
        page.url = "https://example.com"
        page.on = MagicMock()
        page.remove_listener = MagicMock()

        manager.attach_to_page(page)

        # Grab the page-scoped "download" handler registered by attach_to_page().
        handler = page.on.call_args[0][1]

        started = asyncio.Event()
        never_finishes = asyncio.Event()

        async def _save_as(_path: str) -> None:
            started.set()
            await never_finishes.wait()

        download = MagicMock()
        download.url = "https://example.com/file.pdf"
        download.suggested_filename = "document.pdf"
        download.save_as = AsyncMock(side_effect=_save_as)
        download.failure = AsyncMock(return_value="Network error")

        # Trigger download processing: this schedules _handle_download().
        handler(download)
        await asyncio.wait_for(started.wait(), timeout=0.5)

        # Detach must cancel the task while it's mid-save_as().
        manager.detach_from_page(page)
        await asyncio.sleep(0)

        assert manager.downloaded_files == []
        assert manager._pending_downloads == {}
        download.failure.assert_not_called()


class TestDownloadManagerHandleDownload:
    """Tests for DownloadManager download handling."""

    @pytest.mark.asyncio
    async def test_handle_download(self, temp_downloads_dir, mock_download):
        """Test handling a download event."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        await manager._handle_download(mock_download)

        # Should have saved the file
        mock_download.save_as.assert_called_once()

        # Should have recorded the download
        assert len(manager.downloaded_files) == 1
        assert manager.downloaded_files[0].file_name == "document.pdf"

    @pytest.mark.asyncio
    async def test_handle_download_auto_save_disabled(
        self, temp_downloads_dir, mock_download
    ):
        """Test handling download with auto_save disabled."""
        config = DownloadManagerConfig(
            downloads_path=temp_downloads_dir,
            auto_save=False,
        )
        manager = DownloadManager(config=config)

        await manager._handle_download(mock_download)

        # Should NOT have called save_as
        mock_download.save_as.assert_not_called()

        # Should NOT have recorded the download
        assert len(manager.downloaded_files) == 0

    @pytest.mark.asyncio
    async def test_handle_download_unique_filename(self, temp_downloads_dir, mock_download):
        """Test that download generates unique filename on conflict."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        # Create existing file
        existing_file = temp_downloads_dir / "document.pdf"
        existing_file.touch()

        await manager._handle_download(mock_download)

        # Should have saved with unique name
        call_args = mock_download.save_as.call_args[0][0]
        assert "document (1).pdf" in call_args

    @pytest.mark.asyncio
    async def test_handle_download_with_callbacks(
        self, temp_downloads_dir, mock_download
    ):
        """Test download callbacks are called."""
        start_callback = MagicMock()
        complete_callback = MagicMock()

        config = DownloadManagerConfig(
            downloads_path=temp_downloads_dir,
            on_download_start=start_callback,
            on_download_complete=complete_callback,
        )
        manager = DownloadManager(config=config)

        await manager._handle_download(mock_download)

        start_callback.assert_called_once_with(mock_download)
        complete_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_download_async_callback(
        self, temp_downloads_dir, mock_download
    ):
        """Test async callbacks are awaited."""
        async_callback = AsyncMock()

        config = DownloadManagerConfig(
            downloads_path=temp_downloads_dir,
            on_download_complete=async_callback,
        )
        manager = DownloadManager(config=config)

        await manager._handle_download(mock_download)

        async_callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_download_failure(self, temp_downloads_dir, mock_download):
        """Test handling download failure."""
        mock_download.save_as = AsyncMock(side_effect=Exception("Save failed"))
        mock_download.failure = AsyncMock(return_value="Network error")

        manager = DownloadManager(downloads_path=temp_downloads_dir)

        # Should not raise, just log error
        await manager._handle_download(mock_download)

        # Should NOT have recorded the download
        assert len(manager.downloaded_files) == 0


class TestDownloadManagerWaitForDownload:
    """Tests for DownloadManager wait_for_download method."""

    @pytest.mark.asyncio
    async def test_wait_for_download_timeout(self, temp_downloads_dir, mock_page):
        """Test wait_for_download timeout."""
        manager = DownloadManager(downloads_path=temp_downloads_dir)

        # Mock expect_download to timeout
        mock_page.expect_download = MagicMock()
        mock_page.expect_download.return_value.__aenter__ = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        mock_page.expect_download.return_value.__aexit__ = AsyncMock()

        result = await manager.wait_for_download(
            mock_page,
            action=lambda: None,
            timeout=100,
        )

        assert result is None


class TestDownloadManagerFilenameHelpers:
    """Tests for DownloadManager filename helper methods."""

    def test_get_unique_filename_no_conflict(self, temp_downloads_dir):
        """Test unique filename with no existing file."""
        filename = DownloadManager._get_unique_filename(
            temp_downloads_dir,
            "test.pdf",
            overwrite=False,
        )

        assert filename == "test.pdf"

    def test_get_unique_filename_with_conflict(self, temp_downloads_dir):
        """Test unique filename with existing file."""
        # Create existing file
        (temp_downloads_dir / "test.pdf").touch()

        filename = DownloadManager._get_unique_filename(
            temp_downloads_dir,
            "test.pdf",
            overwrite=False,
        )

        assert filename == "test (1).pdf"

    def test_get_unique_filename_overwrite(self, temp_downloads_dir):
        """Test unique filename with overwrite enabled."""
        # Create existing file
        (temp_downloads_dir / "test.pdf").touch()

        filename = DownloadManager._get_unique_filename(
            temp_downloads_dir,
            "test.pdf",
            overwrite=True,
        )

        assert filename == "test.pdf"

    def test_get_file_type_pdf(self):
        """Test file type extraction for PDF."""
        assert DownloadManager._get_file_type("document.pdf") == "pdf"

    def test_get_file_type_uppercase(self):
        """Test file type extraction normalizes to lowercase."""
        assert DownloadManager._get_file_type("image.PNG") == "png"

    def test_get_file_type_double_extension(self):
        """Test file type extraction with double extension."""
        assert DownloadManager._get_file_type("archive.tar.gz") == "gz"

    def test_get_file_type_no_extension(self):
        """Test file type extraction with no extension."""
        assert DownloadManager._get_file_type("README") is None

    def test_get_file_type_hidden_file(self):
        """Test file type extraction for hidden file without extension."""
        # Hidden files like .gitignore have no extension
        # os.path.splitext(".gitignore") returns (".gitignore", "")
        assert DownloadManager._get_file_type(".gitignore") is None

    def test_get_file_type_hidden_with_extension(self):
        """Test file type extraction for hidden file with extension."""
        # Hidden files can have extensions, e.g., .config.json
        assert DownloadManager._get_file_type(".config.json") == "json"


class TestSanitizeFilename:
    """Tests for _sanitize_filename, including Windows reserved-name guard."""

    def test_plain_filename_unchanged(self) -> None:
        assert DownloadManager._sanitize_filename("report.pdf") == "report.pdf"

    def test_traversal_stripped(self) -> None:
        assert (
            DownloadManager._sanitize_filename("../../etc/passwd") == "passwd"
        )

    def test_illegal_chars_replaced(self) -> None:
        assert (
            DownloadManager._sanitize_filename('bad:name"?.pdf')
            == "bad_name__.pdf"
        )

    def test_empty_falls_back_to_download(self) -> None:
        assert DownloadManager._sanitize_filename("") == "download"
        assert DownloadManager._sanitize_filename("   ...   ") == "download"

    def test_windows_reserved_con(self) -> None:
        assert DownloadManager._sanitize_filename("CON.pdf") == "_CON.pdf"

    def test_windows_reserved_com1(self) -> None:
        assert DownloadManager._sanitize_filename("COM1.txt") == "_COM1.txt"

    def test_windows_reserved_bare(self) -> None:
        # No extension: still reserved on Windows.
        assert DownloadManager._sanitize_filename("NUL") == "_NUL"

    def test_windows_reserved_case_insensitive(self) -> None:
        # Windows matches device names case-insensitively.
        assert DownloadManager._sanitize_filename("aux.log") == "_aux.log"

    def test_non_reserved_prefix_unchanged(self) -> None:
        # CONsole.pdf is NOT reserved — only the exact device name.
        assert (
            DownloadManager._sanitize_filename("CONsole.pdf") == "CONsole.pdf"
        )


class TestGetUniqueFilenameFallback:
    """Tests for _get_unique_filename 10000-collision fallback path."""

    def test_collision_fallback_returns_non_existent_name(
        self, temp_downloads_dir
    ) -> None:
        """After 9999 collisions the timestamp-suffix name must not clash.

        Pre-create 9999 ``file (N).pdf`` entries plus the original ``file.pdf``
        so the counter loop exhausts and we take the timestamp branch. The
        returned name must (a) carry the ``file (...)`` shape and (b) not
        already exist in the directory.
        """
        (temp_downloads_dir / "file.pdf").touch()
        for i in range(1, 10000):
            (temp_downloads_dir / f"file ({i}).pdf").touch()

        result = DownloadManager._get_unique_filename(
            temp_downloads_dir,
            "file.pdf",
            overwrite=False,
        )

        # The counter loop is exhausted — the fallback branch is the only
        # way we still return a name here.
        assert result.startswith("file (")
        assert result.endswith(").pdf")
        assert not (temp_downloads_dir / result).exists()
