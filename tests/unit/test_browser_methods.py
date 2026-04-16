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
    "type_text", "key_down", "key_up", "fill_form",
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
    browser._video_recorder = None
    browser._video_session = None
    # CDP-mode attributes — required by start_video / get_pages / _close_page
    # which inspect them to decide whether to filter out user tabs.  Tests in
    # this file simulate launch-mode (non-CDP), so both default to "not CDP".
    browser._cdp_resolved = None
    browser._cdp_raw = None
    browser._cdp_context_owned = False
    # _is_cdp_borrowed is a read-only property derived from _cdp_raw + _cdp_context_owned.
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
    """Regression: start_video() must derive its recording size from CDP
    Page.getLayoutMetrics, NOT from ``page.viewport_size``.

    In CDP attach mode bridgic never calls ``setViewportSize`` on the
    foreign Chrome, so ``page.viewport_size`` returns ``None`` and the
    old code fell back to a hard-coded 800×600. Chrome then captured at
    the real (e.g. 16:9) window aspect ratio and downsampled to fit
    within 800×600, which:
      1. blurred the page (37% downscale)
      2. left a gray strip at the bottom from ffmpeg's pad filter (now fixed: uses scale)
    Querying via CDP avoids both.
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
    browser.get_current_page = AsyncMock(return_value=fake_page)

    # Mock CDPSession on the browser's context so Page.getLayoutMetrics returns real dims.
    fake_cdp_session = MagicMock()
    fake_cdp_session.send = AsyncMock(return_value={
        "cssLayoutViewport": {"clientWidth": 1366, "clientHeight": 768, "pageX": 0, "pageY": 0},
        "cssContentSize": {"width": 1366, "height": 768},
        "cssVisualViewport": {"clientWidth": 1366, "clientHeight": 768},
    })
    fake_cdp_session.detach = AsyncMock()
    browser._context.new_cdp_session = AsyncMock(return_value=fake_cdp_session)

    # Mock the recorder startup — this test only verifies dimension computation.
    async def _fake_start(page):
        browser._video_recorder = MagicMock()
    browser._start_single_video_recorder = _fake_start  # type: ignore[method-assign]

    await browser.start_video()

    # CDP session was used to query dimensions.
    browser._context.new_cdp_session.assert_awaited_once()
    fake_cdp_session.send.assert_awaited_once_with("Page.getLayoutMetrics")

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
    """If CDP session send raises (e.g. session unavailable), start_video()
    should fall back to ``page.viewport_size`` instead of crashing."""
    browser = _make_browser_with_mock_page()

    fake_context = MagicMock()
    fake_context.pages = []
    fake_context.on = MagicMock()

    fake_page = MagicMock()
    fake_page.context = fake_context
    fake_page.viewport_size = {"width": 1280, "height": 800}
    fake_page.is_closed = MagicMock(return_value=False)
    browser.get_current_page = AsyncMock(return_value=fake_page)

    # Make CDP session fail so it falls back to viewport_size.
    fake_cdp_session = MagicMock()
    fake_cdp_session.send = AsyncMock(side_effect=RuntimeError("CDP unavailable"))
    fake_cdp_session.detach = AsyncMock()
    browser._context.new_cdp_session = AsyncMock(return_value=fake_cdp_session)

    # Mock the recorder startup — this test only verifies dimension fallback.
    async def _fake_start(page):
        browser._video_recorder = MagicMock()
    browser._start_single_video_recorder = _fake_start  # type: ignore[method-assign]

    await browser.start_video()

    session = browser._video_session
    assert session is not None
    assert session["width"] == 1280
    assert session["height"] == 800

    browser._video_session = None
    browser._video_recorder = None
    browser._video_state.clear()


@pytest.mark.asyncio
async def test_start_video_rollback_removes_context_listener_if_attached():
    """Regression guard for C3: when start_video() raises AFTER
    ``context.on("page", ...)`` has attached the listener but BEFORE control
    returns normally, the rollback path must call ``context.remove_listener``
    — otherwise the zombie listener survives the rollback and fires on every
    future tab-open, eventually racing with a second start_video().

    The race is narrow but real: any BaseException (KeyboardInterrupt,
    CancelledError) or memory-pressure Exception raised between the two
    synchronous lines ``context.on(...)`` and ``self._video_session[...] = ...``
    leaks the listener with today's code.
    """
    from bridgic.browser.errors import OperationError

    browser = _make_browser_with_mock_page()

    attached_listeners: list = []

    def _capture_on(event, cb):
        # Record the listener reference so we can assert remove_listener
        # is called with the SAME callable later.
        attached_listeners.append(cb)

    fake_context = MagicMock()
    fake_context.pages = []
    fake_context.on = MagicMock(side_effect=_capture_on)
    fake_context.remove_listener = MagicMock()

    fake_page = MagicMock()
    fake_page.context = fake_context
    fake_page.viewport_size = {"width": 800, "height": 600}
    fake_page.is_closed = MagicMock(return_value=False)
    browser.get_current_page = AsyncMock(return_value=fake_page)
    browser._context.new_cdp_session = AsyncMock(
        side_effect=RuntimeError("CDP unavailable")
    )

    async def _fake_start(page):
        browser._video_recorder = MagicMock()
    browser._start_single_video_recorder = _fake_start  # type: ignore[method-assign]

    # Force a failure AFTER context.on runs by making the very next state
    # write raise. ``self._video_session`` is a plain dict; wrap it so the
    # `["page_listener"] = ...` assignment raises.
    class _DictRaisingOnSetItem(dict):
        def __setitem__(self, k, v):
            if k == "page_listener":
                raise RuntimeError("simulated mid-setup failure")
            super().__setitem__(k, v)

    _orig_setattr = Browser.__setattr__

    # Patch the session-assignment step to return a dict that raises on
    # __setitem__, mirroring the narrow window where context.on has run
    # but the listener has not been recorded into the session dict yet.
    def _wrapped_setattr(self, name, value):
        if name == "_video_session" and isinstance(value, dict):
            value = _DictRaisingOnSetItem(value)
        _orig_setattr(self, name, value)

    with pytest.raises((OperationError, RuntimeError)):
        import unittest.mock as _um
        with _um.patch.object(Browser, "__setattr__", _wrapped_setattr):
            await browser.start_video()

    assert attached_listeners, (
        "context.on must have been called — precondition for this test"
    )
    # The rollback must have removed the listener.
    fake_context.remove_listener.assert_called_once_with(
        "page", attached_listeners[0]
    )
    # And must have cleared the session and state.
    assert browser._video_session is None
    assert browser._video_recorder is None


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

    # Mock recorder startup so first call succeeds.
    async def _fake_start(page):
        browser._video_recorder = MagicMock()
    browser._start_single_video_recorder = _fake_start  # type: ignore[method-assign]

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
# CDP borrowed-context behaviour: get_pages returns all tabs, start_video
# records all tabs, _close_page switches to the next available tab.
# ---------------------------------------------------------------------------

def _make_borrowed_cdp_browser_with_pages(owned_page, user_page):
    """Build a Browser configured as if it had connected to a user's Chrome
    via CDP, with two tabs in the same context."""
    browser = _make_browser_with_mock_page()
    browser._cdp_resolved = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_raw = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_context_owned = False  # borrowed → _is_cdp_borrowed is True via property
    fake_context = MagicMock()
    # Order matters — get_pages preserves the underlying tab order
    fake_context.pages = [user_page, owned_page]
    browser._context = fake_context
    browser._page = owned_page
    return browser


def test_get_pages_returns_all_context_pages():
    """get_pages() must return every page in the context regardless of how
    the browser was started (launch, persistent, or CDP)."""
    browser = _make_browser_with_mock_page()
    p1 = MagicMock(name="p1")
    p2 = MagicMock(name="p2")
    browser._context.pages = [p1, p2]

    assert browser.get_pages() == [p1, p2]


@pytest.mark.asyncio
async def test_close_page_switches_to_remaining_tab_in_cdp_borrowed_mode():
    """After closing the active tab in CDP mode, self._page must be set to
    the next available page in the context (there is no ownership filter)."""
    owned = MagicMock(name="bridgic_tab")
    owned.close = AsyncMock()
    owned.title = AsyncMock(return_value="bridgic")
    user = MagicMock(name="user_tab")
    user.is_closed = MagicMock(return_value=False)
    user.title = AsyncMock(return_value="user-tab-title")
    browser = _make_borrowed_cdp_browser_with_pages(owned, user)

    success, _msg = await browser._close_page(owned)
    assert success
    # A remaining page exists → self._page switches to it.
    assert browser._page is user


@pytest.mark.asyncio
async def test_start_video_records_only_active_tab_in_cdp_borrowed_mode():
    """start_video() in single-stream mode MUST start only one recorder on the
    active page, even in CDP borrowed mode with multiple tabs."""
    owned = MagicMock(name="bridgic_tab")
    owned.is_closed = MagicMock(return_value=False)

    user = MagicMock(name="user_tab")
    user.is_closed = MagicMock(return_value=False)

    browser = _make_browser_with_mock_page()
    browser._cdp_resolved = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_raw = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_context_owned = False  # borrowed → _is_cdp_borrowed is True via property

    fake_context = MagicMock()
    fake_context.pages = [owned, user]

    owned.context = fake_context
    user.context = fake_context

    fake_context.on = MagicMock()
    browser._context = fake_context

    started_page = None

    async def _fake_starter(page):
        nonlocal started_page
        started_page = page

    browser._start_single_video_recorder = _fake_starter  # type: ignore[method-assign]
    browser.get_current_page = AsyncMock(return_value=owned)
    owned.evaluate = AsyncMock(return_value={"w": 1280, "h": 720})

    # Make _start_single_video_recorder set _video_recorder so the post-check passes.
    async def _fake_starter_with_recorder(page):
        nonlocal started_page
        started_page = page
        browser._video_recorder = MagicMock()  # simulate recorder created

    browser._start_single_video_recorder = _fake_starter_with_recorder  # type: ignore[method-assign]

    await browser.start_video()

    # Only the active (owned) tab should have been started.
    assert started_page is owned

    # Cleanup.
    browser._video_session = None
    browser._video_recorder = None
    browser._video_state.clear()


# ---------------------------------------------------------------------------
# C1: _cdp_evaluate_on_element must detect scroll race between bounding_box
# acquisition and Runtime.evaluate(elementFromPoint)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cdp_evaluate_on_element_detects_scroll_race():
    """Regression guard for C1: if the page scrolls between the Python-side
    ``locator.bounding_box()`` call and the CDPSession ``elementFromPoint``
    call, the coordinates resolve to a DIFFERENT element — silently running
    JS on the wrong node. Detect via a post-check that the locator's bbox has
    not shifted meaningfully, and raise a clear error on mismatch.
    """
    from bridgic.browser.session._browser import _cdp_evaluate_on_element

    # bbox BEFORE evaluate: element at (0, 100)
    # bbox AFTER evaluate: element at (0, 500) — page scrolled 400px
    # M4: after the mismatch, the helper retries once (smooth-scroll recovery).
    # Return the same shifted bbox on the retry so the race is still detected.
    mock_locator = MagicMock()
    mock_locator.bounding_box = AsyncMock(
        side_effect=[
            {"x": 0, "y": 100, "width": 100, "height": 40},
            {"x": 0, "y": 500, "width": 100, "height": 40},
            {"x": 0, "y": 500, "width": 100, "height": 40},
        ]
    )

    mock_session = MagicMock()
    mock_session.send = AsyncMock(
        return_value={"result": {"objectId": "dummy-object-id"}}
    )
    mock_session.detach = AsyncMock()

    mock_context = MagicMock()
    mock_context.new_cdp_session = AsyncMock(return_value=mock_session)

    mock_page = MagicMock()

    with pytest.raises(RuntimeError) as exc_info:
        await _cdp_evaluate_on_element(
            mock_context, mock_page, mock_locator, "(el) => el.value"
        )
    # Error message must clearly indicate scroll/bbox race so callers can retry.
    assert "scroll" in str(exc_info.value).lower() or "moved" in str(exc_info.value).lower()

    # Session must still be detached on the error path.
    mock_session.detach.assert_awaited_once()


@pytest.mark.asyncio
async def test_cdp_evaluate_on_element_stable_bbox_proceeds_normally():
    """Happy-path regression: when the bbox is stable across the evaluate
    call, _cdp_evaluate_on_element must return the evaluated value normally.
    """
    from bridgic.browser.session._browser import _cdp_evaluate_on_element

    stable_bbox = {"x": 0, "y": 100, "width": 100, "height": 40}
    mock_locator = MagicMock()
    mock_locator.bounding_box = AsyncMock(return_value=stable_bbox)

    mock_session = MagicMock()
    # Sequence: first call = Runtime.evaluate (elementFromPoint),
    #           second call = Runtime.callFunctionOn (user code)
    mock_session.send = AsyncMock(
        side_effect=[
            {"result": {"objectId": "resolved-id"}},
            {"result": {"value": "hello"}},
        ]
    )
    mock_session.detach = AsyncMock()

    mock_context = MagicMock()
    mock_context.new_cdp_session = AsyncMock(return_value=mock_session)
    mock_page = MagicMock()

    result = await _cdp_evaluate_on_element(
        mock_context, mock_page, mock_locator, "(el) => el.value"
    )
    assert result == "hello"
    mock_session.detach.assert_awaited_once()
