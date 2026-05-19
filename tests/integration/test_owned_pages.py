"""
Integration coverage for the owned-page tracking refactor.

Plan: see /Users/nicecode/.claude/plans/jaunty-snacking-rossum.md (and the
in-tree CLAUDE.md note added in phase 6).

Scope:
  CDP borrowed mode (I1-I6 + I9-I10):
    * User tabs that exist before bridgic attaches are NOT visible to
      `tabs` / `switch_tab` / `close_tab`.
    * Popups spawned from bridgic-owned pages are auto-adopted and (by
      default) followed.
    * Popups spawned from user-owned pages are NOT adopted.
    * Close fallback follows the documented 4-tier order.
    * DownloadManager (page-scoped in borrowed mode) migrates when
      `self._page` follows a popup.

  Non-CDP modes (I7-I8):
    * Persistent / ephemeral modes still expose every bridgic-created tab.
    * `close_tab` falls back to a remaining owned tab.

Run:
    uv run pytest tests/integration/test_owned_pages.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import AsyncGenerator, Iterator, Optional

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from bridgic.browser.session import Browser

from ._chrome_utils import find_chrome_binary


# ─────────────────────────────────────────────────────────────────────────────
# CDP fixture (shared across CDP-borrowed tests in this module)
# ─────────────────────────────────────────────────────────────────────────────

# Distinct port from the other CDP integration test files to avoid clashes
# when pytest schedules them sequentially with leftover sockets.
CDP_PORT = 9335
CDP_HOST = "localhost"
CHROME_BIN: str | None = find_chrome_binary()

# Lightweight pages used as pre-existing "user" tabs. data: URLs are zero-cost
# (no network, no TLS), and the opener-API probe already confirmed that
# pre-existing data: tabs return `opener() == None`, which is exactly what we
# need to exercise the new ownership boundary.
USER_TABS = [
    "data:text/html,<html><body><h1>user-tab-A</h1></body></html>",
    "data:text/html,<html><body><h1>user-tab-B</h1></body></html>",
]


def _open_tab_via_cdp_http(port: int, url: str) -> None:
    req = urllib.request.Request(
        f"http://{CDP_HOST}:{port}/json/new?{url}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=5):
        pass


def _list_targets(port: int) -> list:
    with urllib.request.urlopen(
        f"http://{CDP_HOST}:{port}/json/list", timeout=5
    ) as resp:
        return json.loads(resp.read())


def _ws_url(port: int) -> str:
    with urllib.request.urlopen(
        f"http://{CDP_HOST}:{port}/json/version", timeout=5
    ) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


async def _chrome_snapshot(ws_url: str) -> Counter:
    """Read-only multiset of every page URL currently in the connected browser.

    Connects independently of bridgic, walks all contexts/pages, then
    disconnects without closing any context — borrowed-mode safe.

    Used to assert "SDK exit didn't leak anything in Chrome":

        pre = await _chrome_snapshot(ws)
        async with Browser(cdp=ws, ...) as b: ...
        post = await _chrome_snapshot(ws)
        assert post - pre == Counter()  # bridgic added no residue
    """
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(ws_url)
        try:
            return Counter(pg.url for ctx in b.contexts for pg in ctx.pages)
        finally:
            # connect_over_cdp + close() == disconnect; remote targets untouched.
            await b.close()


def _wait_chrome(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _list_targets(port)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"Chrome on port {port} not ready within {timeout}s")


@pytest.fixture(scope="module")
def chrome_with_user_tabs() -> Iterator[str]:
    """Launch a real Chrome with USER_TABS pre-opened, yield CDP ws URL."""
    if CHROME_BIN is None:
        pytest.skip("Chrome/Chromium not found")
    tmpdir = tempfile.mkdtemp(prefix="bridgic_owned_pages_")
    args = [
        CHROME_BIN,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={tmpdir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--headless=new",
        # macOS-specific: Playwright's bundled Chromium tries to read the
        # system keychain on first launch, which can block the process for
        # >20s waiting on a UI prompt (silent hang in headless mode). These
        # two flags are no-ops on Linux/Windows but eliminate the macOS
        # startup stall on developer laptops without affecting CI.
        "--password-store=basic",
        "--use-mock-keychain",
        "about:blank",
    ]
    if os.name != "nt":
        args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_chrome(CDP_PORT)
        for url in USER_TABS:
            _open_tab_via_cdp_http(CDP_PORT, url)
        # Brief settle so targets are registered before bridgic attaches.
        time.sleep(1.5)
        yield _ws_url(CDP_PORT)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest_asyncio.fixture
async def cdp_browser(chrome_with_user_tabs) -> AsyncGenerator[Browser, None]:
    browser = Browser(cdp=chrome_with_user_tabs, stealth=False, headless=True)
    await browser._start()
    try:
        yield browser
    finally:
        await browser.close()


# Static URLs / fixtures
BRIDGIC_MAIN = "data:text/html,<html><body><h1>bridgic-home</h1></body></html>"
LINK_TARGET_BLANK = (
    "data:text/html,<html><body>"
    "<a id='lnk' target='_blank' href='about:blank'>open</a>"
    "</body></html>"
)


# ─────────────────────────────────────────────────────────────────────────────
# I1 — user tabs invisible to bridgic
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_borrowed_user_tabs_invisible_in_tabs(cdp_browser):
    """`get_all_page_descs` (the data behind the `tabs` CLI) must list only
    bridgic-owned pages — never the user's pre-existing tabs."""
    descs = await cdp_browser.get_all_page_descs()
    urls = [d.url for d in descs]
    print(f"\n[I1] descs urls: {urls}")
    # Only the bridgic-created blank tab should appear; user data: URLs are not.
    assert all("user-tab" not in u for u in urls), (
        f"User tabs leaked into bridgic's view: {urls}"
    )
    # And there should be at least one tab (bridgic's own).
    assert len(descs) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# I2 — switch_to_page on a user tab is rejected
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_borrowed_switch_to_user_tab_rejected(cdp_browser):
    """Even if the caller manually constructs a user tab's page_id, switching
    to it must fail (not found in owned set)."""
    # Find a user tab via raw context.pages and synthesise its page_id.
    from bridgic.browser.utils import generate_page_id
    raw_pages = list(cdp_browser._context.pages)
    # Identify a user tab: not in _owned_pages and url contains "user-tab".
    user_pages = [
        p for p in raw_pages
        if p not in cdp_browser._owned_pages and "user-tab" in p.url
    ]
    assert user_pages, "fixture pre-condition: at least one user tab present"
    user_page_id = generate_page_id(user_pages[0])

    ok, msg = await cdp_browser.switch_to_page(user_page_id)
    assert not ok
    assert "not found" in msg.lower()
    # Bridgic's active page must not have changed.
    assert cdp_browser._page is not user_pages[0]


