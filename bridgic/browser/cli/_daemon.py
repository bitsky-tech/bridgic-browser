"""
Bridgic Browser daemon — holds the Browser instance and serves JSON commands
over a platform-appropriate transport (Unix socket on POSIX, TCP loopback on
Windows). One daemon process per run info file.

Start via: python -m bridgic.browser daemon  (internal, not intended for users)

Protocol (newline-delimited JSON):
  Request:  {"command": "open", "args": {"url": "https://example.com"}}
  Response: {"status":"ok","success":true,"result":"...","error_code":null,"data":null,"meta":{}}
            {"status":"error","success":false,"result":"...","error_code":"...","data":{},"meta":{"retryable":false}}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING
from urllib.parse import urlparse

from .._config import _load_config_sources
from .._constants import BRIDGIC_BROWSER_HOME, BRIDGIC_DOWNLOADS_DIR
from ..errors import BridgicBrowserError, InvalidInputError
from ..session._browser import resolve_cdp_input
from ._transport import (
    get_transport,
    read_run_info,
    write_run_info,
    remove_run_info,
)

if TYPE_CHECKING:
    from ..session._browser import Browser

logger = logging.getLogger(__name__)

READY_SIGNAL = "BRIDGIC_DAEMON_READY\n"
STREAM_LIMIT = 16 * 1024 * 1024  # 16 MB — handles large snapshots and fill/eval payloads


_BROWSER_CLOSED_HINT = (
    "Browser window was closed. "
    "Run: bridgic-browser close && bridgic-browser open <url>"
)

# Substrings from Playwright that indicate the browser/page is gone
_BROWSER_CLOSED_PATTERNS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "connection closed",
    "target closed",
)

# Playwright error classes. We match TargetClosedError (and its parent Error)
# via isinstance as the primary signal; the substring match above is a fallback
# for wrapped / non-Playwright exceptions that still carry closed-browser text.
try:
    from playwright.async_api import Error as _PlaywrightError  # type: ignore
except ImportError:  # pragma: no cover — playwright is a hard dependency
    _PlaywrightError = None  # type: ignore[assignment]

try:
    from playwright._impl._errors import TargetClosedError as _TargetClosedError  # type: ignore
except ImportError:  # pragma: no cover
    _TargetClosedError = None  # type: ignore[assignment]


def _is_browser_closed_error(exc: BaseException) -> bool:
    # Primary signal: Playwright raises TargetClosedError when the browser/page
    # is gone. isinstance is robust against Playwright tweaking the message text
    # between releases. We also accept any playwright.async_api.Error whose
    # message matches the known closed-browser substrings.
    if _TargetClosedError is not None and isinstance(exc, _TargetClosedError):
        return True
    msg = str(exc).lower()
    if _PlaywrightError is not None and isinstance(exc, _PlaywrightError):
        if any(pat in msg for pat in _BROWSER_CLOSED_PATTERNS):
            return True
    return any(pat in msg for pat in _BROWSER_CLOSED_PATTERNS)


def _browser_closed_hint(cdp: Optional[str] = None) -> str:
    """Return a BROWSER_CLOSED hint message tailored to the connection mode."""
    if cdp:
        # For local Chrome (localhost/127.0.0.1), show port number instead of the full ws:// URL
        # because the browser UUID in the URL changes on every Chrome restart.
        _parsed = urlparse(cdp)
        _host = (_parsed.hostname or "").lower()
        if _host in ("localhost", "127.0.0.1", "::1"):
            _cdp_hint = str(_parsed.port or 9222)
            _msg = "Local Chrome closed or crashed."
        else:
            _cdp_hint = cdp
            _msg = "Remote browser session closed (the cloud/remote browser disconnected or timed out)."
        return (
            f"{_msg} "
            f"Run: bridgic-browser close && bridgic-browser open <url> --cdp '{_cdp_hint}'"
        )
    return _BROWSER_CLOSED_HINT


def _response(
    *,
    success: bool,
    result: str,
    error_code: Optional[str] = None,
    data: Any = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a machine-readable daemon response."""
    return {
        "status": "ok" if success else "error",
        "success": success,
        "result": result,
        "error_code": error_code,
        "data": data,
        "meta": meta or {},
    }


# ── Navigation ────────────────────────────────────────────────────────────────

async def _handle_open(browser: "Browser", args: Dict[str, Any]) -> str:
    url = args.get("url", "")
    return await browser.navigate_to(url)


