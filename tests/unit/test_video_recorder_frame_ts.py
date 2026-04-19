"""
Regression tests for the ``_first_frame_ts is None`` check.

Before the fix, ``_write_frame`` tested ``if not self._first_frame_ts``, which
is True for both ``None`` *and* ``0.0``. When CDP delivered a frame with
``metadata.timestamp == 0.0`` (observed right after a Chrome restart, because
``monotonicTime`` starts near zero), every frame kept resetting
``_first_frame_ts``, producing a stuck ``frame_number == 0`` and making the
output video freeze on frame 1. The fix replaces the check with
``is None`` — this file locks that invariant.
"""
from unittest.mock import MagicMock

from bridgic.browser.session._video_recorder import VideoRecorder, _FPS


def _make_recorder(tmp_path) -> VideoRecorder:
    out = tmp_path / "out.webm"
    return VideoRecorder(
        context=MagicMock(),
        page=MagicMock(),
        output_path=str(out),
        size=(800, 600),
    )


class TestFirstFrameTsZero:
    def test_zero_timestamp_seeds_first_frame_ts(self, tmp_path) -> None:
        """A legitimate ``timestamp == 0.0`` first frame must set the seed."""
        rec = _make_recorder(tmp_path)
        assert rec._first_frame_ts is None
        rec._write_frame(b"\xff\xd8\xff\xd9", 0.0)  # minimal JPEG bytes
        assert rec._first_frame_ts == 0.0

    def test_second_frame_does_not_reset_seed(self, tmp_path) -> None:
        """With the fix, later frames keep the seed at 0.0 — not whichever
        most-recent timestamp came in. This is what makes frame numbers
        advance past 0 instead of staying stuck."""
        rec = _make_recorder(tmp_path)
        rec._write_frame(b"\xff\xd8\xff\xd9", 0.0)
        rec._write_frame(b"\xff\xd8\xff\xd9", 1.0)
        assert rec._first_frame_ts == 0.0

    def test_frame_number_advances(self, tmp_path) -> None:
        """After the fix, ``_last_frame[2]`` (frame_number) must advance."""
        rec = _make_recorder(tmp_path)
        rec._write_frame(b"\xff\xd8\xff\xd9", 0.0)
        assert rec._last_frame is not None
        first_frame_number = rec._last_frame[2]

        # Simulate a frame one second later.
        rec._write_frame(b"\xff\xd8\xff\xd9", 1.0)
        assert rec._last_frame is not None
        later_frame_number = rec._last_frame[2]

        assert later_frame_number > first_frame_number
        # 25 fps * 1 second = 25 frames gap.
        assert later_frame_number == first_frame_number + _FPS

    def test_nonzero_first_frame_still_works(self, tmp_path) -> None:
        """Backwards-compat: a normal non-zero first timestamp is seeded too."""
        rec = _make_recorder(tmp_path)
        rec._write_frame(b"\xff\xd8\xff\xd9", 12345.678)
        assert rec._first_frame_ts == 12345.678
