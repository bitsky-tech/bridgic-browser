"""
Comprehensive integration tests for CDP borrowed mode.

Scenario: a real Chrome is already running with pre-existing tabs opened
BEFORE bridgic connects. These tabs are the exact ones that trigger the
Playwright _mainContext() race condition — Runtime.executionContextCreated
events arrive before Playwright registers its handlers, so page.evaluate()
/ page.title() would hang forever without our CDPSession bypass.

Setup:
  - Launches system Chrome with --remote-debugging-port=9229
  - Opens 3 pre-existing tabs (Wikipedia, httpbin.org, example.com) via CDP
  - Then connects bridgic via cdp

Coverage:
  - tabs listing (all 3 pre-existing + 1 bridgic-owned tab)
  - switch-tab to each pre-existing tab
  - info (uses _get_page_title + get_page_size_info via CDPSession)
  - snapshot (aria tree on pre-existing tab)
  - reload (uses _get_page_title)
  - evaluate_javascript (asyncio.wait_for timeout guard)
  - evaluate_javascript_on_ref (asyncio.wait_for timeout guard)
  - get_dropdown_options_by_ref
  - focus_element_by_ref
  - input_text_by_ref / click_element_by_ref
  - verify_title / verify_url
  - get_page_size_info (CDP Page.getLayoutMetrics path)
  - get_current_page_info (combined snapshot + size)
"""

import asyncio
import json
import subprocess
import tempfile
import time
import urllib.request

import pytest
import pytest_asyncio

from bridgic.browser.session import Browser

from ._chrome_utils import find_chrome_binary


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

CDP_PORT = 9229
CDP_HOST = "localhost"
CHROME_BIN: str | None = find_chrome_binary()

# Public pages used as "pre-existing tabs" opened before bridgic attaches.
# Chosen for stability, low JS complexity, and HTTPS.
PREOPENED_URLS = [
    "https://example.com",
    "https://httpbin.org/forms/post",   # has a form with select + inputs + checkbox
    "https://en.wikipedia.org/wiki/Browser_automation",
]


