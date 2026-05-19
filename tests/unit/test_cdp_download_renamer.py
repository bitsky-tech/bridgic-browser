"""Unit tests for CdpDownloadRenamer.

The renamer subscribes to CDP ``Browser.downloadWillBegin`` /
``Browser.downloadProgress`` events. When Chrome saves a download under the
``allowAndName`` behavior the file lands as ``<download_path>/<guid>``; this
helper restores the original ``suggestedFilename`` from ``downloadWillBegin``
once the download reports ``state="completed"``.

These tests drive the contract by simulating CDP events against a fake
``CDPSession`` — no real browser is needed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import pytest

from bridgic.browser.session._cdp_download_renamer import (
    CdpDownloadRenamer,
    sanitize_filename,
)


class FakeCDPSession:
    """Minimal stand-in for ``playwright.async_api.CDPSession``.

    Records ``on(...)`` registrations so tests can synthesize events. Mimics
    the pyee semantics used by Playwright: ``on(event, callback)`` returns
    ``None`` and stores the callback; events can be fired multiple times.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[[dict], None]]] = {}
        self.detach_called: bool = False

    def on(self, event: str, handler: Callable[[dict], None]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Callable[[dict], None]) -> None:
        if event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    async def detach(self) -> None:
        self.detach_called = True

    def fire(self, event: str, params: dict) -> None:
        for handler in self._handlers.get(event, []):
            handler(params)


@pytest.fixture
def tmp_downloads(tmp_path: Path) -> Path:
    d = tmp_path / "downloads"
    d.mkdir()
    return d


# ---------- sanitize_filename ----------


class TestSanitizeFilename:
    def test_keeps_safe_name(self) -> None:
        assert sanitize_filename("report.pdf") == "report.pdf"

    def test_replaces_path_separators(self) -> None:
        out = sanitize_filename("../../etc/passwd")
        assert "/" not in out
        assert "\\" not in out
        assert out.endswith("etc_passwd") or out.endswith("_passwd")

    def test_replaces_windows_forbidden(self) -> None:
        out = sanitize_filename('a<b>c:d"e|f?g*h.txt')
        forbidden = set('<>:"|?*')
        assert not forbidden.intersection(out)
        assert out.endswith(".txt")

    def test_strips_control_chars(self) -> None:
        out = sanitize_filename("a\x00b\x01c.bin")
        assert "\x00" not in out
        assert "\x01" not in out

    def test_empty_falls_back(self) -> None:
        assert sanitize_filename("") == "download"
        assert sanitize_filename("   ") == "download"
        assert sanitize_filename("...") == "download"

    def test_truncates_long(self) -> None:
        name = ("a" * 300) + ".txt"
        out = sanitize_filename(name)
        assert len(out.encode("utf-8")) <= 255
        assert out.endswith(".txt")  # extension preserved


# ---------- attach / detach lifecycle ----------


@pytest.mark.asyncio
class TestLifecycle:
    async def test_attach_subscribes_to_events(self, tmp_downloads: Path) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        assert "Browser.downloadWillBegin" in session._handlers
        assert "Browser.downloadProgress" in session._handlers

    async def test_detach_removes_handlers_and_detaches_session(
        self, tmp_downloads: Path
    ) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]
        await renamer.detach()

        assert session.detach_called is True


# ---------- happy-path rename ----------


@pytest.mark.asyncio
class TestRename:
    async def test_renames_guid_to_suggested_filename(
        self, tmp_downloads: Path
    ) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "deadbeef-1234-5678-9abc-def012345678"
        (tmp_downloads / guid).write_bytes(b"hello world")

        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "hello.txt", "url": "https://x/h"},
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "completed", "receivedBytes": 11, "totalBytes": 11},
        )

        assert (tmp_downloads / "hello.txt").read_bytes() == b"hello world"
        assert not (tmp_downloads / guid).exists()

    async def test_conflict_appends_numeric_suffix(
        self, tmp_downloads: Path
    ) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        # Pre-existing file with the desired name
        (tmp_downloads / "hello.txt").write_bytes(b"original")

        guid = "guid-1"
        (tmp_downloads / guid).write_bytes(b"new")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "hello.txt"},
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "completed"},
        )

        assert (tmp_downloads / "hello.txt").read_bytes() == b"original"
        # Renamed copy should exist with a suffix
        renamed = list(tmp_downloads.glob("hello (*.txt"))
        assert len(renamed) == 1
        assert renamed[0].read_bytes() == b"new"

    async def test_canceled_removes_guid_file(self, tmp_downloads: Path) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "canceled-guid"
        (tmp_downloads / guid).write_bytes(b"partial")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "x.zip"},
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "canceled"},
        )

        assert not (tmp_downloads / guid).exists()
        assert not (tmp_downloads / "x.zip").exists()

    async def test_in_progress_is_ignored(self, tmp_downloads: Path) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "g"
        (tmp_downloads / guid).write_bytes(b"midway")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "y.bin"},
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "inProgress", "receivedBytes": 3},
        )

        assert (tmp_downloads / guid).exists()
        assert not (tmp_downloads / "y.bin").exists()


# ---------- set_default_dir hot-swap ----------


