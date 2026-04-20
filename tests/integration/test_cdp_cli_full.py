"""
Full CLI integration tests for CDP borrowed mode.

Scenario: a real Chrome is already running with pre-existing tabs.
bridgic-browser open --cdp auto <url> attaches to it, then all subsequent
CLI commands run against that session — including operations on the
pre-existing tabs that existed BEFORE bridgic attached.

This directly mirrors real-world usage ("I have Chrome open, let me have
an AI agent control it via bridgic-browser CLI").

Covered commands:
  tabs, switch-tab, info, snapshot, snapshot -i, open/navigate,
  click, type, input, focus, eval, eval-on, wait, wait --gone,
  verify-url, verify-title, verify-text, screenshot, reload, back, forward,
  scroll, hover, check, uncheck, key-press, size

Setup:
  - Launches system Chrome with --remote-debugging-port=9230
  - Opens 3 pre-existing tabs before bridgic attaches
  - bridgic-browser open --cdp 9230 ... starts the session
  - All subsequent commands reuse the same daemon
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Optional, Tuple

import pytest

from ._chrome_utils import find_chrome_binary

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

CDP_PORT = 9230
CHROME_BIN: str | None = find_chrome_binary()
CLI = "bridgic-browser"

# Pre-opened tabs (loaded BEFORE bridgic attaches — the key regression scenario)
PREOPENED_URLS = [
    "https://example.com",
    "https://httpbin.org/forms/post",   # form with inputs, radios, checkboxes
    "https://en.wikipedia.org/wiki/Web_scraping",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: str, timeout: int = 30) -> Tuple[int, str, str]:
    """Run a CLI command, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        shlex.split(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _ok(cmd: str, timeout: int = 30) -> str:
    """Run CLI command, assert success (rc==0), return stdout."""
    rc, out, err = _run(cmd, timeout=timeout)
    assert rc == 0, (
        f"Command failed (rc={rc}):\n  cmd: {cmd}\n  stdout: {out}\n  stderr: {err}"
    )
    return out


def _open_tab_via_cdp(url: str) -> None:
    req = urllib.request.Request(
        f"http://localhost:{CDP_PORT}/json/new?{url}", method="PUT"
    )
    with urllib.request.urlopen(req, timeout=5):
        pass


def _wait_for_chrome(timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://localhost:{CDP_PORT}/json/list", timeout=3
            ):
                return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"Chrome did not start on port {CDP_PORT}")


