"""
Unit tests for bridgic.browser.cli (daemon-free, no real browser required).

Coverage:
  _commands  – _strip_ref, SectionedGroup help layout, every CLI command's
               argument/option → send_command mapping, path absolutisation,
               error path, -h shorthand
  _daemon    – _dispatch routing, _handle_connection protocol (valid JSON,
               bad JSON, EOF, timeout, close command), handler smoke tests
  _client    – send_command(start_if_needed=False) socket-missing guard
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import tempfile
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from bridgic.browser._cli_catalog import CLI_ALL_COMMANDS, CLI_HELP_SECTIONS
from bridgic.browser.errors import (
    BridgicBrowserCommandError,
    InvalidInputError,
    OperationError,
    VerificationError,
)
from bridgic.browser.cli._commands import _strip_ref, cli, SectionedGroup
from bridgic.browser.cli._daemon import (
    _BROWSER_CLOSED_HINT,
    _browser_closed_hint,
    _cdp_reconnect,
    _resolve_default_downloads_dir,
    _dispatch,
    _handle_connection,
    _handle_open,
    _handle_snapshot,
    _handle_click,
    _handle_fill,
    _handle_screenshot,
    _handle_scroll,
    _handle_wait,
    _handle_search,
    _handle_close_tab,
    _handle_pdf,
    # new handlers
    _handle_scroll_into_view,
    _handle_drag,
    _handle_options,
    _handle_upload,
    _handle_fill_form,
    _handle_type_text,
    _handle_key_down,
    _handle_key_up,
    _handle_mouse_move,
    _handle_mouse_click,
    _handle_mouse_drag,
    _handle_mouse_down,
    _handle_mouse_up,
    _handle_wait_network,
    _handle_console_start,
    _handle_console_stop,
    _handle_console,
    _handle_network_start,
    _handle_network_stop,
    _handle_network,
    _handle_dialog_setup,
    _handle_dialog,
    _handle_dialog_remove,
    _handle_storage_save,
    _handle_storage_load,
    _handle_cookies_clear,
    _handle_cookies,
    _handle_cookie_set,
    _handle_verify_visible,
    _handle_verify_text,
    _handle_verify_value,
    _handle_verify_state,
    _handle_verify_url,
    _handle_verify_title,
    _handle_eval,
    _handle_eval_on,
    _handle_trace_start,
    _handle_trace_stop,
    _handle_trace_chunk,
    _handle_video_start,
    _handle_video_stop,
    _handle_resize,
    _is_browser_closed_error,
)
from bridgic.browser.cli._transport import (
    TcpTransport,
    UnixTransport,
    get_transport,
    _default_socket_path,
    _safe_remove_socket,
    read_run_info,
    remove_run_info,
    write_run_info,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

runner = CliRunner()


def invoke(args: list[str], catch_exceptions: bool = False):
    """Invoke the CLI with send_command fully mocked to return 'OK'."""
    with patch("bridgic.browser.cli._commands.send_command", return_value="OK") as mock_sc:
        result = runner.invoke(cli, args, catch_exceptions=catch_exceptions)
    return result, mock_sc


def invoke_raw(args: list[str]):
    """Invoke CLI without mocking send_command (for help/parsing tests)."""
    return runner.invoke(cli, args, catch_exceptions=False)


# Minimal valid invocations for every CLI command (ensures full coverage).
CLI_COMMAND_SAMPLE_ARGS: dict[str, list[str]] = {
    # Navigation
    "open": ["open", "https://example.com"],
    "search": ["search", "query"],
    "info": ["info"],
    "reload": ["reload"],
    "back": ["back"],
    "forward": ["forward"],
    # Snapshot
    "snapshot": ["snapshot"],
    # Element interaction
    "click": ["click", "@e1"],
    "double-click": ["double-click", "@e1"],
    "hover": ["hover", "@e1"],
    "focus": ["focus", "@e1"],
    "fill": ["fill", "@e1", "hello"],
    "select": ["select", "@e1", "Option"],
    "options": ["options", "@e1"],
    "check": ["check", "@e1"],
    "uncheck": ["uncheck", "@e1"],
    "scroll-to": ["scroll-to", "@e1"],
    "drag": ["drag", "@e1", "@e2"],
    "upload": ["upload", "@e1", "file.txt"],
    "fill-form": ["fill-form", '[{"ref":"e1","value":"hi"}]'],
    # Tabs
    "tabs": ["tabs"],
    "new-tab": ["new-tab"],
    "switch-tab": ["switch-tab", "page-1"],
    "close-tab": ["close-tab"],
    # Evaluate
    "eval": ["eval", "return 1;"],
    "eval-on": ["eval-on", "@e1", "return 1;"],
    # Keyboard
    "press": ["press", "Enter"],
    "type": ["type", "hello"],
    "key-down": ["key-down", "Shift"],
    "key-up": ["key-up", "Shift"],
    # Mouse
    "scroll": ["scroll"],
    "mouse-move": ["mouse-move", "1", "2"],
    "mouse-click": ["mouse-click", "1", "2"],
    "mouse-drag": ["mouse-drag", "1", "2", "3", "4"],
    "mouse-down": ["mouse-down"],
    "mouse-up": ["mouse-up"],
    # Wait
    "wait": ["wait", "1"],
    # Capture
    "screenshot": ["screenshot", "out.png"],
    "pdf": ["pdf", "out.pdf"],
    # Network
    "network-start": ["network-start"],
    "network": ["network"],
    "network-stop": ["network-stop"],
    "wait-network": ["wait-network"],
    # Dialog
    "dialog-setup": ["dialog-setup"],
    "dialog": ["dialog"],
    "dialog-remove": ["dialog-remove"],
    # Storage
    "cookies": ["cookies"],
    "cookie-set": ["cookie-set", "sid", "abc123"],
    "cookies-clear": ["cookies-clear"],
    "storage-save": ["storage-save", "state.json"],
    "storage-load": ["storage-load", "state.json"],
    # Verify
    "verify-text": ["verify-text", "Hello"],
    "verify-visible": ["verify-visible", "button", "Submit"],
    "verify-value": ["verify-value", "@e1", "expected"],
    "verify-state": ["verify-state", "@e1", "visible"],
    "verify-url": ["verify-url", "example.com"],
    "verify-title": ["verify-title", "Example"],
    # Developer
    "console-start": ["console-start"],
    "console": ["console"],
    "console-stop": ["console-stop"],
    "trace-start": ["trace-start"],
    "trace-chunk": ["trace-chunk", "step-1"],
    "trace-stop": ["trace-stop", "trace.zip"],
    "video-start": ["video-start"],
    "video-stop": ["video-stop"],
    # Lifecycle
    "close": ["close"],
    "resize": ["resize", "800", "600"],
}


def make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def make_writer() -> MagicMock:
    w = MagicMock()
    w.write = MagicMock()
    w.drain = AsyncMock()
    w.close = MagicMock()
    w.wait_closed = AsyncMock()
    return w


def make_browser() -> MagicMock:
    b = MagicMock()
    b.get_snapshot = AsyncMock()
    b.get_element_by_ref = AsyncMock(return_value=None)
    b.navigate_to = AsyncMock()
    b.stop = AsyncMock(return_value="Browser closed successfully")
    b.inspect_pending_close_artifacts = MagicMock(return_value={
        "session_dir": "/tmp/close-test",
        "trace": [],
        "video": [],
    })
    b._cdp_url = None  # explicit None so _dispatch treats as local-launch mode
    return b


def _snapshot_logging_state() -> dict[str, Any]:
    root = logging.getLogger()
    bridgic_logger = logging.getLogger("bridgic.browser")
    return {
        "root_handlers": list(root.handlers),
        "root_level": root.level,
        "bridgic_handlers": list(bridgic_logger.handlers),
        "bridgic_level": bridgic_logger.level,
        "bridgic_propagate": bridgic_logger.propagate,
    }


def _restore_logging_state(state: dict[str, Any]) -> None:
    root = logging.getLogger()
    bridgic_logger = logging.getLogger("bridgic.browser")

    for handler in list(bridgic_logger.handlers):
        if getattr(handler, "baseFilename", None):
            handler.close()

    root.handlers.clear()
    root.handlers.extend(state["root_handlers"])
    root.setLevel(state["root_level"])

    bridgic_logger.handlers.clear()
    bridgic_logger.handlers.extend(state["bridgic_handlers"])
    bridgic_logger.setLevel(state["bridgic_level"])
    bridgic_logger.propagate = state["bridgic_propagate"]


# ─────────────────────────────────────────────────────────────────────────────
# _strip_ref
# ─────────────────────────────────────────────────────────────────────────────

class TestStripRef:
    def test_at_prefix(self):
        assert _strip_ref("@e1") == "e1"

    def test_ref_eq_prefix(self):
        assert _strip_ref("ref=e2") == "e2"

    def test_plain(self):
        assert _strip_ref("e3") == "e3"

    def test_whitespace_trimmed(self):
        assert _strip_ref("  @e4  ") == "e4"

    def test_at_overrides_ref_eq(self):
        # "@ref=e5" → strip @ → "ref=e5" → strip ref= → "e5"
        assert _strip_ref("@ref=e5") == "e5"

    def test_empty_at(self):
        assert _strip_ref("@") == ""


# ─────────────────────────────────────────────────────────────────────────────
# SectionedGroup / help layout
# ─────────────────────────────────────────────────────────────────────────────

class TestSectionedGroupHelp:
    def test_cli_group_is_sectioned(self):
        assert isinstance(cli, SectionedGroup)

    def test_sections_follow_shared_catalog(self):
        assert SectionedGroup.SECTIONS == CLI_HELP_SECTIONS

    def test_registered_commands_match_catalog(self):
        registered = set(cli.commands.keys())
        expected = set(CLI_ALL_COMMANDS)
        assert expected.issubset(registered)
        assert registered == expected

    def test_h_shorthand_on_group(self):
        result = invoke_raw(["-h"])
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "-V" in result.output
        assert "--version" in result.output

    def test_help_longhand_on_group(self):
        result = invoke_raw(["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "-V" in result.output
        assert "--version" in result.output

    def test_h_shorthand_on_subcommand(self):
        result = invoke_raw(["open", "-h"])
        assert result.exit_code == 0
        assert "URL" in result.output

    def test_sections_present(self):
        result = invoke_raw(["-h"])
        out = result.output
        for section in (
            "Navigation", "Snapshot", "Element Interaction",
            "Keyboard", "Mouse", "Wait", "Tabs", "Evaluate", "Capture",
            "Network", "Dialog", "Storage", "Verify",
            "Developer", "Lifecycle",
        ):
            assert section in out, f"Section '{section}' missing from help"

    def test_all_commands_appear_in_help(self):
        result = invoke_raw(["-h"])
        out = result.output
        expected_commands = [
            # Navigation
            "open", "back", "forward", "reload", "search", "info", "scroll-to",
            # Snapshot
            "snapshot",
            # Element Interaction
            "click", "double-click", "hover", "focus", "fill", "select",
            "check", "uncheck", "drag", "options", "upload", "fill-form",
            # Keyboard
            "press", "type", "key-down", "key-up",
            # Mouse
            "scroll", "mouse-move", "mouse-click", "mouse-drag", "mouse-down", "mouse-up",
            # Wait
            "wait",
            # Tabs
            "tabs", "new-tab", "switch-tab", "close-tab",
            # Capture
            "screenshot", "pdf",
            # Network
            "console-start", "console-stop", "console",
            "network-start", "network-stop", "network", "wait-network",
            # Dialog
            "dialog-setup", "dialog", "dialog-remove",
            # Storage
            "storage-save", "storage-load", "cookies-clear", "cookies", "cookie-set",
            # Verify
            "verify-visible", "verify-text", "verify-value",
            "verify-state", "verify-url", "verify-title",
            # Developer
            "eval", "eval-on", "trace-start", "trace-stop", "trace-chunk",
            "video-start", "video-stop",
            # Lifecycle
            "close", "resize",
        ]
        for cmd in expected_commands:
            assert cmd in out, f"Command '{cmd}' missing from help output"

    def test_help_has_no_flat_commands_section(self):
        """Flat 'Commands:' block should not appear — only named sections."""
        result = invoke_raw(["-h"])
        assert "Commands:" not in result.output

    def test_unlisted_command_appears_in_other(self):
        """Commands added outside SECTIONS fall into an 'Other' section."""
        @cli.command("_test_unlisted_cmd", hidden=False)
        def _():
            """Unlisted test command."""

        try:
            result = invoke_raw(["-h"])
            assert "Other" in result.output
            assert "_test_unlisted_cmd" in result.output
        finally:
            cli.commands.pop("_test_unlisted_cmd", None)

    def test_help_text_not_truncated_by_eg(self):
        """No help line should end with 'e.g.' (a known Click truncation pitfall)."""
        result = invoke_raw(["-h"])
        for line in result.output.splitlines():
            assert not line.rstrip().endswith("e.g."), (
                f"Help text truncated at 'e.g.' — fix docstring: {line!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# CLI command → send_command mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestCliCommandRouting:
    """Each test verifies the correct send_command(command, args, **kw) call."""

    # ── Navigation ────────────────────────────────────────────────────────────

    def test_open(self):
        _, sc = invoke(["open", "https://example.com"])
        sc.assert_called_once_with("open", {"url": "https://example.com"}, headed=False, clear_user_data=False, cdp_url=None)

    def test_open_headed(self):
        _, sc = invoke(["open", "--headed", "https://example.com"])
        sc.assert_called_once_with("open", {"url": "https://example.com"}, headed=True, clear_user_data=False, cdp_url=None)

    def test_open_clear_user_data(self):
        _, sc = invoke(["open", "--clear-user-data", "https://example.com"])
        sc.assert_called_once_with("open", {"url": "https://example.com"}, headed=False, clear_user_data=True, cdp_url=None)

    def test_open_cdp_ws_url_passthrough(self):
        """--cdp ws://... passes through without resolution."""
        with patch("bridgic.browser.session._browser.find_cdp_url") as mock_find:
            _, sc = invoke(["open", "--cdp", "ws://localhost:9222/devtools/browser/abc", "https://example.com"])
        mock_find.assert_not_called()
        sc.assert_called_once_with(
            "open", {"url": "https://example.com"},
            headed=False, clear_user_data=False, cdp_url="ws://localhost:9222/devtools/browser/abc",
        )

    def test_open_cdp_port_number(self):
        """--cdp 9222 calls find_cdp_url(mode='port', host='localhost', port=9222)."""
        with patch("bridgic.browser.session._browser.find_cdp_url", return_value="ws://localhost:9222/devtools/browser/xyz") as mock_find:
            _, sc = invoke(["open", "--cdp", "9222", "https://example.com"])
        mock_find.assert_called_once_with(mode="port", host="localhost", port=9222)
        sc.assert_called_once_with(
            "open", {"url": "https://example.com"},
            headed=False, clear_user_data=False, cdp_url="ws://localhost:9222/devtools/browser/xyz",
        )

    def test_open_cdp_http_url(self):
        """--cdp http://host:port calls find_cdp_url(mode='port', host=..., port=...)."""
        with patch("bridgic.browser.session._browser.find_cdp_url", return_value="ws://1.2.3.4:9222/devtools/browser/xyz") as mock_find:
            _, sc = invoke(["open", "--cdp", "http://1.2.3.4:9222", "https://example.com"])
        mock_find.assert_called_once_with(mode="port", host="1.2.3.4", port=9222)
        sc.assert_called_once_with(
            "open", {"url": "https://example.com"},
            headed=False, clear_user_data=False, cdp_url="ws://1.2.3.4:9222/devtools/browser/xyz",
        )

    def test_open_cdp_auto(self):
        """--cdp auto calls find_cdp_url(mode='scan')."""
        with patch("bridgic.browser.session._browser.find_cdp_url", return_value="ws://localhost:57234/devtools/browser/auto") as mock_find:
            _, sc = invoke(["open", "--cdp", "auto", "https://example.com"])
        mock_find.assert_called_once_with(mode="scan")
        sc.assert_called_once_with(
            "open", {"url": "https://example.com"},
            headed=False, clear_user_data=False, cdp_url="ws://localhost:57234/devtools/browser/auto",
        )

    def test_open_cdp_wss_url_passthrough(self):
        """--cdp wss://... passes through unchanged (cloud services like Browserless, Steel.dev)."""
        wss_url = "wss://production.browserless.io/chromium/playwright?token=abc123"
        with patch("bridgic.browser.session._browser.find_cdp_url") as mock_find:
            _, sc = invoke(["open", "--cdp", wss_url, "https://example.com"])
        mock_find.assert_not_called()
        sc.assert_called_once_with(
            "open", {"url": "https://example.com"},
            headed=False, clear_user_data=False, cdp_url=wss_url,
        )

    def test_open_cdp_invalid_format_shows_error(self):
        """--cdp with unrecognized format prints an error and does NOT call send_command."""
        result, sc = invoke(["open", "--cdp", "not-a-valid-cdp", "https://example.com"])
        sc.assert_not_called()
        assert result.exit_code == 1  # _err() calls sys.exit(1)
        assert "Invalid --cdp value" in result.output

    def test_back(self):
        _, sc = invoke(["back"])
        sc.assert_called_once_with("back", start_if_needed=False)

    def test_forward(self):
        _, sc = invoke(["forward"])
        sc.assert_called_once_with("forward", start_if_needed=False)

    def test_reload(self):
        _, sc = invoke(["reload"])
        sc.assert_called_once_with("reload", start_if_needed=False)

    def test_search_default_engine(self):
        _, sc = invoke(["search", "python async"])
        sc.assert_called_once_with("search", {"query": "python async", "engine": "duckduckgo"}, headed=False, clear_user_data=False, cdp_url=None)

    def test_search_custom_engine(self):
        _, sc = invoke(["search", "query", "--engine", "google"])
        sc.assert_called_once_with("search", {"query": "query", "engine": "google"}, headed=False, clear_user_data=False, cdp_url=None)

    def test_search_headed(self):
        _, sc = invoke(["search", "--headed", "python async"])
        sc.assert_called_once_with("search", {"query": "python async", "engine": "duckduckgo"}, headed=True, clear_user_data=False, cdp_url=None)

    def test_search_clear_user_data(self):
        _, sc = invoke(["search", "--clear-user-data", "python async"])
        sc.assert_called_once_with("search", {"query": "python async", "engine": "duckduckgo"}, headed=False, clear_user_data=True, cdp_url=None)

    def test_info(self):
        _, sc = invoke(["info"])
        sc.assert_called_once_with("info", start_if_needed=False)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def test_snapshot_default(self):
        _, sc = invoke(["snapshot"])
        sc.assert_called_once_with("snapshot", {"interactive": False, "full_page": True, "limit": 10000, "file": None}, start_if_needed=False)

    def test_snapshot_interactive(self):
        _, sc = invoke(["snapshot", "--interactive"])
        sc.assert_called_once_with("snapshot", {"interactive": True, "full_page": True, "limit": 10000, "file": None}, start_if_needed=False)

    def test_snapshot_interactive_short(self):
        _, sc = invoke(["snapshot", "-i"])
        sc.assert_called_once_with("snapshot", {"interactive": True, "full_page": True, "limit": 10000, "file": None}, start_if_needed=False)

    def test_snapshot_no_full_page(self):
        _, sc = invoke(["snapshot", "--no-full-page"])
        sc.assert_called_once_with("snapshot", {"interactive": False, "full_page": False, "limit": 10000, "file": None}, start_if_needed=False)

    def test_snapshot_no_full_page_short(self):
        _, sc = invoke(["snapshot", "-F"])
        sc.assert_called_once_with("snapshot", {"interactive": False, "full_page": False, "limit": 10000, "file": None}, start_if_needed=False)

    def test_snapshot_limit(self):
        _, sc = invoke(["snapshot", "-l", "3000"])
        sc.assert_called_once_with("snapshot", {"interactive": False, "full_page": True, "limit": 3000, "file": None}, start_if_needed=False)

    def test_snapshot_file_option(self):
        """Relative path is absolutized via os.path.abspath."""
        _, sc = invoke(["snapshot", "-s", "snap.txt"])
        call_args = sc.call_args
        assert call_args[0][0] == "snapshot"
        sent_path = call_args[0][1]["file"]
        assert os.path.isabs(sent_path)
        assert sent_path.endswith("snap.txt")
        assert call_args[1]["start_if_needed"] is False

    def test_snapshot_limit_rejects_zero(self):
        result, sc = invoke(["snapshot", "-l", "0"])
        assert result.exit_code != 0
        sc.assert_not_called()

    # ── Element interaction ───────────────────────────────────────────────────

    def test_click_strips_at(self):
        _, sc = invoke(["click", "@e2"])
        sc.assert_called_once_with("click", {"ref": "e2"}, start_if_needed=False)

    def test_click_plain_ref(self):
        _, sc = invoke(["click", "e3"])
        sc.assert_called_once_with("click", {"ref": "e3"}, start_if_needed=False)

    def test_double_click(self):
        _, sc = invoke(["double-click", "@e4"])
        sc.assert_called_once_with("double_click", {"ref": "e4"}, start_if_needed=False)

    def test_hover(self):
        _, sc = invoke(["hover", "@e5"])
        sc.assert_called_once_with("hover", {"ref": "e5"}, start_if_needed=False)

    def test_focus(self):
        _, sc = invoke(["focus", "e6"])
        sc.assert_called_once_with("focus", {"ref": "e6"}, start_if_needed=False)

    def test_fill(self):
        _, sc = invoke(["fill", "@e3", "hello"])
        sc.assert_called_once_with("fill", {"ref": "e3", "text": "hello", "submit": False}, start_if_needed=False)

    def test_fill_with_submit(self):
        _, sc = invoke(["fill", "@e3", "hello", "--submit"])
        sc.assert_called_once_with("fill", {"ref": "e3", "text": "hello", "submit": True}, start_if_needed=False)

    def test_select(self):
        _, sc = invoke(["select", "@e7", "Option A"])
        sc.assert_called_once_with("select", {"ref": "e7", "text": "Option A"}, start_if_needed=False)

    def test_check(self):
        _, sc = invoke(["check", "@e8"])
        sc.assert_called_once_with("check", {"ref": "e8"}, start_if_needed=False)

    def test_uncheck(self):
        _, sc = invoke(["uncheck", "@e9"])
        sc.assert_called_once_with("uncheck", {"ref": "e9"}, start_if_needed=False)

    def test_scroll_to(self):
        _, sc = invoke(["scroll-to", "@e5"])
        sc.assert_called_once_with("scroll_into_view", {"ref": "e5"}, start_if_needed=False)

    def test_scroll_into_view_removed(self):
        result, _ = invoke(["scroll-into-view", "@e5"])
        assert result.exit_code != 0
        assert "No such command 'scroll-into-view'" in result.output

    def test_drag(self):
        _, sc = invoke(["drag", "@e1", "@e2"])
        sc.assert_called_once_with("drag", {"start_ref": "e1", "end_ref": "e2"}, start_if_needed=False)

    def test_options(self):
        _, sc = invoke(["options", "@e3"])
        sc.assert_called_once_with("options", {"ref": "e3"}, start_if_needed=False)

    def test_upload_absolutizes_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["upload", "@e4", "file.txt"])
        expected = str(tmp_path / "file.txt")
        sc.assert_called_once_with("upload", {"ref": "e4", "path": expected}, start_if_needed=False)

    def test_fill_form(self):
        fields = '[{"ref":"e1","value":"hi"}]'
        _, sc = invoke(["fill-form", fields])
        sc.assert_called_once_with("fill_form", {"fields": fields, "submit": False}, start_if_needed=False)

    def test_fill_form_with_submit(self):
        fields = '[{"ref":"e1","value":"hi"}]'
        _, sc = invoke(["fill-form", fields, "--submit"])
        sc.assert_called_once_with("fill_form", {"fields": fields, "submit": True}, start_if_needed=False)

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def test_press(self):
        _, sc = invoke(["press", "Control+A"])
        sc.assert_called_once_with("press", {"key": "Control+A"}, start_if_needed=False)

    def test_type(self):
        _, sc = invoke(["type", "hello world"])
        sc.assert_called_once_with("type_text", {"text": "hello world", "submit": False}, start_if_needed=False)

    def test_type_with_submit(self):
        _, sc = invoke(["type", "hello", "--submit"])
        sc.assert_called_once_with("type_text", {"text": "hello", "submit": True}, start_if_needed=False)

    def test_type_text_removed(self):
        result, _ = invoke(["type-text", "hello"])
        assert result.exit_code != 0
        assert "No such command 'type-text'" in result.output

    def test_key_down(self):
        _, sc = invoke(["key-down", "Shift"])
        sc.assert_called_once_with("key_down", {"key": "Shift"}, start_if_needed=False)

    def test_key_up(self):
        _, sc = invoke(["key-up", "Shift"])
        sc.assert_called_once_with("key_up", {"key": "Shift"}, start_if_needed=False)

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def test_scroll_down(self):
        _, sc = invoke(["scroll", "--dy", "300"])
        sc.assert_called_once_with("scroll", {"delta_x": 0.0, "delta_y": 300.0}, start_if_needed=False)

    def test_scroll_up(self):
        _, sc = invoke(["scroll", "--dy", "-200"])
        sc.assert_called_once_with("scroll", {"delta_x": 0.0, "delta_y": -200.0}, start_if_needed=False)

    def test_scroll_with_dx(self):
        _, sc = invoke(["scroll", "--dy", "100", "--dx", "50"])
        sc.assert_called_once_with("scroll", {"delta_x": 50.0, "delta_y": 100.0}, start_if_needed=False)

    def test_mouse_move(self):
        _, sc = invoke(["mouse-move", "100", "200"])
        sc.assert_called_once_with("mouse_move", {"x": 100.0, "y": 200.0}, start_if_needed=False)

    def test_mouse_click_defaults(self):
        _, sc = invoke(["mouse-click", "150", "250"])
        sc.assert_called_once_with("mouse_click", {"x": 150.0, "y": 250.0, "button": "left", "count": 1}, start_if_needed=False)

    def test_mouse_click_right_button(self):
        _, sc = invoke(["mouse-click", "150", "250", "--button", "right"])
        sc.assert_called_once_with("mouse_click", {"x": 150.0, "y": 250.0, "button": "right", "count": 1}, start_if_needed=False)

    def test_mouse_click_double(self):
        _, sc = invoke(["mouse-click", "150", "250", "--count", "2"])
        sc.assert_called_once_with("mouse_click", {"x": 150.0, "y": 250.0, "button": "left", "count": 2}, start_if_needed=False)

    def test_mouse_drag(self):
        _, sc = invoke(["mouse-drag", "10", "20", "100", "200"])
        sc.assert_called_once_with("mouse_drag", {"x1": 10.0, "y1": 20.0, "x2": 100.0, "y2": 200.0}, start_if_needed=False)

    def test_mouse_down_default(self):
        _, sc = invoke(["mouse-down"])
        sc.assert_called_once_with("mouse_down", {"button": "left"}, start_if_needed=False)

    def test_mouse_down_right(self):
        _, sc = invoke(["mouse-down", "--button", "right"])
        sc.assert_called_once_with("mouse_down", {"button": "right"}, start_if_needed=False)

    def test_mouse_up_default(self):
        _, sc = invoke(["mouse-up"])
        sc.assert_called_once_with("mouse_up", {"button": "left"}, start_if_needed=False)

    # ── Wait ──────────────────────────────────────────────────────────────────

    def test_wait_seconds(self):
        _, sc = invoke(["wait", "2.5"])
        sc.assert_called_once_with("wait", {"seconds": 2.5}, start_if_needed=False)

    def test_wait_text_appear(self):
        _, sc = invoke(["wait", "Done"])
        sc.assert_called_once_with("wait", {"text": "Done", "timeout": 30.0}, start_if_needed=False)

    def test_wait_text_appear_custom_timeout(self):
        _, sc = invoke(["wait", "--timeout", "5", "Done"])
        sc.assert_called_once_with("wait", {"text": "Done", "timeout": 5.0}, start_if_needed=False)

    def test_wait_text_gone(self):
        _, sc = invoke(["wait", "--gone", "Loading"])
        sc.assert_called_once_with("wait", {"text_gone": "Loading", "timeout": 30.0}, start_if_needed=False)

    def test_wait_text_gone_custom_timeout(self):
        _, sc = invoke(["wait", "--gone", "--timeout", "10", "Spinner"])
        sc.assert_called_once_with("wait", {"text_gone": "Spinner", "timeout": 10.0}, start_if_needed=False)

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def test_tabs(self):
        _, sc = invoke(["tabs"])
        sc.assert_called_once_with("tabs", start_if_needed=False)

    def test_new_tab_with_url(self):
        _, sc = invoke(["new-tab", "https://example.com"])
        sc.assert_called_once_with("new_tab", {"url": "https://example.com"}, start_if_needed=False)

    def test_new_tab_blank(self):
        _, sc = invoke(["new-tab"])
        sc.assert_called_once_with("new_tab", {"url": None}, start_if_needed=False)

    def test_switch_tab(self):
        _, sc = invoke(["switch-tab", "page_1234"])
        sc.assert_called_once_with("switch_tab", {"page_id": "page_1234"}, start_if_needed=False)

    def test_close_tab_current(self):
        _, sc = invoke(["close-tab"])
        sc.assert_called_once_with("close_tab", {"page_id": None}, start_if_needed=False)

    def test_close_tab_by_id(self):
        _, sc = invoke(["close-tab", "page_5678"])
        sc.assert_called_once_with("close_tab", {"page_id": "page_5678"}, start_if_needed=False)

    # ── Capture ───────────────────────────────────────────────────────────────

    def test_screenshot_absolutizes_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["screenshot", "page.png"])
        expected = str(tmp_path / "page.png")
        sc.assert_called_once_with(
            "screenshot", {"path": expected, "full_page": False}, start_if_needed=False
        )

    def test_screenshot_full_page(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["screenshot", "page.png", "--full-page"])
        expected = str(tmp_path / "page.png")
        sc.assert_called_once_with(
            "screenshot", {"path": expected, "full_page": True}, start_if_needed=False
        )

    def test_screenshot_absolute_path_unchanged(self):
        abs_path = "/tmp/my_screenshot.png"
        _, sc = invoke(["screenshot", abs_path])
        sc.assert_called_once_with(
            "screenshot", {"path": abs_path, "full_page": False}, start_if_needed=False
        )

    def test_pdf_absolutizes_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["pdf", "report.pdf"])
        expected = str(tmp_path / "report.pdf")
        sc.assert_called_once_with("pdf", {"path": expected}, start_if_needed=False)

    # ── Network ───────────────────────────────────────────────────────────────

    def test_console_start(self):
        _, sc = invoke(["console-start"])
        sc.assert_called_once_with("console_start", start_if_needed=False)

    def test_console_stop(self):
        _, sc = invoke(["console-stop"])
        sc.assert_called_once_with("console_stop", start_if_needed=False)

    def test_console_defaults(self):
        _, sc = invoke(["console"])
        sc.assert_called_once_with("console", {"filter": None, "clear": True}, start_if_needed=False)

    def test_console_with_filter(self):
        _, sc = invoke(["console", "--filter", "error"])
        sc.assert_called_once_with("console", {"filter": "error", "clear": True}, start_if_needed=False)

    def test_console_no_clear(self):
        _, sc = invoke(["console", "--no-clear"])
        sc.assert_called_once_with("console", {"filter": None, "clear": False}, start_if_needed=False)

    def test_network_start(self):
        _, sc = invoke(["network-start"])
        sc.assert_called_once_with("network_start", start_if_needed=False)

    def test_network_stop(self):
        _, sc = invoke(["network-stop"])
        sc.assert_called_once_with("network_stop", start_if_needed=False)

    def test_network_defaults(self):
        _, sc = invoke(["network"])
        sc.assert_called_once_with("network", {"include_static": False, "clear": True}, start_if_needed=False)

    def test_network_with_static(self):
        _, sc = invoke(["network", "--static"])
        sc.assert_called_once_with("network", {"include_static": True, "clear": True}, start_if_needed=False)

    def test_wait_network_defaults(self):
        _, sc = invoke(["wait-network"])
        sc.assert_called_once_with("wait_network", {"timeout": 30.0}, start_if_needed=False)

    def test_wait_network_custom_timeout(self):
        _, sc = invoke(["wait-network", "2.5"])
        sc.assert_called_once_with("wait_network", {"timeout": 2.5}, start_if_needed=False)

    # ── Dialog ────────────────────────────────────────────────────────────────

    def test_dialog_setup_defaults(self):
        _, sc = invoke(["dialog-setup"])
        sc.assert_called_once_with("dialog_setup", {"action": "accept", "text": None}, start_if_needed=False)

    def test_dialog_setup_dismiss(self):
        _, sc = invoke(["dialog-setup", "--action", "dismiss"])
        sc.assert_called_once_with("dialog_setup", {"action": "dismiss", "text": None}, start_if_needed=False)

    def test_dialog_setup_with_text(self):
        _, sc = invoke(["dialog-setup", "--text", "yes"])
        sc.assert_called_once_with("dialog_setup", {"action": "accept", "text": "yes"}, start_if_needed=False)

    def test_dialog_accept(self):
        _, sc = invoke(["dialog"])
        sc.assert_called_once_with("dialog", {"dismiss": False, "text": None}, start_if_needed=False)

    def test_dialog_dismiss(self):
        _, sc = invoke(["dialog", "--dismiss"])
        sc.assert_called_once_with("dialog", {"dismiss": True, "text": None}, start_if_needed=False)

    def test_dialog_remove(self):
        _, sc = invoke(["dialog-remove"])
        sc.assert_called_once_with("dialog_remove", start_if_needed=False)

    # ── Storage ───────────────────────────────────────────────────────────────

    def test_storage_save_absolutizes_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["storage-save", "state.json"])
        sc.assert_called_once_with("storage_save", {"path": str(tmp_path / "state.json")}, start_if_needed=False)

    def test_storage_load_absolutizes_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["storage-load", "state.json"])
        sc.assert_called_once_with("storage_load", {"path": str(tmp_path / "state.json")}, start_if_needed=False)

    def test_cookies_clear(self):
        _, sc = invoke(["cookies-clear"])
        sc.assert_called_once_with("cookies_clear", None, start_if_needed=False)

    def test_cookies_no_filter(self):
        _, sc = invoke(["cookies"])
        sc.assert_called_once_with(
            "cookies",
            {"domain": None, "path": None, "name": None},
            start_if_needed=False,
        )

    def test_cookies_with_domain_path_name(self):
        _, sc = invoke(["cookies", "--domain", "example.com", "--path", "/app", "--name", "sid"])
        sc.assert_called_once_with(
            "cookies",
            {"domain": "example.com", "path": "/app", "name": "sid"},
            start_if_needed=False,
        )

    def test_cookies_clear_with_name(self):
        _, sc = invoke(["cookies-clear", "--name", "sid"])
        sc.assert_called_once_with(
            "cookies_clear",
            {"name": "sid"},
            start_if_needed=False,
        )

    def test_cookies_clear_with_domain_path(self):
        _, sc = invoke(["cookies-clear", "--domain", "example.com", "--path", "/app"])
        sc.assert_called_once_with(
            "cookies_clear",
            {"domain": "example.com", "path": "/app"},
            start_if_needed=False,
        )

    def test_cookie_set_minimal(self):
        _, sc = invoke(["cookie-set", "sid", "abc123"])
        sc.assert_called_once_with("cookie_set", {
            "name": "sid", "value": "abc123", "url": None, "domain": None,
            "path": "/", "expires": None, "http_only": False, "secure": False, "same_site": None,
        }, start_if_needed=False)

    def test_cookie_set_full(self):
        _, sc = invoke([
            "cookie-set", "sid", "abc123",
            "--domain", "example.com",
            "--http-only", "--secure",
            "--same-site", "Strict",
        ])
        sc.assert_called_once_with("cookie_set", {
            "name": "sid", "value": "abc123", "url": None, "domain": "example.com",
            "path": "/", "expires": None, "http_only": True, "secure": True, "same_site": "Strict",
        }, start_if_needed=False)

    # ── Verify ────────────────────────────────────────────────────────────────

    def test_verify_visible(self):
        _, sc = invoke(["verify-visible", "button", "Submit"])
        sc.assert_called_once_with("verify_visible", {"role": "button", "name": "Submit", "timeout": 5.0}, start_if_needed=False)

    def test_verify_visible_custom_timeout(self):
        _, sc = invoke(["verify-visible", "button", "OK", "--timeout", "10"])
        sc.assert_called_once_with("verify_visible", {"role": "button", "name": "OK", "timeout": 10.0}, start_if_needed=False)

    def test_verify_text(self):
        _, sc = invoke(["verify-text", "Hello world"])
        sc.assert_called_once_with("verify_text", {"text": "Hello world", "exact": False, "timeout": 5.0}, start_if_needed=False)

    def test_verify_text_exact(self):
        _, sc = invoke(["verify-text", "Hello", "--exact"])
        sc.assert_called_once_with("verify_text", {"text": "Hello", "exact": True, "timeout": 5.0}, start_if_needed=False)

    def test_verify_value(self):
        _, sc = invoke(["verify-value", "@e1", "expected"])
        sc.assert_called_once_with("verify_value", {"ref": "e1", "expected": "expected"}, start_if_needed=False)

    def test_verify_state(self):
        _, sc = invoke(["verify-state", "@e2", "visible"])
        sc.assert_called_once_with("verify_state", {"ref": "e2", "state": "visible"}, start_if_needed=False)

    def test_verify_url(self):
        _, sc = invoke(["verify-url", "https://example.com"])
        sc.assert_called_once_with("verify_url", {"url": "https://example.com", "exact": False}, start_if_needed=False)

    def test_verify_url_exact(self):
        _, sc = invoke(["verify-url", "https://example.com", "--exact"])
        sc.assert_called_once_with("verify_url", {"url": "https://example.com", "exact": True}, start_if_needed=False)

    def test_verify_title(self):
        _, sc = invoke(["verify-title", "My Page"])
        sc.assert_called_once_with("verify_title", {"title": "My Page", "exact": False}, start_if_needed=False)

    # ── Developer ─────────────────────────────────────────────────────────────

    def test_eval(self):
        _, sc = invoke(["eval", "() => document.title"])
        sc.assert_called_once_with("eval", {"code": "() => document.title"}, start_if_needed=False)

    def test_eval_on(self):
        _, sc = invoke(["eval-on", "@e1", "el => el.textContent"])
        sc.assert_called_once_with("eval_on", {"ref": "e1", "code": "el => el.textContent"}, start_if_needed=False)

    def test_trace_start_defaults(self):
        _, sc = invoke(["trace-start"])
        sc.assert_called_once_with("trace_start", {"no_screenshots": False, "no_snapshots": False}, start_if_needed=False)

    def test_trace_start_no_screenshots(self):
        _, sc = invoke(["trace-start", "--no-screenshots"])
        sc.assert_called_once_with("trace_start", {"no_screenshots": True, "no_snapshots": False}, start_if_needed=False)

    def test_trace_stop_absolutizes_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["trace-stop", "trace.zip"])
        sc.assert_called_once_with("trace_stop", {"path": str(tmp_path / "trace.zip")}, start_if_needed=False)

    def test_trace_chunk(self):
        _, sc = invoke(["trace-chunk", "login flow"])
        sc.assert_called_once_with("trace_chunk", {"title": "login flow"}, start_if_needed=False)

    def test_video_start_defaults(self):
        _, sc = invoke(["video-start"])
        sc.assert_called_once_with("video_start", {"width": None, "height": None}, start_if_needed=False)

    def test_video_start_dimensions(self):
        _, sc = invoke(["video-start", "--width", "1280", "--height", "720"])
        sc.assert_called_once_with("video_start", {"width": 1280, "height": 720}, start_if_needed=False)

    def test_video_stop_no_path(self):
        _, sc = invoke(["video-stop"])
        sc.assert_called_once_with("video_stop", {"path": None}, start_if_needed=False)

    def test_video_stop_with_absolute_path(self):
        _, sc = invoke(["video-stop", "/tmp/video.webm"])
        sc.assert_called_once_with("video_stop", {"path": "/tmp/video.webm"}, start_if_needed=False)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def test_resize(self):
        _, sc = invoke(["resize", "1920", "1080"])
        sc.assert_called_once_with("resize", {"width": 1920, "height": 1080}, start_if_needed=False)

    def test_close_uses_start_if_needed_false(self):
        _, sc = invoke(["close"])
        sc.assert_called_once_with("close", {}, start_if_needed=False)

    # ── Error display ─────────────────────────────────────────────────────────

    def test_error_printed_on_exception(self):
        with patch(
            "bridgic.browser.cli._commands.send_command",
            side_effect=RuntimeError("daemon failed"),
        ):
            result = runner.invoke(cli, ["open", "https://example.com"])
        assert "Error: daemon failed" in result.output
        assert result.exit_code != 0

    def test_structured_error_printed_with_code(self):
        with patch(
            "bridgic.browser.cli._commands.send_command",
            side_effect=BridgicBrowserCommandError(
                command="open",
                code="NO_BROWSER_SESSION",
                message="No browser session is running.",
                details={"hint": "run open"},
                retryable=True,
            ),
        ):
            result = runner.invoke(cli, ["open", "https://example.com"])
        assert "Error[NO_BROWSER_SESSION]: No browser session is running." in result.output
        assert result.exit_code != 0

    def test_result_printed_on_success(self):
        with patch(
            "bridgic.browser.cli._commands.send_command",
            return_value="Navigated to: https://example.com",
        ):
            result = runner.invoke(cli, ["open", "https://example.com"])
        assert "Navigated to: https://example.com" in result.output
        assert result.exit_code == 0