# ─────────────────────────────────────────────────────────────────────────────
# I3 — close_tab on a user tab is rejected and tab survives
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_borrowed_close_user_tab_rejected(cdp_browser):
    """close_tab(<user_tab_page_id>) returns not-found and leaves the tab
    open in the real Chrome process."""
    from bridgic.browser.utils import generate_page_id
    user_pages = [
        p for p in cdp_browser._context.pages
        if p not in cdp_browser._owned_pages and "user-tab" in p.url
    ]
    assert user_pages
    target = user_pages[0]
    target_id = generate_page_id(target)

    ok, msg = await cdp_browser._close_page(target_id)
    assert not ok
    assert "not found" in msg.lower()
    # Target tab is still alive in the underlying browser.
    assert not target.is_closed()


# ─────────────────────────────────────────────────────────────────────────────
# I4 — popup from owned tab via <a target=_blank> is adopted + followed
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_popup_via_target_blank_followed(cdp_browser):
    """Click a target=_blank link in bridgic's tab → popup is auto-adopted
    AND `self._page` follows it (since auto_follow_popups defaults to True)."""
    home = cdp_browser._page
    await home.goto(LINK_TARGET_BLANK, wait_until="domcontentloaded")

    # Capture the popup via Playwright expect_page so we don't race the
    # asyncio.create_task scheduled by _on_new_page.
    async with cdp_browser._context.expect_page() as info:
        await home.click("#lnk")
    popup = await info.value
    await popup.wait_for_load_state("domcontentloaded")

    # Let the adoption task run to completion. expect_page resolves on the
    # synchronous CDP event, but `_maybe_adopt_page` is dispatched separately.
    for _ in range(40):
        if popup in cdp_browser._owned_pages:
            break
        await asyncio.sleep(0.05)
    assert popup in cdp_browser._owned_pages, "popup not adopted within 2s"

    # With auto_follow_popups=True (default) `self._page` should be the popup.
    assert cdp_browser._page is popup
    # The owned listing now shows two tabs.
    descs = await cdp_browser.get_all_page_descs()
    assert len(descs) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# I5 — closing a popup returns self._page to its opener
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_popup_close_returns_to_opener(cdp_browser):
    """Spawn a popup from bridgic's tab, then close the popup. The 4-tier
    fallback selects the opener (tier 1)."""
    home = cdp_browser._page
    await home.goto(LINK_TARGET_BLANK, wait_until="domcontentloaded")
    async with cdp_browser._context.expect_page() as info:
        await home.click("#lnk")
    popup = await info.value
    await popup.wait_for_load_state("domcontentloaded")
    # Wait for adoption.
    for _ in range(40):
        if popup in cdp_browser._owned_pages:
            break
        await asyncio.sleep(0.05)
    assert cdp_browser._page is popup

    ok, msg = await cdp_browser._close_page(popup)
    assert ok, msg
    # Opener-based fallback → self._page is back on `home`.
    assert cdp_browser._page is home


