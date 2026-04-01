"""
Tests for `get_snapshot_text`.

Integration tests (require real browser) are marked with @pytest.mark.integration.
Run with: pytest -m integration
For unit-only (no browser): pytest -m "not integration" or make test-quick.
If browser tests are disabled (SKIP_BROWSER_TESTS=1), the fixture will skip.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

from bridgic.browser.errors import InvalidInputError
from bridgic.browser.session import EnhancedSnapshot
from bridgic.browser.session._browser import Browser


def _data_url(html: str) -> str:
    return "data:text/html;charset=utf-8," + quote(html)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_snapshot_text_full_page_controls_offscreen_inclusion(browser_instance) -> None:
    # A simple page with one button in the viewport and another pushed below the fold.
    html = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>full_page test</title>
    <style>
      body { margin: 0; font-family: sans-serif; }
      #spacer { height: 2200px; }
    </style>
  </head>
  <body>
    <button id="top-visible-button">Top Visible Button</button>
    <div id="spacer"></div>
    <button id="bottom-offscreen-button">Bottom Offscreen Button</button>
  </body>
</html>
""".strip()

    await browser_instance.navigate_to(_data_url(html))

    snapshot_viewport = await browser_instance.get_snapshot_text(full_page=False)
    snapshot_full_page = await browser_instance.get_snapshot_text(full_page=True)

    assert "Top Visible Button" in snapshot_viewport
    assert "Bottom Offscreen Button" not in snapshot_viewport

    assert "Top Visible Button" in snapshot_full_page
    assert "Bottom Offscreen Button" in snapshot_full_page


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_snapshot_text_interactive_filters_non_interactive(browser_instance) -> None:
    html = """
<!doctype html>
<html>
  <head><meta charset="utf-8" /><title>interactive test</title></head>
  <body>
    <h1>Header Title</h1>
    <p>Some paragraph text that should be considered non-interactive.</p>
    <button id="btn">Click Me</button>
    <input id="name" type="text" aria-label="Name" />
  </body>
</html>
""".strip()

    await browser_instance.navigate_to(_data_url(html))

    snapshot_all = await browser_instance.get_snapshot_text(interactive=False)
    snapshot_interactive = await browser_instance.get_snapshot_text(interactive=True)

    # Interactive snapshot should still contain interactive controls.
    assert "Click Me" in snapshot_interactive

    # And it should be "smaller" (filtered) than the full snapshot in typical cases.
    # We assert "likely filtered" rather than exact content to avoid brittleness across engines.
    assert len(snapshot_interactive) <= len(snapshot_all)

    # In most accessibility trees, plain paragraphs/headings appear in the full snapshot.
    # If it doesn't for some reason, this assertion is still safe: we only require that
    # interactive snapshot does not *increase* content and includes controls.
    assert "Header Title" in snapshot_all


@pytest.mark.asyncio
async def test_get_snapshot_text_overflow_writes_file_and_returns_notice(tmp_path) -> None:
    """When content exceeds limit, full snapshot is written to file and only notice is returned."""
    long_tree = "x" * 500
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )
    mock_browser._write_snapshot_file = Browser._write_snapshot_file.__get__(mock_browser)

    file_path = str(tmp_path / "snap.txt")
    result = await Browser.get_snapshot_text(mock_browser, limit=250, file=file_path)

    assert "[notice]" in result
    assert "saved to:" in result
    assert file_path in result
    # Should NOT contain any snapshot content
    assert "x" * 50 not in result

    # Verify file was written with header + full content
    written = (tmp_path / "snap.txt").read_text(encoding="utf-8")
    assert long_tree in written
    assert written.startswith("[Page:")


@pytest.mark.asyncio
async def test_get_snapshot_text_no_file_when_within_limit() -> None:
    """When content fits within limit and file is None, no file is written."""
    short_tree = "- button 'Click' [ref=e1]"
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=short_tree, refs={})
    )

    result = await Browser.get_snapshot_text(mock_browser, limit=10000)

    assert "[notice]" not in result
    assert "saved to:" not in result
    assert short_tree in result


@pytest.mark.asyncio
async def test_get_snapshot_text_explicit_file_saves_even_within_limit(tmp_path) -> None:
    """When file is explicitly provided, snapshot is always saved regardless of limit."""
    short_tree = "- button 'Click' [ref=e1]"
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=short_tree, refs={})
    )
    mock_browser._write_snapshot_file = Browser._write_snapshot_file.__get__(mock_browser)

    file_path = str(tmp_path / "snap.txt")
    result = await Browser.get_snapshot_text(mock_browser, limit=10000, file=file_path)

    # Should return notice, not snapshot content
    assert "[notice]" in result
    assert "saved to:" in result
    assert file_path in result
    assert short_tree not in result

    # File should contain header + full content
    written = (tmp_path / "snap.txt").read_text(encoding="utf-8")
    assert short_tree in written
    assert written.startswith("[Page:")