# ─────────────────────────────────────────────────────────────────────────────
# Coverage: every CLI command is invokable
# ─────────────────────────────────────────────────────────────────────────────

def test_all_cli_commands_invokable():
    assert set(CLI_ALL_COMMANDS) == set(CLI_COMMAND_SAMPLE_ARGS)
    for command in CLI_ALL_COMMANDS:
        args = CLI_COMMAND_SAMPLE_ARGS[command]
        result, sc = invoke(args)
        assert result.exit_code == 0, f"Command failed: {args}"
        assert sc.called, f"send_command not called for: {args}"


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch
# ─────────────────────────────────────────────────────────────────────────────

class TestDaemonDispatch:
    async def test_unknown_command_returns_error(self):
        browser = make_browser()
        resp = await _dispatch(browser, "nonexistent", {})
        assert resp["status"] == "error"
        assert resp["success"] is False
        assert resp["error_code"] == "UNKNOWN_COMMAND"
        assert "nonexistent" in resp["result"]

    async def test_known_command_returns_ok(self):
        browser = make_browser()
        browser.navigate_to = AsyncMock(return_value="Navigated")
        resp = await _dispatch(browser, "open", {"url": "https://example.com"})
        assert resp["status"] == "ok"
        assert resp["success"] is True
        assert resp["error_code"] is None
        assert resp["result"] == "Navigated"

    async def test_known_command_business_failure_returns_error(self):
        browser = make_browser()
        browser.navigate_to = AsyncMock(
            side_effect=OperationError("Navigation failed: timeout", code="NAVIGATION_FAILED")
        )
        resp = await _dispatch(browser, "open", {"url": "https://example.com"})
        assert resp["status"] == "error"
        assert resp["success"] is False
        assert resp["error_code"] == "NAVIGATION_FAILED"
        assert "Navigation failed" in resp["result"]

    async def test_eval_command_keeps_ok_for_arbitrary_string(self):
        browser = make_browser()
        browser.evaluate_javascript = AsyncMock(return_value="Failed to load widget title")
        resp = await _dispatch(browser, "eval", {"code": "() => 'x'"})
        assert resp["status"] == "ok"
        assert resp["success"] is True
        assert resp["error_code"] is None
        assert "Failed to load widget title" in resp["result"]

    async def test_handler_exception_returns_error(self):
        browser = make_browser()
        browser.navigate_to = AsyncMock(side_effect=RuntimeError("boom"))
        resp = await _dispatch(browser, "open", {"url": "x"})
        assert resp["status"] == "error"
        assert resp["success"] is False
        assert resp["error_code"] == "HANDLER_EXCEPTION"
        assert "boom" in resp["result"]

    async def test_browser_closed_error_returns_hint(self):
        """Playwright 'browser has been closed' errors surface the recovery hint."""
        browser = make_browser()
        browser.navigate_to = AsyncMock(side_effect=Exception(
            "Page.goto: Target page, context or browser has been closed"
        ))
        resp = await _dispatch(browser, "open", {"url": "x"})
        assert resp["status"] == "error"
        assert resp["success"] is False
        assert resp["error_code"] == "BROWSER_CLOSED"
        assert "bridgic-browser close" in resp["result"]
        assert "bridgic-browser open" in resp["result"]