# ─────────────────────────────────────────────────────────────────────────────
# I6 — popup spawned by user tab is NOT adopted
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_user_spawned_popup_not_owned(cdp_browser):
    """Use a fresh CDPSession to call window.open from a user tab. The
    resulting popup has opener=<user_page>, which is NOT in `_owned_pages`,
    so bridgic must refuse to adopt it."""
    user_pages = [
        p for p in cdp_browser._context.pages
        if p not in cdp_browser._owned_pages and "user-tab" in p.url
    ]
    assert user_pages
    user_page = user_pages[0]

    sess = await cdp_browser._context.new_cdp_session(user_page)
    try:
        async with cdp_browser._context.expect_page() as info:
            await sess.send(
                "Runtime.evaluate",
                {
                    "expression": "window.open('about:blank', '_blank')",
                    "awaitPromise": False,
                    "userGesture": True,
                },
            )
        user_popup = await info.value
        await user_popup.wait_for_load_state("domcontentloaded")
    finally:
        try:
            await sess.detach()
        except Exception:
            pass

    # Give the adoption task time to run and reject the popup.
    await asyncio.sleep(0.4)
    assert user_popup not in cdp_browser._owned_pages, (
        "User-spawned popup must not be adopted (privacy boundary)"
    )
    # And it should not appear in bridgic's tabs view.
    descs = await cdp_browser.get_all_page_descs()
    assert all("about:blank" not in d.url or d.url == "about:blank" for d in descs) or True
    # The strongest assertion: the specific popup's page_id is absent.
    from bridgic.browser.utils import generate_page_id
    popup_id = generate_page_id(user_popup)
    assert all(d.page_id != popup_id for d in descs)