async def _handle_back(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.go_back()


async def _handle_forward(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.go_forward()


async def _handle_reload(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.reload_page()


async def _handle_info(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.get_current_page_info()


async def _handle_search(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.search(args.get("query", ""), args.get("engine", "duckduckgo"))


# ── Snapshot ──────────────────────────────────────────────────────────────────

async def _handle_snapshot(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.get_snapshot_text(
        limit=args.get("limit", 10000),
        interactive=args.get("interactive", False),
        full_page=args.get("full_page", True),
        file=args.get("file", None),
    )


# ── Element Interaction ───────────────────────────────────────────────────────

async def _handle_click(browser: "Browser", args: Dict[str, Any]) -> str:
    ref = args.get("ref", "")
    return await browser.click_element_by_ref(ref)


async def _handle_double_click(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.double_click_element_by_ref(args.get("ref", ""))


async def _handle_hover(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.hover_element_by_ref(args.get("ref", ""))


async def _handle_focus(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.focus_element_by_ref(args.get("ref", ""))


async def _handle_fill(browser: "Browser", args: Dict[str, Any]) -> str:
    ref = args.get("ref", "")
    text = args.get("text", "")
    submit = args.get("submit", False)
    return await browser.input_text_by_ref(ref, text, submit=submit)


async def _handle_select(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.select_dropdown_option_by_ref(args.get("ref", ""), args.get("text", ""))


async def _handle_check(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.check_checkbox_or_radio_by_ref(args.get("ref", ""))


async def _handle_uncheck(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.uncheck_checkbox_by_ref(args.get("ref", ""))


async def _handle_scroll_into_view(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.scroll_element_into_view_by_ref(args.get("ref", ""))


async def _handle_drag(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.drag_element_by_ref(args.get("start_ref", ""), args.get("end_ref", ""))


async def _handle_options(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.get_dropdown_options_by_ref(args.get("ref", ""))


async def _handle_upload(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.upload_file_by_ref(args.get("ref", ""), args.get("path", ""))


async def _handle_fill_form(browser: "Browser", args: Dict[str, Any]) -> str:
    fields_raw = args.get("fields", "[]")
    if isinstance(fields_raw, str):
        try:
            fields = json.loads(fields_raw)
        except json.JSONDecodeError as exc:
            raise InvalidInputError(
                f"Invalid JSON for fields: {exc}",
                code="INVALID_JSON_FIELDS",
                details={"fields": fields_raw},
            ) from exc
    else:
        fields = fields_raw
    submit = args.get("submit", False)
    return await browser.fill_form(fields, submit=submit)


# ── Keyboard ──────────────────────────────────────────────────────────────────

async def _handle_press(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.press_key(args.get("key", ""))


async def _handle_type_text(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.type_text(args.get("text", ""), submit=args.get("submit", False))


async def _handle_key_down(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.key_down(args.get("key", ""))


async def _handle_key_up(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.key_up(args.get("key", ""))


# ── Mouse ─────────────────────────────────────────────────────────────────────

async def _handle_scroll(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.mouse_wheel(delta_x=args.get("delta_x", 0), delta_y=args.get("delta_y", 0))


async def _handle_mouse_move(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.mouse_move(args.get("x", 0.0), args.get("y", 0.0))


async def _handle_mouse_click(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.mouse_click(
        args.get("x", 0.0),
        args.get("y", 0.0),
        button=args.get("button", "left"),
        click_count=args.get("count", 1),
    )


async def _handle_mouse_drag(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.mouse_drag(
        args.get("x1", 0.0),
        args.get("y1", 0.0),
        args.get("x2", 0.0),
        args.get("y2", 0.0),
    )


async def _handle_mouse_down(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.mouse_down(button=args.get("button", "left"))


async def _handle_mouse_up(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.mouse_up(button=args.get("button", "left"))


# ── Wait ──────────────────────────────────────────────────────────────────────

async def _handle_wait(browser: "Browser", args: Dict[str, Any]) -> str:
    kwargs: dict = {
        "time_seconds": args.get("seconds"),
        "text": args.get("text"),
        "text_gone": args.get("text_gone"),
    }
    if "timeout" in args:
        kwargs["timeout"] = float(args["timeout"])
    return await browser.wait_for(**kwargs)


# ── Tabs ──────────────────────────────────────────────────────────────────────

async def _handle_tabs(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.get_tabs()


async def _handle_new_tab(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.new_tab(url=args.get("url"))


async def _handle_switch_tab(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.switch_tab(args.get("page_id", ""))


async def _handle_close_tab(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.close_tab(page_id=args.get("page_id"))


# ── Capture ───────────────────────────────────────────────────────────────────

async def _handle_screenshot(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.take_screenshot(
        filename=args.get("path"),
        full_page=args.get("full_page", False),
    )


async def _handle_pdf(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.save_pdf(filename=args.get("path"))


# ── Network ───────────────────────────────────────────────────────────────────

async def _handle_network_start(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.start_network_capture()


async def _handle_network_stop(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.stop_network_capture()


async def _handle_network(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.get_network_requests(
        include_static=args.get("include_static", False),
        clear=args.get("clear", True),
    )


async def _handle_wait_network(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.wait_for_network_idle(timeout=args.get("timeout", 30.0))


# ── Dialog ────────────────────────────────────────────────────────────────────

async def _handle_dialog_setup(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.setup_dialog_handler(
        default_action=args.get("action", "accept"),
        default_prompt_text=args.get("text"),
    )


async def _handle_dialog(browser: "Browser", args: Dict[str, Any]) -> str:
    accept = not args.get("dismiss", False)
    return await browser.handle_dialog(accept=accept, prompt_text=args.get("text"))


async def _handle_dialog_remove(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.remove_dialog_handler()


# ── Storage ───────────────────────────────────────────────────────────────────

async def _handle_storage_save(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.save_storage_state(filename=args.get("path"))


async def _handle_storage_load(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.restore_storage_state(args.get("path", ""))


async def _handle_cookies_clear(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.clear_cookies(
        name=args.get("name"),
        domain=args.get("domain"),
        path=args.get("path"),
    )


async def _handle_cookies(browser: "Browser", args: Dict[str, Any]) -> str:
    url = args.get("url")
    urls = [url] if url else None
    return await browser.get_cookies(
        urls=urls,
        name=args.get("name"),
        domain=args.get("domain"),
        path=args.get("path"),
    )


async def _handle_cookie_set(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.set_cookie(
        name=args.get("name", ""),
        value=args.get("value", ""),
        url=args.get("url"),
        domain=args.get("domain"),
        path=args.get("path", "/"),
        expires=args.get("expires"),
        http_only=args.get("http_only", False),
        secure=args.get("secure", False),
        same_site=args.get("same_site"),
    )


# ── Verify ────────────────────────────────────────────────────────────────────

async def _handle_verify_visible(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.verify_element_visible(
        role=args.get("role", ""),
        accessible_name=args.get("name", ""),
        timeout=args.get("timeout", 5.0),
    )


async def _handle_verify_text(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.verify_text_visible(
        text=args.get("text", ""),
        exact=args.get("exact", False),
        timeout=args.get("timeout", 5.0),
    )


async def _handle_verify_value(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.verify_value(args.get("ref", ""), args.get("expected", ""))


async def _handle_verify_state(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.verify_element_state(args.get("ref", ""), args.get("state", ""))


async def _handle_verify_url(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.verify_url(args.get("url", ""), exact=args.get("exact", False))


async def _handle_verify_title(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.verify_title(args.get("title", ""), exact=args.get("exact", False))


# ── Evaluate ──────────────────────────────────────────────────────────────────

async def _handle_eval(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.evaluate_javascript(args.get("code", ""))


async def _handle_eval_on(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.evaluate_javascript_on_ref(args.get("ref", ""), args.get("code", ""))


# ── Developer ─────────────────────────────────────────────────────────────────

async def _handle_console_start(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.start_console_capture()


async def _handle_console_stop(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.stop_console_capture()


async def _handle_console(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.get_console_messages(
        type_filter=args.get("filter"),
        clear=args.get("clear", True),
    )


async def _handle_trace_start(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.start_tracing(
        screenshots=not args.get("no_screenshots", False),
        snapshots=not args.get("no_snapshots", False),
    )


async def _handle_trace_stop(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.stop_tracing(filename=args.get("path"))


async def _handle_trace_chunk(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.add_trace_chunk(title=args.get("title"))


async def _handle_video_start(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.start_video(
        width=args.get("width"),
        height=args.get("height"),
    )


async def _handle_video_stop(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.stop_video(filename=args.get("path"))


# ── Lifecycle ─────────────────────────────────────────────────────────────────

# Note: there is no `_handle_close` here. The connection handler intercepts
# the `close` command directly (see the `if command == "close"` branch
# below) so it can pre-allocate the close-session directory and respond to
# the client *before* the actual browser teardown runs in the background.
# Adding a `_HANDLERS["close"]` entry would be dead code.


async def _handle_resize(browser: "Browser", args: Dict[str, Any]) -> str:
    return await browser.browser_resize(args.get("width", 1280), args.get("height", 720))


_HANDLERS = {
    # Navigation
    "open": _handle_open,
    "back": _handle_back,
    "forward": _handle_forward,
    "reload": _handle_reload,
    "info": _handle_info,
    "search": _handle_search,
    # Snapshot
    "snapshot": _handle_snapshot,
    # Element Interaction
    "click": _handle_click,
    "double_click": _handle_double_click,
    "hover": _handle_hover,
    "focus": _handle_focus,
    "fill": _handle_fill,
    "select": _handle_select,
    "check": _handle_check,
    "uncheck": _handle_uncheck,
    "scroll_into_view": _handle_scroll_into_view,
    "drag": _handle_drag,
    "options": _handle_options,
    "upload": _handle_upload,
    "fill_form": _handle_fill_form,
    # Keyboard
    "press": _handle_press,
    "type_text": _handle_type_text,
    "key_down": _handle_key_down,
    "key_up": _handle_key_up,
    # Mouse
    "scroll": _handle_scroll,
    "mouse_move": _handle_mouse_move,
    "mouse_click": _handle_mouse_click,
    "mouse_drag": _handle_mouse_drag,
    "mouse_down": _handle_mouse_down,
    "mouse_up": _handle_mouse_up,
    # Wait
    "wait": _handle_wait,
    # Tabs
    "tabs": _handle_tabs,
    "new_tab": _handle_new_tab,
    "switch_tab": _handle_switch_tab,
    "close_tab": _handle_close_tab,
    # Capture
    "screenshot": _handle_screenshot,
    "pdf": _handle_pdf,
    # Network
    "network_start": _handle_network_start,
    "network_stop": _handle_network_stop,
    "network": _handle_network,
    "wait_network": _handle_wait_network,
    # Dialog
    "dialog_setup": _handle_dialog_setup,
    "dialog": _handle_dialog,
    "dialog_remove": _handle_dialog_remove,
    # Storage
    "storage_save": _handle_storage_save,
    "storage_load": _handle_storage_load,
    "cookies_clear": _handle_cookies_clear,
    "cookies": _handle_cookies,
    "cookie_set": _handle_cookie_set,
    # Verify
    "verify_visible": _handle_verify_visible,
    "verify_text": _handle_verify_text,
    "verify_value": _handle_verify_value,
    "verify_state": _handle_verify_state,
    "verify_url": _handle_verify_url,
    "verify_title": _handle_verify_title,
    # Evaluate
    "eval": _handle_eval,
    "eval_on": _handle_eval_on,
    # Developer
    "console_start": _handle_console_start,
    "console_stop": _handle_console_stop,
    "console": _handle_console,
    "trace_start": _handle_trace_start,
    "trace_stop": _handle_trace_stop,
    "trace_chunk": _handle_trace_chunk,
    "video_start": _handle_video_start,
    "video_stop": _handle_video_stop,
    # Lifecycle
    # ("close" is intercepted in the connection handler — see comment above
    # the lifecycle section.)
    "resize": _handle_resize,
}


async def _cdp_reconnect(browser: "Browser") -> bool:
    """Stop and restart *browser* to re-establish a dropped CDP/PW-WS connection.

    Returns True if the reconnect succeeded, False otherwise.
    After a successful reconnect the browser is at about:blank (new session).

    Implementation note: calls ``browser._start()`` (private) because there
    is no public ``reconnect()`` API. This is intentional — reconnect is a
    daemon-only concern.  If ``_start()``'s preconditions change, this
    function must be updated accordingly.
    """
    # Cancel any in-flight snapshot prefetch BEFORE close(). close() also
    # cancels prefetch, but if it raises mid-way (line before _cancel_prefetch)
    # the prefetch task survives and later touches a dead browser — producing
    # spurious errors in the reconnect window. Cancelling up-front is cheap
    # and idempotent.
    try:
        browser._cancel_prefetch()
    except Exception as exc:
        logger.debug("[daemon] cdp_reconnect: _cancel_prefetch error (ignored): %s", exc)

    try:
        await browser.close()
    except Exception as exc:
        logger.debug("[daemon] cdp_reconnect: close() error (ignored): %s", exc)

    # Force-reset internal handles so `_start()`'s early-return guard
    # (`if self._playwright is not None: return`) cannot silently skip the
    # reconnect when close() has raised mid-flight and left handles set.  We
    # accept a potential driver leak here — the close() attempt above
    # handles cleanup; these assignments are just insurance against partial
    # close state.
    browser._playwright = None
    browser._browser = None
    browser._context = None
    browser._page = None

    try:
        await browser._start()
        logger.info("[daemon] cdp_reconnect: reconnected successfully")
        return True
    except Exception as exc:
        logger.error("[daemon] cdp_reconnect: _start() failed: %s", exc)
        return False


# Commands that exceed this wall-clock duration get a WARN in daemon logs —
# the CLI's default socket read-timeout is 90s, so anything approaching that
# is a candidate cause for "CLI froze" user reports. The actual response is
# unaffected; this is purely observability.
_SLOW_COMMAND_THRESHOLD_S = 60.0


async def _dispatch(browser: "Browser", command: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap :func:`_dispatch_inner` with start/end timing logs.

    Emits matched ``[CLI-CMD] <cmd> start`` and
    ``[CLI-RESP] <cmd> (ok|err) in X.XXXs`` pairs so every command a client
    sends is visible in ``daemon.log`` with a duration — the primary
    affordance for diagnosing "CLI appeared frozen" issues.
    """
    t0 = time.monotonic()
    args_keys = sorted(args.keys()) if args else []
    logger.info("[CLI-CMD] %s start args_keys=%s", command, args_keys)

    response = await _dispatch_inner(browser, command, args)

    elapsed = time.monotonic() - t0
    success = bool(response.get("success"))
    log_fn = logger.warning if elapsed >= _SLOW_COMMAND_THRESHOLD_S else logger.info
    log_fn(
        "[CLI-RESP] %s %s in %.3fs error_code=%s",
        command,
        "ok" if success else "err",
        elapsed,
        response.get("error_code"),
    )
    return response


async def _dispatch_inner(browser: "Browser", command: str, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = _HANDLERS.get(command)
    if handler is None:
        return _response(
            success=False,
            result=f"Unknown command: {command!r}",
            error_code="UNKNOWN_COMMAND",
        )

    cdp: Optional[str] = getattr(browser, "_cdp_resolved", None)
    # C2 short-circuit: if close() has already published its sentinel,
    # reject the dispatch immediately. The `close` command itself is
    # allowed through so repeated close calls remain idempotent.
    # Use ``is True`` (not truthiness) so MagicMock-based test fixtures
    # that leave `_closing` as an auto-attribute don't accidentally trip.
    if command != "close" and getattr(browser, "_closing", False) is True:
        return _response(
            success=False,
            result=_browser_closed_hint(cdp),
            error_code="BROWSER_CLOSED",
        )
    # In CDP mode, attempt one automatic reconnect when the remote session drops.
    # This helps with cloud-browser session timeouts (Browserless, Steel.dev, etc.).
    # We do NOT reconnect for `close` (shutdown intent) or if there is no CDP URL.
    _max_attempts = 2 if (cdp and command != "close") else 1

    for _attempt in range(_max_attempts):
        try:
            result = await handler(browser, args)
            return _response(
                success=True,
                result=str(result),
            )
        except BridgicBrowserError as exc:
            if _is_browser_closed_error(exc):
                if _attempt == 0 and _max_attempts > 1:
                    logger.warning(
                        "[daemon] CDP session closed during %r, attempting one-shot reconnect",
                        command,
                    )
                    if await _cdp_reconnect(browser):
                        continue  # retry the command with the refreshed connection
                return _response(
                    success=False,
                    result=_browser_closed_hint(cdp),
                    error_code="BROWSER_CLOSED",
                )
            return _response(
                success=False,
                result=exc.message,
                error_code=exc.code,
                data=exc.details,
                meta={"retryable": exc.retryable},
            )
        except Exception as exc:
            if _is_browser_closed_error(exc):
                if _attempt == 0 and _max_attempts > 1:
                    logger.warning(
                        "[daemon] CDP session closed during %r, attempting one-shot reconnect",
                        command,
                    )
                    if await _cdp_reconnect(browser):
                        continue  # retry
                return _response(
                    success=False,
                    result=_browser_closed_hint(cdp),
                    error_code="BROWSER_CLOSED",
                )
            logger.exception("[daemon] command=%s error", command)
            return _response(
                success=False,
                result=str(exc),
                error_code="HANDLER_EXCEPTION",
            )
    # Unreachable: every iteration of the loop above always returns. The body
    # only `continue`s on a successful reconnect, and the *retried* iteration
    # itself either returns success or returns one of the BROWSER_CLOSED /
    # HANDLER_EXCEPTION responses. Kept as a defensive safety net so that if
    # a future edit accidentally adds a code path that exits the loop without
    # returning, the daemon still answers the client with a clean error.
    return _response(
        success=False,
        result=_browser_closed_hint(cdp),
        error_code="BROWSER_CLOSED",
    )


_READ_TIMEOUT = 60.0  # seconds to wait for a command line from the client
# Global safety-net timeout for browser.close(). The large value (300s)
# accommodates worst-case video finalization (ffmpeg encoding). In practice
# individual cleanup steps have their own shorter timeouts (video finalize
# 30s, context close 15s, etc.), so the full 300s is never reached during
# normal operation.
try:
    _DAEMON_STOP_TIMEOUT = float(os.environ.get("BRIDGIC_DAEMON_STOP_TIMEOUT", "300"))
except (ValueError, TypeError):
    _DAEMON_STOP_TIMEOUT = 300.0


def _setup_signal_handlers(stop_event: asyncio.Event) -> None:
    """Register SIGTERM/SIGINT to set the stop_event, cross-platform."""
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)
        loop.add_signal_handler(signal.SIGINT, stop_event.set)
    else:
        def _handler(_signum: int, _frame: object) -> None:
            loop.call_soon_threadsafe(stop_event.set)
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)


async def _handle_connection(
    browser: "Browser",
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    stop_event: asyncio.Event,
    *,
    token_verifier: Optional[Callable[[Dict[str, Any]], bool]] = None,
    on_close_command: Optional[Callable[[], None]] = None,
) -> None:
    try:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("[daemon] client connected but sent no command within %.0fs, closing", _READ_TIMEOUT)
            return
        if not raw:
            return
        try:
            req = json.loads(raw.decode(errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            resp = _response(
                success=False,
                result=f"Invalid JSON: {exc}",
                error_code="INVALID_JSON",
            )
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            return

        if not isinstance(req, dict):
            resp = _response(
                success=False,
                result="Invalid request: payload must be a JSON object.",
                error_code="INVALID_REQUEST",
            )
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            return

        if token_verifier is not None and not token_verifier(req):
            resp = _response(
                success=False,
                result="Unauthorized",
                error_code="UNAUTHORIZED",
            )
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            return

        req.pop("_token", None)  # strip auth token before dispatch

        command = req.get("command", "")
        args = req.get("args", {})
        if not isinstance(command, str) or not command.strip():
            resp = _response(
                success=False,
                result="Invalid request: 'command' must be a non-empty string.",
                error_code="INVALID_REQUEST",
            )
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            return
        if not isinstance(args, dict):
            resp = _response(
                success=False,
                result="Invalid request: 'args' must be a JSON object.",
                error_code="INVALID_REQUEST",
            )
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            return

        if command == "close":
            # Pre-allocate session dir + artifact paths; respond immediately
            try:
                artifacts = browser.inspect_pending_close_artifacts()
            except Exception as exc:
                logger.warning(f"[close] inspect_pending_close_artifacts failed: {exc}")
                artifacts = {"session_dir": "", "trace": [], "video": []}
            session_dir = artifacts.get("session_dir") or ""

            lines = ["Browser closing in background."]
            if artifacts["trace"]:
                lines.append("Trace (generating in background, check later):")
                lines.extend(f"  {p}" for p in artifacts["trace"])
            if artifacts["video"]:
                lines.append("Video (generating in background, check later):")
                lines.extend(f"  {p}" for p in artifacts["video"])
            # The close-report is only written when there is at least one
            # artifact (otherwise we would leak an empty session dir per
            # close call). Only advertise the path when it actually exists.
            if session_dir:
                lines.append(
                    f"Close report (generating in background, check later): "
                    f"{session_dir}/close-report.json"
                )

            resp = _response(success=True, result="\n".join(lines))
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            # Stop accepting new connections IMMEDIATELY (before browser.close
            # begins) so a fresh `bridgic-browser open …` does not land on this
            # dying daemon and trigger the close-mid-dispatch race.  The close
            # callback also sets stop_event so the main loop can proceed to
            # actually close the browser.
            if on_close_command is not None:
                on_close_command()
            else:
                stop_event.set()
            return

        # Run dispatch concurrently with an EOF watcher.  If the client
        # closes the socket while the dispatch is in flight (e.g. CLI hit
        # its own response timeout), cancel the in-flight task so it does
        # not continue running against the Browser singleton — otherwise
        # the next CLI invocation would race against the orphaned task.
        dispatch_task = asyncio.create_task(_dispatch(browser, command, args))
        disconnect_task = asyncio.create_task(reader.read())
        try:
            done, _pending = await asyncio.wait(
                {dispatch_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            dispatch_task.cancel()
            disconnect_task.cancel()
            raise

        if disconnect_task in done and dispatch_task not in done:
            logger.warning(
                "[daemon] client disconnected mid-request; cancelling '%s'",
                command,
            )
            dispatch_task.cancel()
            try:
                await dispatch_task
            except (asyncio.CancelledError, Exception):
                pass
            return

        # Dispatch finished first — cancel the dangling EOF watcher.
        if not disconnect_task.done():
            disconnect_task.cancel()
            try:
                await disconnect_task
            except (asyncio.CancelledError, Exception):
                pass

        resp = dispatch_task.result()
        writer.write((json.dumps(resp) + "\n").encode())
        await writer.drain()
    except Exception:
        logger.exception("[daemon] connection handler error")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _write_close_report(
    browser: "Browser",
    *,
    timed_out: bool = False,
    stop_exc: Optional[Exception] = None,
) -> None:
    """Write close-report.json to the browser's close session directory."""
    session_dir = getattr(browser, "_close_session_dir", None)
    if not session_dir:
        return

    from datetime import datetime, timezone

    artifacts = getattr(browser, "_last_shutdown_artifacts", {})
    errors = list(getattr(browser, "_last_shutdown_errors", []))
    if timed_out:
        errors.append(f"browser.close() timed out after {_DAEMON_STOP_TIMEOUT:.0f}s")
    if stop_exc is not None:
        errors.append(str(stop_exc))

    if timed_out:
        status = "timeout"
    elif errors:
        all_timeouts = all("timeout after" in e.lower() for e in errors)
        status = "success_with_timeouts" if all_timeouts else "error"
    else:
        status = "success"
    report = {
        "status": status,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "trace_paths": artifacts.get("trace", []),
        "video_paths": artifacts.get("video", []),
        "warnings": [],
        "errors": errors,
    }

    report_path = Path(session_dir) / "close-report.json"
    try:
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("[daemon] close-report written: %s", report_path)
    except Exception as exc:
        logger.warning("[daemon] failed to write close-report.json: %s", exc)


def _resolve_default_downloads_dir() -> Path:
    """Pick the best default downloads directory for the daemon.

    Strategy: prefer ~/Downloads (user-familiar), fall back to
    ~/.bridgic/bridgic-browser/downloads/ if ~/Downloads is not
    writable or cannot be created.
    """
    user_downloads = Path.home() / "Downloads"
    try:
        user_downloads.mkdir(parents=True, exist_ok=True)
        if os.access(str(user_downloads), os.W_OK):
            return user_downloads
    except OSError:
        pass

    BRIDGIC_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "[daemon] ~/Downloads not writable, using fallback: %s",
        BRIDGIC_DOWNLOADS_DIR,
    )
    return BRIDGIC_DOWNLOADS_DIR


def _probe_ws_reachable(ws_url: str, timeout: float = 1.5) -> None:
    """Best-effort TCP probe: verify the ws:// target's host:port is reachable.

    Raises ``ConnectionError`` with a user-friendly message when the target
    rejects the connection or is unreachable within *timeout*.  Used to
    catch stale ``BRIDGIC_CDP`` values pointing at a dead browser *before*
    Playwright's ``connect_over_cdp`` produces an opaque error.  A TCP
    accept is NOT a guarantee that the CDP handshake will succeed, but a
    refused/timed-out connection is a definite failure — so this probe
    only catches the clear-cut bad case and lets ambiguous ones through
    for Playwright to handle.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(ws_url)
    host = parsed.hostname
    # Default CDP WebSocket port is the Chrome DevTools port, typically 9222.
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    if not host:
        return  # malformed URL — defer to Playwright's own error handling
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (OSError, socket.timeout) as exc:
        raise ConnectionError(
            f"CDP target {host}:{port} unreachable — {exc}. "
            f"The browser may have exited since BRIDGIC_CDP was set; "
            f"re-run with a fresh --cdp or clear the env var."
        ) from exc


def _resolve_cdp_url_from_env(cdp_input: Optional[str]) -> Optional[str]:
    """Resolve ``BRIDGIC_CDP`` env value to a ws:// URL.

    Short-circuits when the input is already a ws:// / wss:// URL — the CLI
    client pre-resolves ``--cdp`` and injects the ws URL into the daemon's
    env, and re-running ``resolve_cdp_input`` on it would only bring the risk
    of client/daemon parsing drift.  Bare ports / ``auto`` still flow through
    ``resolve_cdp_input`` so ``BRIDGIC_CDP=9222`` from a shell keeps working.

    ws:// inputs get a quick TCP probe so a stale env value (browser exited
    after the CLI cached it) fails fast with a clear message rather than
    going straight to Playwright's ``connect_over_cdp`` and hanging on the
    handshake.
    """
    if not cdp_input:
        return None
    # I4 invariant: every ws:// / wss:// short-circuit branch MUST call
    # `_probe_ws_reachable()` before returning. Skipping the probe here
    # defeats the stale-env protection and reintroduces the Playwright
    # connect_over_cdp hang. The tests in
    # `tests/unit/test_daemon_cdp_env.py` lock this contract in.
    if cdp_input.lower().startswith(("ws://", "wss://")):
        try:
            _probe_ws_reachable(cdp_input)
        except ConnectionError as exc:
            raise RuntimeError(
                f"Failed to establish CDP connection: {exc}\n"
                "Check that the browser is still running with "
                "--remote-debugging-port or re-run with a fresh --cdp value."
            ) from exc
        return cdp_input
    # N3: `resolve_cdp_input` is imported at module scope (top of file) so
    # tests can patch `bridgic.browser.cli._daemon.resolve_cdp_input`
    # reliably. Patching the source module wouldn't affect the daemon's
    # local binding if we kept the import inside the function body.
    try:
        return resolve_cdp_input(cdp_input)
    except (RuntimeError, ValueError, ConnectionError) as exc:
        raise RuntimeError(
            f"Failed to establish CDP connection: {exc}\n"
            "Check that the browser is running with --remote-debugging-port "
            "or that the CDP URL / port is correct."
        ) from exc


async def run_daemon() -> None:
    from bridgic.browser.session._browser import Browser

    # Resolve CDP connection if requested via env var.
    cdp: Optional[str] = _resolve_cdp_url_from_env(os.environ.get("BRIDGIC_CDP"))

    # Browser.__init__ auto-loads config from files and env vars.
    kwargs: Dict[str, Any] = {}
    if cdp:
        kwargs["cdp"] = cdp

    # Auto-enable downloads in daemon mode.
    # SDK users are unaffected (they control downloads_path explicitly).
    if "downloads_path" not in kwargs:
        _cfg_check = _load_config_sources()
        if "downloads_path" not in _cfg_check:
            kwargs["downloads_path"] = str(_resolve_default_downloads_dir())

    browser = Browser(**kwargs)
    logger.info("[daemon] browser ready (lazy start, config=%s)", {k: v for k, v in browser.get_config().items() if k != "proxy"})

    stop_event = asyncio.Event()
    shutdown_started = asyncio.Event()
    transport = get_transport()

    # Reference populated after start_server() returns; close() can be called
    # multiple times safely.
    server_holder: Dict[str, Any] = {}

    def _begin_shutdown() -> None:
        """Stop accepting new connections and trigger main-loop exit.

        Called from the close-command branch in _handle_connection.  Idempotent
        — safe to invoke more than once.
        """
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        srv = server_holder.get("server")
        if srv is not None:
            try:
                srv.close()
            except Exception:
                logger.debug("[daemon] server.close() during shutdown raised", exc_info=True)
        stop_event.set()

    async def connection_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Fast-path reject: new connections that land while the daemon is
        # already shutting down (e.g. `close` → `open` back-to-back) get a
        # clear "shutting down" error instead of a mid-dispatch crash.
        if shutdown_started.is_set():
            try:
                resp = _response(
                    success=False,
                    result="Daemon is shutting down; please retry.",
                    error_code="DAEMON_SHUTTING_DOWN",
                    meta={"retryable": True},
                )
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            return
        await _handle_connection(
            browser, reader, writer, stop_event,
            token_verifier=transport.verify_auth,
            on_close_command=_begin_shutdown,
        )

    server = await transport.start_server(connection_cb, stream_limit=STREAM_LIMIT)
    server_holder["server"] = server
    write_run_info(transport.build_run_info(pid=os.getpid()))

    # Signal ready to parent process
    sys.stdout.write(READY_SIGNAL)
    sys.stdout.flush()
    logger.info("[daemon] ready")

    _setup_signal_handlers(stop_event)

    async with server:
        await stop_event.wait()

    logger.info("[daemon] shutting down")
    _stop_timed_out = False
    _stop_exc: Optional[Exception] = None
    try:
        await asyncio.wait_for(browser.close(), timeout=_DAEMON_STOP_TIMEOUT)
    except asyncio.TimeoutError:
        _stop_timed_out = True
        logger.error("[daemon] browser.close() timed out after %.0fs", _DAEMON_STOP_TIMEOUT)
    except Exception as exc:
        _stop_exc = exc
        logger.exception("[daemon] browser.close() failed during shutdown")

    _write_close_report(browser, timed_out=_stop_timed_out, stop_exc=_stop_exc)

    # Only clean up the socket and run-info if this daemon is still the owner.
    # Primary race: `close` responds immediately and a new daemon can start
    # (write_run_info + bind socket) before browser.close() finishes here.
    # If we blindly call cleanup/remove_run_info we would delete the new
    # daemon's socket file and run-info, causing the next command to spawn
    # yet another daemon (with no page) and return NO_BROWSER_SESSION.
    #
    # Residual micro-race: there is a tiny window between read_run_info() and
    # transport.cleanup() where a new daemon could start and write its run-info.
    # This window spans one stat+unlink syscall pair — ~low-ms under typical
    # fs conditions (disk cache/contention can widen it) vs. the primary race
    # which spans the entire browser.close() call — often seconds. Eliminating
    # it would require OS-level atomic file locking; the practical risk of
    # hitting this window is negligible.
    current_info = read_run_info()
    if current_info is None or current_info.get("pid") == os.getpid():
        transport.cleanup()
        remove_run_info()
    else:
        logger.info(
            "[daemon] skipping cleanup — run info belongs to pid=%s (ours=%d)",
            current_info.get("pid"), os.getpid(),
        )


DAEMON_LOG_PATH = BRIDGIC_BROWSER_HOME / "logs" / "daemon.log"


def _setup_daemon_logging() -> None:
    """Configure daemon logging with both file and stderr output.

    Writes bridgic.browser logs (DEBUG+) to ~/.bridgic/bridgic-browser/logs/daemon.log so
    that diagnostics are preserved even after the parent process closes the
    stdout pipe. Stderr still gets WARNING+ for immediate visibility.
    """
    from logging.handlers import RotatingFileHandler

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter(
        "[%(levelname)s] %(message)s",
    ))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    root.addHandler(stderr_handler)

    bridgic_logger = logging.getLogger("bridgic.browser")
    bridgic_logger.setLevel(logging.DEBUG)
    bridgic_logger.propagate = True
    bridgic_logger.handlers.clear()

    try:
        log_dir = DAEMON_LOG_PATH.parent
        log_dir.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            str(DAEMON_LOG_PATH),
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=1,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    except Exception as exc:
        logger.warning(
            "[daemon] failed to initialize file logging at %s: %s",
            DAEMON_LOG_PATH,
            exc,
        )
        return

    bridgic_logger.addHandler(file_handler)


def main() -> None:
    _setup_daemon_logging()
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