# ─────────────────────────────────────────────────────────────────────────────
# _is_browser_closed_error + _handle_snapshot (None guard)
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserClosedDetection:
    def test_detects_playwright_target_closed(self):
        exc = Exception("Page.goto: Target page, context or browser has been closed")
        assert _is_browser_closed_error(exc) is True

    def test_detects_browser_has_been_closed(self):
        assert _is_browser_closed_error(Exception("Browser has been closed")) is True

    def test_detects_connection_closed(self):
        assert _is_browser_closed_error(Exception("Connection closed")) is True

    def test_detects_target_closed(self):
        assert _is_browser_closed_error(Exception("Target closed")) is True

    def test_ignores_unrelated_errors(self):
        assert _is_browser_closed_error(Exception("Element not found")) is False
        assert _is_browser_closed_error(Exception("Timeout exceeded")) is False

    async def test_snapshot_none_returns_hint(self):
        """When get_snapshot_text() returns a failure message (browser gone), propagate it."""
        browser = make_browser()
        browser.get_snapshot_text = AsyncMock(return_value="Failed to get interface information")
        result = await _handle_snapshot(browser, {})
        assert "Failed to get interface information" in result

    async def test_snapshot_ok_returns_tree(self):
        """Normal snapshot path still returns the tree string."""
        browser = make_browser()
        browser.get_snapshot_text = AsyncMock(return_value="- button [ref=e1]")
        result = await _handle_snapshot(browser, {})
        assert result == "- button [ref=e1]"


