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
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SOCKET_PATH = os.environ.get("BRIDGIC_SOCKET", "/tmp/bridgic-browser.sock")
READY_SIGNAL = "BRIDGIC_DAEMON_READY\n"
STREAM_LIMIT = 16 * 1024 * 1024  # 16 MB — handles large snapshots and fill/eval payloads


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
        return f"Element ref {ref!r} not found — page may have changed, try snapshot first"
    return await locator.inner_text()


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


async def _dispatch(browser: Any, command: str, args: Dict[str, Any]) -> Dict[str, str]:
    handler = _HANDLERS.get(command)
    if handler is None:
        return {"status": "error", "result": f"Unknown command: {command!r}"}
    try:
        result = await handler(browser, args)
        return {"status": "ok", "result": result}
    except Exception as exc:
        if _is_browser_closed_error(exc):
            return {"status": "error", "result": _BROWSER_CLOSED_HINT}
        logger.exception("[daemon] command=%s error", command)
        return {"status": "error", "result": str(exc)}


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
            resp = {"status": "error", "result": f"Invalid JSON: {exc}"}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            return

        command = req.get("command", "")
        args = req.get("args", {})

        if command == "close":
            resp = {"status": "ok", "result": "Daemon shutting down"}
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
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = await asyncio.start_unix_server(connection_cb, path=SOCKET_PATH, limit=STREAM_LIMIT)

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
        os.unlink(SOCKET_PATH)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