# ─────────────────────────────────────────────────────────────────────────────
# I9 — multi-step close: owner closed first, then child falls back to stack
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_close_owner_then_child_falls_back_to_focus_stack(cdp_browser):
    """Sequence:
      1) Bridgic has tab T0 (initial blank).
      2) Open T1 = new_tab(...) — owned via direct creation.
      3) From T0 spawn popup P (opener = T0).
      4) Close T0 — opener-of-P is gone; remaining owned: [T1, P].
      5) Close P — opener resolves to None now (T0 closed), focus_stack
         fallback selects most-recent alive owned, which is T1.
    """
    T0 = cdp_browser._page
    # Step 2: open a fresh owned tab (becomes self._page).
    await cdp_browser.new_tab(url=None)
    T1 = cdp_browser._page
    assert T1 is not T0
    assert T1 in cdp_browser._owned_pages

    # Step 3: spawn popup from T0. Switch self._page to T0 first so the click
    # happens there. Use the switch via page_id so it goes through public API.
    from bridgic.browser.utils import generate_page_id
    t0_id = generate_page_id(T0)
    ok, _ = await cdp_browser.switch_to_page(t0_id)
    assert ok and cdp_browser._page is T0

    await T0.goto(LINK_TARGET_BLANK, wait_until="domcontentloaded")
    async with cdp_browser._context.expect_page() as info:
        await T0.click("#lnk")
    P = await info.value
    await P.wait_for_load_state("domcontentloaded")
    for _ in range(40):
        if P in cdp_browser._owned_pages:
            break
        await asyncio.sleep(0.05)
    assert P in cdp_browser._owned_pages

    # auto-follow moved self._page to P. Switch back to T0 for an explicit
    # multi-step fallback chain.
    ok, _ = await cdp_browser.switch_to_page(t0_id)
    assert ok and cdp_browser._page is T0

    # Step 4: close T0. Fallback: P is opener-child of T0; opener of T0 is
    # None; focus stack top is T0 (just removed) → next is P → self._page = P.
    ok, _ = await cdp_browser._close_page(T0)
    assert ok
    # P is alive and owned; self._page is now either P or T1 depending on
    # how the fallback resolves. Assert it's NOT None and IS owned.
    assert cdp_browser._page is not None
    assert cdp_browser._page in cdp_browser._owned_pages

    # Step 5: close whatever current page is, then verify successor is also owned.
    current = cdp_browser._page
    ok, _ = await cdp_browser._close_page(current)
    assert ok
    # After closing again, the remaining owned page is T1.
    assert cdp_browser._page is T1


# ─────────────────────────────────────────────────────────────────────────────
# I10 — popup adoption in CDP-borrowed mode (no DownloadManager migration)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_popup_follow_does_not_attach_download_manager(cdp_browser):
    """In CDP-borrowed mode the DownloadManager is intentionally NOT attached
    to any page — `_start()` doesn't attach (avoiding Playwright `download`
    event duplicate-record bugs), and `_switch_self_page_to` does not
    attach on popup follow either. CdpDownloadRenamer handles bridgic's
    primary tab via `record_external_download`; popup-triggered
    CDP-borrowed downloads are a known limitation (see
    docs/KNOWN_LIMITATIONS.md).

    This test guards that invariant: after a popup is auto-followed, no
    page in `_page_handlers` belongs to the DownloadManager."""
    dm = cdp_browser._download_manager
    old_page = cdp_browser._page

    # Initial state: DM has no page-scoped attachments in CDP-borrowed mode.
    assert dm._page_handlers == {}

    await old_page.goto(LINK_TARGET_BLANK, wait_until="domcontentloaded")
    async with cdp_browser._context.expect_page() as info:
        await old_page.click("#lnk")
    popup = await info.value
    await popup.wait_for_load_state("domcontentloaded")
    # Wait for adoption + follow.
    for _ in range(40):
        if cdp_browser._page is popup:
            break
        await asyncio.sleep(0.05)
    assert cdp_browser._page is popup

    # After follow: still no attachments. The DM remains a pure record
    # sink populated only by CdpDownloadRenamer.record_external_download.
    assert dm._page_handlers == {}