# ─────────────────────────────────────────────────────────────────────────────
# socket path and cleanup helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestDaemonSocketSecurity:
    def test_default_socket_path_is_user_scoped(self, tmp_path):
        fake_browser_home = tmp_path / ".bridgic" / "bridgic-browser"
        with patch("bridgic.browser.cli._transport.BRIDGIC_BROWSER_HOME", fake_browser_home):
            path = _default_socket_path()
        assert path == str(fake_browser_home / "run" / "bridgic-browser.sock")

    def test_safe_remove_socket_removes_owned_socket(self):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.stat.return_value = SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600,
            st_uid=1000,
        )

        with patch("bridgic.browser.cli._transport.Path", return_value=mock_path):
            with patch("bridgic.browser.cli._transport.os.getuid", return_value=1000):
                _safe_remove_socket("/tmp/test.sock")

        mock_path.unlink.assert_called_once()

    def test_safe_remove_socket_rejects_non_socket_path(self):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.stat.return_value = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=1000,
        )

        with patch("bridgic.browser.cli._transport.Path", return_value=mock_path):
            with patch("bridgic.browser.cli._transport.os.getuid", return_value=1000):
                with pytest.raises(RuntimeError, match="non-socket"):
                    _safe_remove_socket("/tmp/not-a-socket")

    def test_safe_remove_socket_rejects_foreign_owner(self, monkeypatch):
        if not hasattr(os, "getuid"):
            pytest.skip("uid ownership checks are unavailable on this platform")

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.stat.return_value = SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600,
            st_uid=1000,
        )

        with patch("bridgic.browser.cli._transport.Path", return_value=mock_path):
            monkeypatch.setattr("bridgic.browser.cli._transport.os.getuid", lambda: 1001)
            with pytest.raises(PermissionError, match="owned by uid"):
                _safe_remove_socket("/tmp/foreign.sock")

        mock_path.unlink.assert_not_called()

    def test_safe_remove_socket_noop_when_path_missing(self):
        mock_path = MagicMock()
        mock_path.stat.side_effect = FileNotFoundError

        with patch("bridgic.browser.cli._transport.Path", return_value=mock_path):
            _safe_remove_socket("/tmp/missing.sock")

        mock_path.unlink.assert_not_called()

    def test_safe_remove_socket_noop_when_stat_races_with_delete(self):
        mock_path = MagicMock()
        mock_path.stat.side_effect = FileNotFoundError("raced")

        with patch("bridgic.browser.cli._transport.Path", return_value=mock_path):
            _safe_remove_socket("/tmp/raced.sock")

        mock_path.unlink.assert_not_called()

    def test_safe_remove_socket_noop_when_unlink_races_with_delete(self):
        mock_path = MagicMock()
        mock_path.stat.return_value = SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600,
            st_uid=1000,
        )
        mock_path.unlink.side_effect = FileNotFoundError("raced")

        with patch("bridgic.browser.cli._transport.Path", return_value=mock_path):
            with patch("bridgic.browser.cli._transport.os.getuid", return_value=1000):
                _safe_remove_socket("/tmp/raced.sock")

# ─────────────────────────────────────────────────────────────────────────────
# Daemon cleanup race condition
# ─────────────────────────────────────────────────────────────────────────────

class TestDaemonCleanupRace:
    """Tests for the run_daemon() cleanup ownership guard.

    When `close` responds immediately and a new daemon starts before the old
    daemon's browser.close() finishes, the old daemon must NOT delete the new
    daemon's socket and run-info file.

    The guard logic in run_daemon():
        current_info = read_run_info()
        if current_info is None or current_info.get("pid") == os.getpid():
            transport.cleanup()
            remove_run_info()
    """

    def _run_guard(self, current_info, my_pid, transport, remove_fn):
        """Replicate the run_daemon cleanup guard for white-box testing."""
        if current_info is None or current_info.get("pid") == my_pid:
            transport.cleanup()
            remove_fn()

    def test_cleanup_runs_when_run_info_is_gone(self):
        """If run-info was already removed (None), clean up is safe."""
        transport = MagicMock()
        remove_fn = MagicMock()
        self._run_guard(None, os.getpid(), transport, remove_fn)
        transport.cleanup.assert_called_once()
        remove_fn.assert_called_once()

    def test_cleanup_runs_when_pid_matches(self):
        """If run-info PID equals our PID, we are still the owner → clean up."""
        transport = MagicMock()
        remove_fn = MagicMock()
        self._run_guard({"pid": os.getpid()}, os.getpid(), transport, remove_fn)
        transport.cleanup.assert_called_once()
        remove_fn.assert_called_once()

    def test_cleanup_skipped_when_pid_differs(self):
        """If run-info PID belongs to a new daemon → skip cleanup to avoid corruption."""
        transport = MagicMock()
        remove_fn = MagicMock()
        my_pid = os.getpid()
        self._run_guard({"pid": my_pid + 1}, my_pid, transport, remove_fn)
        transport.cleanup.assert_not_called()
        remove_fn.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# _handle_connection
# ─────────────────────────────────────────────────────────────────────────────