def _open_tab_via_cdp(url: str) -> None:
    """Open a new tab in the already-running Chrome via the /json/new endpoint."""
    req = urllib.request.Request(
        f"http://{CDP_HOST}:{CDP_PORT}/json/new?{url}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=5):
        pass


def _list_tabs_via_cdp() -> list:
    with urllib.request.urlopen(
        f"http://{CDP_HOST}:{CDP_PORT}/json/list", timeout=5
    ) as resp:
        return json.loads(resp.read())


def _wait_for_chrome(timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _list_tabs_via_cdp()
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"Chrome did not start debugging interface on port {CDP_PORT}")


# ─────────────────────────────────────────────────────────────────────────────
# Session-scoped Chrome fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def chrome_with_preopened_tabs():
    """
    Start a real Chrome process with remote debugging and 3 pre-existing tabs.

    The tabs are opened BEFORE bridgic attaches — this is the exact scenario
    that triggers the Playwright _mainContext() race condition.

    Yields the WebSocket debugger URL for the browser.
    """
    if CHROME_BIN is None:
        pytest.skip("Chrome/Chromium not found on this system")

    tmpdir = tempfile.mkdtemp(prefix="bridgic_cdp_test_")
    proc = subprocess.Popen(
        [
            CHROME_BIN,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={tmpdir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--headless=new",
            "about:blank",           # initial tab
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_chrome(timeout=20.0)

        # Open pre-existing tabs BEFORE bridgic attaches
        for url in PREOPENED_URLS:
            _open_tab_via_cdp(url)
        # Give pages a moment to start loading
        time.sleep(2.0)

        # Get the WS URL from /json/version (authoritative for the browser endpoint).
        _list_tabs_via_cdp()  # probe that CDP is responsive
        with urllib.request.urlopen(
            f"http://{CDP_HOST}:{CDP_PORT}/json/version", timeout=5
        ) as resp:
            info = json.loads(resp.read())
        ws_url = info["webSocketDebuggerUrl"]

        yield ws_url

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Browser fixture that attaches via CDP
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def cdp_browser(chrome_with_preopened_tabs):
    """Attach bridgic to the running Chrome via CDP (borrowed mode)."""
    ws_url = chrome_with_preopened_tabs
    browser = Browser(cdp=ws_url, stealth=False, headless=True)
    await browser._start()
    yield browser
    await browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for common assertions
# ─────────────────────────────────────────────────────────────────────────────

async def _switch_to_url(browser: Browser, url_fragment: str) -> str:
    """Switch to the tab whose URL contains url_fragment and return the page_id."""
    descs = await browser.get_all_page_descs()
    match = next((d for d in descs if url_fragment in d.url), None)
    assert match is not None, (
        f"No tab with URL containing {url_fragment!r}. Available: "
        + str([d.url for d in descs])
    )
    result = await browser.switch_to_page(match.page_id)
    assert result[0], f"switch_to_page failed: {result[1]}"
    return match.page_id


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_tabs_lists_all_preopened_pages(cdp_browser):
    """tabs / get_all_page_descs must include all pre-existing tabs.

    Root cause guarded: get_page_desc() calls _get_page_title() which used
    page.title() — hangs forever on pre-existing CDP tabs.
    """
    descs = await asyncio.wait_for(
        cdp_browser.get_all_page_descs(), timeout=15.0
    )
    urls = [d.url for d in descs]
    print(f"\n[tabs] found {len(descs)} tabs: {urls}")

    # All three pre-opened tabs must appear
    assert any("example.com" in u for u in urls), f"example.com missing: {urls}"
    assert any("httpbin.org" in u for u in urls), f"httpbin.org missing: {urls}"
    assert any("wikipedia.org" in u for u in urls), f"wikipedia.org missing: {urls}"

    # Every tab must have a non-empty page_id
    for d in descs:
        assert d.page_id, f"Missing page_id in {d}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_switch_tab_to_preopened_example(cdp_browser):
    """switch_to_page on a pre-existing tab must not hang.

    Root cause guarded: switch_to_page called _get_page_title() which hung.
    """
    page_id = await asyncio.wait_for(
        _switch_to_url(cdp_browser, "example.com"), timeout=15.0
    )
    print(f"\n[switch-tab] switched to {page_id}")
    assert page_id.startswith("page_")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_info_on_preopened_tab(cdp_browser):
    """get_current_page_info on a pre-existing tab must return URL + title + size.

    Root cause guarded: _get_page_title() and get_page_size_info() both hung.
    """
    await _switch_to_url(cdp_browser, "example.com")

    info = await asyncio.wait_for(
        cdp_browser.get_current_page_info(), timeout=20.0
    )
    print(f"\n[info] {info}")
    assert "example.com" in info.lower() or "example" in info.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_page_size_info_on_preopened_tab(cdp_browser):
    """get_page_size_info must use CDP Page.getLayoutMetrics (not page.evaluate).

    Root cause guarded: page.evaluate() hung indefinitely.
    """
    await _switch_to_url(cdp_browser, "example.com")

    size = await asyncio.wait_for(
        cdp_browser.get_page_size_info(), timeout=10.0
    )
    print(f"\n[size] {size}")
    assert size is not None
    assert size.viewport_width > 0, "viewport_width must be positive"
    assert size.viewport_height > 0, "viewport_height must be positive"
    assert size.page_width > 0, "page_width must be positive"
    assert size.page_height > 0, "page_height must be positive"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_snapshot_on_preopened_example(cdp_browser):
    """get_snapshot on pre-existing tab must return an accessibility tree.

    The snapshot uses snapshotForAI (Playwright's own CDP, 30 s timeout) —
    it works because Playwright's snapshot path is different from evaluate().
    """
    await _switch_to_url(cdp_browser, "example.com")

    snapshot = await asyncio.wait_for(
        cdp_browser.get_snapshot(), timeout=30.0
    )
    print(f"\n[snapshot] tree length={len(snapshot.tree)} chars")
    assert snapshot is not None
    assert len(snapshot.tree) > 50, "Accessibility tree too short"
    assert "heading" in snapshot.tree or "link" in snapshot.tree, (
        "Expected heading or link in example.com tree"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_snapshot_interactive_on_preopened_httpbin(cdp_browser):
    """Interactive snapshot on httpbin.org/forms/post — form inputs must appear."""
    await _switch_to_url(cdp_browser, "httpbin.org")

    snapshot = await asyncio.wait_for(
        cdp_browser.get_snapshot(interactive=True), timeout=30.0
    )
    print(f"\n[snapshot-interactive] {snapshot.tree[:500]}")
    assert snapshot is not None
    # httpbin form has text inputs and a submit button
    assert len(snapshot.refs) > 0, "No refs found — interactive snapshot empty"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_reload_on_preopened_tab(cdp_browser):
    """reload_page on pre-existing tab must complete (uses _get_page_title)."""
    await _switch_to_url(cdp_browser, "example.com")

    result = await asyncio.wait_for(
        cdp_browser.reload_page(), timeout=30.0
    )
    print(f"\n[reload] {result}")
    assert "reloaded" in result.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_evaluate_javascript_on_preopened_tab(cdp_browser):
    """evaluate_javascript on pre-existing tab must complete (asyncio.wait_for guard)."""
    await _switch_to_url(cdp_browser, "example.com")

    result = await asyncio.wait_for(
        cdp_browser.evaluate_javascript("document.title"),
        timeout=15.0,
    )
    print(f"\n[eval] result={result!r}")
    assert result is not None
    # Should return the page title
    assert "example" in str(result).lower() or isinstance(result, str)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_verify_url_on_preopened_tab(cdp_browser):
    """verify_url on pre-existing tab must work without hanging."""
    await _switch_to_url(cdp_browser, "example.com")

    result = await asyncio.wait_for(
        cdp_browser.verify_url("example.com"),
        timeout=15.0,
    )
    print(f"\n[verify-url] {result}")
    assert "PASS" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_verify_title_on_preopened_tab(cdp_browser):
    """verify_title on pre-existing tab uses _get_page_title (CDPSession bypass)."""
    await _switch_to_url(cdp_browser, "example.com")

    result = await asyncio.wait_for(
        cdp_browser.verify_title("Example"),
        timeout=15.0,
    )
    print(f"\n[verify-title] {result}")
    assert "PASS" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_screenshot_on_preopened_tab(cdp_browser):
    """take_screenshot must work on a pre-existing tab."""
    await _switch_to_url(cdp_browser, "example.com")

    data = await asyncio.wait_for(
        cdp_browser.take_screenshot(), timeout=15.0
    )
    print(f"\n[screenshot] {len(data)} bytes")
    assert len(data) > 1000, "Screenshot data too small"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_click_element_on_preopened_example(cdp_browser):
    """click_element_by_ref on pre-existing tab must not hang.

    Root cause guarded: covered-element check used locator.evaluate() without timeout.
    """
    await _switch_to_url(cdp_browser, "example.com")
    snapshot = await asyncio.wait_for(
        cdp_browser.get_snapshot(interactive=True), timeout=30.0
    )
    print(f"\n[click] refs available: {list(snapshot.refs.keys())[:5]}")

    # Find any link ref to click (prefer refs with a non-empty name)
    link_ref = next(
        (ref for ref, rd in snapshot.refs.items() if rd.role == "link" and rd.name),
        None,
    ) or next(
        (ref for ref, rd in snapshot.refs.items() if rd.role == "link"),
        None,
    )
    if link_ref is None:
        pytest.skip("No link found in interactive snapshot")

    result = await asyncio.wait_for(
        cdp_browser.click_element_by_ref(link_ref),
        timeout=15.0,
    )
    print(f"\n[click] {result}")
    assert "clicked" in result.lower() or "click" in result.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_input_text_on_preopened_httpbin(cdp_browser):
    """input_text_by_ref on a pre-existing tab's form input must not hang.

    Root cause guarded: hidden element path used locator.evaluate() without timeout;
    focus() calls used locator.evaluate("el.focus()") instead of locator.focus().
    """
    await _switch_to_url(cdp_browser, "httpbin.org")
    snapshot = await asyncio.wait_for(
        cdp_browser.get_snapshot(interactive=True), timeout=30.0
    )

    # Find a textbox input ref (prefer named ones)
    input_ref = next(
        (ref for ref, rd in snapshot.refs.items() if rd.role == "textbox"),
        None,
    ) or next(
        (ref for ref, rd in snapshot.refs.items()
         if rd.role in {"spinbutton", "searchbox"}),
        None,
    )
    if input_ref is None:
        pytest.skip("No text input found in httpbin interactive snapshot")

    result = await asyncio.wait_for(
        cdp_browser.input_text_by_ref(input_ref, "hello world"),
        timeout=15.0,
    )
    print(f"\n[input] {result}")
    assert "input" in result.lower() or "text" in result.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_focus_element_on_preopened_tab(cdp_browser):
    """focus_element_by_ref must use locator.focus() not locator.evaluate('el.focus()')."""
    await _switch_to_url(cdp_browser, "httpbin.org")
    snapshot = await asyncio.wait_for(
        cdp_browser.get_snapshot(interactive=True), timeout=30.0
    )

    input_ref = next(
        (ref for ref, rd in snapshot.refs.items()
         if rd.role in {"textbox", "spinbutton", "searchbox"}),
        None,
    )
    if input_ref is None:
        pytest.skip("No text input found in httpbin interactive snapshot")

    result = await asyncio.wait_for(
        cdp_browser.focus_element_by_ref(input_ref),
        timeout=10.0,
    )
    print(f"\n[focus] {result}")
    assert "focused" in result.lower() or "focus" in result.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_get_dropdown_options_on_preopened_httpbin(cdp_browser):
    """get_dropdown_options_by_ref on pre-existing tab — asyncio.wait_for guard."""
    await _switch_to_url(cdp_browser, "httpbin.org")
    snapshot = await asyncio.wait_for(
        cdp_browser.get_snapshot(interactive=True), timeout=30.0
    )

    select_ref = next(
        (ref for ref, rd in snapshot.refs.items()
         if rd.role == "combobox" and rd.name),
        None,
    ) or next(
        (ref for ref, rd in snapshot.refs.items() if rd.role == "combobox"),
        None,
    )
    if select_ref is None:
        pytest.skip("No <select> / combobox found in httpbin interactive snapshot")

    result = await asyncio.wait_for(
        cdp_browser.get_dropdown_options_by_ref(select_ref),
        timeout=10.0,
    )
    print(f"\n[dropdown-options] {result}")
    assert result  # non-empty string


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_evaluate_on_ref_on_preopened_tab(cdp_browser):
    """evaluate_javascript_on_ref on pre-existing tab — asyncio.wait_for guard.

    Navigates explicitly because a previous test may have followed a link
    away from example.com.
    """
    # Navigate to a known page (any tab — we just need any link ref)
    await asyncio.wait_for(
        cdp_browser.navigate_to("https://example.com"), timeout=20.0
    )
    snapshot = await asyncio.wait_for(
        cdp_browser.get_snapshot(interactive=True), timeout=30.0
    )

    link_ref = next(
        (ref for ref, rd in snapshot.refs.items() if rd.role == "link" and rd.name),
        None,
    ) or next(
        (ref for ref, rd in snapshot.refs.items() if rd.role == "link"),
        None,
    )
    if link_ref is None:
        pytest.skip("No link found in example.com interactive snapshot")

    result = await asyncio.wait_for(
        cdp_browser.evaluate_javascript_on_ref(
            link_ref, "(el) => el.href"
        ),
        timeout=15.0,
    )
    print(f"\n[eval-on] href={result!r}")
    assert result is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_multiple_tab_switches_without_hang(cdp_browser):
    """Rapidly switch between pre-existing tabs — none should hang.

    This is the full regression test: before the fix, any switch to a
    pre-existing tab would hang indefinitely at the title fetch.

    Uses switch_to_page (which internally calls _get_page_title) and then
    get_current_page_info (which calls both _get_page_title + get_page_size_info).
    All must complete without hanging.
    """
    # Switch to the still-existing pre-opened tabs (httpbin and wikipedia
    # were not navigated away from by earlier tests).
    tabs_to_visit = ["httpbin.org", "wikipedia.org", "httpbin.org"]
    for fragment in tabs_to_visit:
        print(f"\n[multi-switch] switching to {fragment}")
        page_id = await asyncio.wait_for(
            _switch_to_url(cdp_browser, fragment), timeout=15.0
        )
        # Also get current page info (title + size — both CDPSession paths)
        info = await asyncio.wait_for(
            cdp_browser.get_current_page_info(), timeout=20.0
        )
        assert len(info) > 10, f"Info for {fragment} seems empty: {info[:100]}"
        print(f"  → page_id={page_id}, info_len={len(info)}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_new_tab_and_navigate(cdp_browser):
    """bridgic can also open new tabs in CDP borrowed mode."""
    result = await asyncio.wait_for(
        cdp_browser.new_tab("https://example.com"),
        timeout=20.0,
    )
    print(f"\n[new-tab] {result}")
    assert "tab" in result.lower() or "created" in result.lower() or "navigated" in result.lower()

    # Navigate to confirm the new tab is usable
    nav = await asyncio.wait_for(
        cdp_browser.navigate_to("https://example.com"),
        timeout=20.0,
    )
    print(f"[navigate] {nav}")
    assert "example.com" in nav.lower() or "navigated" in nav.lower()
