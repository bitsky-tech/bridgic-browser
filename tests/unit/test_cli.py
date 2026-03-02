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
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from bridgic.browser.cli._commands import _strip_ref, cli, SectionedGroup
from bridgic.browser.cli._daemon import (
    _build_browser_kwargs,
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
    return b


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

    def test_h_shorthand_on_group(self):
        result = invoke_raw(["-h"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    def test_help_longhand_on_group(self):
        result = invoke_raw(["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    def test_h_shorthand_on_subcommand(self):
        result = invoke_raw(["open", "-h"])
        assert result.exit_code == 0
        assert "URL" in result.output

    def test_sections_present(self):
        result = invoke_raw(["-h"])
        out = result.output
        for section in ("Navigation", "Snapshot", "Element Interaction",
                        "Keyboard", "Mouse", "Wait", "Tabs", "Capture",
                        "Developer", "Lifecycle"):
            assert section in out, f"Section '{section}' missing from help"

    def test_all_commands_appear_in_help(self):
        result = invoke_raw(["-h"])
        out = result.output
        expected_commands = [
            "open", "navigate", "back", "forward", "reload", "search", "info",
            "snapshot", "click", "double-click", "hover", "focus", "fill",
            "select", "check", "uncheck", "get", "press", "type", "scroll",
            "wait", "wait-for", "tabs", "new-tab", "switch-tab", "close-tab",
            "screenshot", "pdf", "eval", "close",
        ]
        for cmd in expected_commands:
            assert cmd in out, f"Command '{cmd}' missing from help output"

    def test_help_has_no_flat_commands_section(self):
        """Flat 'Commands:' block should not appear — only named sections."""
        result = invoke_raw(["-h"])
        assert "Commands:" not in result.output

    def test_unlisted_command_appears_in_other(self):
        """Commands added outside SECTIONS fall into an 'Other' section."""
        import click

        @cli.command("_test_unlisted_cmd", hidden=False)
        def _test_cmd():
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
        sc.assert_called_once_with("open", {"url": "https://example.com"})

    def test_navigate(self):
        _, sc = invoke(["navigate", "https://example.com"])
        sc.assert_called_once_with("navigate", {"url": "https://example.com"})

    def test_back(self):
        _, sc = invoke(["back"])
        sc.assert_called_once_with("back")

    def test_forward(self):
        _, sc = invoke(["forward"])
        sc.assert_called_once_with("forward")

    def test_reload(self):
        _, sc = invoke(["reload"])
        sc.assert_called_once_with("reload")

    def test_search_default_engine(self):
        _, sc = invoke(["search", "python async"])
        sc.assert_called_once_with("search", {"query": "python async", "engine": "duckduckgo"})

    def test_search_custom_engine(self):
        _, sc = invoke(["search", "query", "--engine", "google"])
        sc.assert_called_once_with("search", {"query": "query", "engine": "google"})

    def test_info(self):
        _, sc = invoke(["info"])
        sc.assert_called_once_with("info")

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def test_snapshot_default(self):
        _, sc = invoke(["snapshot"])
        sc.assert_called_once_with("snapshot", {"interactive": False})

    def test_snapshot_interactive(self):
        _, sc = invoke(["snapshot", "--interactive"])
        sc.assert_called_once_with("snapshot", {"interactive": True})

    # ── Element interaction ───────────────────────────────────────────────────

    def test_click_strips_at(self):
        _, sc = invoke(["click", "@e2"])
        sc.assert_called_once_with("click", {"ref": "e2"})

    def test_click_plain_ref(self):
        _, sc = invoke(["click", "e3"])
        sc.assert_called_once_with("click", {"ref": "e3"})

    def test_double_click(self):
        _, sc = invoke(["double-click", "@e4"])
        sc.assert_called_once_with("double_click", {"ref": "e4"})

    def test_hover(self):
        _, sc = invoke(["hover", "@e5"])
        sc.assert_called_once_with("hover", {"ref": "e5"})

    def test_focus(self):
        _, sc = invoke(["focus", "e6"])
        sc.assert_called_once_with("focus", {"ref": "e6"})

    def test_fill(self):
        _, sc = invoke(["fill", "@e3", "hello"])
        sc.assert_called_once_with("fill", {"ref": "e3", "text": "hello"})

    def test_select(self):
        _, sc = invoke(["select", "@e7", "Option A"])
        sc.assert_called_once_with("select", {"ref": "e7", "text": "Option A"})

    def test_check(self):
        _, sc = invoke(["check", "@e8"])
        sc.assert_called_once_with("check", {"ref": "e8"})

    def test_uncheck(self):
        _, sc = invoke(["uncheck", "@e9"])
        sc.assert_called_once_with("uncheck", {"ref": "e9"})

    def test_get_text(self):
        _, sc = invoke(["get", "text", "@e1"])
        sc.assert_called_once_with("get_text", {"ref": "e1"})

    def test_get_invalid_property_exits_nonzero(self):
        result, sc = invoke(["get", "html", "@e1"])
        assert result.exit_code != 0
        sc.assert_not_called()

    def test_get_invalid_property_error_message(self):
        result, _ = invoke(["get", "html", "@e1"])
        assert "Unsupported property" in result.output

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def test_press(self):
        _, sc = invoke(["press", "Control+A"])
        sc.assert_called_once_with("press", {"key": "Control+A"})

    def test_type(self):
        _, sc = invoke(["type", "hello world"])
        sc.assert_called_once_with("type", {"text": "hello world"})

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def test_scroll_down(self):
        _, sc = invoke(["scroll", "--dy", "300"])
        sc.assert_called_once_with("scroll", {"delta_x": 0.0, "delta_y": 300.0})

    def test_scroll_up(self):
        _, sc = invoke(["scroll", "--dy", "-200"])
        sc.assert_called_once_with("scroll", {"delta_x": 0.0, "delta_y": -200.0})

    def test_scroll_with_dx(self):
        _, sc = invoke(["scroll", "--dy", "100", "--dx", "50"])
        sc.assert_called_once_with("scroll", {"delta_x": 50.0, "delta_y": 100.0})

    # ── Wait ──────────────────────────────────────────────────────────────────

    def test_wait_seconds(self):
        _, sc = invoke(["wait", "2.5"])
        sc.assert_called_once_with("wait", {"seconds": 2.5})

    def test_wait_for_text_appear(self):
        _, sc = invoke(["wait-for", "Done"])
        sc.assert_called_once_with("wait", {"text": "Done"})

    def test_wait_for_text_gone(self):
        _, sc = invoke(["wait-for", "Loading", "--gone"])
        sc.assert_called_once_with("wait", {"text_gone": "Loading"})

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def test_tabs(self):
        _, sc = invoke(["tabs"])
        sc.assert_called_once_with("tabs")

    def test_new_tab_with_url(self):
        _, sc = invoke(["new-tab", "https://example.com"])
        sc.assert_called_once_with("new_tab", {"url": "https://example.com"})

    def test_new_tab_blank(self):
        _, sc = invoke(["new-tab"])
        sc.assert_called_once_with("new_tab", {"url": None})

    def test_switch_tab(self):
        _, sc = invoke(["switch-tab", "page_1234"])
        sc.assert_called_once_with("switch_tab", {"page_id": "page_1234"})

    def test_close_tab_current(self):
        _, sc = invoke(["close-tab"])
        sc.assert_called_once_with("close_tab", {"page_id": None})

    def test_close_tab_by_id(self):
        _, sc = invoke(["close-tab", "page_5678"])
        sc.assert_called_once_with("close_tab", {"page_id": "page_5678"})

    # ── Capture ───────────────────────────────────────────────────────────────

    def test_screenshot_absolutizes_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["screenshot", "page.png"])
        expected = str(tmp_path / "page.png")
        sc.assert_called_once_with(
            "screenshot", {"path": expected, "full_page": False}
        )

    def test_screenshot_full_page(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["screenshot", "page.png", "--full-page"])
        expected = str(tmp_path / "page.png")
        sc.assert_called_once_with(
            "screenshot", {"path": expected, "full_page": True}
        )

    def test_screenshot_absolute_path_unchanged(self):
        abs_path = "/tmp/my_screenshot.png"
        _, sc = invoke(["screenshot", abs_path])
        sc.assert_called_once_with(
            "screenshot", {"path": abs_path, "full_page": False}
        )

    def test_pdf_absolutizes_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, sc = invoke(["pdf", "report.pdf"])
        expected = str(tmp_path / "report.pdf")
        sc.assert_called_once_with("pdf", {"path": expected})

    # ── Developer ─────────────────────────────────────────────────────────────

    def test_eval(self):
        _, sc = invoke(["eval", "() => document.title"])
        sc.assert_called_once_with("eval", {"code": "() => document.title"})

    # ── Lifecycle ─────────────────────────────────────────────────────────────

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

    def test_result_printed_on_success(self):
        with patch(
            "bridgic.browser.cli._commands.send_command",
            return_value="Navigated to: https://example.com",
        ):
            result = runner.invoke(cli, ["open", "https://example.com"])
        assert "Navigated to: https://example.com" in result.output
        assert result.exit_code == 0


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch
# ─────────────────────────────────────────────────────────────────────────────

class TestDaemonDispatch:
    async def test_unknown_command_returns_error(self):
        browser = make_browser()
        resp = await _dispatch(browser, "nonexistent", {})
        assert resp["status"] == "error"
        assert "nonexistent" in resp["result"]

    async def test_known_command_returns_ok(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_tools.navigate_to_url",
            new=AsyncMock(return_value="Navigated"),
        ):
            resp = await _dispatch(browser, "open", {"url": "https://example.com"})
        assert resp["status"] == "ok"
        assert resp["result"] == "Navigated"

    async def test_handler_exception_returns_error(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_tools.navigate_to_url",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            resp = await _dispatch(browser, "open", {"url": "x"})
        assert resp["status"] == "error"
        assert "boom" in resp["result"]


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
            new=AsyncMock(return_value={"status": "ok", "result": "went back"}),
        ):
            reader = make_reader(req)
            writer = make_writer()
            await _handle_connection(browser, reader, writer, stop)

        written = b"".join(call.args[0] for call in writer.write.call_args_list)
        resp = json.loads(written.decode().strip())
        assert resp["status"] == "ok"
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
        assert "Invalid JSON" in resp["result"]

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
        with patch(
            "bridgic.browser.tools._browser_tools.navigate_to_url",
            new=AsyncMock(return_value="Navigated to: https://example.com"),
        ) as mock_nav:
            result = await _handle_open(browser, {"url": "https://example.com"})
        mock_nav.assert_awaited_once_with(browser, "https://example.com")
        assert result == "Navigated to: https://example.com"

    async def test_handle_snapshot_default(self):
        browser = make_browser()
        snap = MagicMock()
        snap.tree = "- heading 'Example' [ref=e1]"
        browser.get_snapshot = AsyncMock(return_value=snap)

        result = await _handle_snapshot(browser, {})

        browser.get_snapshot.assert_awaited_once_with(interactive=False)
        assert result == snap.tree

    async def test_handle_snapshot_interactive(self):
        browser = make_browser()
        snap = MagicMock(tree="- button 'Submit' [ref=e1]")
        browser.get_snapshot = AsyncMock(return_value=snap)

        await _handle_snapshot(browser, {"interactive": True})

        browser.get_snapshot.assert_awaited_once_with(interactive=True)

    async def test_handle_click_calls_tool(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_action_tools.click_element_by_ref",
            new=AsyncMock(return_value="Clicked e2"),
        ) as mock_click:
            result = await _handle_click(browser, {"ref": "e2"})
        mock_click.assert_awaited_once_with(browser, "e2")
        assert result == "Clicked e2"

    async def test_handle_fill_calls_tool(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_action_tools.input_text_by_ref",
            new=AsyncMock(return_value="Input text 'hello'"),
        ) as mock_fill:
            result = await _handle_fill(browser, {"ref": "e3", "text": "hello"})
        mock_fill.assert_awaited_once_with(browser, "e3", "hello")
        assert "hello" in result

    async def test_handle_screenshot_passes_full_page(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_screenshot_tools.take_screenshot",
            new=AsyncMock(return_value="Screenshot saved to: /tmp/x.png"),
        ) as mock_ss:
            await _handle_screenshot(browser, {"path": "/tmp/x.png", "full_page": True})
        mock_ss.assert_awaited_once_with(browser, filename="/tmp/x.png", full_page=True)

    async def test_handle_screenshot_default_full_page_false(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_screenshot_tools.take_screenshot",
            new=AsyncMock(return_value="ok"),
        ) as mock_ss:
            await _handle_screenshot(browser, {"path": "/tmp/x.png"})
        _, kwargs = mock_ss.call_args
        assert kwargs.get("full_page") is False

    async def test_handle_scroll_passes_deltas(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_mouse_tools.mouse_wheel",
            new=AsyncMock(return_value="Scrolled"),
        ) as mock_scroll:
            await _handle_scroll(browser, {"delta_x": 10, "delta_y": 300})
        mock_scroll.assert_awaited_once_with(browser, delta_x=10, delta_y=300)

    async def test_handle_wait_time_seconds(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_tools.wait_for",
            new=AsyncMock(return_value="Waited for 2.5 seconds"),
        ) as mock_wait:
            await _handle_wait(browser, {"seconds": 2.5})
        mock_wait.assert_awaited_once_with(
            browser, time_seconds=2.5, text=None, text_gone=None
        )

    async def test_handle_wait_text(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_tools.wait_for",
            new=AsyncMock(return_value="Text appeared"),
        ) as mock_wait:
            await _handle_wait(browser, {"text": "Done"})
        mock_wait.assert_awaited_once_with(
            browser, time_seconds=None, text="Done", text_gone=None
        )

    async def test_handle_search_default_engine(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_tools.search",
            new=AsyncMock(return_value="Searched"),
        ) as mock_search:
            await _handle_search(browser, {"query": "python"})
        mock_search.assert_awaited_once_with(browser, "python", "duckduckgo")

    async def test_handle_close_tab_none_page_id(self):
        """page_id=None should close the current tab."""
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_tools.close_tab",
            new=AsyncMock(return_value="Tab closed"),
        ) as mock_ct:
            await _handle_close_tab(browser, {})
        mock_ct.assert_awaited_once_with(browser, page_id=None)

    async def test_handle_pdf_passes_path(self):
        browser = make_browser()
        with patch(
            "bridgic.browser.tools._browser_screenshot_tools.save_pdf",
            new=AsyncMock(return_value="PDF saved"),
        ) as mock_pdf:
            await _handle_pdf(browser, {"path": "/tmp/out.pdf"})
        mock_pdf.assert_awaited_once_with(browser, filename="/tmp/out.pdf")


# ─────────────────────────────────────────────────────────────────────────────
# _client — send_command guard
# ─────────────────────────────────────────────────────────────────────────────

class TestClientSendCommand:
    def test_raises_when_socket_missing_and_no_start(self):
        from bridgic.browser.cli._client import send_command

        with patch("bridgic.browser.cli._client.os.path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="No browser session is running"):
                send_command("close", start_if_needed=False)

    def test_start_if_needed_false_proceeds_when_socket_present(self):
        """When socket file exists, proceed to asyncio.run (mock the coroutine)."""
        from bridgic.browser.cli._client import send_command

        with patch("bridgic.browser.cli._client.os.path.exists", return_value=True):
            with patch(
                "bridgic.browser.cli._client.asyncio.run",
                return_value="Daemon shutting down",
            ) as mock_run:
                result = send_command("close", start_if_needed=False)
        mock_run.assert_called_once()
        assert result == "Daemon shutting down"

    def test_start_if_needed_true_calls_ensure_daemon(self):
        from bridgic.browser.cli._client import send_command

        with patch("bridgic.browser.cli._client.ensure_daemon_running") as mock_ensure:
            with patch("bridgic.browser.cli._client.asyncio.run", return_value="ok"):
                send_command("snapshot")
        mock_ensure.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _build_browser_kwargs  (config file + env var priority chain)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildBrowserKwargs:
    """Tests for the _build_browser_kwargs priority chain."""

    def test_defaults_headless_true(self):
        """With no config files or env vars, headless defaults to True."""
        with patch("bridgic.browser.cli._daemon.Path") as mock_path_cls:
            mock_path_cls.home.return_value.__truediv__ = lambda s, p: _non_existent()
            mock_path_cls.return_value.is_file.return_value = False
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BRIDGIC_BROWSER_JSON", None)
                os.environ.pop("BRIDGIC_HEADLESS", None)
                kwargs = _build_browser_kwargs()
        assert kwargs["headless"] is True

    def test_user_config_file_applied(self, tmp_path):
        """~/.bridgic/bridgic-browser.json values are loaded."""
        cfg = tmp_path / "bridgic-browser.json"
        cfg.write_text(json.dumps({"headless": False, "channel": "chrome"}))

        with patch("bridgic.browser.cli._daemon.Path") as mock_path_cls:
            # home() / ".bridgic" / "bridgic-browser.json" → cfg
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_home.__truediv__ = lambda s, p: mock_home
            mock_home.is_file.return_value = True
            mock_home.read_text.return_value = cfg.read_text()
            # local config doesn't exist
            mock_local = MagicMock()
            mock_local.is_file.return_value = False
            mock_path_cls.return_value = mock_local

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BRIDGIC_BROWSER_JSON", None)
                os.environ.pop("BRIDGIC_HEADLESS", None)
                kwargs = _build_browser_kwargs()

        assert kwargs["headless"] is False
        assert kwargs["channel"] == "chrome"

    def test_local_config_overrides_user_config(self, tmp_path):
        """./bridgic-browser.json overrides ~/.bridgic/bridgic-browser.json."""
        user_json = json.dumps({"headless": False, "channel": "chrome"})
        local_json = json.dumps({"channel": "msedge"})

        with patch("bridgic.browser.cli._daemon.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_home.__truediv__ = lambda s, p: mock_home
            mock_home.is_file.return_value = True
            mock_home.read_text.return_value = user_json

            mock_local = MagicMock()
            mock_local.is_file.return_value = True
            mock_local.read_text.return_value = local_json
            mock_path_cls.return_value = mock_local

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BRIDGIC_BROWSER_JSON", None)
                os.environ.pop("BRIDGIC_HEADLESS", None)
                kwargs = _build_browser_kwargs()

        assert kwargs["channel"] == "msedge"   # local overrides user
        assert kwargs["headless"] is False      # from user config (not overridden)

    def test_env_json_overrides_config_files(self, tmp_path):
        """BRIDGIC_BROWSER_JSON overrides both config files."""
        user_json = json.dumps({"channel": "chrome", "headless": False})
        env_json = json.dumps({"channel": "chromium", "locale": "zh-CN"})

        with patch("bridgic.browser.cli._daemon.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_home.__truediv__ = lambda s, p: mock_home
            mock_home.is_file.return_value = True
            mock_home.read_text.return_value = user_json

            mock_local = MagicMock()
            mock_local.is_file.return_value = False
            mock_path_cls.return_value = mock_local

            with patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": env_json}, clear=False):
                os.environ.pop("BRIDGIC_HEADLESS", None)
                kwargs = _build_browser_kwargs()

        assert kwargs["channel"] == "chromium"  # env JSON overrides user config
        assert kwargs["headless"] is False       # from user config (not in env JSON)
        assert kwargs["locale"] == "zh-CN"

    def test_bridgic_headless_overrides_all(self):
        """BRIDGIC_HEADLESS=0 overrides even BRIDGIC_BROWSER_JSON."""
        env_json = json.dumps({"headless": True})

        with patch("bridgic.browser.cli._daemon.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_home.__truediv__ = lambda s, p: mock_home
            mock_home.is_file.return_value = False
            mock_local = MagicMock()
            mock_local.is_file.return_value = False
            mock_path_cls.return_value = mock_local

            with patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": env_json, "BRIDGIC_HEADLESS": "0"}):
                kwargs = _build_browser_kwargs()

        assert kwargs["headless"] is False  # BRIDGIC_HEADLESS=0 wins

    def test_invalid_env_json_is_ignored(self):
        """Malformed BRIDGIC_BROWSER_JSON is silently ignored (logged as warning)."""
        with patch("bridgic.browser.cli._daemon.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_home.__truediv__ = lambda s, p: mock_home
            mock_home.is_file.return_value = False
            mock_local = MagicMock()
            mock_local.is_file.return_value = False
            mock_path_cls.return_value = mock_local

            with patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": "not valid json"}, clear=False):
                os.environ.pop("BRIDGIC_HEADLESS", None)
                kwargs = _build_browser_kwargs()  # must not raise

        assert kwargs["headless"] is True  # falls back to default

    def test_complex_params_passed_through(self):
        """Complex nested params (proxy, viewport) are passed through as-is."""
        env_json = json.dumps({
            "proxy": {"server": "http://proxy:8080", "username": "u", "password": "p"},
            "viewport": {"width": 1280, "height": 720},
            "extra_http_headers": {"X-Custom": "value"},
        })

        with patch("bridgic.browser.cli._daemon.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_home.__truediv__ = lambda s, p: mock_home
            mock_home.is_file.return_value = False
            mock_local = MagicMock()
            mock_local.is_file.return_value = False
            mock_path_cls.return_value = mock_local

            with patch.dict(os.environ, {"BRIDGIC_BROWSER_JSON": env_json}, clear=False):
                os.environ.pop("BRIDGIC_HEADLESS", None)
                kwargs = _build_browser_kwargs()

        assert kwargs["proxy"] == {"server": "http://proxy:8080", "username": "u", "password": "p"}
        assert kwargs["viewport"] == {"width": 1280, "height": 720}
        assert kwargs["extra_http_headers"] == {"X-Custom": "value"}


def _non_existent() -> MagicMock:
    """Return a MagicMock that acts like a non-existent path."""
    m = MagicMock()
    m.is_file.return_value = False
    return m
