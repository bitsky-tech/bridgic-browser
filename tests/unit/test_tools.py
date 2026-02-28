"""
Unit tests for Browser Tools.

This file tests all browser tools including:
- Navigation tools
- Page control tools
- Tab management tools
- Element interaction tools
- Mouse tools (coordinate-based)
- Keyboard tools
- Screenshot tools
- Network tools
- Dialog tools
- Storage tools
- Verification tools
- DevTools (tracing/video)
- State tools
- BrowserToolSetBuilder
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ==================== Fixtures ====================

@pytest.fixture
def mock_browser():
    """Create a comprehensive mock Browser instance."""
    browser = MagicMock()
    browser.navigate_to = AsyncMock()
    browser.get_current_page = AsyncMock()
    browser.get_pages = MagicMock(return_value=[])
    browser.get_all_page_descs = AsyncMock(return_value=[])
    browser.switch_to_page = AsyncMock(return_value=(True, "Switched"))
    browser.close_page = AsyncMock(return_value=(True, "Closed"))
    browser.new_page = AsyncMock()
    browser.get_current_page_info = AsyncMock()
    browser.get_snapshot = AsyncMock()
    browser.get_element_by_ref = AsyncMock()

    # Mock page
    mock_page = MagicMock()
    mock_page.url = "https://example.com/test"
    mock_page.title = AsyncMock(return_value="Test Page")

    # Mock mouse
    mock_page.mouse = MagicMock()
    mock_page.mouse.move = AsyncMock()
    mock_page.mouse.click = AsyncMock()
    mock_page.mouse.down = AsyncMock()
    mock_page.mouse.up = AsyncMock()
    mock_page.mouse.wheel = AsyncMock()

    # Mock keyboard
    mock_page.keyboard = MagicMock()
    mock_page.keyboard.press = AsyncMock()
    mock_page.keyboard.down = AsyncMock()
    mock_page.keyboard.up = AsyncMock()
    mock_page.keyboard.insert_text = AsyncMock()

    # Mock other page methods
    mock_page.go_back = AsyncMock()
    mock_page.go_forward = AsyncMock()
    mock_page.reload = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"fake_screenshot_data")
    mock_page.pdf = AsyncMock(return_value=b"fake_pdf_data")
    mock_page.evaluate = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.set_viewport_size = AsyncMock()
    mock_page.get_by_text = MagicMock()
    mock_page.get_by_role = MagicMock()

    # Mock context
    mock_context = MagicMock()
    mock_context.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})
    mock_context.add_cookies = AsyncMock()
    mock_context.clear_cookies = AsyncMock()
    mock_context.cookies = AsyncMock(return_value=[])
    mock_context.tracing = MagicMock()
    mock_context.tracing.start = AsyncMock()
    mock_context.tracing.stop = AsyncMock()
    mock_context.new_page = AsyncMock()
    mock_page.context = mock_context

    browser.get_current_page.return_value = mock_page
    browser._page = mock_page
    browser._context = mock_context

    return browser


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ==================== Navigation Tools Tests ====================

class TestNavigationTools:
    """Tests for navigation tools."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("engine,expected_domain", [
        ("duckduckgo", "duckduckgo.com"),
        ("google", "google.com"),
        ("bing", "bing.com"),
    ])
    async def test_search_engines(self, mock_browser, engine, expected_domain):
        """Test search with different engines."""
        from bridgic.browser.tools._browser_tools import search

        result = await search(mock_browser, "test query", engine)

        mock_browser.navigate_to.assert_called_once()
        call_url = mock_browser.navigate_to.call_args[0][0]
        assert expected_domain in call_url

    @pytest.mark.asyncio
    async def test_search_invalid_engine(self, mock_browser):
        """Test search with invalid engine returns error."""
        from bridgic.browser.tools._browser_tools import search

        result = await search(mock_browser, "test query", "invalid")

        assert "unsupported" in result.lower() or "error" in result.lower()
        mock_browser.navigate_to.assert_not_called()

    @pytest.mark.asyncio
    async def test_navigate_to_url(self, mock_browser):
        """Test navigate_to_url."""
        from bridgic.browser.tools._browser_tools import navigate_to_url

        result = await navigate_to_url(mock_browser, "https://example.com")

        mock_browser.navigate_to.assert_called_once_with(
            "https://example.com", wait_until="domcontentloaded", timeout=None
        )
        assert "Navigated to" in result

    @pytest.mark.asyncio
    async def test_navigate_to_url_adds_protocol(self, mock_browser):
        """Test navigate_to_url adds http:// if missing."""
        from bridgic.browser.tools._browser_tools import navigate_to_url

        result = await navigate_to_url(mock_browser, "example.com")

        mock_browser.navigate_to.assert_called_once_with(
            "http://example.com", wait_until="domcontentloaded", timeout=None
        )

    @pytest.mark.asyncio
    async def test_navigate_to_url_empty(self, mock_browser):
        """Test navigate_to_url with empty URL."""
        from bridgic.browser.tools._browser_tools import navigate_to_url

        result = await navigate_to_url(mock_browser, "")

        assert "empty" in result.lower()
        mock_browser.navigate_to.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dangerous_url", [
        "javascript:alert(1)",
        "data:text/html,<h1>test</h1>",
    ])
    async def test_navigate_to_url_blocks_dangerous(self, mock_browser, dangerous_url):
        """Test navigate_to_url blocks dangerous URLs."""
        from bridgic.browser.tools._browser_tools import navigate_to_url

        result = await navigate_to_url(mock_browser, dangerous_url)

        assert "not allowed" in result.lower()
        mock_browser.navigate_to.assert_not_called()

    @pytest.mark.asyncio
    async def test_go_back(self, mock_browser):
        """Test go_back."""
        from bridgic.browser.tools._browser_tools import go_back

        result = await go_back(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.go_back.assert_called_once()
        assert "back" in result.lower()

    @pytest.mark.asyncio
    async def test_go_forward(self, mock_browser):
        """Test go_forward."""
        from bridgic.browser.tools._browser_tools import go_forward

        result = await go_forward(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.go_forward.assert_called_once()
        assert "forward" in result.lower()

    @pytest.mark.asyncio
    async def test_reload_page(self, mock_browser):
        """Test reload_page."""
        from bridgic.browser.tools._browser_tools import reload_page

        result = await reload_page(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.reload.assert_called_once()


# ==================== Page Control Tools Tests ====================

class TestPageControlTools:
    """Tests for page control tools."""

    @pytest.mark.asyncio
    async def test_scroll_to_text(self, mock_browser):
        """Test scroll_to_text."""
        from bridgic.browser.tools._browser_tools import scroll_to_text

        mock_page = mock_browser.get_current_page.return_value
        mock_first_locator = MagicMock()
        mock_first_locator.bounding_box = AsyncMock(return_value={"x": 0, "y": 100})
        mock_first_locator.scroll_into_view_if_needed = AsyncMock()

        mock_locator = MagicMock()
        mock_locator.first = mock_first_locator
        mock_page.get_by_text = MagicMock(return_value=mock_locator)

        result = await scroll_to_text(mock_browser, "Some text")

        mock_page.get_by_text.assert_called_with("Some text", exact=False)

    @pytest.mark.asyncio
    async def test_evaluate_javascript(self, mock_browser):
        """Test evaluate_javascript."""
        from bridgic.browser.tools._browser_tools import evaluate_javascript

        mock_page = mock_browser.get_current_page.return_value
        mock_page.evaluate.return_value = {"result": "test"}

        result = await evaluate_javascript(mock_browser, "return {result: 'test'}")

        mock_page.evaluate.assert_called()
        assert "result" in result

    @pytest.mark.asyncio
    async def test_wait_for_time(self, mock_browser):
        """Test wait_for function with time parameter."""
        from bridgic.browser.tools._browser_tools import wait_for
        import time

        start = time.time()
        result = await wait_for(mock_browser, time_seconds=0.5)
        elapsed = time.time() - start

        assert elapsed >= 0.5
        assert "wait" in result.lower() or "0.5" in result

    @pytest.mark.asyncio
    async def test_wait_for_text(self, mock_browser):
        """Test wait_for function with text parameter."""
        from bridgic.browser.tools._browser_tools import wait_for

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_locator.first = MagicMock()
        mock_locator.first.wait_for = AsyncMock()
        mock_page.get_by_text = MagicMock(return_value=mock_locator)

        result = await wait_for(mock_browser, text="Loading complete")

        mock_page.get_by_text.assert_called_once_with("Loading complete", exact=False)
        mock_locator.first.wait_for.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_current_page_info(self, mock_browser):
        """Test get_current_page_info."""
        from bridgic.browser.tools._browser_tools import get_current_page_info

        mock_browser.get_current_page_info.return_value = MagicMock(
            url="https://example.com",
            title="Example",
            viewport_width=1920,
            viewport_height=1080,
        )

        result = await get_current_page_info(mock_browser)

        mock_browser.get_current_page_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_browser_resize(self, mock_browser):
        """Test resizing browser viewport."""
        from bridgic.browser.tools._browser_tools import browser_resize

        result = await browser_resize(mock_browser, width=1280, height=720)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.set_viewport_size.assert_called_once_with({"width": 1280, "height": 720})
        assert "1280" in result and "720" in result


# ==================== Tab Management Tools Tests ====================

class TestTabManagementTools:
    """Tests for tab management tools."""

    @pytest.mark.asyncio
    async def test_new_tab(self, mock_browser):
        """Test new_tab."""
        from bridgic.browser.tools._browser_tools import new_tab

        mock_browser.new_page.return_value = MagicMock()

        result = await new_tab(mock_browser)

        mock_browser.new_page.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_tab_with_url(self, mock_browser):
        """Test new_tab with URL."""
        from bridgic.browser.tools._browser_tools import new_tab

        mock_browser.new_page.return_value = MagicMock()

        result = await new_tab(mock_browser, "https://example.com")

        mock_browser.new_page.assert_called_once_with(
            "https://example.com", wait_until="domcontentloaded", timeout=None
        )

    @pytest.mark.asyncio
    async def test_get_tabs(self, mock_browser):
        """Test get_tabs."""
        from bridgic.browser.tools._browser_tools import get_tabs

        mock_browser.get_all_page_descs.return_value = [
            MagicMock(url="https://example.com", title="Example", page_id="1"),
            MagicMock(url="https://test.com", title="Test", page_id="2"),
        ]

        result = await get_tabs(mock_browser)

        mock_browser.get_all_page_descs.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_tab(self, mock_browser):
        """Test switch_tab."""
        from bridgic.browser.tools._browser_tools import switch_tab

        result = await switch_tab(mock_browser, "page_123")

        mock_browser.switch_to_page.assert_called_once_with("page_123")

    @pytest.mark.asyncio
    async def test_close_tab(self, mock_browser):
        """Test close_tab."""
        from bridgic.browser.tools._browser_tools import close_tab

        result = await close_tab(mock_browser, "page_123")

        mock_browser.close_page.assert_called_once_with("page_123")


# ==================== Element Interaction Tools Tests ====================

class TestElementInteractionTools:
    """Tests for element interaction tools."""

    @pytest.mark.asyncio
    async def test_click_element_by_ref(self, mock_browser):
        """Test click_element_by_ref."""
        from bridgic.browser.tools._browser_action_tools import click_element_by_ref

        mock_locator = MagicMock()
        mock_locator.click = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await click_element_by_ref(mock_browser, "e1")

        mock_browser.get_element_by_ref.assert_called_once_with("e1")
        mock_locator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_click_element_by_ref_not_found(self, mock_browser):
        """Test click_element_by_ref when element not found."""
        from bridgic.browser.tools._browser_action_tools import click_element_by_ref

        mock_browser.get_element_by_ref.return_value = None

        result = await click_element_by_ref(mock_browser, "e999")

        assert "not found" in result.lower() or "failed" in result.lower() or "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_input_text_by_ref(self, mock_browser):
        """Test input_text_by_ref."""
        from bridgic.browser.tools._browser_action_tools import input_text_by_ref

        mock_locator = MagicMock()
        mock_locator.clear = AsyncMock()
        mock_locator.fill = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await input_text_by_ref(mock_browser, "e1", "test text")

        mock_locator.clear.assert_called_once()
        mock_locator.fill.assert_called_once_with("test text")

    @pytest.mark.asyncio
    async def test_input_text_by_ref_secret(self, mock_browser):
        """Test input_text_by_ref with secret flag doesn't log value."""
        from bridgic.browser.tools._browser_action_tools import input_text_by_ref

        mock_locator = MagicMock()
        mock_locator.fill = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await input_text_by_ref(
            mock_browser, "e1", "secret_password", is_secret=True
        )

        assert "secret_password" not in result

    @pytest.mark.asyncio
    async def test_hover_element_by_ref(self, mock_browser):
        """Test hover_element_by_ref."""
        from bridgic.browser.tools._browser_action_tools import hover_element_by_ref

        mock_locator = MagicMock()
        mock_locator.hover = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await hover_element_by_ref(mock_browser, "e1")

        mock_locator.hover.assert_called_once()

    @pytest.mark.asyncio
    async def test_focus_element_by_ref(self, mock_browser):
        """Test focus_element_by_ref."""
        from bridgic.browser.tools._browser_action_tools import focus_element_by_ref

        mock_locator = MagicMock()
        mock_locator.focus = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await focus_element_by_ref(mock_browser, "e1")

        mock_locator.focus.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_dropdown_options_by_ref(self, mock_browser):
        """Test get_dropdown_options_by_ref."""
        from bridgic.browser.tools._browser_action_tools import get_dropdown_options_by_ref

        mock_locator = MagicMock()
        mock_option1 = MagicMock()
        mock_option1.text_content = AsyncMock(return_value="Option 1")
        mock_option1.get_attribute = AsyncMock(return_value="value1")
        mock_option2 = MagicMock()
        mock_option2.text_content = AsyncMock(return_value="Option 2")
        mock_option2.get_attribute = AsyncMock(return_value="value2")

        mock_locator.locator = MagicMock()
        mock_locator.locator.return_value.all = AsyncMock(
            return_value=[mock_option1, mock_option2]
        )

        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await get_dropdown_options_by_ref(mock_browser, "e1")

        assert "Option 1" in result or "value1" in result

    @pytest.mark.asyncio
    async def test_select_dropdown_option_by_ref(self, mock_browser):
        """Test select_dropdown_option_by_ref."""
        from bridgic.browser.tools._browser_action_tools import select_dropdown_option_by_ref

        mock_locator = MagicMock()
        mock_locator.select_option = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await select_dropdown_option_by_ref(mock_browser, "e1", "Option 1")

        mock_locator.select_option.assert_called()

    @pytest.mark.asyncio
    async def test_upload_file_by_ref(self, mock_browser, temp_dir):
        """Test upload_file_by_ref."""
        from bridgic.browser.tools._browser_action_tools import upload_file_by_ref

        test_file = temp_dir / "test.txt"
        test_file.write_text("test content")

        mock_locator = MagicMock()
        mock_locator.evaluate = AsyncMock(return_value="input")
        mock_locator.get_attribute = AsyncMock(return_value="file")
        mock_locator.set_input_files = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await upload_file_by_ref(mock_browser, "e1", str(test_file))

        mock_locator.set_input_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_drag_element_by_ref(self, mock_browser):
        """Test dragging element to another element."""
        from bridgic.browser.tools._browser_action_tools import drag_element_by_ref

        mock_source = MagicMock()
        mock_source.bounding_box = AsyncMock(return_value={"x": 100, "y": 100, "width": 50, "height": 50})
        mock_source.drag_to = AsyncMock()

        mock_target = MagicMock()
        mock_target.bounding_box = AsyncMock(return_value={"x": 300, "y": 300, "width": 50, "height": 50})

        async def get_element(ref):
            return mock_source if ref == "e1" else mock_target

        mock_browser.get_element_by_ref.side_effect = get_element

        result = await drag_element_by_ref(mock_browser, start_ref="e1", end_ref="e2")

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_check_element_by_ref(self, mock_browser):
        """Test checking a checkbox."""
        from bridgic.browser.tools._browser_action_tools import check_element_by_ref

        mock_locator = MagicMock()
        mock_locator.check = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await check_element_by_ref(mock_browser, ref="e1")

        mock_locator.check.assert_called_once()
        assert "check" in result.lower()

    @pytest.mark.asyncio
    async def test_uncheck_element_by_ref(self, mock_browser):
        """Test unchecking a checkbox."""
        from bridgic.browser.tools._browser_action_tools import uncheck_element_by_ref

        mock_locator = MagicMock()
        mock_locator.uncheck = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await uncheck_element_by_ref(mock_browser, ref="e1")

        mock_locator.uncheck.assert_called_once()
        assert "uncheck" in result.lower()

    @pytest.mark.asyncio
    async def test_double_click_element_by_ref(self, mock_browser):
        """Test double-clicking an element."""
        from bridgic.browser.tools._browser_action_tools import double_click_element_by_ref

        mock_locator = MagicMock()
        mock_locator.dblclick = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await double_click_element_by_ref(mock_browser, ref="e1")

        mock_locator.dblclick.assert_called_once()
        assert "double" in result.lower() or "click" in result.lower()

    @pytest.mark.asyncio
    async def test_scroll_element_into_view_by_ref(self, mock_browser):
        """Test scrolling element into view."""
        from bridgic.browser.tools._browser_action_tools import scroll_element_into_view_by_ref

        mock_locator = MagicMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await scroll_element_into_view_by_ref(mock_browser, ref="e1")

        mock_locator.scroll_into_view_if_needed.assert_called_once()
        assert "scroll" in result.lower()


# ==================== Mouse Tools Tests ====================

class TestMouseTools:
    """Tests for coordinate-based mouse tools."""

    @pytest.mark.asyncio
    async def test_mouse_move(self, mock_browser):
        """Test mouse_move to specific coordinates."""
        from bridgic.browser.tools._browser_mouse_tools import mouse_move

        result = await mouse_move(mock_browser, x=100, y=200)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.move.assert_called_once_with(100.0, 200.0)
        assert "100" in result and "200" in result

    @pytest.mark.asyncio
    async def test_mouse_click(self, mock_browser):
        """Test mouse_click at coordinates."""
        from bridgic.browser.tools._browser_mouse_tools import mouse_click

        result = await mouse_click(mock_browser, x=150, y=250)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.click.assert_called_once()
        assert "click" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_click_with_button(self, mock_browser):
        """Test mouse_click with specific button."""
        from bridgic.browser.tools._browser_mouse_tools import mouse_click

        result = await mouse_click(mock_browser, x=150, y=250, button="right")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.click.assert_called_once()
        call_kwargs = mock_page.mouse.click.call_args
        assert call_kwargs[1]["button"] == "right"

    @pytest.mark.asyncio
    async def test_mouse_drag(self, mock_browser):
        """Test mouse_drag from one point to another."""
        from bridgic.browser.tools._browser_mouse_tools import mouse_drag

        result = await mouse_drag(
            mock_browser,
            start_x=100, start_y=100,
            end_x=300, end_y=300
        )

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.move.assert_called()
        mock_page.mouse.down.assert_called_once()
        mock_page.mouse.up.assert_called_once()
        assert "drag" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_down(self, mock_browser):
        """Test mouse_down (press button)."""
        from bridgic.browser.tools._browser_mouse_tools import mouse_down

        result = await mouse_down(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.down.assert_called_once()
        assert "down" in result.lower() or "pressed" in result.lower()

    @pytest.mark.asyncio
    async def test_mouse_up(self, mock_browser):
        """Test mouse_up (release button)."""
        from bridgic.browser.tools._browser_mouse_tools import mouse_up

        result = await mouse_up(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.up.assert_called_once()
        assert "up" in result.lower() or "released" in result.lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("delta_x,delta_y,direction", [
        (0, 500, "down"),
        (0, -500, "up"),
        (0, 0, "none"),
    ])
    async def test_mouse_wheel(self, mock_browser, delta_x, delta_y, direction):
        """Test mouse_wheel scrolling with various deltas."""
        from bridgic.browser.tools._browser_mouse_tools import mouse_wheel

        result = await mouse_wheel(mock_browser, delta_x=delta_x, delta_y=delta_y)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.mouse.wheel.assert_called_once_with(delta_x=float(delta_x), delta_y=float(delta_y))
        assert "scroll" in result.lower()


# ==================== Keyboard Tools Tests ====================

class TestKeyboardTools:
    """Tests for keyboard tools."""

    @pytest.mark.asyncio
    async def test_press_key(self, mock_browser):
        """Test press_key."""
        from bridgic.browser.tools._browser_tools import press_key

        mock_page = mock_browser.get_current_page.return_value

        result = await press_key(mock_browser, "Enter")

        mock_page.keyboard.press.assert_called_once_with("Enter")
        assert "Enter" in result

    @pytest.mark.asyncio
    async def test_press_sequentially(self, mock_browser):
        """Test typing text character by character."""
        from bridgic.browser.tools._browser_keyboard_tools import press_sequentially

        result = await press_sequentially(mock_browser, "hello")

        mock_page = mock_browser.get_current_page.return_value
        assert mock_page.keyboard.press.call_count == 5
        assert "5" in result

    @pytest.mark.asyncio
    async def test_press_sequentially_with_submit(self, mock_browser):
        """Test typing with submit (Enter key)."""
        from bridgic.browser.tools._browser_keyboard_tools import press_sequentially

        result = await press_sequentially(mock_browser, "test", submit=True)

        mock_page = mock_browser.get_current_page.return_value
        assert mock_page.keyboard.press.call_count == 5  # 4 chars + 1 Enter
        assert "submit" in result.lower()

    @pytest.mark.asyncio
    async def test_key_down(self, mock_browser):
        """Test holding a key down."""
        from bridgic.browser.tools._browser_keyboard_tools import key_down

        result = await key_down(mock_browser, "Shift")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.keyboard.down.assert_called_once_with("Shift")
        assert "Shift" in result

    @pytest.mark.asyncio
    async def test_key_up(self, mock_browser):
        """Test releasing a key."""
        from bridgic.browser.tools._browser_keyboard_tools import key_up

        result = await key_up(mock_browser, "Shift")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.keyboard.up.assert_called_once_with("Shift")
        assert "Shift" in result

    @pytest.mark.asyncio
    async def test_fill_form(self, mock_browser):
        """Test filling multiple form fields."""
        from bridgic.browser.tools._browser_keyboard_tools import fill_form

        mock_locator = MagicMock()
        mock_locator.fill = AsyncMock()
        mock_browser.get_element_by_ref.return_value = mock_locator

        fields = [
            {"ref": "e1", "value": "john@example.com"},
            {"ref": "e2", "value": "password123"},
        ]

        result = await fill_form(mock_browser, fields)

        assert mock_locator.fill.call_count == 2
        assert "2" in result

    @pytest.mark.asyncio
    async def test_fill_form_with_errors(self, mock_browser):
        """Test fill_form with some invalid refs."""
        from bridgic.browser.tools._browser_keyboard_tools import fill_form

        mock_locator = MagicMock()
        mock_locator.fill = AsyncMock()

        async def get_element(ref):
            return mock_locator if ref == "e1" else None

        mock_browser.get_element_by_ref.side_effect = get_element

        fields = [
            {"ref": "e1", "value": "valid"},
            {"ref": "e999", "value": "invalid"},
        ]

        result = await fill_form(mock_browser, fields)

        assert "1/2" in result or "1" in result
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_insert_text(self, mock_browser):
        """Test inserting text at cursor position."""
        from bridgic.browser.tools._browser_keyboard_tools import insert_text

        result = await insert_text(mock_browser, "Hello World")

        mock_page = mock_browser.get_current_page.return_value
        mock_page.keyboard.insert_text.assert_called_once_with("Hello World")
        assert "11" in result


# ==================== Screenshot Tools Tests ====================

class TestScreenshotTools:
    """Tests for screenshot and PDF tools."""

    @pytest.mark.asyncio
    async def test_take_screenshot(self, mock_browser, temp_dir):
        """Test taking a screenshot."""
        from bridgic.browser.tools._browser_screenshot_tools import take_screenshot

        result = await take_screenshot(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.screenshot.assert_called_once()
        assert "screenshot" in result.lower() or "base64" in result.lower()

    @pytest.mark.asyncio
    async def test_take_screenshot_full_page(self, mock_browser):
        """Test taking a full-page screenshot."""
        from bridgic.browser.tools._browser_screenshot_tools import take_screenshot

        result = await take_screenshot(mock_browser, full_page=True)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.screenshot.assert_called_once()
        call_kwargs = mock_page.screenshot.call_args[1]
        assert call_kwargs.get("full_page") is True

    @pytest.mark.asyncio
    async def test_take_screenshot_to_file(self, mock_browser, temp_dir):
        """Test saving screenshot to file."""
        from bridgic.browser.tools._browser_screenshot_tools import take_screenshot

        filepath = str(temp_dir / "test.png")
        result = await take_screenshot(mock_browser, filename=filepath)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.screenshot.assert_called_once()
        assert "saved" in result.lower() or filepath in result

    @pytest.mark.asyncio
    async def test_save_pdf(self, mock_browser, temp_dir):
        """Test saving page as PDF."""
        from bridgic.browser.tools._browser_screenshot_tools import save_pdf

        result = await save_pdf(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.pdf.assert_called_once()


# ==================== Network Tools Tests ====================

class TestNetworkTools:
    """Tests for network and console monitoring tools."""

    @pytest.mark.asyncio
    async def test_start_console_capture(self, mock_browser):
        """Test starting console message capture."""
        from bridgic.browser.tools._browser_network_tools import start_console_capture

        result = await start_console_capture(mock_browser)

        assert "started" in result.lower() or "capture" in result.lower()

    @pytest.mark.asyncio
    async def test_get_console_messages(self, mock_browser):
        """Test getting captured console messages."""
        from bridgic.browser.tools._browser_network_tools import get_console_messages

        result = await get_console_messages(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_start_network_capture(self, mock_browser):
        """Test starting network request capture."""
        from bridgic.browser.tools._browser_network_tools import start_network_capture

        result = await start_network_capture(mock_browser)

        assert "started" in result.lower() or "capture" in result.lower() or "network" in result.lower()

    @pytest.mark.asyncio
    async def test_get_network_requests(self, mock_browser):
        """Test getting captured network requests."""
        from bridgic.browser.tools._browser_network_tools import get_network_requests

        result = await get_network_requests(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_stop_console_capture(self, mock_browser):
        """Test stopping console message capture."""
        from bridgic.browser.tools._browser_network_tools import (
            start_console_capture,
            stop_console_capture,
        )

        # Start then stop to exercise cleanup path
        await start_console_capture(mock_browser)
        result = await stop_console_capture(mock_browser)

        assert isinstance(result, str)
        assert "stop" in result.lower() or "console" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_network_capture(self, mock_browser):
        """Test stopping network request capture."""
        from bridgic.browser.tools._browser_network_tools import (
            start_network_capture,
            stop_network_capture,
        )

        await start_network_capture(mock_browser)
        result = await stop_network_capture(mock_browser)

        assert isinstance(result, str)
        assert "stop" in result.lower() or "network" in result.lower()

    @pytest.mark.asyncio
    async def test_wait_for_network_idle(self, mock_browser):
        """Test waiting for network to become idle."""
        from bridgic.browser.tools._browser_network_tools import wait_for_network_idle

        result = await wait_for_network_idle(mock_browser)

        mock_page = mock_browser.get_current_page.return_value
        mock_page.wait_for_load_state.assert_called()
        assert "idle" in result.lower() or "network" in result.lower()


# ==================== Dialog Tools Tests ====================

class TestDialogTools:
    """Tests for dialog handling tools."""

    @pytest.mark.asyncio
    async def test_setup_dialog_handler(self, mock_browser):
        """Test setting up a dialog handler."""
        from bridgic.browser.tools._browser_dialog_tools import setup_dialog_handler

        result = await setup_dialog_handler(mock_browser, default_action="accept")

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_handle_dialog_accept(self, mock_browser):
        """Test accepting a dialog."""
        from bridgic.browser.tools._browser_dialog_tools import handle_dialog

        result = await handle_dialog(mock_browser, accept=True)

        assert "accept" in result.lower() or "dialog" in result.lower() or "no" in result.lower()

    @pytest.mark.asyncio
    async def test_handle_dialog_dismiss(self, mock_browser):
        """Test dismissing a dialog."""
        from bridgic.browser.tools._browser_dialog_tools import handle_dialog

        result = await handle_dialog(mock_browser, accept=False)

        assert "dismiss" in result.lower() or "dialog" in result.lower() or "no" in result.lower()

    @pytest.mark.asyncio
    async def test_remove_dialog_handler(self, mock_browser):
        """Test removing a dialog handler."""
        from bridgic.browser.tools._browser_dialog_tools import remove_dialog_handler

        result = await remove_dialog_handler(mock_browser)

        assert "removed" in result.lower() or "handler" in result.lower()


# ==================== Storage Tools Tests ====================

class TestStorageTools:
    """Tests for storage state tools."""

    @pytest.mark.asyncio
    async def test_save_storage_state(self, mock_browser, temp_dir):
        """Test saving storage state."""
        from bridgic.browser.tools._browser_storage_tools import save_storage_state

        filepath = str(temp_dir / "storage.json")
        result = await save_storage_state(mock_browser, filename=filepath)

        mock_context = mock_browser._context
        mock_context.storage_state.assert_called()
        assert "saved" in result.lower() or "storage" in result.lower()

    @pytest.mark.asyncio
    async def test_restore_storage_state(self, mock_browser, temp_dir):
        """Test restoring storage state."""
        from bridgic.browser.tools._browser_storage_tools import restore_storage_state

        filepath = temp_dir / "storage.json"
        filepath.write_text('{"cookies": [], "origins": []}')

        result = await restore_storage_state(mock_browser, filename=str(filepath))

        assert "restored" in result.lower() or "storage" in result.lower() or "loaded" in result.lower()

    @pytest.mark.asyncio
    async def test_clear_cookies(self, mock_browser):
        """Test clearing cookies."""
        from bridgic.browser.tools._browser_storage_tools import clear_cookies

        result = await clear_cookies(mock_browser)

        mock_context = mock_browser._context
        mock_context.clear_cookies.assert_called_once()
        assert "cleared" in result.lower() or "cookies" in result.lower()

    @pytest.mark.asyncio
    async def test_get_cookies(self, mock_browser):
        """Test getting cookies."""
        from bridgic.browser.tools._browser_storage_tools import get_cookies

        mock_browser._context.cookies.return_value = [
            {"name": "session", "value": "abc123", "domain": "example.com"}
        ]

        result = await get_cookies(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_set_cookie(self, mock_browser):
        """Test setting a cookie."""
        from bridgic.browser.tools._browser_storage_tools import set_cookie

        result = await set_cookie(
            mock_browser,
            name="test_cookie",
            value="test_value",
            domain="example.com"
        )

        mock_browser._context.add_cookies.assert_called()
        assert "set" in result.lower() or "cookie" in result.lower()


# ==================== Verification Tools Tests ====================

class TestVerifyTools:
    """Tests for verification/assertion tools."""

    @pytest.mark.asyncio
    async def test_verify_element_visible_pass(self, mock_browser):
        """Test verify_element_visible when element is visible."""
        from bridgic.browser.tools._browser_verify_tools import verify_element_visible

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_locator.wait_for = AsyncMock()
        mock_page.get_by_role.return_value = mock_locator

        result = await verify_element_visible(mock_browser, role="button", accessible_name="Submit")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_element_visible_fail(self, mock_browser):
        """Test verify_element_visible when element is not visible."""
        from bridgic.browser.tools._browser_verify_tools import verify_element_visible

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_locator.wait_for = AsyncMock(side_effect=Exception("Timeout"))
        mock_page.get_by_role.return_value = mock_locator

        result = await verify_element_visible(mock_browser, role="button", accessible_name="NonExistent")

        assert "FAIL" in result

    @pytest.mark.asyncio
    async def test_verify_text_visible(self, mock_browser):
        """Test verify_text_visible."""
        from bridgic.browser.tools._browser_verify_tools import verify_text_visible

        mock_page = mock_browser.get_current_page.return_value
        mock_locator = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock()
        mock_locator.first = mock_first
        mock_page.get_by_text.return_value = mock_locator

        result = await verify_text_visible(mock_browser, text="Welcome")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_value(self, mock_browser):
        """Test verify_value for input element."""
        from bridgic.browser.tools._browser_verify_tools import verify_value

        mock_locator = MagicMock()
        mock_locator.input_value = AsyncMock(return_value="expected_value")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await verify_value(mock_browser, ref="e1", value="expected_value")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_value_mismatch(self, mock_browser):
        """Test verify_value when values don't match."""
        from bridgic.browser.tools._browser_verify_tools import verify_value

        mock_locator = MagicMock()
        mock_locator.input_value = AsyncMock(return_value="actual_value")
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await verify_value(mock_browser, ref="e1", value="expected_value")

        assert "FAIL" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state,method,return_value", [
        ("visible", "is_visible", True),
        ("disabled", "is_disabled", True),
    ])
    async def test_verify_element_state(self, mock_browser, state, method, return_value):
        """Test verify_element_state for various states."""
        from bridgic.browser.tools._browser_verify_tools import verify_element_state

        mock_locator = MagicMock()
        setattr(mock_locator, method, AsyncMock(return_value=return_value))
        mock_browser.get_element_by_ref.return_value = mock_locator

        result = await verify_element_state(mock_browser, ref="e1", state=state)

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_url(self, mock_browser):
        """Test verify_url."""
        from bridgic.browser.tools._browser_verify_tools import verify_url

        result = await verify_url(mock_browser, expected_url="example.com")

        assert "PASS" in result

    @pytest.mark.asyncio
    async def test_verify_title(self, mock_browser):
        """Test verify_title."""
        from bridgic.browser.tools._browser_verify_tools import verify_title

        result = await verify_title(mock_browser, expected_title="Test")

        assert "PASS" in result


# ==================== DevTools Tests ====================

class TestDevTools:
    """Tests for DevTools (tracing/video) tools."""

    @pytest.mark.asyncio
    async def test_start_tracing(self, mock_browser):
        """Test starting trace recording."""
        from bridgic.browser.tools._browser_devtools import start_tracing

        result = await start_tracing(mock_browser)

        mock_context = mock_browser._context
        mock_context.tracing.start.assert_called_once()
        assert "started" in result.lower() or "tracing" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_tracing(self, mock_browser, temp_dir):
        """Test stopping trace recording."""
        from bridgic.browser.tools._browser_devtools import stop_tracing

        mock_browser._tracing_active = True

        result = await stop_tracing(mock_browser)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_start_video(self, mock_browser):
        """Test starting video recording."""
        from bridgic.browser.tools._browser_devtools import start_video

        result = await start_video(mock_browser)

        assert "video" in result.lower() or "recording" in result.lower() or "not" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_video(self, mock_browser):
        """Test stopping video recording."""
        from bridgic.browser.tools._browser_devtools import stop_video

        result = await stop_video(mock_browser)

        assert "video" in result.lower() or "stopped" in result.lower() or "not" in result.lower()


# ==================== State Tools Tests ====================

class TestStateTools:
    """Tests for state retrieval tools."""

    @pytest.mark.asyncio
    async def test_get_llm_repr(self, mock_browser):
        """Test get_llm_repr."""
        from bridgic.browser.tools._browser_state_tools import get_llm_repr

        mock_snapshot = MagicMock()
        mock_snapshot.tree = "- button 'Click me' [ref=e1]\n- link 'Home' [ref=e2]"
        mock_browser.get_snapshot.return_value = mock_snapshot

        result = await get_llm_repr(mock_browser)

        mock_browser.get_snapshot.assert_called_once()
        assert "button" in result or "Click me" in result

    @pytest.mark.asyncio
    async def test_get_llm_repr_pagination(self, mock_browser):
        """Test get_llm_repr with pagination."""
        from bridgic.browser.tools._browser_state_tools import get_llm_repr

        long_text = "x" * 50000
        mock_snapshot = MagicMock()
        mock_snapshot.tree = long_text
        mock_browser.get_snapshot.return_value = mock_snapshot

        result = await get_llm_repr(mock_browser, start_from_char=0)

        assert len(result) < len(long_text) + 500
        assert "start_from_char" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("interactive,full_page", [
        (True, False),
        (False, True),
        (True, True),
    ])
    async def test_get_llm_repr_options(self, mock_browser, interactive, full_page):
        """Test get_llm_repr with various options."""
        from bridgic.browser.tools._browser_state_tools import get_llm_repr

        mock_snapshot = MagicMock()
        mock_snapshot.tree = "- button 'Click me' [ref=e1]"
        mock_browser.get_snapshot.return_value = mock_snapshot

        result = await get_llm_repr(
            mock_browser,
            interactive=interactive,
            full_page=full_page,
        )

        mock_browser.get_snapshot.assert_called_once_with(
            interactive=interactive,
            full_page=full_page,
        )

    @pytest.mark.asyncio
    async def test_get_llm_repr_snapshot_failed(self, mock_browser):
        """Test get_llm_repr when snapshot fails."""
        from bridgic.browser.tools._browser_state_tools import get_llm_repr

        mock_browser.get_snapshot.return_value = None

        result = await get_llm_repr(mock_browser)

        assert "failed" in result.lower()


# ==================== BrowserToolSetBuilder Tests ====================

class TestBrowserToolSetBuilder:
    """Tests for the BrowserToolSetBuilder API."""

    def test_list_presets(self):
        """Test listing available presets."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        presets = BrowserToolSetBuilder.list_presets()

        assert "MINIMAL" in presets
        assert "FORM_FILLING" in presets
        assert "TESTING" in presets
        assert "COMPLETE" in presets

    def test_list_categories(self):
        """Test listing available categories."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        categories = BrowserToolSetBuilder.list_categories()

        assert "navigation" in categories
        assert "action" in categories
        assert "mouse" in categories
        assert "keyboard" in categories

    def test_with_preset_adds_tools(self):
        """Test that with_preset adds the expected tool names."""
        from bridgic.browser.tools import BrowserToolSetBuilder, ToolPreset

        builder = BrowserToolSetBuilder.__new__(BrowserToolSetBuilder)
        builder._selected_tools = set()

        builder.with_preset(ToolPreset.NAVIGATION)

        assert "search" in builder._selected_tools
        assert "navigate_to_url" in builder._selected_tools
        assert "go_back" in builder._selected_tools
        assert "go_forward" in builder._selected_tools

    def test_with_category_adds_tools(self):
        """Test that with_category adds tools from that category."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        builder = BrowserToolSetBuilder.__new__(BrowserToolSetBuilder)
        builder._selected_tools = set()

        builder.with_category("screenshot")

        assert "take_screenshot" in builder._selected_tools
        assert "save_pdf" in builder._selected_tools

    def test_without_tools_removes(self):
        """Test that without_tools removes specified tools."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        builder = BrowserToolSetBuilder.__new__(BrowserToolSetBuilder)
        builder._selected_tools = {"tool1", "tool2", "tool3"}

        builder.without_tools("tool2")

        assert "tool1" in builder._selected_tools
        assert "tool2" not in builder._selected_tools
        assert "tool3" in builder._selected_tools

    def test_with_tools_adds_by_name(self):
        """Test that with_tools adds tools by name."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        builder = BrowserToolSetBuilder.__new__(BrowserToolSetBuilder)
        builder._selected_tools = set()

        builder.with_tools("custom_tool", "another_tool")

        assert "custom_tool" in builder._selected_tools
        assert "another_tool" in builder._selected_tools

    def test_with_tools_adds_by_function(self):
        """Test that with_tools adds tools by function reference."""
        from bridgic.browser.tools import BrowserToolSetBuilder, search

        builder = BrowserToolSetBuilder.__new__(BrowserToolSetBuilder)
        builder._selected_tools = set()

        builder.with_tools(search)

        assert "search" in builder._selected_tools

    def test_with_tool_names_adds_names(self):
        """Test that with_tool_names adds tool function names."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        builder = BrowserToolSetBuilder.__new__(BrowserToolSetBuilder)
        builder._selected_tools = set()

        builder.with_tool_names("search", "navigate_to_url")

        assert "search" in builder._selected_tools
        assert "navigate_to_url" in builder._selected_tools

    def test_with_tool_names_strict_raises_on_unknown(self):
        """Test strict mode raises when unknown tool names are provided."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        builder = BrowserToolSetBuilder.__new__(BrowserToolSetBuilder)
        builder._selected_tools = set()

        with pytest.raises(ValueError, match="Unknown tool name"):
            builder.with_tool_names("search", "not_a_real_tool", strict=True)

    def test_from_tool_names_builds_specs(self):
        """Test from_tool_names builds tool specs from names."""
        from bridgic.browser.tools import BrowserToolSetBuilder

        browser = MagicMock()
        specs = BrowserToolSetBuilder.from_tool_names(
            browser,
            "search",
            "navigate_to_url",
        )

        names = {spec.to_tool().name for spec in specs}
        assert "search" in names
        assert "navigate_to_url" in names

    def test_preset_categories_are_valid(self):
        """Test that all preset categories exist."""
        from bridgic.browser.tools._browser_tool_set_builder import BrowserToolSetBuilder, ToolPreset

        for preset in ToolPreset:
            categories = BrowserToolSetBuilder._PRESETS.get(preset, [])
            for cat in categories:
                assert cat in BrowserToolSetBuilder._CATEGORIES, f"Category {cat} not found for preset {preset}"