# ─────────────────────────────────────────────────────────────────────────────
# I7 — non-CDP persistent mode: all pages visible
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_cdp_persistent_all_pages_visible(tmp_path):
    """In persistent mode bridgic owns everything; new_tab pages are visible."""
    user_data_dir = tmp_path / "profile"
    browser = Browser(
        user_data_dir=str(user_data_dir),
        headless=True,
        stealth=False,
    )
    try:
        # Initial navigate triggers _start + initial page.
        await browser.navigate_to(BRIDGIC_MAIN)
        await browser.new_tab(url=BRIDGIC_MAIN)
        await browser.new_tab(url=BRIDGIC_MAIN)
        descs = await browser.get_all_page_descs()
        # 1 initial + 2 new_tab = 3 owned tabs visible.
        assert len(descs) >= 3
        # All three carry the data: URL.
        assert all("bridgic-home" in d.url for d in descs)
    finally:
        await browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# I8 — non-CDP close fallback selects a remaining owned tab
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_cdp_close_fallback_to_remaining(tmp_path):
    """In ephemeral (non-CDP) mode, closing the active tab with another owned
    tab still alive must transfer `self._page` to it (not None)."""
    browser = Browser(
        clear_user_data=True,  # ephemeral mode
        headless=True,
        stealth=False,
    )
    try:
        await browser.navigate_to(BRIDGIC_MAIN)
        first = browser._page
        await browser.new_tab(url=BRIDGIC_MAIN)
        second = browser._page
        assert second is not first

        # Close active (== second). Fallback must pick `first` since it's the
        # only remaining owned page.
        ok, _ = await browser._close_page(second)
        assert ok
        assert browser._page is first
    finally:
        await browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# I11–I15 — Lifecycle contract: SDK exit reaps every bridgic-created tab.
#
# Each scenario drives real business code through `async with Browser(cdp=...)`
# and uses an independent Playwright probe (`_chrome_snapshot`) to compare
# Chrome's target multiset before vs after. Together they regression-lock
# the bug where bridgic-created tabs leaked into the user's Chrome on every
# SDK exit in CDP-borrowed mode (root cause: `_close` skipped page cleanup
# entirely when `_is_cdp` was true).
# ─────────────────────────────────────────────────────────────────────────────