class TestDaemonConnection:
    async def test_valid_command_writes_response(self):
        browser = make_browser()
        stop = asyncio.Event()
        req = json.dumps({"command": "back", "args": {}}).encode() + b"\n"

        with patch(
            "bridgic.browser.cli._daemon._dispatch",
            new=AsyncMock(return_value={
                "status": "ok",
                "success": True,
                "result": "went back",
                "error_code": None,
                "data": None,
                "meta": {},
            }),
        ):
            reader = make_reader(req)
            writer = make_writer()
            await _handle_connection(browser, reader, writer, stop)

        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "ok"
        assert resp["success"] is True
        assert resp["error_code"] is None
        assert resp["result"] == "went back"

    async def test_invalid_json_writes_error(self):
        browser = make_browser()
        stop = asyncio.Event()
        reader = make_reader(b"not valid json\n")
        writer = make_writer()

        await _handle_connection(browser, reader, writer, stop)

        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "error"
        assert resp["success"] is False
        assert resp["error_code"] == "INVALID_JSON"
        assert "Invalid JSON" in resp["result"]

    async def test_non_object_payload_writes_invalid_request(self):
        browser = make_browser()
        stop = asyncio.Event()
        reader = make_reader(b"[]\n")
        writer = make_writer()

        await _handle_connection(browser, reader, writer, stop)

        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "error"
        assert resp["error_code"] == "INVALID_REQUEST"
        assert "payload must be a JSON object" in resp["result"]

    async def test_non_object_args_writes_invalid_request(self):
        browser = make_browser()
        stop = asyncio.Event()
        req = json.dumps({"command": "back", "args": []}).encode() + b"\n"
        reader = make_reader(req)
        writer = make_writer()

        await _handle_connection(browser, reader, writer, stop)

        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "error"
        assert resp["error_code"] == "INVALID_REQUEST"
        assert "'args' must be a JSON object" in resp["result"]

    async def test_non_string_command_writes_invalid_request(self):
        browser = make_browser()
        stop = asyncio.Event()
        req = json.dumps({"command": 123, "args": {}}).encode() + b"\n"
        reader = make_reader(req)
        writer = make_writer()

        await _handle_connection(browser, reader, writer, stop)

        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "error"
        assert resp["error_code"] == "INVALID_REQUEST"
        assert "'command' must be a non-empty string" in resp["result"]

    async def test_eof_returns_silently(self):
        browser = make_browser()
        stop = asyncio.Event()
        reader = make_reader(b"")  # immediate EOF
        writer = make_writer()

        await _handle_connection(browser, reader, writer, stop)

        writer.write.assert_not_called()

    async def test_timeout_returns_without_response(self):
        browser = make_browser()
        stop = asyncio.Event()
        writer = make_writer()

        # Simulate readline timing out
        reader = MagicMock()
        reader.readline = AsyncMock()

        with patch(
            "bridgic.browser.cli._daemon.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            await _handle_connection(browser, reader, writer, stop)

        writer.write.assert_not_called()

    async def test_close_command_sets_stop_event(self):
        browser = make_browser()
        stop = asyncio.Event()
        req = json.dumps({"command": "close", "args": {}}).encode() + b"\n"
        reader = make_reader(req)
        writer = make_writer()

        await _handle_connection(browser, reader, writer, stop)

        assert stop.is_set()
        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "ok"
        assert resp["success"] is True
        assert resp["error_code"] is None

    async def test_close_command_includes_report_path(self):
        """close responds immediately with report path and sets stop_event."""
        browser = make_browser()
        browser.inspect_pending_close_artifacts = MagicMock(return_value={
            "session_dir": "/tmp/close-20240101-120000-abcd",
            "trace": [],
            "video": [],
        })
        stop = asyncio.Event()
        req = json.dumps({"command": "close", "args": {}}).encode() + b"\n"
        reader = make_reader(req)
        writer = make_writer()

        await _handle_connection(browser, reader, writer, stop)

        assert stop.is_set()
        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "ok"
        assert resp["success"] is True
        assert "close-report.json" in resp["result"]
        assert "Background" in resp["result"] or "background" in resp["result"]

    async def test_writer_always_closed(self):
        """writer.close() must be called even when an exception occurs."""
        browser = make_browser()
        stop = asyncio.Event()
        writer = make_writer()

        reader = MagicMock()
        reader.readline = AsyncMock()

        with patch(
            "bridgic.browser.cli._daemon.asyncio.wait_for",
            side_effect=Exception("unexpected"),
        ):
            await _handle_connection(browser, reader, writer, stop)

        writer.close.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Daemon handler smoke tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDaemonHandlers:
    async def test_handle_open_calls_navigate(self):
        browser = make_browser()
        browser.navigate_to = AsyncMock(return_value="Navigated to: https://example.com")
        result = await _handle_open(browser, {"url": "https://example.com"})
        browser.navigate_to.assert_awaited_once_with("https://example.com")
        assert result == "Navigated to: https://example.com"

    async def test_handle_snapshot_default(self):
        browser = make_browser()
        browser.get_snapshot_text = AsyncMock(return_value="- heading 'Example' [ref=e1]")

        result = await _handle_snapshot(browser, {})

        browser.get_snapshot_text.assert_awaited_once_with(
            limit=10000, interactive=False, full_page=True, file=None
        )
        assert result == "- heading 'Example' [ref=e1]"

    async def test_handle_snapshot_interactive(self):
        browser = make_browser()
        browser.get_snapshot_text = AsyncMock(return_value="- button 'Submit' [ref=e1]")

        await _handle_snapshot(browser, {"interactive": True})

        browser.get_snapshot_text.assert_awaited_once_with(
            limit=10000, interactive=True, full_page=True, file=None
        )

    async def test_handle_snapshot_full_page_false(self):
        """full_page=False is passed through to get_snapshot_text."""
        browser = make_browser()
        browser.get_snapshot_text = AsyncMock(return_value="- button [ref=e1]")
        result = await _handle_snapshot(browser, {"full_page": False})
        browser.get_snapshot_text.assert_awaited_once_with(
            limit=10000, interactive=False, full_page=False, file=None
        )
        assert result == "- button [ref=e1]"

    async def test_handle_snapshot_limit(self):
        """limit is passed through to get_snapshot_text."""
        browser = make_browser()
        browser.get_snapshot_text = AsyncMock(return_value="A" * 90)
        result = await _handle_snapshot(browser, {"limit": 5000})
        browser.get_snapshot_text.assert_awaited_once_with(
            limit=5000, interactive=False, full_page=True, file=None
        )
        assert result == "A" * 90

    async def test_handle_snapshot_file(self):
        """file is passed through to get_snapshot_text."""
        browser = make_browser()
        browser.get_snapshot_text = AsyncMock(return_value="[notice] ...")
        result = await _handle_snapshot(browser, {"file": "/tmp/snap.txt"})
        browser.get_snapshot_text.assert_awaited_once_with(
            limit=10000, interactive=False, full_page=True, file="/tmp/snap.txt"
        )
        assert result == "[notice] ..."

    async def test_handle_click_calls_tool(self):
        browser = make_browser()
        browser.click_element_by_ref = AsyncMock(return_value="Clicked e2")
        result = await _handle_click(browser, {"ref": "e2"})
        browser.click_element_by_ref.assert_awaited_once_with("e2")
        assert result == "Clicked e2"

    async def test_handle_fill_calls_tool(self):
        browser = make_browser()
        browser.input_text_by_ref = AsyncMock(return_value="Input text 'hello'")
        result = await _handle_fill(browser, {"ref": "e3", "text": "hello"})
        browser.input_text_by_ref.assert_awaited_once_with("e3", "hello", submit=False)
        assert "hello" in result

    async def test_handle_fill_with_submit(self):
        browser = make_browser()
        browser.input_text_by_ref = AsyncMock(return_value="Input text 'hello'")
        await _handle_fill(browser, {"ref": "e3", "text": "hello", "submit": True})
        browser.input_text_by_ref.assert_awaited_once_with("e3", "hello", submit=True)

    async def test_handle_screenshot_passes_full_page(self):
        browser = make_browser()
        browser.take_screenshot = AsyncMock(return_value="Screenshot saved to: /tmp/x.png")
        await _handle_screenshot(browser, {"path": "/tmp/x.png", "full_page": True})
        browser.take_screenshot.assert_awaited_once_with(filename="/tmp/x.png", full_page=True)

    async def test_handle_screenshot_default_full_page_false(self):
        browser = make_browser()
        browser.take_screenshot = AsyncMock(return_value="ok")
        await _handle_screenshot(browser, {"path": "/tmp/x.png"})
        _, kwargs = browser.take_screenshot.call_args
        assert kwargs.get("full_page") is False

    async def test_handle_scroll_passes_deltas(self):
        browser = make_browser()
        browser.mouse_wheel = AsyncMock(return_value="Scrolled")
        await _handle_scroll(browser, {"delta_x": 10, "delta_y": 300})
        browser.mouse_wheel.assert_awaited_once_with(delta_x=10, delta_y=300)

    async def test_handle_wait_time_seconds(self):
        browser = make_browser()
        browser.wait_for = AsyncMock(return_value="Waited for 2.5 seconds")
        await _handle_wait(browser, {"seconds": 2.5})
        browser.wait_for.assert_awaited_once_with(
            time_seconds=2.5, text=None, text_gone=None
        )

    async def test_handle_wait_text(self):
        browser = make_browser()
        browser.wait_for = AsyncMock(return_value="Text appeared")
        await _handle_wait(browser, {"text": "Done"})
        browser.wait_for.assert_awaited_once_with(
            time_seconds=None, text="Done", text_gone=None
        )

    async def test_handle_search_default_engine(self):
        browser = make_browser()
        browser.search = AsyncMock(return_value="Searched")
        await _handle_search(browser, {"query": "python"})
        browser.search.assert_awaited_once_with("python", "duckduckgo")

    async def test_handle_close_tab_none_page_id(self):
        """page_id=None should close the current tab."""
        browser = make_browser()
        browser.close_tab = AsyncMock(return_value="Tab closed")
        await _handle_close_tab(browser, {})
        browser.close_tab.assert_awaited_once_with(page_id=None)

    async def test_handle_pdf_passes_path(self):
        browser = make_browser()
        browser.save_pdf = AsyncMock(return_value="PDF saved")
        await _handle_pdf(browser, {"path": "/tmp/out.pdf"})
        browser.save_pdf.assert_awaited_once_with(filename="/tmp/out.pdf")

    async def test_handle_scroll_into_view(self):
        browser = make_browser()
        browser.scroll_element_into_view_by_ref = AsyncMock(return_value="Scrolled e5 into view")
        await _handle_scroll_into_view(browser, {"ref": "e5"})
        browser.scroll_element_into_view_by_ref.assert_awaited_once_with("e5")

    async def test_handle_drag(self):
        browser = make_browser()
        browser.drag_element_by_ref = AsyncMock(return_value="Dragged e1 to e2")
        await _handle_drag(browser, {"start_ref": "e1", "end_ref": "e2"})
        browser.drag_element_by_ref.assert_awaited_once_with("e1", "e2")

    async def test_handle_options(self):
        browser = make_browser()
        browser.get_dropdown_options_by_ref = AsyncMock(return_value="Option A\nOption B")
        await _handle_options(browser, {"ref": "e3"})
        browser.get_dropdown_options_by_ref.assert_awaited_once_with("e3")

    async def test_handle_upload(self):
        browser = make_browser()
        browser.upload_file_by_ref = AsyncMock(return_value="File uploaded")
        await _handle_upload(browser, {"ref": "e4", "path": "/tmp/file.txt"})
        browser.upload_file_by_ref.assert_awaited_once_with("e4", "/tmp/file.txt")

    async def test_handle_fill_form_valid_json(self):
        browser = make_browser()
        browser.fill_form = AsyncMock(return_value="Form filled")
        fields_json = '[{"ref": "e1", "value": "hello"}]'
        await _handle_fill_form(browser, {"fields": fields_json, "submit": False})
        browser.fill_form.assert_awaited_once_with([{"ref": "e1", "value": "hello"}], submit=False)

    async def test_handle_fill_form_invalid_json_raises(self):
        browser = make_browser()
        with pytest.raises(InvalidInputError) as exc_info:
            await _handle_fill_form(browser, {"fields": "not json", "submit": False})
        assert exc_info.value.code == "INVALID_JSON_FIELDS"

    async def test_handle_type_text(self):
        browser = make_browser()
        browser.type_text = AsyncMock(return_value="Typed")
        await _handle_type_text(browser, {"text": "hello", "submit": True})
        browser.type_text.assert_awaited_once_with("hello", submit=True)

    async def test_handle_key_down(self):
        browser = make_browser()
        browser.key_down = AsyncMock(return_value="Key down: Shift")
        await _handle_key_down(browser, {"key": "Shift"})
        browser.key_down.assert_awaited_once_with("Shift")

    async def test_handle_key_up(self):
        browser = make_browser()
        browser.key_up = AsyncMock(return_value="Key up: Shift")
        await _handle_key_up(browser, {"key": "Shift"})
        browser.key_up.assert_awaited_once_with("Shift")

    async def test_handle_mouse_move(self):
        browser = make_browser()
        browser.mouse_move = AsyncMock(return_value="Mouse moved")
        await _handle_mouse_move(browser, {"x": 100.0, "y": 200.0})
        browser.mouse_move.assert_awaited_once_with(100.0, 200.0)

    async def test_handle_mouse_click(self):
        browser = make_browser()
        browser.mouse_click = AsyncMock(return_value="Clicked")
        await _handle_mouse_click(browser, {"x": 50.0, "y": 60.0, "button": "right", "count": 2})
        browser.mouse_click.assert_awaited_once_with(50.0, 60.0, button="right", click_count=2)

    async def test_handle_mouse_drag(self):
        browser = make_browser()
        browser.mouse_drag = AsyncMock(return_value="Dragged")
        await _handle_mouse_drag(browser, {"x1": 10.0, "y1": 20.0, "x2": 100.0, "y2": 200.0})
        browser.mouse_drag.assert_awaited_once_with(10.0, 20.0, 100.0, 200.0)

    async def test_handle_mouse_down(self):
        browser = make_browser()
        browser.mouse_down = AsyncMock(return_value="Mouse down")
        await _handle_mouse_down(browser, {"button": "right"})
        browser.mouse_down.assert_awaited_once_with(button="right")

    async def test_handle_mouse_up(self):
        browser = make_browser()
        browser.mouse_up = AsyncMock(return_value="Mouse up")
        await _handle_mouse_up(browser, {"button": "left"})
        browser.mouse_up.assert_awaited_once_with(button="left")

    async def test_handle_wait_network(self):
        browser = make_browser()
        browser.wait_for_network_idle = AsyncMock(return_value="Network idle")
        await _handle_wait_network(browser, {"timeout": 5.0})
        browser.wait_for_network_idle.assert_awaited_once_with(timeout=5.0)

    async def test_handle_console_start(self):
        browser = make_browser()
        browser.start_console_capture = AsyncMock(return_value="Console capture started")
        await _handle_console_start(browser, {})
        browser.start_console_capture.assert_awaited_once_with()

    async def test_handle_console_stop(self):
        browser = make_browser()
        browser.stop_console_capture = AsyncMock(return_value="Console capture stopped")
        await _handle_console_stop(browser, {})
        browser.stop_console_capture.assert_awaited_once_with()

    async def test_handle_console(self):
        browser = make_browser()
        browser.get_console_messages = AsyncMock(return_value="[error] boom")
        await _handle_console(browser, {"filter": "error", "clear": True})
        browser.get_console_messages.assert_awaited_once_with(type_filter="error", clear=True)

    async def test_handle_network_start(self):
        browser = make_browser()
        browser.start_network_capture = AsyncMock(return_value="Network capture started")
        await _handle_network_start(browser, {})
        browser.start_network_capture.assert_awaited_once_with()

    async def test_handle_network_stop(self):
        browser = make_browser()
        browser.stop_network_capture = AsyncMock(return_value="Network capture stopped")
        await _handle_network_stop(browser, {})
        browser.stop_network_capture.assert_awaited_once_with()

    async def test_handle_network(self):
        browser = make_browser()
        browser.get_network_requests = AsyncMock(return_value="GET /api")
        await _handle_network(browser, {"include_static": True, "clear": False})
        browser.get_network_requests.assert_awaited_once_with(include_static=True, clear=False)

    async def test_handle_dialog_setup(self):
        browser = make_browser()
        browser.setup_dialog_handler = AsyncMock(return_value="Dialog handler set")
        await _handle_dialog_setup(browser, {"action": "dismiss", "text": None})
        browser.setup_dialog_handler.assert_awaited_once_with(
            default_action="dismiss", default_prompt_text=None
        )

    async def test_handle_dialog_accept(self):
        browser = make_browser()
        browser.handle_dialog = AsyncMock(return_value="Dialog accepted")
        await _handle_dialog(browser, {"dismiss": False, "text": None})
        browser.handle_dialog.assert_awaited_once_with(accept=True, prompt_text=None)

    async def test_handle_dialog_dismiss(self):
        browser = make_browser()
        browser.handle_dialog = AsyncMock(return_value="Dialog dismissed")
        await _handle_dialog(browser, {"dismiss": True, "text": "yes"})
        browser.handle_dialog.assert_awaited_once_with(accept=False, prompt_text="yes")

    async def test_handle_dialog_remove(self):
        browser = make_browser()
        browser.remove_dialog_handler = AsyncMock(return_value="Handler removed")
        await _handle_dialog_remove(browser, {})
        browser.remove_dialog_handler.assert_awaited_once_with()

    async def test_handle_storage_save(self):
        browser = make_browser()
        browser.save_storage_state = AsyncMock(return_value="Saved")
        await _handle_storage_save(browser, {"path": "/tmp/state.json"})
        browser.save_storage_state.assert_awaited_once_with(filename="/tmp/state.json")

    async def test_handle_storage_load(self):
        browser = make_browser()
        browser.restore_storage_state = AsyncMock(return_value="Loaded")
        await _handle_storage_load(browser, {"path": "/tmp/state.json"})
        browser.restore_storage_state.assert_awaited_once_with("/tmp/state.json")

    async def test_handle_cookies_clear(self):
        browser = make_browser()
        browser.clear_cookies = AsyncMock(return_value="Cookies cleared")
        await _handle_cookies_clear(browser, {})
        browser.clear_cookies.assert_awaited_once_with(name=None, domain=None, path=None)

    async def test_handle_cookies_no_url(self):
        browser = make_browser()
        browser.get_cookies = AsyncMock(return_value="[]")
        await _handle_cookies(browser, {})
        browser.get_cookies.assert_awaited_once_with(urls=None, name=None, domain=None, path=None)

    async def test_handle_cookies_with_url(self):
        browser = make_browser()
        browser.get_cookies = AsyncMock(return_value="[]")
        await _handle_cookies(browser, {"url": "https://example.com"})
        browser.get_cookies.assert_awaited_once_with(
            urls=["https://example.com"], name=None, domain=None, path=None
        )

    async def test_handle_cookies_with_filters(self):
        browser = make_browser()
        browser.get_cookies = AsyncMock(return_value="[]")
        await _handle_cookies(browser, {"domain": "example.com", "path": "/app", "name": "sid"})
        browser.get_cookies.assert_awaited_once_with(
            urls=None, name="sid", domain="example.com", path="/app"
        )

    async def test_handle_cookie_set(self):
        browser = make_browser()
        browser.set_cookie = AsyncMock(return_value="Cookie set")
        await _handle_cookie_set(browser, {
            "name": "sid", "value": "abc", "url": None, "domain": None,
            "path": "/", "expires": None, "http_only": False, "secure": False, "same_site": None,
        })
        browser.set_cookie.assert_awaited_once_with(
            name="sid", value="abc", url=None, domain=None,
            path="/", expires=None, http_only=False, secure=False, same_site=None,
        )

    async def test_handle_verify_visible(self):
        browser = make_browser()
        browser.verify_element_visible = AsyncMock(return_value="PASS: element visible")
        result = await _handle_verify_visible(browser, {"role": "button", "name": "Submit", "timeout": 5.0})
        browser.verify_element_visible.assert_awaited_once_with(
            role="button", accessible_name="Submit", timeout=5.0
        )
        assert result == "PASS: element visible"

    async def test_handle_verify_text(self):
        browser = make_browser()
        browser.verify_text_visible = AsyncMock(return_value="PASS: text visible")
        await _handle_verify_text(browser, {"text": "Hello", "exact": True, "timeout": 3.0})
        browser.verify_text_visible.assert_awaited_once_with(text="Hello", exact=True, timeout=3.0)

    async def test_handle_verify_value(self):
        browser = make_browser()
        browser.verify_value = AsyncMock(return_value="PASS: value matches")
        await _handle_verify_value(browser, {"ref": "e1", "expected": "hello"})
        browser.verify_value.assert_awaited_once_with("e1", "hello")

    async def test_handle_verify_state(self):
        browser = make_browser()
        browser.verify_element_state = AsyncMock(return_value="PASS: state matches")
        await _handle_verify_state(browser, {"ref": "e2", "state": "visible"})
        browser.verify_element_state.assert_awaited_once_with("e2", "visible")

    async def test_handle_verify_url(self):
        browser = make_browser()
        browser.verify_url = AsyncMock(return_value="PASS: url matches")
        await _handle_verify_url(browser, {"url": "https://example.com", "exact": False})
        browser.verify_url.assert_awaited_once_with("https://example.com", exact=False)

    async def test_handle_verify_title(self):
        browser = make_browser()
        browser.verify_title = AsyncMock(return_value="PASS: title matches")
        await _handle_verify_title(browser, {"title": "My Page", "exact": True})
        browser.verify_title.assert_awaited_once_with("My Page", exact=True)

    async def test_handle_eval(self):
        browser = make_browser()
        browser.evaluate_javascript = AsyncMock(return_value="42")
        result = await _handle_eval(browser, {"code": "() => 42"})
        browser.evaluate_javascript.assert_awaited_once_with("() => 42")
        assert result == "42"

    async def test_handle_eval_on(self):
        browser = make_browser()
        browser.evaluate_javascript_on_ref = AsyncMock(return_value="el text")
        await _handle_eval_on(browser, {"ref": "e1", "code": "el => el.textContent"})
        browser.evaluate_javascript_on_ref.assert_awaited_once_with("e1", "el => el.textContent")

    async def test_handle_trace_start(self):
        browser = make_browser()
        browser.start_tracing = AsyncMock(return_value="Tracing started")
        await _handle_trace_start(browser, {"no_screenshots": True, "no_snapshots": False})
        browser.start_tracing.assert_awaited_once_with(screenshots=False, snapshots=True)

    async def test_handle_trace_stop(self):
        browser = make_browser()
        browser.stop_tracing = AsyncMock(return_value="Trace saved")
        await _handle_trace_stop(browser, {"path": "/tmp/trace.zip"})
        browser.stop_tracing.assert_awaited_once_with(filename="/tmp/trace.zip")

    async def test_handle_trace_chunk(self):
        browser = make_browser()
        browser.add_trace_chunk = AsyncMock(return_value="Chunk added")
        await _handle_trace_chunk(browser, {"title": "login"})
        browser.add_trace_chunk.assert_awaited_once_with(title="login")

    async def test_handle_video_start(self):
        browser = make_browser()
        browser.start_video = AsyncMock(return_value="Recording started")
        await _handle_video_start(browser, {"width": 1280, "height": 720})
        browser.start_video.assert_awaited_once_with(width=1280, height=720)

    async def test_handle_video_stop(self):
        browser = make_browser()
        browser.stop_video = AsyncMock(return_value="Video saved")
        await _handle_video_stop(browser, {"path": "/tmp/video.webm"})
        browser.stop_video.assert_awaited_once_with(filename="/tmp/video.webm")

    async def test_handle_resize(self):
        browser = make_browser()
        browser.browser_resize = AsyncMock(return_value="Resized to 1920x1080")
        await _handle_resize(browser, {"width": 1920, "height": 1080})
        browser.browser_resize.assert_awaited_once_with(1920, 1080)

    async def test_verify_fail_result_is_classified_as_error(self):
        """verify_* handler exceptions should surface as error via _dispatch."""
        browser = make_browser()
        browser.verify_url = AsyncMock(
            side_effect=VerificationError("URL mismatch", code="VERIFICATION_FAILED")
        )
        resp = await _dispatch(browser, "verify_url", {"url": "https://example.com", "exact": False})
        assert resp["status"] == "error"
        assert resp["success"] is False
        assert resp["error_code"] == "VERIFICATION_FAILED"

    async def test_eval_on_arbitrary_result_not_misclassified(self):
        """eval_on returning a string starting with 'Failed to' must not be an error."""
        browser = make_browser()
        browser.evaluate_javascript_on_ref = AsyncMock(return_value="Failed to parse: intentional")
        resp = await _dispatch(browser, "eval_on", {"ref": "e1", "code": "el => el.dataset.error"})
        assert resp["status"] == "ok"
        assert resp["success"] is True


# ─────────────────────────────────────────────────────────────────────────────
# _client — send_command guard
# ─────────────────────────────────────────────────────────────────────────────

class TestClientSendCommand:
    def test_raises_when_socket_missing_and_no_start(self):
        from bridgic.browser.cli._client import send_command

        with patch("bridgic.browser.cli._client.RUN_INFO_PATH") as mock_rip:
            mock_rip.exists.return_value = False
            with pytest.raises(BridgicBrowserCommandError) as exc_info:
                send_command("close", start_if_needed=False)
        assert exc_info.value.code == "NO_BROWSER_SESSION"

    def test_start_if_needed_false_proceeds_when_socket_present(self):
        """When run info exists and daemon is reachable, proceed to send command."""
        from bridgic.browser.cli._client import send_command

        async def mock_send_command(_cmd: str, _args: dict) -> str:
            return "Daemon shutting down"

        with patch("bridgic.browser.cli._client.RUN_INFO_PATH") as mock_rip:
            mock_rip.exists.return_value = True
            with patch("bridgic.browser.cli._client._probe_socket_sync", return_value=True):
                with patch(
                    "bridgic.browser.cli._client._send_command_async",
                    mock_send_command,
                ):
                    result = send_command("close", start_if_needed=False)
        assert result == "Daemon shutting down"

    def test_start_if_needed_false_raises_when_socket_is_stale(self):
        from bridgic.browser.cli._client import send_command

        with patch("bridgic.browser.cli._client.RUN_INFO_PATH") as mock_rip:
            mock_rip.exists.return_value = True
            with patch("bridgic.browser.cli._client._probe_socket_sync", return_value=False):
                with pytest.raises(BridgicBrowserCommandError) as exc_info:
                    send_command("close", start_if_needed=False)
        assert exc_info.value.code == "NO_BROWSER_SESSION"

    def test_start_if_needed_true_wraps_daemon_start_failure(self):
        from bridgic.browser.cli._client import send_command

        with patch(
            "bridgic.browser.cli._client.ensure_daemon_running",
            side_effect=RuntimeError("spawn failed"),
        ):
            with pytest.raises(BridgicBrowserCommandError) as exc_info:
                send_command("snapshot")
        assert exc_info.value.code == "DAEMON_START_FAILED"

    def test_send_command_wraps_connection_error_as_no_session(self):
        from bridgic.browser.cli._client import send_command

        async def mock_send_command(_cmd: str, _args: dict) -> str:
            raise ConnectionRefusedError("refused")

        with patch("bridgic.browser.cli._client.RUN_INFO_PATH") as mock_rip:
            mock_rip.exists.return_value = True
            with patch(
                "bridgic.browser.cli._client._probe_socket_sync",
                return_value=True,
            ):
                with patch(
                    "bridgic.browser.cli._client._send_command_async",
                    mock_send_command,
                ):
                    with pytest.raises(BridgicBrowserCommandError) as exc_info:
                        send_command("close", start_if_needed=False)
        assert exc_info.value.code == "NO_BROWSER_SESSION"

    def test_start_if_needed_true_calls_ensure_daemon(self):
        from bridgic.browser.cli._client import send_command

        async def mock_send_command(_cmd: str, _args: dict) -> str:
            return "ok"

        with patch("bridgic.browser.cli._client.ensure_daemon_running") as mock_ensure:
            with patch(
                "bridgic.browser.cli._client._send_command_async",
                mock_send_command,
            ):
                send_command("snapshot")
        mock_ensure.assert_called_once()

    def test_ensure_daemon_running_removes_stale_socket_then_spawns(self):
        from bridgic.browser.cli import _client

        with patch("bridgic.browser.cli._client.RUN_INFO_PATH") as mock_rip:
            mock_rip.exists.return_value = True
            with patch(
                "bridgic.browser.cli._client._probe_socket_sync",
                return_value=False,
            ):
                with patch(
                    "bridgic.browser.cli._client.read_run_info",
                    return_value={"transport": "unix", "socket": "/tmp/test.sock"},
                ):
                    with patch("bridgic.browser.cli._client._safe_remove_socket") as mock_remove:
                        with patch("bridgic.browser.cli._client.remove_run_info") as mock_rm_info:
                            with patch("bridgic.browser.cli._client._spawn_daemon") as mock_spawn:
                                _client.ensure_daemon_running()

        mock_remove.assert_called_once_with("/tmp/test.sock")
        mock_rm_info.assert_called_once()
        mock_spawn.assert_called_once()

    def test_ensure_daemon_running_raises_when_stale_socket_is_unsafe(self):
        from bridgic.browser.cli import _client

        with patch("bridgic.browser.cli._client.RUN_INFO_PATH") as mock_rip:
            mock_rip.exists.return_value = True
            with patch(
                "bridgic.browser.cli._client._probe_socket_sync",
                return_value=False,
            ):
                with patch(
                    "bridgic.browser.cli._client.read_run_info",
                    return_value={"transport": "unix", "socket": "/tmp/test.sock"},
                ):
                    with patch(
                        "bridgic.browser.cli._client._safe_remove_socket",
                        side_effect=PermissionError("owned by another user"),
                    ):
                        with pytest.raises(RuntimeError, match="cannot remove it safely"):
                            _client.ensure_daemon_running()

    def test_spawn_daemon_includes_output_tail_on_timeout(self):
        import io
        from bridgic.browser.cli import _client

        fake_proc = MagicMock()
        fake_proc.stdout = io.BytesIO(b"startup failed\n")

        with patch("bridgic.browser.cli._client.subprocess.Popen", return_value=fake_proc):
            with pytest.raises(RuntimeError) as exc_info:
                _client._spawn_daemon()
        assert "Daemon output (tail):" in str(exc_info.value)
        assert "python -m playwright install" in str(exc_info.value)
        assert "Daemon log:" in str(exc_info.value)

    def test_spawn_daemon_clear_user_data_injects_env(self):
        """_spawn_daemon(clear_user_data=True) injects clear_user_data into BRIDGIC_BROWSER_JSON."""
        import io, json
        from bridgic.browser.cli import _client

        captured_env: dict = {}

        def fake_popen(_cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = MagicMock()
            # Emit READY_SIGNAL so _spawn_daemon completes without timeout
            proc.stdout = io.BytesIO(b"BRIDGIC_DAEMON_READY\n")
            return proc

        with patch("bridgic.browser.cli._client.subprocess.Popen", side_effect=fake_popen):
            _client._spawn_daemon(clear_user_data=True)

        raw = captured_env.get("BRIDGIC_BROWSER_JSON", "{}")
        merged = json.loads(raw)
        assert merged.get("clear_user_data") is True
        assert "headless" not in merged  # headed flag not set

    def test_spawn_daemon_headed_and_clear_user_data_both_inject(self):
        """_spawn_daemon(headed=True, clear_user_data=True) sets both keys in BRIDGIC_BROWSER_JSON."""
        import io, json
        from bridgic.browser.cli import _client

        captured_env: dict = {}

        def fake_popen(_cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = MagicMock()
            proc.stdout = io.BytesIO(b"BRIDGIC_DAEMON_READY\n")
            return proc

        with patch("bridgic.browser.cli._client.subprocess.Popen", side_effect=fake_popen):
            _client._spawn_daemon(headed=True, clear_user_data=True)

        raw = captured_env.get("BRIDGIC_BROWSER_JSON", "{}")
        merged = json.loads(raw)
        assert merged.get("headless") is False
        assert merged.get("clear_user_data") is True

    @pytest.mark.asyncio
    async def test_send_command_async_uses_success_field_when_present(self):
        from bridgic.browser.cli._client import _send_command_async

        reader = make_reader(b'{"status":"ok","success":false,"result":"x"}\n')
        writer = make_writer()

        mock_transport = MagicMock()
        mock_transport.open_connection = AsyncMock(return_value=(reader, writer))
        mock_transport.inject_auth = lambda req: req

        with patch("bridgic.browser.cli._client.get_transport", return_value=mock_transport):
            with pytest.raises(BridgicBrowserCommandError) as exc_info:
                await _send_command_async("snapshot", {})
        assert exc_info.value.message == "x"
        assert exc_info.value.code == "DAEMON_ERROR"

    @pytest.mark.asyncio
    async def test_send_command_async_falls_back_to_status_for_legacy_responses(self):
        from bridgic.browser.cli._client import _send_command_async

        reader = make_reader(b'{"status":"error","result":"legacy error"}\n')
        writer = make_writer()

        mock_transport = MagicMock()
        mock_transport.open_connection = AsyncMock(return_value=(reader, writer))
        mock_transport.inject_auth = lambda req: req

        with patch("bridgic.browser.cli._client.get_transport", return_value=mock_transport):
            with pytest.raises(BridgicBrowserCommandError) as exc_info:
                await _send_command_async("snapshot", {})
        assert exc_info.value.message == "legacy error"
        assert exc_info.value.code == "DAEMON_ERROR"

    @pytest.mark.asyncio
    async def test_send_command_async_times_out_when_daemon_never_replies(self):
        from bridgic.browser.cli._client import _send_command_async

        reader = MagicMock()
        reader.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        writer = make_writer()

        mock_transport = MagicMock()
        mock_transport.open_connection = AsyncMock(return_value=(reader, writer))
        mock_transport.inject_auth = lambda req: req

        with (
            patch("bridgic.browser.cli._client.get_transport", return_value=mock_transport),
            patch("bridgic.browser.cli._client._DAEMON_RESPONSE_TIMEOUT", 0.01),
            patch("bridgic.browser.cli._client.asyncio.wait_for", side_effect=asyncio.TimeoutError),
        ):
            with pytest.raises(BridgicBrowserCommandError) as exc_info:
                await _send_command_async("snapshot", {})
        assert exc_info.value.code == "DAEMON_RESPONSE_TIMEOUT"


class TestDaemonLogging:
    def test_setup_daemon_logging_scopes_debug_to_bridgic_logger(self, tmp_path):
        from bridgic.browser.cli import _daemon

        state = _snapshot_logging_state()
        try:
            log_path = tmp_path / "logs" / "daemon.log"
            with patch("bridgic.browser.cli._daemon.DAEMON_LOG_PATH", log_path):
                _daemon._setup_daemon_logging()

            root = logging.getLogger()
            bridgic_logger = logging.getLogger("bridgic.browser")

            assert root.level == logging.WARNING
            assert bridgic_logger.level == logging.DEBUG
            assert bridgic_logger.propagate is True
            assert any(getattr(h, "baseFilename", None) == str(log_path) for h in bridgic_logger.handlers)
            assert not any(getattr(h, "baseFilename", None) == str(log_path) for h in root.handlers)
        finally:
            _restore_logging_state(state)

    def test_setup_daemon_logging_falls_back_when_file_logging_fails(self, tmp_path):
        from bridgic.browser.cli import _daemon

        state = _snapshot_logging_state()
        try:
            log_path = tmp_path / "logs" / "daemon.log"
            with (
                patch("bridgic.browser.cli._daemon.DAEMON_LOG_PATH", log_path),
                patch("logging.handlers.RotatingFileHandler", side_effect=OSError("no space left")),
                patch("bridgic.browser.cli._daemon.logger.warning") as mock_warning,
            ):
                _daemon._setup_daemon_logging()

            root = logging.getLogger()
            bridgic_logger = logging.getLogger("bridgic.browser")

            assert root.level == logging.WARNING
            assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
            assert bridgic_logger.handlers == []
            mock_warning.assert_called_once()
            assert "failed to initialize file logging" in mock_warning.call_args[0][0]
        finally:
            _restore_logging_state(state)


# ─────────────────────────────────────────────────────────────────────────────
# Transport layer
# ─────────────────────────────────────────────────────────────────────────────

class TestTransport:

    def test_get_transport_returns_unix_on_posix(self):
        with patch("bridgic.browser.cli._transport.sys") as mock_sys:
            mock_sys.platform = "linux"
            t = get_transport()
        assert isinstance(t, UnixTransport)

    def test_get_transport_returns_tcp_on_windows(self):
        with patch("bridgic.browser.cli._transport.sys") as mock_sys:
            mock_sys.platform = "win32"
            with patch("bridgic.browser.cli._transport.read_run_info", return_value=None):
                t = get_transport()
        assert isinstance(t, TcpTransport)

    def test_get_transport_reads_run_info_on_windows(self):
        run_info = {"transport": "tcp", "port": 12345, "token": "abc123"}
        with patch("bridgic.browser.cli._transport.sys") as mock_sys:
            mock_sys.platform = "win32"
            with patch("bridgic.browser.cli._transport.read_run_info", return_value=run_info):
                t = get_transport()
        assert isinstance(t, TcpTransport)
        assert t._port == 12345
        assert t._token == "abc123"

    def test_unix_transport_probe_returns_false_when_no_socket(self, tmp_path):
        sock_path = str(tmp_path / "no.sock")
        t = UnixTransport(sock_path)
        assert t.probe() is False

    def test_tcp_transport_inject_auth_adds_token(self):
        t = TcpTransport(port=9999, token="mytoken")
        result = t.inject_auth({"command": "snapshot", "args": {}})
        assert result["_token"] == "mytoken"
        assert result["command"] == "snapshot"

    def test_tcp_transport_verify_auth_passes_correct_token(self):
        t = TcpTransport(port=9999, token="correct")
        assert t.verify_auth({"_token": "correct"}) is True

    def test_tcp_transport_verify_auth_rejects_wrong_token(self):
        t = TcpTransport(port=9999, token="correct")
        assert t.verify_auth({"_token": "wrong"}) is False

    def test_tcp_transport_verify_auth_rejects_missing_token(self):
        t = TcpTransport(port=9999, token="correct")
        assert t.verify_auth({}) is False

    def test_tcp_transport_verify_auth_rejects_when_token_not_initialised(self):
        # Before start_server() is called, self._token is None.
        # A request with no _token must NOT be accepted (None == None must not pass).
        t = TcpTransport()
        assert t.verify_auth({}) is False
        assert t.verify_auth({"_token": None}) is False

    def test_write_run_info_overwrites_existing(self, tmp_path):
        """write_run_info must succeed even when the file already exists (Windows replace() semantics)."""
        fake_path = tmp_path / "run" / "daemon.json"
        with patch("bridgic.browser.cli._transport.RUN_INFO_PATH", fake_path):
            write_run_info({"transport": "unix", "pid": 1})
            write_run_info({"transport": "unix", "pid": 2})  # must not raise
            result = read_run_info()
        assert result["pid"] == 2

    def test_write_and_read_run_info(self, tmp_path):
        fake_path = tmp_path / "run" / "daemon.json"
        with patch("bridgic.browser.cli._transport.RUN_INFO_PATH", fake_path):
            write_run_info({"transport": "unix", "socket": "/tmp/test.sock", "pid": 42})
            result = read_run_info()
        assert result == {"transport": "unix", "socket": "/tmp/test.sock", "pid": 42}

    def test_remove_run_info_noop_when_missing(self, tmp_path):
        fake_path = tmp_path / "nonexistent.json"
        with patch("bridgic.browser.cli._transport.RUN_INFO_PATH", fake_path):
            remove_run_info()  # must not raise

    def test_unix_transport_verify_auth_always_true(self):
        t = UnixTransport("/tmp/test.sock")
        assert t.verify_auth({"command": "snapshot"}) is True
        assert t.verify_auth({}) is True

    def test_unix_transport_inject_auth_is_noop(self):
        t = UnixTransport("/tmp/test.sock")
        req = {"command": "snapshot", "args": {}}
        result = t.inject_auth(req)
        assert result == req
        assert "_token" not in result


# ─────────────────────────────────────────────────────────────────────────────
# resolve_cdp_input unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveCdpInput:
    """Direct unit tests for resolve_cdp_input() — all branches."""

    def test_port_number_calls_find_cdp_url(self, monkeypatch):
        monkeypatch.setattr(
            "bridgic.browser.session._browser.find_cdp_url",
            lambda mode, host, port: f"ws://{host}:{port}/fake",
        )
        from bridgic.browser.session._browser import resolve_cdp_input
        assert resolve_cdp_input("9222") == "ws://localhost:9222/fake"

    def test_ws_passthrough(self):
        from bridgic.browser.session._browser import resolve_cdp_input
        url = "ws://localhost:9222/devtools/browser/abc123"
        assert resolve_cdp_input(url) == url

    def test_wss_passthrough(self):
        from bridgic.browser.session._browser import resolve_cdp_input
        url = "wss://production.browserless.io/chromium/playwright?token=xyz"
        assert resolve_cdp_input(url) == url

    def test_http_url_calls_find_cdp_url(self, monkeypatch):
        monkeypatch.setattr(
            "bridgic.browser.session._browser.find_cdp_url",
            lambda mode, host, port: f"ws://{host}:{port}/fake",
        )
        from bridgic.browser.session._browser import resolve_cdp_input
        assert resolve_cdp_input("http://remote.host:9222") == "ws://remote.host:9222/fake"

    def test_auto_calls_scan(self, monkeypatch):
        monkeypatch.setattr(
            "bridgic.browser.session._browser.find_cdp_url",
            lambda mode: "ws://localhost:54321/fake",
        )
        from bridgic.browser.session._browser import resolve_cdp_input
        assert resolve_cdp_input("auto") == "ws://localhost:54321/fake"

    def test_scan_alias_calls_scan(self, monkeypatch):
        monkeypatch.setattr(
            "bridgic.browser.session._browser.find_cdp_url",
            lambda mode: "ws://localhost:54321/fake",
        )
        from bridgic.browser.session._browser import resolve_cdp_input
        assert resolve_cdp_input("scan") == "ws://localhost:54321/fake"

    def test_invalid_raises_value_error(self):
        from bridgic.browser.session._browser import resolve_cdp_input
        with pytest.raises(ValueError, match="Invalid --cdp value"):
            resolve_cdp_input("not-a-valid-input")

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setattr(
            "bridgic.browser.session._browser.find_cdp_url",
            lambda mode, host, port: f"ws://{host}:{port}/fake",
        )
        from bridgic.browser.session._browser import resolve_cdp_input
        assert resolve_cdp_input("  9222  ") == "ws://localhost:9222/fake"


# ─────────────────────────────────────────────────────────────────────────────
# find_cdp_url() unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFindCdpUrl:
    """Direct unit tests for find_cdp_url() — all branches, all mocked."""

    def test_service_mode_returns_ws_endpoint(self):
        from bridgic.browser import find_cdp_url
        url = "wss://my-cloud-service.io/browser?token=abc"
        assert find_cdp_url(mode="service", ws_endpoint=url) == url

    def test_service_mode_no_endpoint_raises_value_error(self):
        from bridgic.browser import find_cdp_url
        with pytest.raises(ValueError, match="ws_endpoint is required"):
            find_cdp_url(mode="service")

    def _make_loopback_opener_patch(self, mock_resp):
        """Return a patch context manager for urllib.request.build_opener that
        returns an opener whose .open() returns mock_resp. Used for loopback
        host tests because find_cdp_url() bypasses the system proxy via
        ProxyHandler({}) on loopback hosts."""
        opener = MagicMock()
        opener.open = MagicMock(return_value=mock_resp)
        return patch("urllib.request.build_opener", return_value=opener), opener

    def test_port_mode_returns_ws_url(self):
        from bridgic.browser import find_cdp_url
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}'
        patch_ctx, _ = self._make_loopback_opener_patch(mock_resp)
        with patch_ctx:
            url = find_cdp_url(mode="port", port=9222)
        assert url == "ws://localhost:9222/devtools/browser/abc"

    def test_port_remote_host_replaces_localhost(self):
        from bridgic.browser import find_cdp_url
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}'
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            url = find_cdp_url(mode="port", host="192.168.1.100", port=9222)
        assert url == "ws://192.168.1.100:9222/devtools/browser/abc"
        mock_open.assert_called_once_with("http://192.168.1.100:9222/json/version", timeout=5)

    def test_port_localhost_uppercase_keeps_localhost(self):
        """host='LOCALHOST' must be normalized to lowercase 'localhost' so the
        ws_url is not rewritten with a misleading uppercase host. Regression
        guard for L2."""
        from bridgic.browser import find_cdp_url
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}'
        patch_ctx, _ = self._make_loopback_opener_patch(mock_resp)
        with patch_ctx:
            url = find_cdp_url(mode="port", host="LOCALHOST", port=9222)
        # Must NOT contain uppercase LOCALHOST in the result.
        assert url == "ws://localhost:9222/devtools/browser/abc"

    def test_port_chrome_not_running_raises_connection_error(self):
        import urllib.error
        from bridgic.browser import find_cdp_url
        # Loopback path: patch build_opener so .open() raises URLError.
        opener = MagicMock()
        opener.open = MagicMock(side_effect=urllib.error.URLError("Connection refused"))
        with patch("urllib.request.build_opener", return_value=opener):
            with pytest.raises(ConnectionError, match="--remote-debugging-port=9222"):
                find_cdp_url(mode="port", port=9222)

    def test_port_invalid_json_raises_value_error(self):
        from bridgic.browser import find_cdp_url
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'hey'
        patch_ctx, _ = self._make_loopback_opener_patch(mock_resp)
        with patch_ctx:
            with pytest.raises(ValueError, match="Failed to parse /json/version response"):
                find_cdp_url(mode="port", port=9222)

    def test_port_missing_key_raises_value_error(self):
        from bridgic.browser import find_cdp_url
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"Browser": "Chrome/124"}'
        patch_ctx, _ = self._make_loopback_opener_patch(mock_resp)
        with patch_ctx:
            with pytest.raises(ValueError, match="Failed to parse /json/version response"):
                find_cdp_url(mode="port", port=9222)

    def test_port_urlopen_uses_timeout_5(self):
        from bridgic.browser import find_cdp_url
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"webSocketDebuggerUrl": "ws://localhost:9222/fake"}'
        # Loopback path uses build_opener(...).open(url, timeout=5).
        patch_ctx, opener = self._make_loopback_opener_patch(mock_resp)
        with patch_ctx:
            find_cdp_url(mode="port", port=9222)
        _, kwargs = opener.open.call_args
        assert kwargs.get("timeout") == 5

    def test_scan_mode_returns_url_from_file(self):
        from bridgic.browser import find_cdp_url
        fake_url = "ws://localhost:9222/devtools/browser/chrome-uuid"
        with patch("bridgic.browser.session._browser._read_devtools_active_port", return_value=fake_url):
            url = find_cdp_url(mode="scan")
        assert url == fake_url

    def test_scan_mode_returns_first_active(self):
        from bridgic.browser import find_cdp_url
        chrome_url = "ws://localhost:9222/devtools/browser/chrome-uuid"

        def fake_read(base):
            if "Chrome" in base and "Canary" not in base and "Beta" not in base:
                return chrome_url
            return None

        with patch("bridgic.browser.session._browser._read_devtools_active_port", side_effect=fake_read):
            result = find_cdp_url(mode="scan")
        assert result == chrome_url

    def test_scan_mode_no_profiles_raises_runtime_error(self):
        from bridgic.browser import find_cdp_url
        with patch("bridgic.browser.session._browser._read_devtools_active_port", return_value=None):
            with pytest.raises(RuntimeError, match="--remote-debugging-port=9222"):
                find_cdp_url(mode="scan")

    def test_scan_mode_unsupported_platform_raises_runtime_error(self):
        from bridgic.browser import find_cdp_url
        with patch("sys.platform", "freebsd"):
            with pytest.raises(RuntimeError, match="not supported on platform"):
                find_cdp_url(mode="scan")


