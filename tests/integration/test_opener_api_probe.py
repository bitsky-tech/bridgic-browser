"""
API verification probe for Playwright's opener / page-event / close-event APIs.

These tests verify the foundational assumptions of the upcoming "owned-page
tracking" design:

  Claim A. `context.on("page")` fires for ALL new pages — bridgic-created
           via `context.new_page()`, popups from `window.open`, and popups
           from `<a target="_blank">` clicks.

  Claim B. `page.opener()` returns the EXACT same Page object that spawned a
           popup (identity comparison `is` must hold), and returns None for
           `context.new_page()` and for pre-existing tabs at CDP attach time.

  Claim C. The opener relationship survives CDP borrowed mode — popups
           spawned from bridgic's own page resolve their opener to that
           page's Python object.

  Claim D. `page.on("close")` fires reliably when a page is closed.

If any of these break in a given Playwright/Chromium version, the
"owned-page tracking" design must be adjusted before implementation.

Run:
    uv run pytest tests/integration/test_opener_api_probe.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from typing import List

import pytest
import pytest_asyncio
from playwright.async_api import BrowserContext, Page, async_playwright

from ._chrome_utils import find_chrome_binary


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

DATA_MAIN = "data:text/html,<html><body><h1>main</h1></body></html>"
DATA_POPUP = "data:text/html,<html><body><h1>popup</h1></body></html>"
# Chromium blocks <a target=_blank> from one data: URL to another (cross-origin
# popup with opaque origin), so the link's href points at about:blank instead.
HTML_WITH_LINK = (
    "data:text/html,<html><body>"
    "<a id='lnk' target='_blank' href='about:blank'>open</a>"
    "</body></html>"
)


async def _wait_event(event: asyncio.Event, timeout: float, what: str) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pytest.fail(f"Timed out waiting for {what} ({timeout}s)")


def _make_recorder(context: BrowserContext) -> List[Page]:
    """Attach a `page` listener and return the list it appends to."""
    captured: List[Page] = []
    context.on("page", lambda p: captured.append(p))
    return captured


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Non-CDP launch mode (fast, no external Chrome needed)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_opener_api_in_launch_mode():
    """
    Validates Claims A / B / D using a Playwright-launched Chromium.
    This is the fast smoke test — if THIS breaks, the design is unsalvageable.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            captured = _make_recorder(context)

            # --- Claim A.1: context.new_page() → opener None, listener fires
            base = await context.new_page()
            assert await base.opener() is None, (
                "context.new_page() should produce a page with opener() == None"
            )
            assert base in captured, (
                "context.on('page') listener should fire for context.new_page()"
            )

            await base.goto(HTML_WITH_LINK, wait_until="domcontentloaded")

            # --- Claim B.1: <a target=_blank> click → popup.opener() is base
            async with context.expect_page() as popup_info_link:
                await base.click("#lnk")
            popup_link = await popup_info_link.value
            await popup_link.wait_for_load_state("domcontentloaded")
            opener_link = await popup_link.opener()
            assert opener_link is base, (
                f"<a target=_blank> click: popup.opener() should be the EXACT "
                f"base Page object via `is`; got id={id(opener_link)} "
                f"vs base id={id(base)}"
            )
            assert popup_link in captured, (
                "context.on('page') listener should fire for <a target=_blank> popup"
            )

            # --- Claim B.2: window.open() → popup.opener() is base
            # Use about:blank for the popup target — Chromium blocks data:→data:
            # cross-origin popups (opaque origin), and the opener relationship
            # is the same regardless of the popup URL.
            async with context.expect_page() as popup_info_jsopen:
                await base.evaluate("window.open('about:blank', '_blank')")
            popup_jsopen = await popup_info_jsopen.value
            await popup_jsopen.wait_for_load_state("domcontentloaded")
            opener_jsopen = await popup_jsopen.opener()
            assert opener_jsopen is base, (
                "window.open(): popup.opener() should be the EXACT base Page object"
            )
            assert popup_jsopen in captured

            # --- Claim D: page.on("close") fires when a page is closed.
            close_fired = asyncio.Event()
            popup_link.on("close", lambda _p: close_fired.set())
            await popup_link.close()
            await _wait_event(close_fired, timeout=5.0, what="popup.on('close')")

            # --- Bonus: closing the opener (base) is observable too.
            base_close_fired = asyncio.Event()
            base.on("close", lambda _p: base_close_fired.set())
            await base.close()
            await _wait_event(base_close_fired, timeout=5.0, what="base.on('close')")

            # --- After base closes, popup_jsopen.opener() should be None
            # (Playwright: opener() returns None if the opener was closed —
            #  see playwright/_impl/_page.py:374-377)
            assert await popup_jsopen.opener() is None, (
                "After opener closes, popup.opener() must return None per "
                "Playwright contract (_page.py:374-377)"
            )

        finally:
            await browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — CDP borrowed mode (real Chrome subprocess + pre-existing user tabs)
