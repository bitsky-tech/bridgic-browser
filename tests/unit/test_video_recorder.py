"""Unit tests for the CDP screencast VideoRecorder."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridgic.browser.session._video_recorder import (
    VideoRecorder,
    _create_white_jpeg,
    _find_ffmpeg,
)


# ---------------------------------------------------------------------------
# _find_ffmpeg
# ---------------------------------------------------------------------------

class TestFindFfmpeg:
    def test_returns_system_ffmpeg(self, tmp_path: Path) -> None:
        """Falls back to system ffmpeg when no Playwright ffmpeg found."""
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}):
            with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
                assert _find_ffmpeg() == "/usr/bin/ffmpeg"

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}):
            with patch("shutil.which", return_value=None):
                with pytest.raises(FileNotFoundError, match="ffmpeg not found"):
                    _find_ffmpeg()

    def test_finds_playwright_ffmpeg(self, tmp_path: Path) -> None:
        """Finds ffmpeg in Playwright cache directory."""
        ffmpeg_dir = tmp_path / "ffmpeg-1011"
        ffmpeg_dir.mkdir()
        ffmpeg_bin = ffmpeg_dir / "ffmpeg-mac"
        ffmpeg_bin.touch()
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}):
            with patch("platform.system", return_value="Darwin"):
                assert _find_ffmpeg() == str(ffmpeg_bin)

    def test_picks_highest_numeric_version_not_lexicographic(self, tmp_path: Path) -> None:
        """Regression: ffmpeg-1011 must beat ffmpeg-999 (numeric, not lex).

        Lexicographic sort would pick 'ffmpeg-999' because '9' > '1'. The
        production code must extract the numeric part and sort numerically.
        """
        for rev in ("999", "1011", "1000"):
            d = tmp_path / f"ffmpeg-{rev}"
            d.mkdir()
            (d / "ffmpeg-mac").touch()
        # Distractor: a non-version directory must be ignored.
        (tmp_path / "ffmpeg-").mkdir()
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}):
            with patch("platform.system", return_value="Darwin"):
                resolved = _find_ffmpeg()
        assert resolved == str(tmp_path / "ffmpeg-1011" / "ffmpeg-mac")


# ---------------------------------------------------------------------------
# _create_white_jpeg
# ---------------------------------------------------------------------------

class TestCreateWhiteJpeg:
    def test_returns_bytes(self) -> None:
        data = _create_white_jpeg(100, 100)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_starts_with_jpeg_soi(self) -> None:
        """JPEG data must start with SOI marker 0xFFD8."""
        data = _create_white_jpeg(200, 150)
        assert data[:2] == b"\xff\xd8"

    def test_fallback_without_pillow(self) -> None:
        """Even without Pillow, a valid JPEG is returned."""
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            # Force ImportError path
            import importlib
            from bridgic.browser.session import _video_recorder as mod
            # Call the function — it should use the fallback bytes
            data = mod._create_white_jpeg(1, 1)
            assert data[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# VideoRecorder
# ---------------------------------------------------------------------------

class TestVideoRecorder:
    def _make_recorder(self, tmp_path: Path) -> VideoRecorder:
        ctx = MagicMock()
        page = MagicMock()
        output = str(tmp_path / "test.webm")
        return VideoRecorder(ctx, page, output, (800, 600))

    def test_init_validates_extension(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must have .webm extension"):
            VideoRecorder(MagicMock(), MagicMock(), str(tmp_path / "bad.mp4"), (800, 600))

    def test_init_sets_state(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        assert rec.is_stopped is False
        assert rec.output_path == str(tmp_path / "test.webm")

    @pytest.mark.asyncio
    async def test_stop_returns_immediately_when_already_stopped(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        rec._is_stopped = True
        path = await rec.stop()
        assert path == rec.output_path

    @pytest.mark.asyncio
    async def test_start_kills_ffmpeg_on_cdp_failure(self, tmp_path: Path) -> None:
        """If CDP session creation fails, ffmpeg process must be killed."""
        rec = self._make_recorder(tmp_path)

        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.stdin = MagicMock()

        with patch("bridgic.browser.session._video_recorder._find_ffmpeg", return_value="/usr/bin/ffmpeg"):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                rec._context.new_cdp_session = AsyncMock(side_effect=RuntimeError("CDP failed"))
                with pytest.raises(RuntimeError, match="CDP failed"):
                    await rec.start()
                # ffmpeg must have been killed
                mock_proc.kill.assert_called_once()
                assert rec._ffmpeg is None

    def test_write_frame_queues_frames(self, tmp_path: Path) -> None:
        """_write_frame should queue repeated frames based on timestamp diff."""
        rec = self._make_recorder(tmp_path)
        # First frame — sets _first_frame_ts
        rec._write_frame(b"frame1", 1000.0)
        assert rec._last_frame is not None
        assert rec._last_frame[0] == b"frame1"
        assert len(rec._frame_queue) == 0  # no repeat yet

        # Second frame 1 second later — should queue ~25 repeats of frame1
        rec._write_frame(b"frame2", 1001.0)
        assert len(rec._frame_queue) == 25  # 25 fps * 1 second
        assert all(f == b"frame1" for f in rec._frame_queue)

    def test_write_frame_empty_sentinel_pads(self, tmp_path: Path) -> None:
        """Empty frame sentinel should pad with last frame data."""
        rec = self._make_recorder(tmp_path)
        rec._write_frame(b"frame1", 1000.0)
        rec._frame_queue.clear()

        # Empty sentinel 0.5s later
        rec._write_frame(b"", 1000.5)
        # Should queue ~12 repeats (floor(0.5 * 25) = 12)
        assert len(rec._frame_queue) == 12
        assert all(f == b"frame1" for f in rec._frame_queue)

    def test_write_frame_ignores_when_stopped(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        rec._is_stopped = True
        rec._write_frame(b"data", 1000.0)
        assert rec._last_frame is None

    @pytest.mark.asyncio
    async def test_flush_queue_writes_to_ffmpeg(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        mock_stdin = MagicMock()
        mock_stdin.is_closing = MagicMock(return_value=False)
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_proc = MagicMock()
        mock_proc.stdin = mock_stdin
        rec._ffmpeg = mock_proc

        from collections import deque
        rec._frame_queue = deque([b"a", b"b", b"c"])
        await rec._flush_queue()

        assert mock_stdin.write.call_count == 3
        assert mock_stdin.drain.await_count == 3
        assert len(rec._frame_queue) == 0

    @pytest.mark.asyncio
    async def test_send_frame_handles_write_error(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        mock_stdin = MagicMock()
        mock_stdin.is_closing = MagicMock(return_value=False)
        mock_stdin.write = MagicMock(side_effect=BrokenPipeError("pipe closed"))
        mock_stdin.drain = AsyncMock()
        mock_proc = MagicMock()
        mock_proc.stdin = mock_stdin
        rec._ffmpeg = mock_proc

        # Should not raise
        await rec._send_frame(b"data")

    @pytest.mark.asyncio
    async def test_send_frame_skips_when_stdin_closing(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        mock_stdin = MagicMock()
        mock_stdin.is_closing = MagicMock(return_value=True)
        mock_stdin.write = MagicMock()
        mock_proc = MagicMock()
        mock_proc.stdin = mock_stdin
        rec._ffmpeg = mock_proc

        await rec._send_frame(b"data")
        mock_stdin.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_uses_devnull_for_stdout_stderr(self, tmp_path: Path) -> None:
        """ffmpeg stdout/stderr must be DEVNULL to avoid pipe-buffer back-pressure.

        Regression guard for M3: PIPE without a reader fills the OS pipe buffer
        (~64 KB on Linux) when ffmpeg writes errors, which then blocks ffmpeg's
        next write() call, which back-pressures stdin.drain(), which deadlocks
        the recorder's stop() path.
        """
        rec = self._make_recorder(tmp_path)

        captured: dict = {}

        async def fake_create(*args, **kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.stdin = MagicMock()
            m.stdin.is_closing = MagicMock(return_value=False)
            m.kill = MagicMock()
            return m

        rec._context.new_cdp_session = AsyncMock()
        rec._context.new_cdp_session.return_value.on = MagicMock()
        rec._context.new_cdp_session.return_value.send = AsyncMock()

        with patch(
            "bridgic.browser.session._video_recorder._find_ffmpeg",
            return_value="/usr/bin/ffmpeg",
        ):
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=fake_create,
            ):
                await rec.start()

        assert captured.get("stdout") == asyncio.subprocess.DEVNULL
        assert captured.get("stderr") == asyncio.subprocess.DEVNULL
        # stdin must remain PIPE — bridgic feeds JPEG bytes into it.
        assert captured.get("stdin") == asyncio.subprocess.PIPE


# ---------------------------------------------------------------------------
# switch_page()
# ---------------------------------------------------------------------------

class TestSwitchPage:
    """Tests for VideoRecorder.switch_page() — hot-swap screencast source."""

    def _make_recorder(self, tmp_path: Path) -> VideoRecorder:
        ctx = MagicMock()
        page = MagicMock()
        output = str(tmp_path / "test.webm")
        return VideoRecorder(ctx, page, output, (800, 600))

    @pytest.mark.asyncio
    async def test_noop_when_stopped(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        rec._is_stopped = True
        old_page = rec._page
        new_page = MagicMock()
        await rec.switch_page(new_page)
        assert rec._page is old_page  # unchanged

    @pytest.mark.asyncio
    async def test_noop_same_page(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        old_page = rec._page
        rec._context.new_cdp_session = AsyncMock()
        await rec.switch_page(old_page)
        # No CDP calls should have been made
        rec._context.new_cdp_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tears_down_old_sets_up_new(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        old_cdp = MagicMock()
        old_cdp.send = AsyncMock()
        old_cdp.remove_listener = MagicMock()
        old_cdp.detach = AsyncMock()
        rec._cdp_session = old_cdp

        new_page = MagicMock()
        new_cdp = MagicMock()
        new_cdp.on = MagicMock()
        new_cdp.send = AsyncMock()
        rec._context.new_cdp_session = AsyncMock(return_value=new_cdp)

        await rec.switch_page(new_page)

        # Old CDP torn down
        old_cdp.send.assert_awaited_once_with("Page.stopScreencast")
        old_cdp.remove_listener.assert_called_once()
        old_cdp.detach.assert_awaited_once()

        # New CDP set up
        rec._context.new_cdp_session.assert_awaited_once_with(new_page)
        new_cdp.on.assert_called_once()
        new_cdp.send.assert_awaited_once()
        assert rec._page is new_page
        assert rec._cdp_session is new_cdp

    @pytest.mark.asyncio
    async def test_survives_cdp_failure(self, tmp_path: Path) -> None:
        """If CDP setup fails on the new page, recorder degrades gracefully."""
        rec = self._make_recorder(tmp_path)
        rec._cdp_session = None  # no old session

        new_page = MagicMock()
        rec._context.new_cdp_session = AsyncMock(
            side_effect=RuntimeError("CDP unavailable"),
        )

        await rec.switch_page(new_page)  # must not raise

        assert rec._page is new_page
        assert rec._cdp_session is None  # degraded

    def test_current_page_property(self, tmp_path: Path) -> None:
        rec = self._make_recorder(tmp_path)
        assert rec.current_page is rec._page
