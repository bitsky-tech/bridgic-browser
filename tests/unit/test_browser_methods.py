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
    # `_closing` flag is checked by the owned-page listeners as a shutdown
    # guard. In real init it defaults to False (set by Browser.__init__) but
    # `__new__` skips that path, so set it explicitly here.
    browser._closing = False
    browser._context = MagicMock()
    browser._page = MagicMock()
    # Owned-page tracking: in non-CDP modes every page is owned. By default
    # mark `_page` (the current page) as owned. Tests that exercise the
    # filter / fallback directly may override these.
    browser._owned_pages = {browser._page}
    browser._focus_stack = [browser._page]
    browser._auto_follow_popups = True
    # _invalidate_page_state is called by switch_to_page / _close_page on
    # successful state transitions; stub it so tests don't need to set up the
    # full snapshot/prefetch state.
    browser._invalidate_page_state = MagicMock()
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
    fake_context.new_cdp_session = AsyncMock(return_value=fake_cdp_session)

    # Mock the recorder startup — this test only verifies dimension computation.
    async def _fake_start(page):
        browser._video_recorder = MagicMock()
    browser._start_single_video_recorder = _fake_start  # type: ignore[method-assign]

    await browser.start_video()

    # CDP session was used to query dimensions (via page.context).
    fake_context.new_cdp_session.assert_awaited_once()
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
async def test_start_video_rollback_clears_state_on_failure():
    """If start_video() raises mid-setup, it must rollback internal state
    (session + recorder + context video_state). Since single-stream video no
    longer auto-listens to context page creation, the rollback must not try to
    remove any page listener.
    """
    from bridgic.browser.errors import OperationError

    browser = _make_browser_with_mock_page()

    fake_context = MagicMock()
    fake_context.pages = []
    fake_context.on = MagicMock()
    fake_context.remove_listener = MagicMock()

    fake_page = MagicMock()
    fake_page.context = fake_context
    fake_page.viewport_size = {"width": 800, "height": 600}
    fake_page.is_closed = MagicMock(return_value=False)
    browser.get_current_page = AsyncMock(return_value=fake_page)

    # Make CDP session fail so start_video falls back to viewport_size.
    browser._context.new_cdp_session = AsyncMock(
        side_effect=RuntimeError("CDP unavailable")
    )

    async def _fake_start(page):
        raise RuntimeError("simulated recorder start failure")

    browser._start_single_video_recorder = _fake_start  # type: ignore[method-assign]

    with pytest.raises((OperationError, RuntimeError)):
        await browser.start_video()

    fake_context.on.assert_not_called()
    fake_context.remove_listener.assert_not_called()
    assert browser._video_session is None
    assert browser._video_recorder is None
    assert not browser._video_state


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
    via CDP, with two tabs in the same context.

    Ownership semantics: the user tab is NOT owned (pre-existing at attach
    time); bridgic's own tab IS owned (created via `_context.new_page()`
    after attach).
    """
    browser = _make_browser_with_mock_page()
    browser._cdp_resolved = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_raw = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_context_owned = False  # borrowed → _is_cdp_borrowed is True via property
    fake_context = MagicMock()
    # Order matters — get_pages preserves the underlying tab order
    fake_context.pages = [user_page, owned_page]
    browser._context = fake_context
    browser._page = owned_page
    # CDP borrowed mode: only the bridgic-created tab is owned. The user tab
    # must NOT be in `_owned_pages` so the new filter excludes it.
    browser._owned_pages = {owned_page}
    browser._focus_stack = [owned_page]
    return browser


# U15 (改造原 test_get_pages_returns_all_context_pages):
# get_pages() must now FILTER to owned pages only. In non-CDP modes the
# `_make_browser_with_mock_page` helper marks every page as owned, so
# behaviour for those callers is unchanged in practice.
def test_get_pages_returns_only_owned_pages():
    browser = _make_browser_with_mock_page()
    owned1 = MagicMock(name="owned1")
    owned2 = MagicMock(name="owned2")
    user = MagicMock(name="user")
    browser._context.pages = [user, owned1, owned2]
    browser._owned_pages = {owned1, owned2}

    # User tab is filtered out; order from context.pages is preserved.
    assert browser.get_pages() == [owned1, owned2]


# U16 (改造原 test_close_page_switches_to_remaining_tab_in_cdp_borrowed_mode):
# Closing the only owned tab in CDP borrowed mode MUST NOT silently select
# the user's tab. self._page becomes None; user tab is left intact.
@pytest.mark.asyncio
async def test_close_only_owned_tab_in_cdp_borrowed_mode_yields_none():
    owned = MagicMock(name="bridgic_tab")
    owned.close = AsyncMock()
    owned.is_closed = MagicMock(return_value=False)
    owned.opener = AsyncMock(return_value=None)
    owned.title = AsyncMock(return_value="bridgic")
    user = MagicMock(name="user_tab")
    user.is_closed = MagicMock(return_value=False)
    user.title = AsyncMock(return_value="user-tab-title")
    browser = _make_borrowed_cdp_browser_with_pages(owned, user)

    success, msg = await browser._close_page(owned)
    assert success
    # No other owned tab exists → self._page becomes None. The user tab is
    # NOT picked up as a fallback (would be a privacy boundary violation).
    assert browser._page is None
    assert "No tabs remaining" in msg
    # The user's tab is untouched — bridgic never called close() on it.
    user.close.assert_not_called()


# ---------------------------------------------------------------------------
# Owned-page tracking unit tests (U1-U14, U17, U18 from plan)
# ---------------------------------------------------------------------------

def _mock_owned_page(name: str, *, closed: bool = False):
    """Helper: a mock Page that participates in ownership tests."""
    p = MagicMock(name=name)
    p.is_closed = MagicMock(return_value=closed)
    p.close = AsyncMock()
    # `on()` accepts the listener; record it for inspection if needed.
    p.on = MagicMock()
    return p


# U3
def test_get_pages_filters_to_owned():
    browser = _make_browser_with_mock_page()
    user = _mock_owned_page("user")
    owned = _mock_owned_page("owned")
    browser._context.pages = [user, owned]
    browser._owned_pages = {owned}

    assert browser.get_pages() == [owned]


# U4
def test_get_pages_preserves_context_order():
    browser = _make_browser_with_mock_page()
    u1, o1, u2, o2 = (
        _mock_owned_page("u1"),
        _mock_owned_page("o1"),
        _mock_owned_page("u2"),
        _mock_owned_page("o2"),
    )
    browser._context.pages = [u1, o1, u2, o2]
    browser._owned_pages = {o1, o2}

    assert browser.get_pages() == [o1, o2]


# U17 — Page.on("close") cleanup
def test_on_owned_page_close_prunes_state():
    browser = _make_browser_with_mock_page()
    a = _mock_owned_page("a")
    b = _mock_owned_page("b")
    browser._owned_pages = {a, b}
    browser._focus_stack = [a, b]

    browser._on_owned_page_close(a)

    assert a not in browser._owned_pages
    assert a not in browser._focus_stack
    # b is untouched
    assert b in browser._owned_pages
    assert browser._focus_stack == [b]


def test_on_owned_page_close_idempotent_for_unknown_page():
    browser = _make_browser_with_mock_page()
    ghost = _mock_owned_page("ghost")
    # Not in any tracking — should be a no-op, not raise.
    browser._on_owned_page_close(ghost)
    # _owned_pages from the helper still contains browser._page.
    assert ghost not in browser._owned_pages


# _mark_owned core behaviour (used implicitly by U5/U6)
def test_mark_owned_idempotent_and_registers_listener():
    browser = _make_browser_with_mock_page()
    browser._owned_pages = set()
    browser._focus_stack = []
    p = _mock_owned_page("p")

    browser._mark_owned(p)
    assert p in browser._owned_pages
    assert browser._focus_stack == [p]
    # Listener registered for cleanup on close.
    p.on.assert_called_once()
    args, _kwargs = p.on.call_args
    assert args[0] == "close"

    # Second call must be a no-op (no duplicate listener, no duplicate stack push).
    browser._mark_owned(p)
    assert browser._focus_stack == [p]
    assert p.on.call_count == 1


def test_mark_owned_handles_none():
    browser = _make_browser_with_mock_page()
    browser._owned_pages = set()
    browser._focus_stack = []
    browser._mark_owned(None)  # type: ignore[arg-type]
    assert browser._owned_pages == set()
    assert browser._focus_stack == []


# U6
@pytest.mark.asyncio
async def test_popup_with_owned_opener_is_adopted():
    browser = _make_browser_with_mock_page()
    parent = browser._page  # already owned by helper
    popup = _mock_owned_page("popup")
    popup.opener = AsyncMock(return_value=parent)

    # auto_follow=False so we can isolate adoption logic from page-switch logic.
    browser._auto_follow_popups = False
    await browser._maybe_adopt_page(popup)

    assert popup in browser._owned_pages


# U7
@pytest.mark.asyncio
async def test_popup_with_none_opener_is_not_adopted():
    browser = _make_browser_with_mock_page()
    popup = _mock_owned_page("popup")
    popup.opener = AsyncMock(return_value=None)

    before = set(browser._owned_pages)
    await browser._maybe_adopt_page(popup)

    assert popup not in browser._owned_pages
    assert browser._owned_pages == before


# U8
@pytest.mark.asyncio
async def test_popup_with_external_opener_is_not_adopted():
    """Opener exists but is NOT in _owned_pages (CDP borrowed user tab scenario)."""
    browser = _make_browser_with_mock_page()
    external = _mock_owned_page("external_user_tab")
    popup = _mock_owned_page("popup")
    popup.opener = AsyncMock(return_value=external)

    await browser._maybe_adopt_page(popup)

    assert popup not in browser._owned_pages


@pytest.mark.asyncio
async def test_maybe_adopt_skips_already_owned():
    """Page already in `_owned_pages` (e.g., `_new_page` registered it first)
    must not trigger another adoption or follow-switch."""
    browser = _make_browser_with_mock_page()
    p = browser._page
    p.opener = AsyncMock(return_value=browser._page)

    # Track whether _switch_self_page_to is called.
    called = []
    async def _fake_switch(_np):
        called.append(_np)
    browser._switch_self_page_to = _fake_switch  # type: ignore[method-assign]

    await browser._maybe_adopt_page(p)  # already owned

    # opener() should not even be queried.
    p.opener.assert_not_awaited()
    assert called == []


# U9
@pytest.mark.asyncio
async def test_popup_follow_switches_self_page_when_enabled():
    browser = _make_browser_with_mock_page()
    parent = browser._page
    popup = _mock_owned_page("popup")
    popup.opener = AsyncMock(return_value=parent)
    browser._auto_follow_popups = True

    # Stub the heavy side-effects out — we only care that `self._page` flips.
    async def _fake_switch_video(_p):
        pass
    browser._switch_video_to_page = _fake_switch_video  # type: ignore[method-assign]

    await browser._maybe_adopt_page(popup)

    assert browser._page is popup
    assert popup in browser._owned_pages
    # Focus stack: popup must be at the tail.
    assert browser._focus_stack[-1] is popup


# U10
@pytest.mark.asyncio
async def test_popup_follow_disabled_keeps_self_page():
    browser = _make_browser_with_mock_page()
    parent = browser._page
    popup = _mock_owned_page("popup")
    popup.opener = AsyncMock(return_value=parent)
    browser._auto_follow_popups = False

    await browser._maybe_adopt_page(popup)

    assert browser._page is parent  # unchanged
    assert popup in browser._owned_pages  # still adopted


# U11
@pytest.mark.asyncio
async def test_close_fallback_prefers_opener():
    browser = _make_browser_with_mock_page()
    opener = _mock_owned_page("opener")
    child = _mock_owned_page("child")
    child.opener = AsyncMock(return_value=opener)
    browser._owned_pages = {opener, child}
    browser._focus_stack = [opener, child]

    selected = await browser._select_fallback_page(child)

    assert selected is opener


# U12
@pytest.mark.asyncio
async def test_close_fallback_uses_focus_stack_when_opener_dead():
    browser = _make_browser_with_mock_page()
    dead_opener = _mock_owned_page("dead_opener", closed=True)
    other = _mock_owned_page("other")
    child = _mock_owned_page("child")
    child.opener = AsyncMock(return_value=dead_opener)
    browser._owned_pages = {dead_opener, other, child}
    browser._focus_stack = [dead_opener, other, child]
    # Set up context.pages so the owned-first tier (#3) would only fire after stack.
    browser._context.pages = [dead_opener, other, child]

    selected = await browser._select_fallback_page(child)

    # dead_opener pruned (is_closed=True); stack top-down: child (skipped),
    # other (alive, owned) → selected.
    assert selected is other


@pytest.mark.asyncio
async def test_close_fallback_skips_opener_not_in_owned():
    """If the opener is alive but no longer in _owned_pages (e.g., user
    closed bridgic ownership semantics), the opener is rejected."""
    browser = _make_browser_with_mock_page()
    external = _mock_owned_page("external")
    child = _mock_owned_page("child")
    child.opener = AsyncMock(return_value=external)
    other = _mock_owned_page("other")
    browser._owned_pages = {child, other}  # external NOT owned
    browser._focus_stack = [other, child]
    browser._context.pages = [external, other, child]

    selected = await browser._select_fallback_page(child)

    # external rejected (not in owned); other selected from stack.
    assert selected is other


# U13
@pytest.mark.asyncio
async def test_close_fallback_uses_owned_first_when_stack_empty():
    browser = _make_browser_with_mock_page()
    alive = _mock_owned_page("alive")
    child = _mock_owned_page("child")
    child.opener = AsyncMock(return_value=None)
    browser._owned_pages = {alive, child}
    browser._focus_stack = []  # empty
    browser._context.pages = [alive, child]

    selected = await browser._select_fallback_page(child)

    # Tier 3: get_pages() order → alive is first non-closed-page-being-closed candidate.
    assert selected is alive


# U14
@pytest.mark.asyncio
async def test_close_fallback_returns_none_when_no_owned_left():
    browser = _make_browser_with_mock_page()
    child = _mock_owned_page("child")
    child.opener = AsyncMock(return_value=None)
    browser._owned_pages = {child}
    browser._focus_stack = [child]
    browser._context.pages = [child]

    selected = await browser._select_fallback_page(child)

    assert selected is None


@pytest.mark.asyncio
async def test_close_fallback_handles_opener_exception():
    """opener() may raise (e.g., page already detached). Treat as None."""
    browser = _make_browser_with_mock_page()
    other = _mock_owned_page("other")
    child = _mock_owned_page("child")
    child.opener = AsyncMock(side_effect=RuntimeError("page is closed"))
    browser._owned_pages = {child, other}
    browser._focus_stack = [other, child]
    browser._context.pages = [other, child]

    selected = await browser._select_fallback_page(child)

    assert selected is other


# U5 — _new_page registers ownership
@pytest.mark.asyncio
async def test_new_page_registers_ownership():
    browser = _make_browser_with_mock_page()
    new_page = _mock_owned_page("brand_new")
    new_page.bring_to_front = AsyncMock()
    browser._context.new_page = AsyncMock(return_value=new_page)
    # Avoid the side-effect helpers — they require more elaborate setup.
    async def _noop_video(_p):
        pass
    browser._switch_video_to_page = _noop_video  # type: ignore[method-assign]

    # navigate_to is only invoked when url is provided; we pass url=None.
    result = await browser._new_page(url=None)

    assert result is new_page
    assert new_page in browser._owned_pages
    assert browser._focus_stack[-1] is new_page


# U18 — switch_to_page updates focus stack
@pytest.mark.asyncio
async def test_switch_to_page_updates_focus_stack():
    browser = _make_browser_with_mock_page()
    a = _mock_owned_page("a")
    b = _mock_owned_page("b")
    c = _mock_owned_page("c")
    # Build context.pages so find_page_by_id can resolve the page_id.
    browser._context.pages = [a, b, c]
    browser._owned_pages = {a, b, c}
    browser._focus_stack = [a, b, c]
    # Stubs for the heavy bits.
    a.bring_to_front = AsyncMock()
    b.bring_to_front = AsyncMock()
    c.bring_to_front = AsyncMock()
    a.url = "https://a"
    b.url = "https://b"
    c.url = "https://c"
    browser._get_page_title = AsyncMock(return_value="t")
    async def _noop_video(_p):
        pass
    browser._switch_video_to_page = _noop_video  # type: ignore[method-assign]

    from bridgic.browser.utils import generate_page_id
    a_id = generate_page_id(a)
    # Switch to `a` (was first in stack) → should move to tail.
    ok, _ = await browser.switch_to_page(a_id)
    assert ok
    assert browser._focus_stack[-1] is a
    # b should still be present and earlier in the stack than a.
    assert browser._focus_stack.index(b) < browser._focus_stack.index(a)


# ---------------------------------------------------------------------------
# CR follow-up tests (U19, U20 + close-race guard)
# ---------------------------------------------------------------------------

# U19 — closing a non-current owned page must NOT change self._page
@pytest.mark.asyncio
async def test_close_non_current_owned_page_keeps_self_page():
    current = _mock_owned_page("current")
    current.is_closed = MagicMock(return_value=False)
    other = _mock_owned_page("other")
    other.is_closed = MagicMock(return_value=False)
    other.title = AsyncMock(return_value="t-other")
    other.opener = AsyncMock(return_value=None)
    browser = _make_browser_with_mock_page()
    browser._page = current
    browser._owned_pages = {current, other}
    browser._focus_stack = [current, other]
    browser._context.pages = [current, other]
    # _get_page_title falls back to URL; provide one for the result message.
    current.url = "https://current.example/"
    browser._get_page_title = AsyncMock(return_value="t-current")

    success, msg = await browser._close_page(other)

    assert success
    # self._page stays on the same page; only `other` is gone.
    assert browser._page is current
    assert other not in browser._owned_pages
    assert other not in browser._focus_stack
    # Message reports a successor (which is the still-current page).
    assert "current.example" in msg


# U20 — closing a non-current page with video recording it: video must
# switch to self._page (NOT detach). Validates the HIGH-A fix where video
# target now tracks self._page when the closed page is not the current one.
@pytest.mark.asyncio
async def test_close_non_current_recorded_page_switches_video_to_self_page():
    current = _mock_owned_page("current")
    current.is_closed = MagicMock(return_value=False)
    current.url = "https://current.example/"
    recorded = _mock_owned_page("recorded")
    recorded.is_closed = MagicMock(return_value=False)
    recorded.opener = AsyncMock(return_value=None)
    recorded.title = AsyncMock(return_value="rec")
    browser = _make_browser_with_mock_page()
    browser._page = current  # currently driving `current`
    browser._owned_pages = {current, recorded}
    browser._focus_stack = [current, recorded]
    browser._context.pages = [current, recorded]
    browser._get_page_title = AsyncMock(return_value="t-current")
    # Wire up a recorder that's recording `recorded`, NOT `current`.
    recorder = MagicMock()
    recorder.is_stopped = False
    recorder.current_page = recorded
    recorder.switch_page = AsyncMock()
    recorder.detach_screencast = AsyncMock()
    browser._video_recorder = recorder

    success, _ = await browser._close_page(recorded)
    assert success

    # Video should have followed self._page (which is `current`), not
    # detached (the pre-fix behaviour was detach because candidate=None).
    recorder.switch_page.assert_awaited_once_with(current)
    recorder.detach_screencast.assert_not_awaited()
    # And self._page is still `current`.
    assert browser._page is current


# Close-race guard — _maybe_adopt_page must early-return when _closing is set
@pytest.mark.asyncio
async def test_maybe_adopt_page_returns_early_when_closing():
    browser = _make_browser_with_mock_page()
    browser._closing = True
    popup = _mock_owned_page("popup")
    popup.opener = AsyncMock(return_value=browser._page)

    await browser._maybe_adopt_page(popup)

    # No adoption took place; opener() must not have been awaited.
    popup.opener.assert_not_awaited()
    assert popup not in browser._owned_pages


def test_on_new_page_skipped_when_closing():
    """Synchronous listener must skip task scheduling when _closing is True."""
    import asyncio as _asyncio
    browser = _make_browser_with_mock_page()
    browser._closing = True

    # If a task were created, asyncio.create_task without a running loop would
    # raise RuntimeError — but the guard returns before that. We assert no
    # exception is raised, which is enough to prove the guard fires.
    page = _mock_owned_page("popup")
    browser._on_new_page(page)  # must not raise

    # Negative control: with closing=False and no running loop, the listener
    # would attempt create_task and RuntimeError-swallow. We don't test that
    # path here since it's exercised by integration tests.


@pytest.mark.asyncio
async def test_switch_self_page_to_skips_dead_page():
    """Race: popup closed between adoption and follow-switch."""
    browser = _make_browser_with_mock_page()
    original = browser._page
    dead = _mock_owned_page("dead", closed=True)

    await browser._switch_self_page_to(dead)

    # self._page must NOT have moved to the dead page.
    assert browser._page is original


# ---------------------------------------------------------------------------
# get_tabs CDP-borrowed-mode hint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tabs_appends_hint_in_cdp_borrowed_when_user_tabs_present():
    owned = MagicMock(name="owned_tab")
    owned.is_closed = MagicMock(return_value=False)
    owned.url = "https://bridgic.example/"
    owned.title = AsyncMock(return_value="Owned")
    user_a = MagicMock(name="user_a")
    user_a.is_closed = MagicMock(return_value=False)
    user_b = MagicMock(name="user_b")
    user_b.is_closed = MagicMock(return_value=False)
    browser = _make_borrowed_cdp_browser_with_pages(owned, user_a)
    # _make_borrowed_cdp_browser_with_pages only puts user_a in context.pages.
    # Extend to two user tabs to exercise the count.
    browser._context.pages = [user_a, user_b, owned]
    browser._get_page_title = AsyncMock(return_value="Owned")

    result = await browser.get_tabs()

    # Owned tab appears.
    assert "page_" in result
    # Hint mentions 2 hidden tabs.
    assert "Note:" in result
    assert "2 other tab(s)" in result
    assert "hidden" in result
    # Note must come BEFORE tab rows (LLMs read top-to-bottom), with a blank
    # line separator.
    note_idx = result.index("# Note:")
    tab_idx = result.index("page_")
    assert note_idx < tab_idx
    assert "\n\n" in result  # blank line between note and tabs


@pytest.mark.asyncio
async def test_get_tabs_omits_hint_in_non_cdp_mode():
    """Launch / persistent / ephemeral mode: all pages are owned, no filter,
    no hint should appear (avoid line-noise in the common case)."""
    browser = _make_browser_with_mock_page()
    # Non-CDP: _cdp_resolved is None (default in fixture), so _is_cdp_borrowed
    # property is False. All pages are owned by the helper's default setup.
    browser._page.url = "https://example.com/"
    browser._page.title = AsyncMock(return_value="Example")
    browser._context.pages = [browser._page]
    browser._get_page_title = AsyncMock(return_value="Example")

    result = await browser.get_tabs()

    assert "Note:" not in result
    assert "hidden" not in result


@pytest.mark.asyncio
async def test_get_tabs_no_tabs_with_user_tabs_present_hints():
    """Edge case: bridgic has zero owned tabs but the connected Chrome has
    user tabs. Show a helpful 'No open tabs + hint' message."""
    user_a = MagicMock(name="user_a")
    user_a.is_closed = MagicMock(return_value=False)
    browser = _make_browser_with_mock_page()
    # Manually configure CDP-borrowed mode.
    browser._cdp_resolved = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_raw = "ws://localhost:9222/devtools/browser/abc"
    browser._cdp_context_owned = False
    browser._context.pages = [user_a]
    browser._owned_pages = set()
    browser._focus_stack = []
    browser._page = None
    # get_current_page returns None → no descs.
    browser.get_current_page = AsyncMock(return_value=None)

    result = await browser.get_tabs()

    assert "No open tabs" in result
    assert "1 tab(s)" in result
    # Hint should suggest the actual CLI verb (`open`), not the SDK method name.
    assert "open <url>" in result
    # Note before "No open tabs", with blank-line separator.
    note_idx = result.index("# Note:")
    body_idx = result.index("No open tabs")
    assert note_idx < body_idx
    assert "\n\n" in result


@pytest.mark.asyncio
async def test_recover_page_in_existing_context_marks_new_page_owned():
    """Regression: navigate_to's "all tabs closed" recovery path
    (`_recover_page_in_existing_context`) used to create a new page via
    `_context.new_page()` without calling `_mark_owned`, leaving an
    orphaned `self._page` that:
      - `close_tab` (no arg) could see (uses self._page directly)
      - `tabs` could NOT see (filters by _owned_pages)
      - Popups from this page were rejected (parent not in owned set)
    The fix registers ownership; this test guards that the registration
    happens.
    """
    browser = _make_browser_with_mock_page()
    # Simulate the post-close-all state: no current page, empty owned set.
    browser._page = None
    browser._owned_pages = set()
    browser._focus_stack = []
    new_page = _mock_owned_page("recovered")
    browser._context.new_page = AsyncMock(return_value=new_page)
    async def _noop_video(_p):
        pass
    browser._switch_video_to_page = _noop_video  # type: ignore[method-assign]

    await browser._recover_page_in_existing_context()

    assert browser._page is new_page
    assert new_page in browser._owned_pages
    assert browser._focus_stack[-1] is new_page


@pytest.mark.asyncio
async def test_recover_page_in_existing_context_noop_when_no_context():
    """If `_context` is None (close has already torn it down), the recovery
    helper must be a safe no-op rather than raising."""
    browser = _make_browser_with_mock_page()
    browser._context = None
    browser._page = None
    browser._owned_pages = set()
    browser._focus_stack = []

    # Must not raise; must not mutate ownership state.
    await browser._recover_page_in_existing_context()
    assert browser._page is None
    assert browser._owned_pages == set()
    assert browser._focus_stack == []


@pytest.mark.asyncio
async def test_switch_self_page_to_skips_when_closing():
    """Shutdown guard: a popup-follow scheduled before close() must abort if
    close() has already flipped `_closing` by the time the coroutine runs.
    Prevents dangling download-manager attachments / dirty bookkeeping
    after `close()` has already torn things down."""
    browser = _make_browser_with_mock_page()
    original = browser._page
    new_page = _mock_owned_page("new")
    browser._closing = True

    await browser._switch_self_page_to(new_page)

    # self._page must NOT have moved — the guard tripped before any state
    # mutation. is_closed() should not even have been queried.
    assert browser._page is original
    new_page.is_closed.assert_not_called()


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
    fake_context.on.assert_not_called()

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
