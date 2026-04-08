"""
Unit tests verifying that the Browser class has all expected tool methods.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bridgic.browser.errors import StateError
from bridgic.browser.session import Browser


EXPECTED_METHODS = [
    "search", "navigate_to", "go_back", "go_forward",
    "reload_page", "scroll_to_text", "press_key", "evaluate_javascript",
    "get_current_page_info", "new_tab", "get_tabs", "switch_tab",
    "close_tab", "close", "browser_resize", "wait_for",
    "get_snapshot_text",
    "input_text_by_ref", "click_element_by_ref", "get_dropdown_options_by_ref",
    "select_dropdown_option_by_ref", "hover_element_by_ref", "focus_element_by_ref",
    "evaluate_javascript_on_ref", "upload_file_by_ref", "drag_element_by_ref",
    "check_checkbox_or_radio_by_ref", "uncheck_checkbox_by_ref", "double_click_element_by_ref",
    "scroll_element_into_view_by_ref",
    "mouse_move", "mouse_click", "mouse_drag", "mouse_down", "mouse_up", "mouse_wheel",
    "type_text", "key_down", "key_up", "fill_form", "insert_text",
    "take_screenshot", "save_pdf",
    "start_console_capture", "stop_console_capture", "get_console_messages",
    "start_network_capture", "stop_network_capture", "get_network_requests",
    "wait_for_network_idle",
    "setup_dialog_handler", "handle_dialog", "remove_dialog_handler",
    "save_storage_state", "restore_storage_state", "clear_cookies",
    "get_cookies", "set_cookie",
    "verify_element_visible", "verify_text_visible", "verify_value",
    "verify_element_state", "verify_url", "verify_title",
    "start_tracing", "stop_tracing", "start_video", "stop_video", "add_trace_chunk",
]


def test_browser_has_all_tool_methods():
    """Browser class should have all expected tool methods."""
    for method_name in EXPECTED_METHODS:
        assert hasattr(Browser, method_name), f"Browser is missing method: {method_name}"


def test_browser_tool_set_builder():
    import functools
    from bridgic.browser.tools import BrowserToolSetBuilder, ToolCategory
    mock_browser = MagicMock(spec=Browser)
    for name in EXPECTED_METHODS:
        real_method = getattr(Browser, name)
        mock_method = AsyncMock()
        # Copy function metadata so inspect.signature works
        functools.update_wrapper(mock_method, real_method)
        setattr(mock_browser, name, mock_method)

    # ALL category should include all CLI-mapped tools (67 tools)
    builder = BrowserToolSetBuilder.for_categories(mock_browser, ToolCategory.ALL)
    specs = builder.build()["tool_specs"]
    tool_names = {s._tool_name for s in specs}

    assert len(specs) >= 60, f"Expected >=60 tools, got {len(specs)}"
    for expected in ("click_element_by_ref", "input_text_by_ref", "navigate_to", "get_snapshot_text", "browser_resize"):
        assert expected in tool_names, f"Expected tool {expected!r} missing from ALL category"

    # NAVIGATION category should include navigation tools only
    nav_builder = BrowserToolSetBuilder.for_categories(mock_browser, ToolCategory.NAVIGATION)
    nav_specs = nav_builder.build()["tool_specs"]
    nav_names = {s._tool_name for s in nav_specs}
    assert "navigate_to" in nav_names
    assert "click_element_by_ref" not in nav_names


# ---------------------------------------------------------------------------
# State guard tests: stop_* methods raise structured state errors when inactive
# ---------------------------------------------------------------------------

def _make_browser_with_mock_page() -> tuple:
    """Create a Browser instance with a mocked page (no real Playwright)."""
    browser = Browser.__new__(Browser)
    # Minimal instance state so stop_* methods can run without start().
    browser._console_messages = {}
    browser._network_requests = {}
    browser._console_handlers = {}
    browser._network_handlers = {}
    browser._dialog_handlers = {}
    browser._tracing_state = {}
    browser._video_state = {}
    browser._video_recorders = {}
    browser._video_session = None
    # CDP-mode attributes — required by start_video / get_pages / _close_page
    # which inspect them to decide whether to filter out user tabs.  Tests in
    # this file simulate launch-mode (non-CDP), so both default to "not CDP".
    browser._cdp_url = None
    browser._cdp_context_owned = False
    browser._cdp_owned_pages = set()
    browser._context = MagicMock()
    browser._page = MagicMock()
    # get_current_page() returns self._page
    browser.get_current_page = AsyncMock(return_value=browser._page)
    return browser


@pytest.mark.asyncio
async def test_stop_console_capture_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_console_capture()
    assert exc_info.value.code == "NO_ACTIVE_CAPTURE"


@pytest.mark.asyncio
async def test_stop_network_capture_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_network_capture()
    assert exc_info.value.code == "NO_ACTIVE_CAPTURE"


@pytest.mark.asyncio
async def test_stop_video_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_video()
    assert exc_info.value.code == "NO_ACTIVE_RECORDING"


@pytest.mark.asyncio
async def test_stop_tracing_guard():
    browser = _make_browser_with_mock_page()
    with pytest.raises(StateError) as exc_info:
        await browser.stop_tracing()
    assert exc_info.value.code == "NO_ACTIVE_TRACING"


@pytest.mark.asyncio
async def test_start_video_uses_window_inner_dimensions_not_viewport_size():
    """Regression: start_video() must derive its recording size from
    ``window.innerWidth/innerHeight`` (queried via JS), NOT from
    ``page.viewport_size``.

    In CDP attach mode bridgic never calls ``setViewportSize`` on the
    foreign Chrome, so ``page.viewport_size`` returns ``None`` and the
    old code fell back to a hard-coded 800×600. Chrome then captured at
    the real (e.g. 16:9) window aspect ratio and downsampled to fit
    within 800×600, which:
      1. blurred the page (37% downscale)
      2. left a gray strip at the bottom from ffmpeg's pad filter
    Querying the page directly avoids both.
    """
    browser = _make_browser_with_mock_page()

    fake_context = MagicMock()
    fake_context.pages = []  # no pages → no recorders to start
    fake_context.on = MagicMock()

    fake_page = MagicMock()
    fake_page.context = fake_context
    # Simulate CDP attach mode: viewport_size is None.
    fake_page.viewport_size = None
    fake_page.is_closed = MagicMock(return_value=False)
    # window.innerWidth/innerHeight reports the real window — 16:9, much
    # larger than the old 800×600 fallback.
    fake_page.evaluate = AsyncMock(return_value={"w": 1366, "h": 768})
    browser.get_current_page = AsyncMock(return_value=fake_page)

    await browser.start_video()

    # JS query was performed.
    fake_page.evaluate.assert_awaited_once()
    call_arg = fake_page.evaluate.await_args.args[0]
    assert "innerWidth" in call_arg
    assert "innerHeight" in call_arg

    # Recording size matches the queried dimensions, NOT the 800×600
    # fallback. (& ~1 rounds to even, both are already even here.)
    session = browser._video_session
    assert session is not None
    assert session["width"] == 1366
    assert session["height"] == 768

    # Cleanup so subsequent tests don't see a leaked session.
    browser._video_session = None
    browser._video_state.clear()


@pytest.mark.asyncio
async def test_start_video_falls_back_to_viewport_size_when_evaluate_fails():
    """If ``page.evaluate`` raises (hardened CSP, page closed mid-call,
    etc.), start_video() should fall back to ``page.viewport_size``
    instead of crashing."""
    browser = _make_browser_with_mock_page()

    fake_context = MagicMock()
    fake_context.pages = []
    fake_context.on = MagicMock()

    fake_page = MagicMock()
    fake_page.context = fake_context
    fake_page.viewport_size = {"width": 1280, "height": 800}
    fake_page.is_closed = MagicMock(return_value=False)
    fake_page.evaluate = AsyncMock(side_effect=RuntimeError("CSP blocked"))
    browser.get_current_page = AsyncMock(return_value=fake_page)

    await browser.start_video()

    session = browser._video_session
    assert session is not None
    assert session["width"] == 1280
    assert session["height"] == 800

    browser._video_session = None
    browser._video_state.clear()


@pytest.mark.asyncio
async def test_start_video_already_active_does_not_destroy_existing_session():
    """Regression: a duplicate start_video() must raise VIDEO_ALREADY_ACTIVE
    *without* tearing down the previously-started session.

    Earlier the rollback `except` block fired unconditionally, wiping out
    `_video_session` and stopping every recorder in `_video_recorders` —
    so calling `start_video()` twice silently destroyed the user's first
    recording while reporting "already active".
    """
    browser = _make_browser_with_mock_page()

    fake_context = MagicMock()
    fake_context.pages = []  # no pages → no recorders to start
    fake_context.on = MagicMock()

    fake_page = MagicMock()
    fake_page.context = fake_context
    fake_page.viewport_size = {"width": 800, "height": 600}
    fake_page.is_closed = MagicMock(return_value=False)
    browser.get_current_page = AsyncMock(return_value=fake_page)

    # First call: sets up a session.
    await browser.start_video()
    sentinel_session = browser._video_session
    assert sentinel_session is not None

    # Second call: must error out without touching the existing session.
    with pytest.raises(StateError) as exc_info:
        await browser.start_video()
    assert exc_info.value.code == "VIDEO_ALREADY_ACTIVE"

    assert browser._video_session is sentinel_session
    assert browser._video_state  # context_key entry still present


# ---------------------------------------------------------------------------
# CDP borrowed-context isolation: bridgic must never expose, record, or
# auto-switch to user-owned tabs in CDP borrowed mode.  These tests cover
# the regression set R2-A / R2-B / R2-C found in the second-round CR.
# ---------------------------------------------------------------------------

def _make_borrowed_cdp_browser_with_pages(owned_page, user_page):
    """Build a Browser configured as if it had connected to a user's Chrome
    via CDP, with one bridgic-owned tab and one user-owned tab in the
    same context."""
    browser = _make_browser_with_mock_page()
    browser._cdp_url = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_context_owned = False  # borrowed
    browser._cdp_owned_pages = {owned_page}
    fake_context = MagicMock()
    # Order matters — get_pages preserves the underlying tab order
    fake_context.pages = [user_page, owned_page]
    browser._context = fake_context
    browser._page = owned_page
    return browser


def test_get_pages_filters_user_tabs_in_cdp_borrowed_mode():
    """get_pages must hide user-owned tabs when bridgic borrowed the context."""
    owned = MagicMock(name="bridgic_tab")
    user = MagicMock(name="user_tab")
    browser = _make_borrowed_cdp_browser_with_pages(owned, user)

    visible = browser.get_pages()
    assert visible == [owned]
    assert user not in visible


def test_get_pages_returns_all_pages_when_context_owned():
    """When bridgic owns the context (launch / persistent / owned-CDP),
    every page in the context should be visible."""
    browser = _make_browser_with_mock_page()
    browser._cdp_url = None  # launch mode
    browser._cdp_context_owned = False
    p1 = MagicMock(name="p1")
    p2 = MagicMock(name="p2")
    browser._context.pages = [p1, p2]

    assert browser.get_pages() == [p1, p2]


@pytest.mark.asyncio
async def test_close_page_does_not_switch_to_user_tab_in_cdp_borrowed_mode():
    """Closing the last bridgic tab must NOT silently land self._page on
    a user-owned tab — that would route subsequent commands into the
    user's banking / email page.
    """
    owned = MagicMock(name="bridgic_tab")
    owned.close = AsyncMock()
    owned.title = AsyncMock(return_value="bridgic")
    user = MagicMock(name="user_tab")
    browser = _make_borrowed_cdp_browser_with_pages(owned, user)

    success, _msg = await browser._close_page(owned)
    assert success
    # No bridgic-owned pages remain → self._page must be None,
    # NOT the user's still-open tab.
    assert browser._page is None


@pytest.mark.asyncio
async def test_start_video_records_all_tabs_in_cdp_borrowed_mode():
    """start_video() MUST install a recorder on every page (including the
    user's existing tabs) when bridgic is a guest on a borrowed CDP context.
    """
    owned = MagicMock(name="bridgic_tab")
    owned.is_closed = MagicMock(return_value=False)

    user = MagicMock(name="user_tab")
    user.is_closed = MagicMock(return_value=False)

    browser = _make_browser_with_mock_page()
    browser._cdp_url = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_context_owned = False
    browser._cdp_owned_pages = {owned}

    fake_context = MagicMock()
    fake_context.pages = [owned, user]

    owned.context = fake_context
    user.context = fake_context

    fake_context.on = MagicMock()
    browser._context = fake_context

    started: list = []

    async def _fake_starter(page):
        started.append(page)

    browser._start_page_video_recorder = _fake_starter  # type: ignore[method-assign]
    browser.get_current_page = AsyncMock(return_value=owned)
    owned.evaluate = AsyncMock(return_value={"w": 1280, "h": 720})

    await browser.start_video()

    # Both bridgic-owned tab AND the user's pre-existing tab must be recorded.
    assert owned in started
    assert user in started

    # Cleanup: avoid leaking the fake session into other tests.
    browser._video_session = None
    browser._video_state.clear()