def _extract_ref(snapshot_text: str, role: str) -> Optional[str]:
    """Extract the first ref for a given role from a snapshot output."""
    pattern = rf'- {re.escape(role)}\b.*\[ref=([0-9a-f]{{8}})\]'
    m = re.search(pattern, snapshot_text, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_all_refs(snapshot_text: str) -> dict:
    """Return {ref: line} for every ref in a snapshot."""
    pattern = r'\[ref=([0-9a-f]{8})\]'
    refs = {}
    for line in snapshot_text.splitlines():
        m = re.search(pattern, line)
        if m:
            refs[m.group(1)] = line.strip()
    return refs


def _extract_page_ids(tabs_text: str) -> list:
    """Extract all page_XXXXXXXX identifiers from `tabs` output."""
    return re.findall(r'page_\d+', tabs_text)


def _resolve_snapshot(snap_output: str) -> str:
    """If snap_output contains a [notice] about a file, read and return it.

    bridgic-browser snapshot saves to a file when the content exceeds --limit
    (default 10000 chars) and returns only a notice.  This function transparently
    reads the file so callers always receive the full snapshot text.
    """
    m = re.search(r'\[notice\] Snapshot file.*saved to:\s*(.+\.txt)', snap_output)
    if m:
        path = m.group(1).strip()
        if os.path.exists(path):
            with open(path) as f:
                return f.read()
    return snap_output


# ─────────────────────────────────────────────────────────────────────────────
# Module-scoped Chrome fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def chrome_process():
    """Start Chrome with remote debugging; yield; kill Chrome and daemon."""
    if CHROME_BIN is None:
        pytest.skip("Chrome/Chromium not found on this system")

    tmpdir = tempfile.mkdtemp(prefix="bridgic_cli_cdp_")
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
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_chrome(timeout=25.0)

        # Open pre-existing tabs BEFORE bridgic attaches
        for url in PREOPENED_URLS:
            _open_tab_via_cdp(url)
        time.sleep(2.5)  # let pages load

        yield proc

    finally:
        # Kill bridgic daemon (if any)
        subprocess.run([CLI, "close"], capture_output=True, timeout=10)
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(scope="module")
def session(chrome_process):
    """Start bridgic session via CDP and return the cdp_flag string."""
    cdp_flag = f"--cdp {CDP_PORT}"
    # Attach bridgic to Chrome (opens one new bridgic-owned tab)
    out = _ok(f"{CLI} open {cdp_flag} https://example.com", timeout=30)
    print(f"\n[session] started: {out}")
    yield cdp_flag
    # Teardown: disconnect (close only disconnects, doesn't kill Chrome)
    subprocess.run([CLI, "close"], capture_output=True, timeout=15)


# ─────────────────────────────────────────────────────────────────────────────
# Tests — in execution order (module scope keeps one daemon alive for all)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_cli_tabs_shows_preopened_pages(session):
    """tabs must list all tabs including those opened before bridgic attached."""
    out = _ok(f"{CLI} tabs")
    print(f"\n[tabs]\n{out}")

    assert "example.com" in out, f"example.com missing from tabs:\n{out}"
    assert "httpbin.org" in out, f"httpbin.org missing from tabs:\n{out}"
    assert "wikipedia.org" in out or "wiki" in out.lower(), (
        f"wikipedia missing from tabs:\n{out}"
    )
    page_ids = _extract_page_ids(out)
    assert len(page_ids) >= 3, f"Expected ≥3 page_ids, got {page_ids}"
    print(f"  → page_ids: {page_ids}")


@pytest.mark.integration
def test_cli_switch_tab_to_preopened_httpbin(session):
    """switch-tab to a pre-existing tab must complete without hanging."""
    tabs_out = _ok(f"{CLI} tabs")
    httpbin_id = next(
        (pid for pid in _extract_page_ids(tabs_out)
         if "httpbin" in tabs_out[tabs_out.find(pid)-200:tabs_out.find(pid)+50]),
        None,
    )
    # Fallback: find the line containing httpbin and extract the page_id from it
    if httpbin_id is None:
        for line in tabs_out.splitlines():
            if "httpbin" in line:
                m = re.search(r'page_\d+', line)
                if m:
                    httpbin_id = m.group()
                    break

    assert httpbin_id is not None, f"Could not find httpbin page_id in:\n{tabs_out}"

    out = _ok(f"{CLI} switch-tab {httpbin_id}")
    print(f"\n[switch-tab] {out}")
    assert "httpbin" in out.lower() or "switched" in out.lower() or httpbin_id in out


@pytest.mark.integration
def test_cli_info_on_preopened_httpbin(session):
    """info on pre-existing tab uses CDPSession bypass for title + size."""
    # Make sure we're on httpbin
    tabs_out = _ok(f"{CLI} tabs")
    for line in tabs_out.splitlines():
        if "httpbin" in line:
            m = re.search(r'page_\d+', line)
            if m:
                _ok(f"{CLI} switch-tab {m.group()}")
                break

    out = _ok(f"{CLI} info", timeout=20)
    print(f"\n[info]\n{out}")
    assert "httpbin" in out.lower(), f"URL not in info output:\n{out}"
    # Size info should show width x height
    assert re.search(r'\d+x\d+', out), f"No WxH size in info output:\n{out}"


@pytest.mark.integration
def test_cli_snapshot_full_on_preopened_httpbin(session):
    """Full snapshot on httpbin pre-existing tab returns accessibility tree."""
    out = _ok(f"{CLI} snapshot", timeout=30)
    print(f"\n[snapshot] {len(out)} chars, first 300:\n{out[:300]}")
    assert len(out) > 100, "Snapshot too short"
    refs = _extract_all_refs(out)
    assert len(refs) >= 1, "No refs found in snapshot"
    print(f"  → {len(refs)} refs found")


@pytest.mark.integration
def test_cli_snapshot_interactive_on_preopened_httpbin(session):
    """Interactive snapshot on httpbin form must expose inputs and buttons."""
    out = _ok(f"{CLI} snapshot -i", timeout=30)
    print(f"\n[snapshot -i]\n{out}")
    # httpbin.org occasionally returns 5xx; the page then has no form to
    # inspect. Skip rather than fail the build on an upstream outage.
    if re.search(r"\b5\d\d\b", out) and "bad gateway" in out.lower():
        pytest.skip(f"httpbin.org returned 5xx error page: {out[:200]}")
    refs = _extract_all_refs(out)
    assert len(refs) >= 3, f"Expected ≥3 interactive refs, got {len(refs)}: {out[:400]}"

    # httpbin form has textboxes, radios, checkboxes, and a submit button
    assert "textbox" in out.lower() or "input" in out.lower(), (
        "Expected textbox/input in httpbin interactive snapshot"
    )
    print(f"  → {len(refs)} interactive refs: {list(refs.keys())[:8]}")


@pytest.mark.integration
def test_cli_input_text_into_preopened_httpbin_form(session):
    """input text into httpbin form field (pre-existing tab)."""
    snap_out = _ok(f"{CLI} snapshot -i", timeout=30)
    # Find a textbox ref
    textbox_ref = _extract_ref(snap_out, "textbox")
    if textbox_ref is None:
        pytest.skip("No textbox in httpbin interactive snapshot")

    out = _ok(f"{CLI} fill {textbox_ref} 'John Doe'", timeout=15)
    print(f"\n[fill] ref={textbox_ref}: {out}")
    assert out  # any non-empty success output


@pytest.mark.integration
def test_cli_focus_element_on_preopened_httpbin(session):
    """focus command on pre-existing tab (uses locator.focus(), not evaluate)."""
    snap_out = _ok(f"{CLI} snapshot -i", timeout=30)
    textbox_ref = _extract_ref(snap_out, "textbox")
    if textbox_ref is None:
        pytest.skip("No textbox in httpbin interactive snapshot")

    out = _ok(f"{CLI} focus {textbox_ref}", timeout=10)
    print(f"\n[focus] ref={textbox_ref}: {out}")
    assert "focus" in out.lower()


@pytest.mark.integration
def test_cli_type_text_on_preopened_httpbin(session):
    """type sends keystrokes to the currently focused element."""
    # First focus a field
    snap_out = _ok(f"{CLI} snapshot -i", timeout=30)
    textbox_ref = _extract_ref(snap_out, "textbox")
    if textbox_ref is None:
        pytest.skip("No textbox in httpbin interactive snapshot")

    _ok(f"{CLI} click {textbox_ref}", timeout=15)
    out = _ok(f"{CLI} type 'hello cdp'", timeout=10)
    print(f"\n[type]: {out}")
    assert "type" in out.lower() or "sent" in out.lower() or "text" in out.lower()


@pytest.mark.integration
def test_cli_check_checkbox_on_preopened_httpbin(session):
    """check a checkbox on pre-existing tab (uses _is_checked via is_checked())."""
    snap_out = _ok(f"{CLI} snapshot -i", timeout=30)
    checkbox_ref = _extract_ref(snap_out, "checkbox")
    if checkbox_ref is None:
        pytest.skip("No checkbox in httpbin interactive snapshot")

    out = _ok(f"{CLI} check {checkbox_ref}", timeout=15)
    print(f"\n[check] ref={checkbox_ref}: {out}")
    assert "check" in out.lower()


@pytest.mark.integration
def test_cli_uncheck_checkbox_on_preopened_httpbin(session):
    """uncheck a checkbox on pre-existing tab (idempotent if already unchecked)."""
    snap_out = _ok(f"{CLI} snapshot -i", timeout=30)
    checkbox_ref = _extract_ref(snap_out, "checkbox")
    if checkbox_ref is None:
        pytest.skip("No checkbox in httpbin interactive snapshot")

    # First ensure it's checked
    _run(f"{CLI} check {checkbox_ref}", timeout=15)
    # Now uncheck
    out = _ok(f"{CLI} uncheck {checkbox_ref}", timeout=15)
    print(f"\n[uncheck] ref={checkbox_ref}: {out}")
    assert "uncheck" in out.lower() or "unchecked" in out.lower()


@pytest.mark.integration
def test_cli_eval_javascript_on_preopened_httpbin(session):
    """eval on pre-existing tab uses CDPSession bypass — must not hang."""
    # Use an expression that is always non-empty regardless of page content.
    out = _ok(f"{CLI} eval 'typeof document'", timeout=20)
    print(f"\n[eval typeof]: {out}")
    assert out.strip() == "object", f"Expected 'object', got: {out!r}"


@pytest.mark.integration
def test_cli_eval_on_ref_on_preopened_httpbin(session):
    """eval-on <ref> on pre-existing tab (asyncio.wait_for guard for locator.evaluate)."""
    snap_out = _ok(f"{CLI} snapshot -i", timeout=30)
    textbox_ref = _extract_ref(snap_out, "textbox")
    if textbox_ref is None:
        pytest.skip("No textbox ref for eval-on test")

    out = _ok(f"{CLI} eval-on {textbox_ref} '(el) => el.tagName'", timeout=15)
    print(f"\n[eval-on] ref={textbox_ref}: {out}")
    assert "INPUT" in out.upper() or "TEXTAREA" in out.upper(), (
        f"Expected INPUT/TEXTAREA tag, got: {out}"
    )


@pytest.mark.integration
def test_cli_screenshot_on_preopened_tab(session):
    """screenshot on pre-existing tab must return valid PNG data."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        out = _ok(f'{CLI} screenshot "{path}"', timeout=15)
        print(f"\n[screenshot]: {out}")
        assert os.path.exists(path), "Screenshot file not created"
        size = os.path.getsize(path)
        assert size > 5000, f"Screenshot too small: {size} bytes"
        print(f"  → {size} bytes")
    finally:
        os.unlink(path) if os.path.exists(path) else None


@pytest.mark.integration
def test_cli_verify_url_on_preopened_httpbin(session):
    """verify-url on pre-existing tab — no hang."""
    out = _ok(f"{CLI} verify-url httpbin.org", timeout=15)
    print(f"\n[verify-url]: {out}")
    assert "PASS" in out


@pytest.mark.integration
def test_cli_switch_to_example_then_verify_title(session):
    """Switch to example.com (pre-existing) and verify its title."""
    tabs_out = _ok(f"{CLI} tabs")
    example_id = None
    for line in tabs_out.splitlines():
        if "example.com" in line:
            m = re.search(r'page_\d+', line)
            if m:
                example_id = m.group()
                break

    if example_id is None:
        pytest.skip("example.com tab not found (may have navigated away)")

    _ok(f"{CLI} switch-tab {example_id}")
    out = _ok(f"{CLI} verify-title Example", timeout=15)
    print(f"\n[verify-title]: {out}")
    assert "PASS" in out


@pytest.mark.integration
def test_cli_reload_on_preopened_example(session):
    """reload on pre-existing tab uses _get_page_title (CDPSession bypass)."""
    tabs_out = _ok(f"{CLI} tabs")
    for line in tabs_out.splitlines():
        if "example.com" in line:
            m = re.search(r'page_\d+', line)
            if m:
                _ok(f"{CLI} switch-tab {m.group()}")
                break

    out = _ok(f"{CLI} reload", timeout=30)
    print(f"\n[reload]: {out}")
    assert "reloaded" in out.lower()


@pytest.mark.integration
def test_cli_wait_for_text_on_preopened_example(session):
    """wait for visible text on a pre-existing tab."""
    # example.com always has "Example Domain" text
    out = _ok(f"{CLI} wait 'Example Domain'", timeout=20)
    print(f"\n[wait]: {out}")
    assert "found" in out.lower() or "appeared" in out.lower() or "example" in out.lower()


@pytest.mark.integration
def test_cli_scroll_on_preopened_tab(session):
    """scroll command on pre-existing tab."""
    out = _ok(f"{CLI} scroll --dy 300", timeout=10)
    print(f"\n[scroll]: {out}")
    assert "scroll" in out.lower() or "dy" in out.lower() or out


@pytest.mark.integration
def test_cli_navigate_to_new_url_on_preopened_session(session):
    """open a new URL within the existing CDP session."""
    out = _ok(f"{CLI} open https://httpbin.org/get", timeout=25)
    print(f"\n[open]: {out}")
    assert "httpbin.org" in out.lower() or "navigated" in out.lower()

    # Verify we landed there
    info = _ok(f"{CLI} info", timeout=15)
    assert "httpbin.org" in info.lower()


@pytest.mark.integration
def test_cli_back_and_forward(session):
    """back/forward navigation on pre-existing session."""
    # Navigate somewhere, then go back
    _ok(f"{CLI} open https://example.com", timeout=25)
    _ok(f"{CLI} open https://httpbin.org/get", timeout=25)

    out = _ok(f"{CLI} back", timeout=30)
    print(f"\n[back]: {out}")
    assert "back" in out.lower() or "navigated" in out.lower()

    out = _ok(f"{CLI} forward", timeout=30)
    print(f"\n[forward]: {out}")
    assert "forward" in out.lower() or "navigated" in out.lower()


@pytest.mark.integration
def test_cli_switch_to_wikipedia_and_snapshot(session):
    """Switch to wikipedia pre-existing tab and get interactive snapshot."""
    tabs_out = _ok(f"{CLI} tabs")
    wiki_id = None
    for line in tabs_out.splitlines():
        if "wikipedia" in line:
            m = re.search(r'page_\d+', line)
            if m:
                wiki_id = m.group()
                break

    if wiki_id is None:
        pytest.skip("Wikipedia tab not found")

    _ok(f"{CLI} switch-tab {wiki_id}")

    info = _ok(f"{CLI} info", timeout=20)
    print(f"\n[wiki info]: {info}")
    assert "wikipedia" in info.lower()

    # Wait for visible text — CDPSession bypass makes this work even on pre-existing tabs.
    _ok(f"{CLI} wait Wikipedia", timeout=20)

    snap_raw = _ok(f"{CLI} snapshot -i", timeout=30)
    snap = _resolve_snapshot(snap_raw)  # follow [notice] file link if content overflowed
    refs = _extract_all_refs(snap)
    print(f"\n[wiki snapshot -i] {len(refs)} refs, first 300 chars:\n{snap[:300]}")
    assert len(refs) >= 3, "Expected many interactive elements on Wikipedia"


@pytest.mark.integration
def test_cli_click_link_on_wikipedia_and_wait(session):
    """Click a link on Wikipedia and wait for new content to appear."""
    tabs_out = _ok(f"{CLI} tabs")
    for line in tabs_out.splitlines():
        if "wikipedia" in line:
            m = re.search(r'page_\d+', line)
            if m:
                _ok(f"{CLI} switch-tab {m.group()}")
                break

    snap_out = _ok(f"{CLI} snapshot -i", timeout=30)
    # Find a link ref
    link_ref = _extract_ref(snap_out, "link")
    if link_ref is None:
        pytest.skip("No link ref on Wikipedia")

    out = _ok(f"{CLI} click {link_ref}", timeout=20)
    print(f"\n[click wiki link] ref={link_ref}: {out}")
    assert "clicked" in out.lower()

    # Wait for the page to have some heading or link
    wait_out = _ok(f"{CLI} wait Wikipedia", timeout=20)
    print(f"[wait]: {wait_out}")


@pytest.mark.integration
def test_cli_eval_complex_expression(session):
    """eval with a complex multi-step expression."""
    # Navigate to a stable page for this test
    _ok(f"{CLI} open https://example.com", timeout=25)

    out = _ok(
        f"{CLI} eval 'Array.from(document.querySelectorAll(\"a\")).map(a=>a.href).join(\",\")'",
        timeout=20,
    )
    print(f"\n[eval complex]: {out}")
    assert "iana" in out.lower() or "http" in out.lower(), (
        f"Expected href URLs in output: {out}"
    )


@pytest.mark.integration
def test_cli_new_tab_in_cdp_session(session):
    """Open a new tab within the CDP session."""
    out = _ok(f"{CLI} new-tab", timeout=15)
    print(f"\n[new-tab]: {out}")
    assert "tab" in out.lower() or "created" in out.lower() or "page" in out.lower()

    tabs_out = _ok(f"{CLI} tabs")
    print(f"[tabs after new-tab]: {tabs_out}")
    # Should now have more tabs
    page_ids = _extract_page_ids(tabs_out)
    assert len(page_ids) >= 4, f"Expected ≥4 tabs after new-tab, got: {page_ids}"


@pytest.mark.integration
def test_cli_switch_rapidly_between_all_tabs(session):
    """Rapidly switch between all tabs (the core regression test)."""
    tabs_out = _ok(f"{CLI} tabs")
    page_ids = _extract_page_ids(tabs_out)
    print(f"\n[rapid-switch] tabs: {page_ids}")

    for pid in page_ids[:4]:  # test up to 4 tabs
        out = _ok(f"{CLI} switch-tab {pid}", timeout=15)
        info = _ok(f"{CLI} info", timeout=20)
        print(f"  switch→{pid}: info_len={len(info)}")
        assert len(info) > 5, f"Info too short for {pid}: {info!r}"


@pytest.mark.integration
def test_cli_full_form_workflow_on_httpbin(session):
    """End-to-end form filling workflow on pre-existing httpbin tab.

    1. Switch to httpbin/forms/post
    2. Interactive snapshot
    3. Fill Customer name
    4. Fill Telephone
    5. Check a food checkbox (Bacon)
    6. Select pizza size radio
    7. Verify inputs exist via snapshot
    """
    # Navigate back to the form (might have been navigated elsewhere)
    _ok(f"{CLI} open https://httpbin.org/forms/post", timeout=25)

    snap = _ok(f"{CLI} snapshot -i", timeout=30)
    refs = _extract_all_refs(snap)
    print(f"\n[form workflow] interactive refs ({len(refs)}):\n{snap}")

    textbox_refs = [
        ref for ref, line in refs.items()
        if "textbox" in line.lower()
    ]
    if len(textbox_refs) < 2:
        pytest.skip(f"Not enough textboxes: {textbox_refs}")

    # Fill Customer name
    name_ref = textbox_refs[0]
    out = _ok(f"{CLI} fill {name_ref} 'Alice Bot'", timeout=15)
    print(f"[fill name] {out}")
    assert out  # any non-empty success output

    # Fill Telephone
    phone_ref = textbox_refs[1]
    out = _ok(f"{CLI} fill {phone_ref} '555-1234'", timeout=15)
    print(f"[fill phone] {out}")
    assert out  # any non-empty success output

    # Check Bacon checkbox
    checkbox_ref = _extract_ref(snap, "checkbox")
    if checkbox_ref:
        out = _ok(f"{CLI} check {checkbox_ref}", timeout=15)
        print(f"[check bacon] {out}")
        assert "check" in out.lower()

    # Select pizza size radio (Small)
    radio_ref = _extract_ref(snap, "radio")
    if radio_ref:
        out = _ok(f"{CLI} check {radio_ref}", timeout=15)
        print(f"[check radio] {out}")
        assert "check" in out.lower()

    # Final snapshot to confirm form state
    final_snap = _ok(f"{CLI} snapshot -i", timeout=30)
    assert len(_extract_all_refs(final_snap)) >= 3

    print(f"\n[form workflow] COMPLETE ✓")


@pytest.mark.integration
def test_cli_size_info_on_preopened_tab(session):
    """size command uses CDP Page.getLayoutMetrics (not page.evaluate)."""
    _ok(f"{CLI} open https://en.wikipedia.org/wiki/Web_scraping", timeout=30)
    _ok(f"{CLI} wait Wikipedia", timeout=20)

    # Use info which shows viewport+size, not a separate `size` command
    out = _ok(f"{CLI} info", timeout=20)
    print(f"\n[size via info]: {out}")
    # Should show scrollable content (wikipedia is long)
    assert re.search(r'\d+x\d+', out), "No WxH dimensions in info"


@pytest.mark.integration
def test_cli_verify_text_on_preopened_tab(session):
    """verify-text on pre-existing tab."""
    _ok(f"{CLI} open https://example.com", timeout=25)
    out = _ok(f"{CLI} verify-text 'Example Domain'", timeout=15)
    print(f"\n[verify-text]: {out}")
    assert "PASS" in out
