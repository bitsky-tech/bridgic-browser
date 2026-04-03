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
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from .._constants import BRIDGIC_BROWSER_HOME
from ..errors import BridgicBrowserError, InvalidInputError
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


def _is_browser_closed_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(pat in msg for pat in _BROWSER_CLOSED_PATTERNS)


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
    return await browser.wait_for(
        time_seconds=args.get("seconds"),
        text=args.get("text"),
        text_gone=args.get("text_gone"),
    )


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

async def _handle_close(browser: "Browser", _args: Dict[str, Any]) -> str:
    return await browser.close()


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
    "close": _handle_close,
    "resize": _handle_resize,
}


async def _dispatch(browser: "Browser", command: str, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = _HANDLERS.get(command)
    if handler is None:
        return _response(
            success=False,
            result=f"Unknown command: {command!r}",
            error_code="UNKNOWN_COMMAND",
        )
    try:
        result = await handler(browser, args)
        return _response(
            success=True,
            result=str(result),
        )
    except BridgicBrowserError as exc:
        if _is_browser_closed_error(exc):
            return _response(
                success=False,
                result=_BROWSER_CLOSED_HINT,
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
            return _response(
                success=False,
                result=_BROWSER_CLOSED_HINT,
                error_code="BROWSER_CLOSED",
            )
        logger.exception("[daemon] command=%s error", command)
        return _response(
            success=False,
            result=str(exc),
            error_code="HANDLER_EXCEPTION",
        )


_READ_TIMEOUT = 60.0  # seconds to wait for a command line from the client
try:
    _DAEMON_STOP_TIMEOUT = float(os.environ.get("BRIDGIC_DAEMON_STOP_TIMEOUT", "45"))
except (ValueError, TypeError):
    _DAEMON_STOP_TIMEOUT = 45.0


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
            # Close the browser synchronously so Chrome's SingletonLock on
            # user_data_dir is released before responding to the client.
            # This ensures a subsequent ``open`` command can start a new
            # browser immediately without hitting a profile lock error.
            try:
                artifacts = browser.inspect_pending_close_artifacts()
            except Exception as exc:
                logger.warning(f"[close] inspect_pending_close_artifacts failed: {exc}")
                artifacts = {"session_dir": None, "trace": [], "video": []}
            session_dir = artifacts.get("session_dir") or "(unknown)"

            _close_timed_out = False
            _close_exc: Optional[Exception] = None
            try:
                close_result = await asyncio.wait_for(
                    browser.close(), timeout=_DAEMON_STOP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                _close_timed_out = True
                close_result = f"Browser close timed out after {_DAEMON_STOP_TIMEOUT:.0f}s"
                logger.error("[daemon] browser.close() timed out during close command")
            except Exception as exc:
                _close_exc = exc
                close_result = f"Browser close failed: {exc}"
                logger.exception("[daemon] browser.close() failed during close command")

            _write_close_report(browser, timed_out=_close_timed_out, stop_exc=_close_exc)

            lines = [close_result]
            if artifacts["trace"]:
                lines.append("Trace:")
                lines.extend(f"  {p}" for p in artifacts["trace"])
            if artifacts["video"]:
                lines.append("Video:")
                lines.extend(f"  {p}" for p in artifacts["video"])
            lines.append(f"Close report: {session_dir}/close-report.json")

            resp = _response(success=True, result="\n".join(lines))
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            stop_event.set()
            return

        resp = await _dispatch(browser, command, args)
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


async def run_daemon() -> None:
    from bridgic.browser.session._browser import Browser

    # Browser.__init__ auto-loads config from files and env vars.
    browser = Browser()
    logger.info("[daemon] browser ready (lazy start, config=%s)", {k: v for k, v in browser.get_config().items() if k != "proxy"})

    stop_event = asyncio.Event()
    transport = get_transport()

    async def connection_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(
            browser, reader, writer, stop_event,
            token_verifier=transport.verify_auth,
        )

    server = await transport.start_server(connection_cb, stream_limit=STREAM_LIMIT)
    write_run_info(transport.build_run_info(pid=os.getpid()))

    # Signal ready to parent process
    sys.stdout.write(READY_SIGNAL)
    sys.stdout.flush()
    logger.info("[daemon] ready")

    _setup_signal_handlers(stop_event)

    async with server:
        await stop_event.wait()

    logger.info("[daemon] shutting down")
    # browser.close() is normally called in the close command handler before
    # responding to the client (so SingletonLock is released immediately).
    # Only call it here if it wasn't already done (e.g. daemon stopped by
    # SIGTERM/SIGINT instead of the close command).
    if browser._playwright is not None:
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
    # The close command now calls browser.close() synchronously before
    # responding, so the primary race (new daemon starts while old Chrome is
    # still running) is mitigated.  However, the guard is still needed for
    # edge cases: SIGTERM-based shutdown, daemon stop timeout, or a new
    # daemon that was spawned for a different reason during shutdown.
    #
    # Residual micro-race: there is a tiny window between read_run_info() and
    # transport.cleanup() where a new daemon could start and write its run-info.
    # This window is measured in microseconds; the practical risk is negligible.
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