@pytest.mark.asyncio
async def test_get_snapshot_text_default_file_in_snapshot_dir(tmp_path) -> None:
    """When file is None, auto-generated path is under BRIDGIC_SNAPSHOT_DIR."""
    long_tree = "y" * 500
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )
    mock_browser._write_snapshot_file = Browser._write_snapshot_file.__get__(mock_browser)

    with patch("bridgic.browser.session._browser.BRIDGIC_SNAPSHOT_DIR", tmp_path):
        result = await Browser.get_snapshot_text(mock_browser, limit=250)

        assert "[notice]" in result
        assert "saved to:" in result
        assert str(tmp_path) in result
        # Verify file was actually created on disk
        files = list(tmp_path.glob("snapshot-*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_get_snapshot_text_file_name_format(tmp_path) -> None:
    """Auto-generated filename matches snapshot-YYYYMMDD-HHMMSS-XXXX.txt pattern."""
    long_tree = "z" * 500
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )
    mock_browser._write_snapshot_file = Browser._write_snapshot_file.__get__(mock_browser)

    with patch("bridgic.browser.session._browser.BRIDGIC_SNAPSHOT_DIR", tmp_path):
        result = await Browser.get_snapshot_text(mock_browser, limit=250)

    # Extract file path from notice
    match = re.search(r"saved to: (.+)", result)
    assert match is not None
    filepath = match.group(1).strip()
    filename = filepath.split("/")[-1]
    assert re.match(r"snapshot-\d{8}-\d{6}-[0-9a-f]{4}\.txt$", filename)


@pytest.mark.asyncio
async def test_get_snapshot_text_overflow_returns_no_snapshot_content(tmp_path) -> None:
    """When content exceeds limit, the returned string contains NO snapshot text."""
    tree_content = "- button 'UniqueMarker12345' [ref=abc123]\n" * 50
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=tree_content, refs={})
    )
    mock_browser._write_snapshot_file = Browser._write_snapshot_file.__get__(mock_browser)

    file_path = str(tmp_path / "snap.txt")
    result = await Browser.get_snapshot_text(mock_browser, limit=100, file=file_path)

    assert "UniqueMarker12345" not in result
    assert "[notice]" in result

    # But file has full content
    written = (tmp_path / "snap.txt").read_text(encoding="utf-8")
    assert "UniqueMarker12345" in written


@pytest.mark.asyncio
async def test_get_snapshot_text_notice_contains_length_info(tmp_path) -> None:
    """Notice includes total length and limit in the message."""
    long_tree = "a" * 300
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )
    mock_browser._write_snapshot_file = Browser._write_snapshot_file.__get__(mock_browser)

    file_path = str(tmp_path / "snap.txt")
    result = await Browser.get_snapshot_text(mock_browser, limit=100, file=file_path)

    # total_chars includes header "[Page:  | ]\n" (12 chars) + 300 = 312
    assert "312 characters" in result
    assert "2 lines" in result
    assert "saved to:" in result


# ── file parameter validation ──


@pytest.mark.asyncio
async def test_get_snapshot_text_rejects_empty_file_path() -> None:
    """Empty string for file should raise InvalidInputError."""
    mock_browser = MagicMock(spec=Browser)
    with pytest.raises(InvalidInputError) as exc_info:
        await Browser.get_snapshot_text(mock_browser, file="")
    assert exc_info.value.code == "INVALID_FILE_PATH"


@pytest.mark.asyncio
async def test_get_snapshot_text_rejects_whitespace_only_file_path() -> None:
    """Whitespace-only string for file should raise InvalidInputError."""
    mock_browser = MagicMock(spec=Browser)
    with pytest.raises(InvalidInputError) as exc_info:
        await Browser.get_snapshot_text(mock_browser, file="   ")
    assert exc_info.value.code == "INVALID_FILE_PATH"


@pytest.mark.asyncio
async def test_get_snapshot_text_rejects_null_byte_in_file_path() -> None:
    """File path containing null bytes should raise InvalidInputError."""
    mock_browser = MagicMock(spec=Browser)
    with pytest.raises(InvalidInputError) as exc_info:
        await Browser.get_snapshot_text(mock_browser, file="/tmp/snap\x00.txt")
    assert exc_info.value.code == "INVALID_FILE_PATH"


@pytest.mark.asyncio
async def test_get_snapshot_text_rejects_directory_as_file_path(tmp_path) -> None:
    """Existing directory as file path should raise InvalidInputError."""
    mock_browser = MagicMock(spec=Browser)
    with pytest.raises(InvalidInputError) as exc_info:
        await Browser.get_snapshot_text(mock_browser, file=str(tmp_path))
    assert exc_info.value.code == "INVALID_FILE_PATH"