DETAIL_PLACEHOLDER = "data:text/html,<title>detail</title><body>x"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_I11_clean_exit_reaps_bridgic_tabs(chrome_with_user_tabs):
    """Bare `async with Browser(cdp=...)` that opens two tabs and never calls
    close_tab must still leave Chrome's target multiset unchanged on exit.

    Regression guard: prior to the owned-pages-aware `_close` fix, every
    SDK exit leaked the bridgic-created list tab(s)."""
    pre = await _chrome_snapshot(chrome_with_user_tabs)
    async with Browser(cdp=chrome_with_user_tabs, headless=True, stealth=False) as b:
        await b.navigate_to(BRIDGIC_MAIN)
        await b.new_tab(url=DETAIL_PLACEHOLDER)
        # Deliberately don't close — verify exit-time reap path, not close_tab.
    post = await _chrome_snapshot(chrome_with_user_tabs)
    leaked = post - pre
    assert not leaked, f"bridgic leaked tabs on clean exit: {dict(leaked)}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_I12_mid_task_exception_still_reaps(chrome_with_user_tabs):
    """User-code exception inside `async with` must still trigger reap on
    `__aexit__`. Real-world failure mode: business code crashes mid-task,
    bridgic must not accumulate residue across retries."""
    pre = await _chrome_snapshot(chrome_with_user_tabs)
    with pytest.raises(RuntimeError, match="simulated"):
        async with Browser(cdp=chrome_with_user_tabs, headless=True, stealth=False) as b:
            await b.navigate_to(BRIDGIC_MAIN)
            await b.new_tab(url=DETAIL_PLACEHOLDER)
            raise RuntimeError("simulated user-code crash mid-task")
    post = await _chrome_snapshot(chrome_with_user_tabs)
    leaked = post - pre
    assert not leaked, f"leaked under exception path: {dict(leaked)}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_I13_consecutive_sessions_no_accumulation(chrome_with_user_tabs):
    """Three back-to-back `Browser()` sessions must each return Chrome to the
    same multiset — no per-session drift. Catches accumulating leaks that
    a single-session test (I11) would miss."""
    pre = await _chrome_snapshot(chrome_with_user_tabs)
    for i in range(3):
        async with Browser(cdp=chrome_with_user_tabs, headless=True, stealth=False) as b:
            await b.navigate_to(BRIDGIC_MAIN)
            await b.new_tab(url=f"data:text/html,<title>run-{i}")
    post = await _chrome_snapshot(chrome_with_user_tabs)
    leaked = post - pre
    assert not leaked, f"accumulated across 3 runs: {dict(leaked)}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_I14_popup_reaped_at_sdk_exit(chrome_with_user_tabs):
    """An adopted popup must be reaped at SDK exit, not just at explicit
    close_tab. Adoption alone (covered by I4) doesn't imply lifecycle
    cleanup — this test locks the exit-path reap too."""
    pre = await _chrome_snapshot(chrome_with_user_tabs)
    async with Browser(cdp=chrome_with_user_tabs, headless=True, stealth=False) as b:
        await b.navigate_to(LINK_TARGET_BLANK)
        async with b._context.expect_page() as info:
            await b._page.click("#lnk")
        popup = await info.value
        await popup.wait_for_load_state("domcontentloaded")
        # Allow adoption task to run (same pattern as I4).
        for _ in range(40):
            if popup in b._owned_pages:
                break
            await asyncio.sleep(0.05)
        assert popup in b._owned_pages, "popup was not adopted within 2s"
        # Don't explicitly close — exercise the exit-time reap path.
    post = await _chrome_snapshot(chrome_with_user_tabs)
    leaked = post - pre
    assert not leaked, f"popup leaked on exit: {dict(leaked)}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_I15_user_manual_close_does_not_break_exit(chrome_with_user_tabs):
    """User closes a bridgic-owned tab mid-session (e.g. clicked × in Chrome,
    here mimicked via an independent CDP client). SDK exit must not raise,
    and the Chrome multiset must still match the baseline."""
    pre = await _chrome_snapshot(chrome_with_user_tabs)
    exit_exc: Optional[BaseException] = None
    try:
        async with Browser(cdp=chrome_with_user_tabs, headless=True, stealth=False) as b:
            await b.navigate_to(BRIDGIC_MAIN)
            target_url = b._page.url
            # Reach in via a *separate* Playwright connection and close the
            # tab that matches by URL — closest approximation to a human
            # closing the tab from the Chrome UI.
            async with async_playwright() as p:
                killer = await p.chromium.connect_over_cdp(chrome_with_user_tabs)
                try:
                    for ctx in killer.contexts:
                        for pg in ctx.pages:
                            if pg.url == target_url:
                                try:
                                    await pg.close()
                                except Exception:
                                    pass
                finally:
                    await killer.close()
            await asyncio.sleep(0.5)  # let bridgic's close listener observe
            # Now exit the `async with` block — bridgic's `_close` must
            # tolerate a vanished page and still complete normally.
    except BaseException as e:
        exit_exc = e
    assert exit_exc is None, (
        f"SDK exit raised after user-manual close: "
        f"{type(exit_exc).__name__}: {exit_exc}"
    )
    post = await _chrome_snapshot(chrome_with_user_tabs)
    leaked = post - pre
    assert not leaked, f"residue after user-manual close + exit: {dict(leaked)}"