# ─────────────────────────────────────────────────────────────────────────────
# _read_devtools_active_port() unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestReadDevToolsActivePort:
    """Unit tests for _read_devtools_active_port() using tempfile."""

    def _fn(self):
        from bridgic.browser.session._browser import _read_devtools_active_port
        return _read_devtools_active_port

    def test_valid_file_returns_ws_url(self):
        fn = self._fn()
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "DevToolsActivePort"), "w").write("9222\n/devtools/browser/abc123\n")
            result = fn(d)
        assert result == "ws://localhost:9222/devtools/browser/abc123"

    def test_missing_file_returns_none(self):
        fn = self._fn()
        result = fn("/tmp/nonexistent-bridgic-profile-xyz-abc")
        assert result is None

    def test_single_line_file_returns_none(self):
        fn = self._fn()
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "DevToolsActivePort"), "w") as f:
                f.write("9222\n")
            result = fn(d)
        assert result is None

    def test_no_read_permission_returns_none(self):
        fn = self._fn()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "DevToolsActivePort")
            with open(p, "w") as f:
                f.write("9222\n/devtools/browser/abc\n")
            os.chmod(p, 0o000)
            try:
                result = fn(d)
            finally:
                os.chmod(p, 0o644)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# _browser_closed_hint() unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBrowserClosedHint:
    """Unit tests for _browser_closed_hint()."""

    def test_no_cdp_returns_default_hint(self):
        assert _browser_closed_hint(None) == _BROWSER_CLOSED_HINT
        assert _browser_closed_hint() == _BROWSER_CLOSED_HINT

    @pytest.mark.parametrize("host,url_host", [
        ("localhost", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("::1", "[::1]"),
    ])
    def test_local_host_shows_port_only(self, host, url_host):
        url = f"ws://{url_host}:9222/devtools/browser/some-uuid"
        msg = _browser_closed_hint(url)
        assert "9222" in msg
        assert "some-uuid" not in msg
        assert "Local Chrome" in msg
        assert "bridgic-browser close" in msg

    def test_remote_host_exposes_full_url(self):
        url = "wss://my-cloud.io/browser?token=secret123"
        msg = _browser_closed_hint(url)
        assert url in msg
        assert "Remote browser session" in msg
        assert "bridgic-browser close" in msg


# ─────────────────────────────────────────────────────────────────────────────
# find_cdp_url() — invalid mode
# ─────────────────────────────────────────────────────────────────────────────

class TestFindCdpUrlInvalidMode:
    def test_invalid_mode_raises_value_error(self):
        from bridgic.browser import find_cdp_url
        with pytest.raises(ValueError, match="Unknown mode"):
            find_cdp_url(mode="bogus")


# ─────────────────────────────────────────────────────────────────────────────
# _cdp_reconnect() unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCdpReconnect:
    """Unit tests for _cdp_reconnect() using AsyncMock."""

    async def test_close_and_start_succeed_returns_true(self):
        browser = MagicMock()
        browser.close = AsyncMock()
        browser._start = AsyncMock()
        result = await _cdp_reconnect(browser)
        assert result is True
        browser.close.assert_awaited_once()
        browser._start.assert_awaited_once()

    async def test_close_raises_ignored_start_called_returns_true(self):
        browser = MagicMock()
        browser.close = AsyncMock(side_effect=RuntimeError("already closed"))
        browser._start = AsyncMock()
        result = await _cdp_reconnect(browser)
        assert result is True
        browser._start.assert_awaited_once()

    async def test_start_fails_returns_false(self):
        browser = MagicMock()
        browser.close = AsyncMock()
        browser._start = AsyncMock(side_effect=ConnectionError("Chrome not found"))
        result = await _cdp_reconnect(browser)
        assert result is False

    async def test_close_and_start_both_fail_returns_false(self):
        browser = MagicMock()
        browser.close = AsyncMock(side_effect=RuntimeError("gone"))
        browser._start = AsyncMock(side_effect=ConnectionError("still gone"))
        result = await _cdp_reconnect(browser)
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch() CDP reconnect logic
# ─────────────────────────────────────────────────────────────────────────────

class TestDispatchCdpReconnect:
    """Tests for _dispatch() CDP reconnect retry logic."""

    def _make_cdp_browser(self, cdp_url="ws://cloud.io/browser/abc"):
        b = make_browser()
        b._cdp_url = cdp_url
        return b

    async def test_cdp_browser_closed_reconnect_success_retry_success(self):
        browser = self._make_cdp_browser()
        call_count = 0

        async def navigate(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("browser has been closed")
            return "Navigated"

        browser.navigate_to = navigate
        with patch("bridgic.browser.cli._daemon._cdp_reconnect", new=AsyncMock(return_value=True)):
            resp = await _dispatch(browser, "open", {"url": "x"})

        assert resp["success"] is True
        assert resp["result"] == "Navigated"
        assert call_count == 2

    async def test_cdp_browser_closed_reconnect_success_retry_fails(self):
        browser = self._make_cdp_browser()
        browser.navigate_to = AsyncMock(
            side_effect=RuntimeError("browser has been closed")
        )
        with patch("bridgic.browser.cli._daemon._cdp_reconnect", new=AsyncMock(return_value=True)):
            resp = await _dispatch(browser, "open", {"url": "x"})

        assert resp["success"] is False
        assert resp["error_code"] == "BROWSER_CLOSED"
        assert browser.navigate_to.await_count == 2

    async def test_cdp_browser_closed_reconnect_fails(self):
        browser = self._make_cdp_browser()
        browser.navigate_to = AsyncMock(
            side_effect=RuntimeError("browser has been closed")
        )
        with patch("bridgic.browser.cli._daemon._cdp_reconnect", new=AsyncMock(return_value=False)):
            resp = await _dispatch(browser, "open", {"url": "x"})

        assert resp["success"] is False
        assert resp["error_code"] == "BROWSER_CLOSED"
        browser.navigate_to.assert_awaited_once()

    async def test_cdp_close_command_no_reconnect(self):
        browser = self._make_cdp_browser()
        browser.inspect_pending_close_artifacts = MagicMock(return_value={
            "session_dir": "/tmp/close-test", "trace": [], "video": [],
        })
        mock_reconnect = AsyncMock(return_value=True)
        with patch("bridgic.browser.cli._daemon._cdp_reconnect", new=mock_reconnect):
            with patch("bridgic.browser.cli._daemon._HANDLERS", {
                "close": AsyncMock(side_effect=RuntimeError("browser has been closed"))
            }):
                resp = await _dispatch(browser, "close", {})
        mock_reconnect.assert_not_called()
        assert resp["error_code"] == "BROWSER_CLOSED"

    async def test_non_cdp_browser_closed_no_reconnect(self):
        browser = make_browser()  # _cdp_url = None
        browser.navigate_to = AsyncMock(
            side_effect=RuntimeError("browser has been closed")
        )
        mock_reconnect = AsyncMock(return_value=True)
        with patch("bridgic.browser.cli._daemon._cdp_reconnect", new=mock_reconnect):
            resp = await _dispatch(browser, "open", {"url": "x"})
        mock_reconnect.assert_not_called()
        assert resp["error_code"] == "BROWSER_CLOSED"
        browser.navigate_to.assert_awaited_once()

    async def test_cdp_non_browser_closed_error_no_reconnect(self):
        browser = self._make_cdp_browser()
        browser.navigate_to = AsyncMock(
            side_effect=OperationError(code="ELEMENT_NOT_FOUND", message="element not found")
        )
        mock_reconnect = AsyncMock(return_value=True)
        with patch("bridgic.browser.cli._daemon._cdp_reconnect", new=mock_reconnect):
            resp = await _dispatch(browser, "open", {"url": "x"})
        mock_reconnect.assert_not_called()
        assert resp["error_code"] == "ELEMENT_NOT_FOUND"
        browser.navigate_to.assert_awaited_once()

    async def test_cdp_plain_exception_with_closed_message_triggers_reconnect(self):
        browser = self._make_cdp_browser()
        call_count = 0

        async def navigate(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Target page, context or browser has been closed")
            return "Navigated"

        browser.navigate_to = navigate
        with patch("bridgic.browser.cli._daemon._cdp_reconnect", new=AsyncMock(return_value=True)):
            resp = await _dispatch(browser, "open", {"url": "x"})

        assert resp["success"] is True
        assert call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# _spawn_daemon() env var passing
# ─────────────────────────────────────────────────────────────────────────────

class TestSpawnDaemonEnv:
    """Unit tests for _spawn_daemon() environment variable propagation."""

    def _fake_popen_factory(self, captured_env: dict):
        """Return a fake Popen that records the env and signals READY."""
        def fake_popen(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            m = MagicMock()
            m.stdout = MagicMock()
            lines = [b"BRIDGIC_DAEMON_READY\n"]
            m.stdout.__iter__ = lambda self: iter(lines)
            m.stdout.close = MagicMock()
            return m
        return fake_popen

    def _run_spawn(self, captured_env, **kwargs):
        from bridgic.browser.cli._client import _spawn_daemon
        fake_popen = self._fake_popen_factory(captured_env)
        with patch("subprocess.Popen", side_effect=fake_popen):
            _spawn_daemon(**kwargs)

    def test_cdp_url_sets_env_var(self):
        captured_env: dict = {}
        self._run_spawn(captured_env, cdp_url="ws://localhost:9222/devtools/browser/abc")
        assert captured_env.get("BRIDGIC_CDP") == "ws://localhost:9222/devtools/browser/abc"

    def test_no_cdp_url_env_var_absent(self):
        captured_env: dict = {}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRIDGIC_CDP", None)
            self._run_spawn(captured_env)
        assert "BRIDGIC_CDP" not in captured_env

    def test_headed_and_cdp_url_both_set(self):
        captured_env: dict = {}
        self._run_spawn(
            captured_env,
            headed=True,
            cdp_url="ws://localhost:9222/devtools/browser/abc",
        )
        assert captured_env.get("BRIDGIC_HEADLESS") is None or "BRIDGIC_BROWSER_JSON" in captured_env
        assert captured_env.get("BRIDGIC_CDP") == "ws://localhost:9222/devtools/browser/abc"


# ---------------------------------------------------------------------------
# TestDaemonDownloadsPath — default downloads_path injection in run_daemon()
# ---------------------------------------------------------------------------


class TestDaemonDownloadsPath:
    """Verify that run_daemon() auto-injects downloads_path when not configured."""

    @pytest.mark.asyncio
    async def test_daemon_injects_default_downloads_path(self):
        """When no config sets downloads_path, daemon injects a default."""
        captured_kwargs: Dict[str, Any] = {}

        def fake_browser(**kw: Any) -> MagicMock:
            captured_kwargs.update(kw)
            b = MagicMock()
            b.get_config.return_value = kw
            return b

        with (
            patch("bridgic.browser.cli._daemon._load_config_sources", return_value={}),
            patch("bridgic.browser.cli._daemon._resolve_default_downloads_dir", return_value=Path.home() / "Downloads"),
            patch("bridgic.browser.session._browser.Browser", side_effect=fake_browser),
            patch("bridgic.browser.cli._daemon.get_transport") as mock_transport,
            patch("bridgic.browser.cli._daemon.write_run_info"),
            patch("bridgic.browser.cli._daemon.asyncio.Event") as mock_event,
        ):
            mock_event.return_value.wait = AsyncMock()
            mock_server = AsyncMock()
            mock_transport.return_value.start_server = AsyncMock(return_value=mock_server)
            mock_transport.return_value.build_run_info.return_value = {}
            mock_transport.return_value.verify_auth = None
            mock_event.return_value.is_set.return_value = True

            with patch("bridgic.browser.cli._daemon.logger"):
                from bridgic.browser.cli._daemon import run_daemon

                with patch("sys.stdout"):
                    try:
                        await run_daemon()
                    except Exception:
                        pass

            assert "downloads_path" in captured_kwargs
            assert captured_kwargs["downloads_path"] == str(Path.home() / "Downloads")

    @pytest.mark.asyncio
    async def test_daemon_respects_config_downloads_path(self):
        """When config already sets downloads_path, daemon does not override."""
        captured_kwargs: Dict[str, Any] = {}

        def fake_browser(**kw: Any) -> MagicMock:
            captured_kwargs.update(kw)
            b = MagicMock()
            b.get_config.return_value = kw
            return b

        with (
            patch("bridgic.browser.cli._daemon._load_config_sources", return_value={"downloads_path": "/custom/path"}),
            patch("bridgic.browser.session._browser.Browser", side_effect=fake_browser),
            patch("bridgic.browser.cli._daemon.get_transport") as mock_transport,
            patch("bridgic.browser.cli._daemon.write_run_info"),
            patch("bridgic.browser.cli._daemon.asyncio.Event") as mock_event,
        ):
            mock_event.return_value.wait = AsyncMock()
            mock_server = AsyncMock()
            mock_transport.return_value.start_server = AsyncMock(return_value=mock_server)
            mock_transport.return_value.build_run_info.return_value = {}
            mock_transport.return_value.verify_auth = None
            mock_event.return_value.is_set.return_value = True

            with patch("bridgic.browser.cli._daemon.logger"):
                from bridgic.browser.cli._daemon import run_daemon

                with patch("sys.stdout"):
                    try:
                        await run_daemon()
                    except Exception:
                        pass

            # Should NOT have downloads_path injected (config already has it)
            assert captured_kwargs.get("downloads_path") is None

    def test_resolve_default_downloads_dir_prefers_user_downloads(self, tmp_path: Path):
        """When ~/Downloads is writable, it is preferred."""
        fake_home = tmp_path / "home"
        fake_downloads = fake_home / "Downloads"
        fake_downloads.mkdir(parents=True)

        with patch("bridgic.browser.cli._daemon.Path.home", return_value=fake_home):
            result = _resolve_default_downloads_dir()

        assert result == fake_downloads

    def test_resolve_default_downloads_dir_fallback(self, tmp_path: Path):
        """When ~/Downloads is not writable, falls back to app-managed dir."""
        fake_home = tmp_path / "home"
        fake_downloads = fake_home / "Downloads"
        fake_downloads.mkdir(parents=True)
        # Make ~/Downloads read-only so probe.touch() fails
        fake_downloads.chmod(0o444)

        fallback_dir = tmp_path / "fallback"

        with (
            patch("bridgic.browser.cli._daemon.Path.home", return_value=fake_home),
            patch("bridgic.browser.cli._daemon.BRIDGIC_DOWNLOADS_DIR", fallback_dir),
        ):
            result = _resolve_default_downloads_dir()

        assert result == fallback_dir
        assert fallback_dir.exists()

        # Restore permissions for cleanup
        fake_downloads.chmod(0o755)