# ─────────────────────────────────────────────────────────────────────────────

CDP_HOST = "localhost"
# Pick a port unlikely to clash. Match the convention from test_cdp_borrowed_mode.
CDP_PORT_PROBE = 9332
CHROME_BIN: str | None = find_chrome_binary()

USER_PREOPENED_URLS = [
    "data:text/html,<html><body><h1>user-tab-A</h1></body></html>",
    "data:text/html,<html><body><h1>user-tab-B</h1></body></html>",
]


def _list_targets(port: int) -> list:
    with urllib.request.urlopen(
        f"http://{CDP_HOST}:{port}/json/list", timeout=5
    ) as resp:
        return json.loads(resp.read())


def _open_tab_via_cdp(port: int, url: str) -> None:
    req = urllib.request.Request(
        f"http://{CDP_HOST}:{port}/json/new?{url}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=5):
        pass


def _ws_url(port: int) -> str:
    with urllib.request.urlopen(
        f"http://{CDP_HOST}:{port}/json/version", timeout=5
    ) as resp:
        info = json.loads(resp.read())
    return info["webSocketDebuggerUrl"]


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
def chrome_with_user_tabs():
    """Launch a real Chrome with 2 pre-existing user tabs, yield CDP ws:// URL."""
    if CHROME_BIN is None:
        pytest.skip("Chrome/Chromium not found")

    tmpdir = tempfile.mkdtemp(prefix="bridgic_opener_probe_")
    launch_args = [
        CHROME_BIN,
        f"--remote-debugging-port={CDP_PORT_PROBE}",
        f"--user-data-dir={tmpdir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--headless=new",
        "about:blank",
    ]
    if os.name != "nt":
        launch_args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

    proc = subprocess.Popen(
        launch_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        _wait_chrome(CDP_PORT_PROBE)
        for url in USER_PREOPENED_URLS:
            _open_tab_via_cdp(CDP_PORT_PROBE, url)
        # Brief settle to let pages register as targets.
        time.sleep(1.5)
        yield _ws_url(CDP_PORT_PROBE)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_opener_api_in_cdp_borrowed_mode(chrome_with_user_tabs):
    """
    Critical scenario: bridgic attaches via CDP to a Chrome that ALREADY has
    user tabs. Validates Claims A / B / C / D in this mode.

    Specifically asserts:
      * pre-existing user tabs have opener() == None (no parent in our tree)
      * bridgic's freshly created tab has opener() == None
      * popup spawned by bridgic's tab via window.open has opener() that
        IS (identity) the bridgic Page object
      * popup spawned by a user tab (simulating user activity) has opener()
        that IS the user Page object — so we can DETECT this and refuse to
        claim it as owned
      * context.on("page") fires for all of the above
      * page.on("close") fires for the popup
    """
    ws_url = chrome_with_user_tabs

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(ws_url)
        try:
            assert browser.contexts, "connect_over_cdp should yield a default context"
            context = browser.contexts[0]

            captured = _make_recorder(context)

            # ─── Inspect pre-existing user pages ─────────────────────────────
            pre_existing = list(context.pages)
            assert len(pre_existing) >= 2, (
                f"Expected >=2 pre-existing user tabs from fixture, "
                f"got {len(pre_existing)}: {[p.url for p in pre_existing]}"
            )
            print(f"\n[probe] pre-existing user tabs: {len(pre_existing)}")
            for up in pre_existing:
                op = await up.opener()
                print(f"  - {up.url[:60]}  opener={op}")
                assert op is None, (
                    f"Pre-existing user tab {up.url!r} should have opener=None "
                    f"(no parent in Playwright's tree), got {op}"
                )

            # ─── Bridgic creates its own tab ─────────────────────────────────
            bridgic = await context.new_page()
            assert await bridgic.opener() is None, (
                "context.new_page() must produce opener=None"
            )
            # Load a page with a target=_blank link — popup via real click is
            # the most reliable way to defeat headless Chrome's popup blocker.
            # `window.open` from evaluate() has been observed to be blocked
            # under CDP attach mode in some Chromium builds even though
            # Playwright sets userGesture=true.
            await bridgic.goto(HTML_WITH_LINK, wait_until="domcontentloaded")

            # ─── Claim B (CDP): bridgic clicks <a target=_blank> ─────────────
            async with context.expect_page() as popup_info:
                await bridgic.click("#lnk")
            popup_from_bridgic = await popup_info.value
            await popup_from_bridgic.wait_for_load_state("domcontentloaded")

            op_bridgic = await popup_from_bridgic.opener()
            print(
                f"[probe] popup-from-bridgic opener id={id(op_bridgic)} "
                f"bridgic id={id(bridgic)}"
            )
            assert op_bridgic is bridgic, (
                "CDP borrowed mode: popup from bridgic.window.open must have "
                "opener() identical to bridgic page (is-comparison)"
            )
            assert popup_from_bridgic in captured

            # ─── Claim B (CDP): popup from a USER tab → opener = user tab ────
            # Use a fresh CDPSession to run window.open on the user page,
            # avoiding the page.evaluate() hang on pre-existing tabs.
            # `userGesture: True` is required — without it Chrome's popup
            # blocker silently drops the window.open call in headless mode.
            user_page = pre_existing[0]
            sess = await context.new_cdp_session(user_page)
            try:
                async with context.expect_page() as user_popup_info:
                    await sess.send(
                        "Runtime.evaluate",
                        {
                            "expression": "window.open('about:blank', '_blank')",
                            "awaitPromise": False,
                            "userGesture": True,
                        },
                    )
                user_popup = await user_popup_info.value
                await user_popup.wait_for_load_state("domcontentloaded")
            finally:
                try:
                    await sess.detach()
                except Exception:
                    pass

            op_user = await user_popup.opener()
            print(
                f"[probe] popup-from-user opener id={id(op_user)} "
                f"user_page id={id(user_page)}"
            )
            assert op_user is user_page, (
                "CDP borrowed mode: popup from user_page.window.open must have "
                "opener() identical to user_page object — this is how we DETECT "
                "and EXCLUDE user-spawned popups from bridgic's owned set"
            )
            assert user_popup in captured

            # ─── Claim D: page.on("close") fires for popup close ─────────────
            close_fired = asyncio.Event()
            popup_from_bridgic.on("close", lambda _p: close_fired.set())
            await popup_from_bridgic.close()
            await _wait_event(close_fired, timeout=5.0, what="popup.on('close')")

            # ─── Bonus diagnostic: print full owner-graph view ───────────────
            print("\n[probe] final context.pages owner-graph:")
            for pg in context.pages:
                opener = await pg.opener()
                print(
                    f"  - {pg.url[:60]:60s}  "
                    f"opener={'None' if opener is None else opener.url[:30]}"
                )

        finally:
            # Don't close the underlying browser — it's owned by the fixture.
            # In CDP mode, browser.close() would tear down the user's Chrome,
            # which is shared across module-scoped fixture.
            try:
                # Best-effort: close bridgic-created tab so we don't leak it.
                if "bridgic" in locals() and not bridgic.is_closed():
                    await bridgic.close()
            except Exception:
                pass
            # Just disconnect Playwright from the CDP endpoint.
            try:
                await browser.close()
            except Exception:
                pass
