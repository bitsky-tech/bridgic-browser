"""
Tests for `get_llm_repr` (integration: require real browser).

This file focuses on `get_llm_repr` behavior across different options:
- pagination via start_from_char
- truncation notice when snapshot text is too long
- real snapshot differences for interactive / full_page

These tests use a real `Browser` instance via the `browser_instance` fixture,
so they are marked as integration. Run with: pytest -m integration
For unit-only (no browser): pytest -m "not integration" or make test-quick.
If browser tests are disabled (SKIP_BROWSER_TESTS=1), the fixture will skip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest

from bridgic.browser.session import EnhancedSnapshot
from bridgic.browser.tools._browser_state_tools import get_llm_repr


def _data_url(html: str) -> str:
    # Keep it simple + robust for Playwright's page.goto(...)
    return "data:text/html;charset=utf-8," + quote(html)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_llm_repr_full_page_controls_offscreen_inclusion(browser_instance) -> None:
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

    snapshot_viewport = await get_llm_repr(browser_instance, full_page=False)
    snapshot_full_page = await get_llm_repr(browser_instance, full_page=True)

    assert "Top Visible Button" in snapshot_viewport
    assert "Bottom Offscreen Button" not in snapshot_viewport

    assert "Top Visible Button" in snapshot_full_page
    assert "Bottom Offscreen Button" in snapshot_full_page


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_llm_repr_interactive_filters_non_interactive(browser_instance) -> None:
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

    snapshot_all = await get_llm_repr(browser_instance, interactive=False)
    snapshot_interactive = await get_llm_repr(browser_instance, interactive=True)

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
async def test_get_llm_repr_pagination_start_from_char_slices_text(browser_instance) -> None:
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

    full = await get_llm_repr(browser_instance, start_from_char=0)
    assert len(full) > 20

    start = 10
    sliced = await get_llm_repr(browser_instance, start_from_char=start)
    assert sliced == full[start:]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_llm_repr_pagination_start_from_char_exceeds_total_length(browser_instance) -> None:
    html = "<!doctype html><html><body><button>Short</button></body></html>"
    await browser_instance.navigate_to(_data_url(html))

    full = await get_llm_repr(browser_instance)
    result = await get_llm_repr(browser_instance, start_from_char=len(full) + 1)

    assert "exceeds total page state length" in result.lower()


@pytest.mark.asyncio
async def test_get_llm_repr_truncates_and_adds_notice_with_next_start_char(monkeypatch) -> None:
    # Use a mock snapshot with a long tree so truncation is deterministic and does not
    # depend on Playwright including long paragraph text in the accessibility tree.
    from bridgic.browser.tools import _browser_state_tools as state_tools

    monkeypatch.setattr(state_tools, "MAX_CHAR_LIMIT", 250)
    long_tree = "x" * 500  # Exceeds 250 so get_llm_repr will truncate and add notice
    mock_browser = MagicMock()
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )

    result = await get_llm_repr(mock_browser, start_from_char=0)

    assert "[notice]" in result
    assert "start_from_char=250" in result
    assert "bridgic-browser snapshot -s 250" in result


@pytest.mark.asyncio
async def test_get_llm_repr_truncation_next_start_char_accounts_for_offset(monkeypatch) -> None:
    # Use a mock snapshot with a long tree so that start_from_char=50 still leaves
    # more than MAX_CHAR_LIMIT chars, triggering truncation and a notice that mentions
    # the offset (does not depend on real browser snapshot length).
    from bridgic.browser.tools import _browser_state_tools as state_tools

    monkeypatch.setattr(state_tools, "MAX_CHAR_LIMIT", 200)
    # Tree length 500: after slice from 50 we have 450 chars, so truncation + notice.
    long_tree = "y" * 500
    mock_browser = MagicMock()
    mock_browser.get_snapshot = AsyncMock(
        return_value=EnhancedSnapshot(tree=long_tree, refs={})
    )

    start = 50
    result = await get_llm_repr(mock_browser, start_from_char=start)

    assert "[notice]" in result
    assert f"from character {start}" in result


# @pytest.mark.integration
# @pytest.mark.asyncio
# async def test_get_llm_repr(browser_instance) -> None:
#     from pathlib import Path
#     SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "fixtures"
#     TEST_PAGE_PATH = SNAPSHOT_DIR / "test_page.html"
#     test_url = f"file://{TEST_PAGE_PATH.absolute()}"
#     await browser_instance.navigate_to(test_url)

#     interactive_full_page_path = SNAPSHOT_DIR / "snapshot_interactive_full_page.yaml"
#     interactive_full_page_result = await get_llm_repr(browser_instance, interactive=True, full_page=True)
#     interactive_full_page_path.write_text(interactive_full_page_result, encoding="utf-8")
#     assert interactive_full_page_result is not None
#     interactive_path = SNAPSHOT_DIR / "snapshot_interactive.yaml"
#     interactive_result = await get_llm_repr(browser_instance, interactive=True, full_page=False)
#     interactive_path.write_text(interactive_result, encoding="utf-8")
#     assert interactive_result is not None
#     full_page_path = SNAPSHOT_DIR / "snapshot_full_page.yaml"
#     full_page_result = await get_llm_repr(browser_instance, interactive=False, full_page=True)
#     full_page_path.write_text(full_page_result, encoding="utf-8")
#     assert full_page_result is not None 
#     default_path = SNAPSHOT_DIR / "snapshot_default.yaml"
#     default_result = await get_llm_repr(browser_instance, interactive=False, full_page=False)
#     default_path.write_text(default_result, encoding="utf-8")
#     assert default_result is not None