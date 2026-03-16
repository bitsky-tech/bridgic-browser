"""
Bridgic Browser daemon — holds the Browser instance and serves JSON commands
over a Unix domain socket. One daemon process per socket path.

Start via: python -m bridgic.browser daemon  (internal, not intended for users)

Protocol (newline-delimited JSON):
  Request:  {"command": "open", "args": {"url": "https://example.com"}}
  Response: {"status": "ok", "result": "Navigated to: https://example.com"}
            {"status": "error", "result": "...error message..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .._constants import BRIDGIC_HOME

logger = logging.getLogger(__name__)


class _DaemonCommandError(RuntimeError):
    """Expected command-level failure with stable machine-readable code."""

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


def _default_socket_path() -> str:
    """Return per-user default socket path."""
    return str(BRIDGIC_HOME / "run" / "bridgic-browser.sock")


SOCKET_PATH = os.environ.get("BRIDGIC_SOCKET", _default_socket_path())
READY_SIGNAL = "BRIDGIC_DAEMON_READY\n"
STREAM_LIMIT = 16 * 1024 * 1024  # 16 MB — handles large snapshots and fill/eval payloads


def _ensure_socket_parent_dir(path: str) -> None:
    """Create socket parent dir with private permissions when possible."""
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        # Custom socket directories (e.g. /tmp) may not be chmod-able by us.
        pass


def _safe_remove_socket(path: str) -> None:
    """Remove a socket file only if it is owned by the current user."""
    socket_path = Path(path)
    try:
        st = socket_path.stat()
    except FileNotFoundError:
        return

    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"Refusing to remove non-socket path: {path}")

    if hasattr(os, "getuid"):
        current_uid = os.getuid()
        if st.st_uid != current_uid:
            raise PermissionError(
                f"Socket path {path} is owned by uid={st.st_uid}, current uid={current_uid}"
            )

    try:
        socket_path.unlink()
    except FileNotFoundError:
        # Another process may remove the socket between stat() and unlink().
        return


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


def _is_command_result_error(command: str, result: Any) -> bool:
    """Infer business-level failures from handler return values.

    Most tool handlers return string messages instead of raising exceptions.
    This keeps CLI output human-friendly but makes machine-level success/failure
    ambiguous unless we classify known error signatures here.
    """
    if not isinstance(result, str):
        return False

    msg = result.strip().lower()
    if not msg:
        return False

    # eval() / eval_on() can legitimately return any string; do not infer errors.
    # eval_on() returns arbitrary JS evaluation results, same as eval().
    if command in {"eval", "eval_on"}:
        return False

    generic_prefixes = (
        "failed to ",
        "fail: ",           # verify_* tools use PASS:/FAIL: format
        "navigation failed",
        "search failed",
        "wait condition not met",
        "url cannot be empty",
        "search query cannot be empty",
        "unsupported search engine",
        "url scheme ",
        "no active page available",
        "no context is open",
        "cannot navigate",
        "unknown command:",
        "invalid json:",
        "could not hover element ",
    )
    if msg.startswith(generic_prefixes):
        return True

    if " is not available - page may have changed" in msg:
        return True
    if " does not exist" in msg and msg.startswith("file "):
        return True
    if " not found" in msg and "page_id" in msg:
        return True

    # Snapshot pagination overflow should surface as an error status.
    if command == "snapshot" and msg.startswith("start_from_char ("):
        return True

    return False


# ── Navigation ────────────────────────────────────────────────────────────────

async def _handle_open(browser: Any, args: Dict[str, Any]) -> str:
    url = args.get("url", "")
    return await browser.navigate_to_url(url)


async def _handle_back(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.go_back()


async def _handle_forward(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.go_forward()


async def _handle_reload(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.reload_page()


async def _handle_info(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.get_current_page_info_str()


async def _handle_search(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.search(args.get("query", ""), args.get("engine", "duckduckgo"))


# ── Snapshot ──────────────────────────────────────────────────────────────────

async def _handle_snapshot(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.get_snapshot_text(
        start_from_char=args.get("start_from_char", 0),
        interactive=args.get("interactive", False),
        full_page=args.get("full_page", True),
    )


# ── Element Interaction ───────────────────────────────────────────────────────

async def _handle_click(browser: Any, args: Dict[str, Any]) -> str:
    ref = args.get("ref", "")
    return await browser.click_element_by_ref(ref)


async def _handle_double_click(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.double_click_element_by_ref(args.get("ref", ""))


async def _handle_hover(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.hover_element_by_ref(args.get("ref", ""))


async def _handle_focus(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.focus_element_by_ref(args.get("ref", ""))


async def _handle_fill(browser: Any, args: Dict[str, Any]) -> str:
    ref = args.get("ref", "")
    text = args.get("text", "")
    return await browser.input_text_by_ref(ref, text)


async def _handle_select(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.select_dropdown_option_by_ref(args.get("ref", ""), args.get("text", ""))


async def _handle_check(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.check_checkbox_by_ref(args.get("ref", ""))


async def _handle_uncheck(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.uncheck_checkbox_by_ref(args.get("ref", ""))



async def _handle_scroll_into_view(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.scroll_element_into_view_by_ref(args.get("ref", ""))


async def _handle_drag(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.drag_element_by_ref(args.get("start_ref", ""), args.get("end_ref", ""))


async def _handle_options(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.get_dropdown_options_by_ref(args.get("ref", ""))


async def _handle_upload(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.upload_file_by_ref(args.get("ref", ""), args.get("path", ""))


async def _handle_fill_form(browser: Any, args: Dict[str, Any]) -> str:
    fields_raw = args.get("fields", "[]")
    if isinstance(fields_raw, str):
        try:
            fields = json.loads(fields_raw)
        except json.JSONDecodeError as exc:
            raise _DaemonCommandError(f"Invalid JSON for fields: {exc}", "INVALID_JSON_FIELDS") from exc
    else:
        fields = fields_raw
    submit = args.get("submit", False)
    return await browser.fill_form(fields, submit=submit)


# ── Keyboard ──────────────────────────────────────────────────────────────────

async def _handle_press(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.press_key(args.get("key", ""))


async def _handle_type_text(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.type_text(args.get("text", ""), submit=args.get("submit", False))


async def _handle_key_down(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.key_down(args.get("key", ""))


async def _handle_key_up(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.key_up(args.get("key", ""))


# ── Mouse ─────────────────────────────────────────────────────────────────────

async def _handle_scroll(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.mouse_wheel(delta_x=args.get("delta_x", 0), delta_y=args.get("delta_y", 0))


async def _handle_mouse_move(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.mouse_move(args.get("x", 0.0), args.get("y", 0.0))


async def _handle_mouse_click(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.mouse_click(
        args.get("x", 0.0),
        args.get("y", 0.0),
        button=args.get("button", "left"),
        click_count=args.get("count", 1),
    )


async def _handle_mouse_drag(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.mouse_drag(
        args.get("x1", 0.0),
        args.get("y1", 0.0),
        args.get("x2", 0.0),
        args.get("y2", 0.0),
    )


async def _handle_mouse_down(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.mouse_down(button=args.get("button", "left"))


async def _handle_mouse_up(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.mouse_up(button=args.get("button", "left"))


# ── Wait ──────────────────────────────────────────────────────────────────────

async def _handle_wait(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.wait_for(
        time_seconds=args.get("seconds"),
        text=args.get("text"),
        text_gone=args.get("text_gone"),
    )


# ── Tabs ──────────────────────────────────────────────────────────────────────

async def _handle_tabs(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.get_tabs()


async def _handle_new_tab(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.new_tab(url=args.get("url"))


async def _handle_switch_tab(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.switch_tab(args.get("page_id", ""))


async def _handle_close_tab(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.close_tab(page_id=args.get("page_id"))


# ── Capture ───────────────────────────────────────────────────────────────────

async def _handle_screenshot(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.take_screenshot(
        filename=args.get("path"),
        full_page=args.get("full_page", False),
    )


async def _handle_pdf(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.save_pdf(filename=args.get("path"))


# ── Network ───────────────────────────────────────────────────────────────────

async def _handle_network_start(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.start_network_capture()


async def _handle_network_stop(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.stop_network_capture()


async def _handle_network(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.get_network_requests(
        include_static=args.get("include_static", False),
        clear=args.get("clear", True),
    )


async def _handle_wait_network(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.wait_for_network_idle(timeout=args.get("timeout", 30000))


# ── Dialog ────────────────────────────────────────────────────────────────────

async def _handle_dialog_setup(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.setup_dialog_handler(
        default_action=args.get("action", "accept"),
        default_prompt_text=args.get("text"),
    )


async def _handle_dialog(browser: Any, args: Dict[str, Any]) -> str:
    accept = not args.get("dismiss", False)
    return await browser.handle_dialog(accept=accept, prompt_text=args.get("text"))


async def _handle_dialog_remove(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.remove_dialog_handler()


# ── Storage ───────────────────────────────────────────────────────────────────

async def _handle_storage_save(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.save_storage_state(filename=args.get("path"))


async def _handle_storage_load(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.restore_storage_state(args.get("path", ""))


async def _handle_cookies_clear(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.clear_cookies()


async def _handle_cookies(browser: Any, args: Dict[str, Any]) -> str:
    url = args.get("url")
    urls = [url] if url else None
    return await browser.get_cookies(urls=urls)


async def _handle_cookie_set(browser: Any, args: Dict[str, Any]) -> str:
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

async def _handle_verify_visible(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.verify_element_visible(
        role=args.get("role", ""),
        accessible_name=args.get("name", ""),
        timeout=args.get("timeout", 5000),
    )


async def _handle_verify_text(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.verify_text_visible(
        text=args.get("text", ""),
        exact=args.get("exact", False),
        timeout=args.get("timeout", 5000),
    )


async def _handle_verify_value(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.verify_value(args.get("ref", ""), args.get("expected", ""))


async def _handle_verify_state(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.verify_element_state(args.get("ref", ""), args.get("state", ""))


async def _handle_verify_url(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.verify_url(args.get("url", ""), exact=args.get("exact", False))


async def _handle_verify_title(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.verify_title(args.get("title", ""), exact=args.get("exact", False))


# ── Evaluate ──────────────────────────────────────────────────────────────────

async def _handle_eval(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.evaluate_javascript(args.get("code", ""))


async def _handle_eval_on(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.evaluate_javascript_on_ref(args.get("ref", ""), args.get("code", ""))


# ── Developer ─────────────────────────────────────────────────────────────────

async def _handle_console_start(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.start_console_capture()


async def _handle_console_stop(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.stop_console_capture()


async def _handle_console(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.get_console_messages(
        type_filter=args.get("filter"),
        clear=args.get("clear", True),
    )


async def _handle_trace_start(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.start_tracing(
        screenshots=not args.get("no_screenshots", False),
        snapshots=not args.get("no_snapshots", False),
    )


async def _handle_trace_stop(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.stop_tracing(filename=args.get("path"))


async def _handle_trace_chunk(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.add_trace_chunk(title=args.get("title"))


async def _handle_video_start(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.start_video(
        width=args.get("width"),
        height=args.get("height"),
    )


async def _handle_video_stop(browser: Any, args: Dict[str, Any]) -> str:
    return await browser.stop_video(filename=args.get("path"))


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def _handle_close(browser: Any, _args: Dict[str, Any]) -> str:
    return await browser.browser_close()


async def _handle_resize(browser: Any, args: Dict[str, Any]) -> str:
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


def _infer_error_code(command: str, result: Any) -> Optional[str]:
    """Infer a coarse error_code from legacy string-based tool results."""
    if not _is_command_result_error(command, result):
        return None

    msg = str(result).strip().lower()
    if " is not available - page may have changed" in msg:
        return "REF_NOT_AVAILABLE"
    if msg.startswith("start_from_char ("):
        return "INVALID_PAGINATION_OFFSET"
    if "url scheme" in msg:
        return "URL_SCHEME_BLOCKED"
    if "url cannot be empty" in msg:
        return "URL_EMPTY"
    if "search query cannot be empty" in msg:
        return "QUERY_EMPTY"
    if "unsupported search engine" in msg:
        return "UNSUPPORTED_SEARCH_ENGINE"
    if "no active page available" in msg:
        return "NO_ACTIVE_PAGE"
    if "no context is open" in msg:
        return "NO_CONTEXT"
    if "not found" in msg and "page_id" in msg:
        return "TAB_NOT_FOUND"
    if msg.startswith("fail: "):
        return "VERIFICATION_FAILED"
    if command == "wait":
        return "WAIT_CONDITION_NOT_MET"
    if command in {"open", "search"}:
        return "NAVIGATION_FAILED"
    if command == "snapshot":
        return "SNAPSHOT_FAILED"
    return "TOOL_ERROR"


async def _dispatch(browser: Any, command: str, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = _HANDLERS.get(command)
    if handler is None:
        return _response(
            success=False,
            result=f"Unknown command: {command!r}",
            error_code="UNKNOWN_COMMAND",
        )
    try:
        result = await handler(browser, args)
        error_code = _infer_error_code(command, result)
        return _response(
            success=error_code is None,
            result=str(result),
            error_code=error_code,
        )
    except _DaemonCommandError as exc:
        cause = exc.__cause__
        if cause is not None and _is_browser_closed_error(cause):
            return _response(
                success=False,
                result=_BROWSER_CLOSED_HINT,
                error_code="BROWSER_CLOSED",
            )
        return _response(
            success=False,
            result=str(exc),
            error_code=exc.error_code,
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


async def _handle_connection(
    browser: Any,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    stop_event: asyncio.Event,
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
            req = json.loads(raw.decode())
        except json.JSONDecodeError as exc:
            resp = _response(
                success=False,
                result=f"Invalid JSON: {exc}",
                error_code="INVALID_JSON",
            )
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            return

        command = req.get("command", "")
        args = req.get("args", {})

        if command == "close":
            # Close the browser first so we can return auto-saved artifact paths,
            # then signal daemon shutdown.
            resp = await _dispatch(browser, command, args)
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


def _build_browser_kwargs() -> Dict[str, Any]:
    """Build Browser constructor kwargs from config files and environment variables.

    Priority (lowest → highest):
      1. defaults                          — headless=True
      2. ~/.bridgic/bridgic-browser.json  — user persistent config
      3. ./bridgic-browser.json           — project-local config
      4. BRIDGIC_BROWSER_JSON env var     — runtime override (full JSON)
      5. BRIDGIC_HEADLESS env var         — backward-compatible single override
    """
    kwargs: Dict[str, Any] = {"headless": True}

    # 1. User persistent config: ~/.bridgic/bridgic-browser.json
    user_cfg = BRIDGIC_HOME / "bridgic-browser.json"
    if user_cfg.is_file():
        try:
            kwargs.update(json.loads(user_cfg.read_text()))
        except Exception:
            logger.warning("[daemon] failed to parse user config %s", user_cfg)

    # 2. Project-local config: ./bridgic-browser.json
    local_cfg = Path("bridgic-browser.json")
    if local_cfg.is_file():
        try:
            kwargs.update(json.loads(local_cfg.read_text()))
        except Exception:
            logger.warning("[daemon] failed to parse local config %s", local_cfg)

    # 3. BRIDGIC_BROWSER_JSON env var — full JSON override
    raw = os.environ.get("BRIDGIC_BROWSER_JSON")
    if raw:
        try:
            kwargs.update(json.loads(raw))
        except Exception:
            logger.warning("[daemon] failed to parse BRIDGIC_BROWSER_JSON: %s", raw)

    # 4. BRIDGIC_HEADLESS env var — backward-compatible single override
    if "BRIDGIC_HEADLESS" in os.environ:
        kwargs["headless"] = os.environ["BRIDGIC_HEADLESS"] != "0"

    return kwargs


async def run_daemon() -> None:
    from bridgic.browser.session._browser import Browser

    kwargs = _build_browser_kwargs()
    browser = Browser(**kwargs)
    await browser.start()
    logger.info("[daemon] browser started (kwargs=%s)", {k: v for k, v in kwargs.items() if k != "proxy"})

    stop_event = asyncio.Event()

    async def connection_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(browser, reader, writer, stop_event)

    # Remove stale socket if it exists
    _ensure_socket_parent_dir(SOCKET_PATH)
    if os.path.exists(SOCKET_PATH):
        _safe_remove_socket(SOCKET_PATH)

    server = await asyncio.start_unix_server(connection_cb, path=SOCKET_PATH, limit=STREAM_LIMIT)
    try:
        os.chmod(SOCKET_PATH, 0o600)
    except OSError:
        # Best effort; if chmod is unsupported we still proceed.
        pass

    # Signal ready to parent process
    sys.stdout.write(READY_SIGNAL)
    sys.stdout.flush()
    logger.info("[daemon] listening on %s", SOCKET_PATH)

    # Use loop.add_signal_handler — safe for asyncio (unlike signal.signal)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    async with server:
        await stop_event.wait()

    logger.info("[daemon] shutting down")
    try:
        await browser.stop()
    except Exception:
        pass

    if os.path.exists(SOCKET_PATH):
        try:
            _safe_remove_socket(SOCKET_PATH)
        except Exception as exc:
            logger.warning("[daemon] failed to remove socket %s: %s", SOCKET_PATH, exc)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
