"""Unit tests for the daemon's ``_cdp_reconnect`` helper (C-3)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridgic.browser.cli._daemon import _cdp_reconnect, _dispatch_inner


@pytest.fixture
def browser_stub() -> MagicMock:
    """A minimal stand-in for ``Browser`` with the handles _cdp_reconnect touches.

    Populate the private handles so the test can verify they are reset to
    None before ``_start()`` runs — otherwise ``_start()``'s early-return
    guard (``if self._playwright is not None: return``) silently skips the
    reconnect when ``close()`` raised mid-flight and left state behind.
    """
    b = MagicMock()
    b.close = AsyncMock()
    b._start = AsyncMock()
    b._cancel_prefetch = MagicMock()
    b._playwright = object()
    b._browser = object()
    b._context = object()
    b._page = object()
    return b


class TestCdpReconnect:
    @pytest.mark.asyncio
    async def test_success_resets_handles_and_calls_start(
        self, browser_stub: MagicMock,
    ) -> None:
        """Happy path: close + reset handles + _start → returns True."""
        ok = await _cdp_reconnect(browser_stub)
        assert ok is True

        browser_stub.close.assert_awaited_once()
        browser_stub._start.assert_awaited_once()
        # N4: symmetric to test_cancels_prefetch_before_close — the up-front
        # _cancel_prefetch() must fire on the happy path too.
        browser_stub._cancel_prefetch.assert_called_once()
        # All four handles reset to None before _start runs.
        assert browser_stub._playwright is None
        assert browser_stub._browser is None
        assert browser_stub._context is None
        assert browser_stub._page is None

    @pytest.mark.asyncio
    async def test_forces_reset_even_when_close_raises(
        self, browser_stub: MagicMock,
    ) -> None:
        """C-3 core: close() raising must not abort reset.

        If close() fails mid-flight and we skipped the explicit reset, the
        leftover ``_playwright`` handle would trip ``_start()``'s early-return
        guard → ``_cdp_reconnect`` would report success without having
        reconnected.  The reset must happen unconditionally.
        """
        browser_stub.close.side_effect = RuntimeError("remote peer gone")

        ok = await _cdp_reconnect(browser_stub)
        assert ok is True

        # Close was attempted, error was swallowed, reset still happened.
        browser_stub.close.assert_awaited_once()
        assert browser_stub._playwright is None
        assert browser_stub._browser is None
        assert browser_stub._context is None
        assert browser_stub._page is None
        # _start was called AFTER the reset.
        browser_stub._start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_start_fails(
        self, browser_stub: MagicMock,
    ) -> None:
        """If _start() itself raises, return False (let caller decide retry)."""
        browser_stub._start.side_effect = RuntimeError("playwright launch failed")

        ok = await _cdp_reconnect(browser_stub)
        assert ok is False

        # Even on failure, handles must have been reset to None — otherwise
        # a future retry hits the early-return guard.
        assert browser_stub._playwright is None

    @pytest.mark.asyncio
    async def test_cancels_prefetch_before_close(
        self, browser_stub: MagicMock,
    ) -> None:
        """I2: _cancel_prefetch() must be called BEFORE close().

        close() also cancels prefetch, but if close() raises early (before
        reaching its own _cancel_prefetch line) an in-flight prefetch task
        survives into the reconnect window and touches a dead browser. The
        explicit up-front cancel is idempotent and prevents this race.
        """
        call_order: list = []

        browser_stub._cancel_prefetch = MagicMock(
            side_effect=lambda: call_order.append("cancel_prefetch")
        )

        async def _close_impl(*_a, **_k):
            call_order.append("close")

        browser_stub.close.side_effect = _close_impl

        ok = await _cdp_reconnect(browser_stub)
        assert ok is True
        assert call_order[:2] == ["cancel_prefetch", "close"], (
            f"Expected _cancel_prefetch before close, got: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_cancel_prefetch_error_is_swallowed(
        self, browser_stub: MagicMock,
    ) -> None:
        """I2: if _cancel_prefetch raises, the error is swallowed so reconnect
        still proceeds to close + _start.
        """
        browser_stub._cancel_prefetch = MagicMock(side_effect=RuntimeError("boom"))

        ok = await _cdp_reconnect(browser_stub)
        assert ok is True
        browser_stub.close.assert_awaited_once()
        browser_stub._start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resets_before_start_even_when_close_succeeds(
        self, browser_stub: MagicMock,
    ) -> None:
        """Handles must be None at the moment _start is entered.

        Even without a close() failure, the explicit reset matters: close()
        on some code paths leaves non-None references around (driver leak
        insurance).  Use a side-effect on _start to snapshot the state
        exactly when _start is entered.
        """
        observed: dict = {}

        async def snapshot_on_start(*_a, **_k) -> None:
            observed["pw"] = browser_stub._playwright
            observed["br"] = browser_stub._browser
            observed["ctx"] = browser_stub._context
            observed["pg"] = browser_stub._page

        browser_stub._start.side_effect = snapshot_on_start

        ok = await _cdp_reconnect(browser_stub)
        assert ok is True
        assert observed["pw"] is None
        assert observed["br"] is None
        assert observed["ctx"] is None
        assert observed["pg"] is None


class TestDispatchDetectsPlaywrightClose:
    """I2: a raw Playwright Error surfacing from a handler must trigger the
    one-shot ``_cdp_reconnect`` path. This locks in the isinstance-based
    detection so Playwright upstream rewording the message won't silently
    regress the reconnect behaviour."""

    @pytest.mark.asyncio
    async def test_playwright_error_target_closed_triggers_reconnect(self):
        from playwright.async_api import Error as PlaywrightError

        browser = MagicMock()
        browser._cdp_resolved = "ws://127.0.0.1:9222/devtools/browser/abc"
        browser._closing = False

        # First call raises a bare Playwright Error (not a TargetClosedError
        # subclass); second call succeeds. This proves the substring branch
        # is still engaged for the generic parent class.
        handler = AsyncMock(side_effect=[PlaywrightError("Target closed"), "ok"])

        with patch.dict(
            "bridgic.browser.cli._daemon._HANDLERS",
            {"open": handler},
            clear=False,
        ), patch(
            "bridgic.browser.cli._daemon._cdp_reconnect",
            new=AsyncMock(return_value=True),
        ) as reconnect_mock:
            resp = await _dispatch_inner(browser, "open", {"url": "https://x"})

        reconnect_mock.assert_awaited_once_with(browser)
        assert handler.await_count == 2
        assert resp["success"] is True

    @pytest.mark.asyncio
    async def test_target_closed_error_isinstance_triggers_reconnect(self):
        """A ``TargetClosedError`` with an unfamiliar message still reconnects
        (isinstance short-circuit — no reliance on substring matching)."""
        from playwright._impl._errors import TargetClosedError

        browser = MagicMock()
        browser._cdp_resolved = "ws://127.0.0.1:9222/devtools/browser/abc"
        browser._closing = False

        handler = AsyncMock(side_effect=[
            TargetClosedError("some future message with no known substring"),
            "ok",
        ])

        with patch.dict(
            "bridgic.browser.cli._daemon._HANDLERS",
            {"open": handler},
            clear=False,
        ), patch(
            "bridgic.browser.cli._daemon._cdp_reconnect",
            new=AsyncMock(return_value=True),
        ) as reconnect_mock:
            resp = await _dispatch_inner(browser, "open", {"url": "https://x"})

        reconnect_mock.assert_awaited_once_with(browser)
        assert resp["success"] is True
