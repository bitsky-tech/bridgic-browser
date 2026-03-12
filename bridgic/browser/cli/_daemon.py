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

logger = logging.getLogger(__name__)


class _DaemonCommandError(RuntimeError):
    """Expected command-level failure with stable machine-readable code."""

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


def _default_socket_path() -> str:
    """Return per-user default socket path."""
    return str(Path.home() / ".bridgic" / "run" / "bridgic-browser.sock")


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


async def _handle_open(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import navigate_to_url
    url = args.get("url", "")
    return await navigate_to_url(browser, url)


async def _handle_navigate(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import navigate_to_url
    url = args.get("url", "")
    return await navigate_to_url(browser, url)


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

    # eval() and get_text() can legitimately return any string; do not infer errors.
    # get_text() returns raw inner_text() which may start with "Failed to ..." as page content.
    if command in {"eval", "get_text"}:
        return False

    generic_prefixes = (
        "failed to ",
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


async def _handle_snapshot(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_state_tools import get_llm_repr
    return await get_llm_repr(
        browser,
        start_from_char=args.get("start_from_char", 0),
        interactive=args.get("interactive", False),
        full_page=args.get("full_page", True),
    )


async def _handle_click(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import click_element_by_ref
    ref = args.get("ref", "")
    return await click_element_by_ref(browser, ref)


async def _handle_fill(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import input_text_by_ref
    ref = args.get("ref", "")
    text = args.get("text", "")
    return await input_text_by_ref(browser, ref, text)


async def _handle_get_text(browser: Any, args: Dict[str, Any]) -> str:
    ref = args.get("ref", "")
    locator = await browser.get_element_by_ref(ref)
    if locator is None:
        raise _DaemonCommandError(
            f"Element ref {ref} is not available - page may have changed. Please try refreshing browser state.",
            "REF_NOT_AVAILABLE",
        )
    try:
        return await locator.inner_text()
    except Exception as exc:
        raise _DaemonCommandError(
            f"Failed to get text from element {ref}: {exc}",
            "GET_TEXT_FAILED",
        ) from exc


async def _handle_screenshot(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_screenshot_tools import take_screenshot
    return await take_screenshot(
        browser,
        filename=args.get("path"),
        full_page=args.get("full_page", False),
    )


async def _handle_back(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import go_back
    return await go_back(browser)


async def _handle_forward(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import go_forward
    return await go_forward(browser)


async def _handle_reload(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import reload_page
    return await reload_page(browser)


async def _handle_info(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import get_current_page_info
    return await get_current_page_info(browser)


async def _handle_search(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import search
    return await search(browser, args.get("query", ""), args.get("engine", "duckduckgo"))


async def _handle_hover(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import hover_element_by_ref
    return await hover_element_by_ref(browser, args.get("ref", ""))


async def _handle_select(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import select_dropdown_option_by_ref
    return await select_dropdown_option_by_ref(browser, args.get("ref", ""), args.get("text", ""))


async def _handle_check(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import check_element_by_ref
    return await check_element_by_ref(browser, args.get("ref", ""))


async def _handle_uncheck(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import uncheck_element_by_ref
    return await uncheck_element_by_ref(browser, args.get("ref", ""))


async def _handle_double_click(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import double_click_element_by_ref
    return await double_click_element_by_ref(browser, args.get("ref", ""))


async def _handle_focus(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_action_tools import focus_element_by_ref
    return await focus_element_by_ref(browser, args.get("ref", ""))


async def _handle_press(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import press_key
    return await press_key(browser, args.get("key", ""))


async def _handle_type(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_keyboard_tools import insert_text
    return await insert_text(browser, args.get("text", ""))


async def _handle_scroll(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_mouse_tools import mouse_wheel
    return await mouse_wheel(browser, delta_x=args.get("delta_x", 0), delta_y=args.get("delta_y", 0))


async def _handle_wait(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import wait_for
    return await wait_for(
        browser,
        time_seconds=args.get("seconds"),
        text=args.get("text"),
        text_gone=args.get("text_gone"),
    )


async def _handle_tabs(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import get_tabs
    return await get_tabs(browser)


async def _handle_new_tab(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import new_tab
    return await new_tab(browser, url=args.get("url"))


async def _handle_switch_tab(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import switch_tab
    return await switch_tab(browser, args.get("page_id", ""))


async def _handle_close_tab(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import close_tab
    return await close_tab(browser, page_id=args.get("page_id"))


async def _handle_eval(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_tools import evaluate_javascript
    return await evaluate_javascript(browser, args.get("code", ""))


async def _handle_pdf(browser: Any, args: Dict[str, Any]) -> str:
    from bridgic.browser.tools._browser_screenshot_tools import save_pdf
    return await save_pdf(browser, filename=args.get("path"))


_HANDLERS = {
    "open": _handle_open,
    "navigate": _handle_navigate,
    "snapshot": _handle_snapshot,
    "click": _handle_click,
    "fill": _handle_fill,
    "get_text": _handle_get_text,
    "screenshot": _handle_screenshot,
    "back": _handle_back,
    "forward": _handle_forward,
    "reload": _handle_reload,
    "info": _handle_info,
    "search": _handle_search,
    "hover": _handle_hover,
    "select": _handle_select,
    "check": _handle_check,
    "uncheck": _handle_uncheck,
    "double_click": _handle_double_click,
    "focus": _handle_focus,
    "press": _handle_press,
    "type": _handle_type,
    "scroll": _handle_scroll,
    "wait": _handle_wait,
    "tabs": _handle_tabs,
    "new_tab": _handle_new_tab,
    "switch_tab": _handle_switch_tab,
    "close_tab": _handle_close_tab,
    "eval": _handle_eval,
    "pdf": _handle_pdf,
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
    if command == "wait":
        return "WAIT_CONDITION_NOT_MET"
    if command in {"open", "navigate", "search"}:
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
            resp = _response(success=True, result="Daemon shutting down")
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
    user_cfg = Path.home() / ".bridgic" / "bridgic-browser.json"
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
        await browser.kill()
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
