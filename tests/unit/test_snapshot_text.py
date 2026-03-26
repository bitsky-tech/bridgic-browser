"""
Tests for `get_snapshot_text`.

Integration tests (require real browser) are marked with @pytest.mark.integration.
Run with: pytest -m integration
For unit-only (no browser): pytest -m "not integration" or make test-quick.
If browser tests are disabled (SKIP_BROWSER_TESTS=1), the fixture will skip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_snapshot_text_pagination_offset_slices_text(browser_instance) -> None:
    html = """
<!doctype html>
<html><head><meta charset="utf-8" /><title>pagination test</title></head>
<body>
  <button>Alpha</button>
  <button>Beta</button>
  <button>Gamma</button>
</body></html>
""".strip()
    await browser_instance.navigate_to(_data_url(html))

    full = await browser_instance.get_snapshot_text(offset=0)
    assert len(full) > 20

    # The header line ([Page: url | title]\n) is always prepended regardless of
    # offset. Offsets are relative to snapshot.tree (post-header).
    header_len = full.index('\n') + 1
    start = 10
    sliced = await browser_instance.get_snapshot_text(offset=start)
    assert sliced == full[:header_len] + full[header_len + start:]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_snapshot_text_pagination_offset_exceeds_total_length(browser_instance) -> None:
    html = "<!doctype html><html><body><button>Short</button></body></html>"
    await browser_instance.navigate_to(_data_url(html))

    full = await browser_instance.get_snapshot_text()
    with pytest.raises(InvalidInputError) as exc_info:
        await browser_instance.get_snapshot_text(offset=len(full) + 1)
    assert exc_info.value.code == "OFFSET_OUT_OF_RANGE"


@pytest.mark.asyncio
async def test_get_snapshot_text_truncates_and_adds_notice_with_next_offset() -> None:
    # Use a mock snapshot with a long tree so truncation is deterministic and does not
    # depend on Playwright including long paragraph text in the accessibility tree.
    long_tree = "x" * 500  # Exceeds limit=250 so get_snapshot_text will truncate and add notice
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )

    result = await Browser.get_snapshot_text(mock_browser, offset=0, limit=250)

    assert "[notice]" in result
    assert "offset=250" in result
    assert "call get_snapshot_text(" in result
    assert "run: bridgic-browser snapshot" not in result


@pytest.mark.asyncio
async def test_get_snapshot_text_truncation_next_offset_accounts_for_offset() -> None:
    # Use a mock snapshot with a long tree so that offset=50 still leaves
    # more than limit chars, triggering truncation and a notice that mentions
    # the offset (does not depend on real browser snapshot length).
    # Tree length 500: after slice from 50 we have 450 chars, so truncation + notice.
    long_tree = "y" * 500
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )

    start = 50
    result = await Browser.get_snapshot_text(mock_browser, offset=start, limit=200)

    assert "[notice]" in result
    assert f"from character {start}" in result


@pytest.mark.asyncio
async def test_get_snapshot_text_truncation_notice_preserves_snapshot_mode_params() -> None:
    long_tree = "z" * 500
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )

    result = await Browser.get_snapshot_text(
        mock_browser,
        offset=0,
        limit=120,
        interactive=True,
        full_page=False,
    )

    assert "interactive=True, full_page=False" in result
    assert "call get_snapshot_text(" in result
    assert "run: bridgic-browser snapshot" not in result


@pytest.mark.asyncio
async def test_get_snapshot_text_truncation_notice_cli_only() -> None:
    long_tree = "q" * 500
    mock_browser = MagicMock(spec=Browser)
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )

    result = await Browser.get_snapshot_text(
        mock_browser,
        offset=0,
        limit=120,
        interactive=True,
        full_page=False,
        from_cli=True,
    )

    assert "run: bridgic-browser snapshot -i -F -o 120 -l 120" in result
    assert "call get_snapshot_text(" not in result