@pytest.mark.asyncio
class TestSetDefaultDir:
    async def test_only_affects_future_downloads(
        self, tmp_path: Path
    ) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        renamer = CdpDownloadRenamer(default_dir=dir_a)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        # Download #1 begins under dir_a
        guid1 = "guid-a"
        (dir_a / guid1).write_bytes(b"data-a")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid1, "suggestedFilename": "first.txt"},
        )

        # Hot-swap to dir_b *before* completion
        renamer.set_default_dir(dir_b)

        # Download #2 begins under dir_b
        guid2 = "guid-b"
        (dir_b / guid2).write_bytes(b"data-b")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid2, "suggestedFilename": "second.txt"},
        )

        # Both complete
        session.fire("Browser.downloadProgress", {"guid": guid1, "state": "completed"})
        session.fire("Browser.downloadProgress", {"guid": guid2, "state": "completed"})

        # In-flight #1 must rename to its captured dir (dir_a), not the new one
        assert (dir_a / "first.txt").read_bytes() == b"data-a"
        assert (dir_b / "second.txt").read_bytes() == b"data-b"

    async def test_get_default_dir_returns_current(self, tmp_path: Path) -> None:
        d1 = tmp_path / "x"; d1.mkdir()
        d2 = tmp_path / "y"; d2.mkdir()
        renamer = CdpDownloadRenamer(default_dir=d1)
        assert renamer.default_dir == d1
        renamer.set_default_dir(d2)
        assert renamer.default_dir == d2


# ---------- robustness ----------


@pytest.mark.asyncio
class TestRobustness:
    async def test_completed_without_will_begin_is_a_noop(
        self, tmp_downloads: Path
    ) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        # Synthetic ghost completion — no prior willBegin
        session.fire(
            "Browser.downloadProgress",
            {"guid": "ghost", "state": "completed"},
        )
        # Nothing should crash and nothing should be created.
        assert list(tmp_downloads.iterdir()) == []

    async def test_missing_source_file_does_not_raise(
        self, tmp_downloads: Path
    ) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "no-file"
        # Note: we do NOT create tmp_downloads / guid
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "missing.bin"},
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "completed"},
        )
        # Should not raise; no file should appear
        assert not (tmp_downloads / "missing.bin").exists()

    async def test_path_traversal_filename_is_neutralized(
        self, tmp_downloads: Path
    ) -> None:
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "trav"
        (tmp_downloads / guid).write_bytes(b"nope")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "../../escape.sh"},
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "completed"},
        )

        # File must be inside tmp_downloads, not in any parent dir
        parent = tmp_downloads.parent
        assert not (parent / "escape.sh").exists()
        # Some sanitized variant should be present in tmp_downloads
        landed = [p for p in tmp_downloads.iterdir() if p.name != guid]
        assert len(landed) == 1
        assert tmp_downloads in landed[0].parents or landed[0].parent == tmp_downloads


@pytest.mark.asyncio
class TestOnCompletedCallback:
    """The on_completed callback wires CdpDownloadRenamer into
    DownloadManager so CDP-borrowed downloads surface uniformly through
    downloaded_files / wait_for_next_download in the rest of bridgic."""

    async def test_callback_receives_populated_downloaded_file(
        self, tmp_downloads: Path
    ) -> None:
        captured: list = []
        renamer = CdpDownloadRenamer(
            default_dir=tmp_downloads,
            on_completed=lambda df: captured.append(df),
        )
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "abc-123"
        (tmp_downloads / guid).write_bytes(b"some-pdf-content-here")
        session.fire(
            "Browser.downloadWillBegin",
            {
                "guid": guid,
                "suggestedFilename": "report.pdf",
                "url": "https://example.com/report.pdf",
            },
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "completed"},
        )

        assert len(captured) == 1
        df = captured[0]
        assert df.file_name == "report.pdf"
        assert df.path.endswith("report.pdf")
        assert df.url == "https://example.com/report.pdf"
        assert df.file_size == len(b"some-pdf-content-here")
        assert df.file_type == "pdf"
        assert df.suggested_filename == "report.pdf"

    async def test_no_callback_when_not_registered(self, tmp_downloads: Path) -> None:
        """Renamer constructed without on_completed must still rename the
        file — only the callback step is skipped."""
        renamer = CdpDownloadRenamer(default_dir=tmp_downloads)  # no callback
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "no-cb"
        (tmp_downloads / guid).write_bytes(b"x")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "x.bin"},
        )
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "completed"},
        )

        # Rename still succeeded
        assert (tmp_downloads / "x.bin").exists()

    async def test_callback_exception_does_not_break_renamer(
        self, tmp_downloads: Path
    ) -> None:
        """A buggy on_completed callback must not corrupt the rename
        pipeline — exception is swallowed and logged."""
        def bad_callback(_df):
            raise RuntimeError("simulated downstream failure")

        renamer = CdpDownloadRenamer(
            default_dir=tmp_downloads, on_completed=bad_callback
        )
        session = FakeCDPSession()
        await renamer.attach(session)  # type: ignore[arg-type]

        guid = "boom"
        (tmp_downloads / guid).write_bytes(b"payload")
        session.fire(
            "Browser.downloadWillBegin",
            {"guid": guid, "suggestedFilename": "ok.bin"},
        )
        # Must not raise.
        session.fire(
            "Browser.downloadProgress",
            {"guid": guid, "state": "completed"},
        )

        # Rename completed despite callback exception.
        assert (tmp_downloads / "ok.bin").read_bytes() == b"payload"
        assert not (tmp_downloads / guid).exists()
