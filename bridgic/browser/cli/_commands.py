"""
Bridgic Browser CLI commands (click-based).

Common usage examples:
    bridgic-browser open https://example.com
    bridgic-browser snapshot [-i] [-F]
    bridgic-browser click @e2
    bridgic-browser fill @e3 "test@example.com"
    bridgic-browser screenshot page.png
    bridgic-browser close
"""
from __future__ import annotations

import json
import os
import sys

import click

from ._client import send_command
from .._cli_catalog import (
    CLI_COMMAND_META,
    CLI_HELP_SECTIONS,
)

# Support both -h and --help for every command
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


class SectionedGroup(click.Group):
    """A Click Group that renders help output grouped into named sections."""

    # Keep high-frequency commands first and related commands adjacent.
    SECTIONS: list[tuple[str, list[str]]] = CLI_HELP_SECTIONS

    def _short_help(self, name: str, cmd: click.Command, width: int) -> str:
        """Prefer metadata help text to keep top-level help concise and consistent."""
        row = CLI_COMMAND_META.get(name)
        if isinstance(row, tuple) and len(row) == 2 and isinstance(row[1], str):
            return row[1]
        return cmd.get_short_help_str(limit=width)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        assigned: set[str] = set()
        for section_title, cmd_names in self.SECTIONS:
            rows = []
            for name in cmd_names:
                cmd = self.commands.get(name)
                if cmd is None or cmd.hidden:
                    continue
                rows.append((name, self._short_help(name, cmd, formatter.width)))
                assigned.add(name)
            if rows:
                with formatter.section(section_title):
                    formatter.write_dl(rows)

        # Any command registered but not assigned to a section goes here
        leftover = [
            (name, self._short_help(name, self.commands[name], formatter.width))
            for name in sorted(self.commands)
            if name not in assigned and not self.commands[name].hidden
        ]
        if leftover:
            with formatter.section("Other"):
                formatter.write_dl(leftover)


def _strip_ref(ref: str) -> str:
    """Normalize ref: strip leading '@' and optional 'ref=' prefix."""
    ref = ref.strip()
    if ref.startswith("@"):
        ref = ref[1:]
    if ref.startswith("ref="):
        ref = ref[4:]
    return ref


def _ok(result: str) -> None:
    click.echo(result)


def _err(msg: str) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


@click.group(cls=SectionedGroup, context_settings=CONTEXT_SETTINGS)
def cli() -> None:
    """Bridgic Browser CLI — control a persistent browser session.

    Workflow: open/search -> snapshot -> interact by ref -> verify/capture(optional).
    Refs are from the latest snapshot; run `snapshot` again after page changes.
    """


# ── Navigation ────────────────────────────────────────────────────────────────

@cli.command("open", context_settings=CONTEXT_SETTINGS)
@click.argument("url")
def cmd_open(url: str) -> None:
    """Navigate to URL (starts browser if needed)."""
    try:
        _ok(send_command("open", {"url": url}))
    except Exception as exc:
        _err(str(exc))


@cli.command("back", context_settings=CONTEXT_SETTINGS)
def cmd_back() -> None:
    """Go back to the previous page."""
    try:
        _ok(send_command("back"))
    except Exception as exc:
        _err(str(exc))


@cli.command("forward", context_settings=CONTEXT_SETTINGS)
def cmd_forward() -> None:
    """Go forward to the next page."""
    try:
        _ok(send_command("forward"))
    except Exception as exc:
        _err(str(exc))


@cli.command("reload", context_settings=CONTEXT_SETTINGS)
def cmd_reload() -> None:
    """Reload the current page."""
    try:
        _ok(send_command("reload"))
    except Exception as exc:
        _err(str(exc))


@cli.command("search", context_settings=CONTEXT_SETTINGS)
@click.argument("query")
@click.option(
    "--engine", default="duckduckgo",
    type=click.Choice(["duckduckgo", "google", "bing"], case_sensitive=False),
    help="Search engine (default: duckduckgo).",
)
def cmd_search(query: str, engine: str) -> None:
    """Search the web using a search engine."""
    try:
        _ok(send_command("search", {"query": query, "engine": engine}))
    except Exception as exc:
        _err(str(exc))


@cli.command("info", context_settings=CONTEXT_SETTINGS)
def cmd_info() -> None:
    """Show current page URL, title, viewport, and scroll position."""
    try:
        _ok(send_command("info"))
    except Exception as exc:
        _err(str(exc))


# ── Snapshot ──────────────────────────────────────────────────────────────────

@cli.command("snapshot", context_settings=CONTEXT_SETTINGS)
@click.option("-i", "--interactive", is_flag=True, default=False,
              help="Only show clickable/editable elements.")
@click.option("-f/-F", "--full-page/--no-full-page", default=True,
              help="Include elements outside the viewport (default: true). -F = viewport only.")
@click.option("-s", "--start-from-char", default=0, type=int,
              help="Pagination offset. Use next_start_char from the truncation notice.")
def cmd_snapshot(interactive: bool, full_page: bool, start_from_char: int) -> None:
    """Print the current accessibility tree snapshot."""
    try:
        _ok(send_command("snapshot", {
            "interactive": interactive,
            "full_page": full_page,
            "start_from_char": start_from_char,
        }))
    except Exception as exc:
        _err(str(exc))


# ── Element Interaction ───────────────────────────────────────────────────────

@cli.command("click", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_click(ref: str) -> None:
    """Click an element by ref (@e2 or e2)."""
    try:
        _ok(send_command("click", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("double-click", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_double_click(ref: str) -> None:
    """Double-click an element by ref."""
    try:
        _ok(send_command("double_click", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("hover", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_hover(ref: str) -> None:
    """Hover over an element by ref."""
    try:
        _ok(send_command("hover", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("focus", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_focus(ref: str) -> None:
    """Focus an element by ref."""
    try:
        _ok(send_command("focus", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("fill", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
@click.argument("text")
def cmd_fill(ref: str, text: str) -> None:
    """Fill an input element by ref with TEXT."""
    try:
        _ok(send_command("fill", {"ref": _strip_ref(ref), "text": text}))
    except Exception as exc:
        _err(str(exc))


@cli.command("select", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
@click.argument("option")
def cmd_select(ref: str, option: str) -> None:
    """Select a dropdown option by ref and option text."""
    try:
        _ok(send_command("select", {"ref": _strip_ref(ref), "text": option}))
    except Exception as exc:
        _err(str(exc))


@cli.command("check", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_check(ref: str) -> None:
    """Check a checkbox or radio by ref."""
    try:
        _ok(send_command("check", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("uncheck", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_uncheck(ref: str) -> None:
    """Uncheck a checkbox by ref (radios usually require selecting another option)."""
    try:
        _ok(send_command("uncheck", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))



@cli.command("scroll-to", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_scroll_into_view(ref: str) -> None:
    """Scroll an element into view by ref."""
    try:
        _ok(send_command("scroll_into_view", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("drag", context_settings=CONTEXT_SETTINGS)
@click.argument("start_ref")
@click.argument("end_ref")
def cmd_drag(start_ref: str, end_ref: str) -> None:
    """Drag from START_REF element to END_REF element."""
    try:
        _ok(send_command("drag", {
            "start_ref": _strip_ref(start_ref),
            "end_ref": _strip_ref(end_ref),
        }))
    except Exception as exc:
        _err(str(exc))


@cli.command("options", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_options(ref: str) -> None:
    """Get all available options for a dropdown element by ref."""
    try:
        _ok(send_command("options", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("upload", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
@click.argument("path")
def cmd_upload(ref: str, path: str) -> None:
    """Upload a file at PATH to a file input element by ref."""
    try:
        abs_path = os.path.abspath(path)
        _ok(send_command("upload", {"ref": _strip_ref(ref), "path": abs_path}))
    except Exception as exc:
        _err(str(exc))


@cli.command("fill-form", context_settings=CONTEXT_SETTINGS)
@click.argument("fields_json")
@click.option("--submit", is_flag=True, default=False,
              help="Press Enter after filling the last field.")
def cmd_fill_form(fields_json: str, submit: bool) -> None:
    """Fill multiple form fields at once. FIELDS_JSON is a JSON array like '[{"ref":"e1","value":"hi"}]'."""
    try:
        _ok(send_command("fill_form", {"fields": fields_json, "submit": submit}))
    except Exception as exc:
        _err(str(exc))


# ── Keyboard ──────────────────────────────────────────────────────────────────

@cli.command("press", context_settings=CONTEXT_SETTINGS)
@click.argument("key")
def cmd_press(key: str) -> None:
    """Press a keyboard key or combination (Enter, Control+A, Shift+Tab…)."""
    try:
        _ok(send_command("press", {"key": key}))
    except Exception as exc:
        _err(str(exc))


@cli.command("type", context_settings=CONTEXT_SETTINGS)
@click.argument("text")
@click.option("--submit", is_flag=True, default=False,
              help="Press Enter after typing.")
def cmd_type(text: str, submit: bool) -> None:
    """Type text character-by-character (triggers keyboard events)."""
    try:
        _ok(send_command("type_text", {"text": text, "submit": submit}))
    except Exception as exc:
        _err(str(exc))


@cli.command("key-down", context_settings=CONTEXT_SETTINGS)
@click.argument("key")
def cmd_key_down(key: str) -> None:
    """Press and hold a keyboard key."""
    try:
        _ok(send_command("key_down", {"key": key}))
    except Exception as exc:
        _err(str(exc))


@cli.command("key-up", context_settings=CONTEXT_SETTINGS)
@click.argument("key")
def cmd_key_up(key: str) -> None:
    """Release a held keyboard key."""
    try:
        _ok(send_command("key_up", {"key": key}))
    except Exception as exc:
        _err(str(exc))


# ── Mouse ─────────────────────────────────────────────────────────────────────

@cli.command("scroll", context_settings=CONTEXT_SETTINGS)
@click.option("--dy", default=0.0, help="Vertical scroll amount. Positive = down, negative = up (pixels).")
@click.option("--dx", default=0.0, help="Horizontal scroll amount (pixels).")
def cmd_scroll(dy: float, dx: float) -> None:
    """Scroll the page vertically (--dy) and/or horizontally (--dx)."""
    try:
        _ok(send_command("scroll", {"delta_x": dx, "delta_y": dy}))
    except Exception as exc:
        _err(str(exc))


@cli.command("mouse-move", context_settings=CONTEXT_SETTINGS)
@click.argument("x", type=float)
@click.argument("y", type=float)
def cmd_mouse_move(x: float, y: float) -> None:
    """Move the mouse to coordinates (X, Y)."""
    try:
        _ok(send_command("mouse_move", {"x": x, "y": y}))
    except Exception as exc:
        _err(str(exc))


@cli.command("mouse-click", context_settings=CONTEXT_SETTINGS)
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.option("--button", default="left",
              type=click.Choice(["left", "right", "middle"], case_sensitive=False),
              help="Mouse button (default: left).")
@click.option("--count", default=1, type=int, help="Number of clicks (default: 1).")
def cmd_mouse_click(x: float, y: float, button: str, count: int) -> None:
    """Click the mouse at coordinates (X, Y)."""
    try:
        _ok(send_command("mouse_click", {"x": x, "y": y, "button": button, "count": count}))
    except Exception as exc:
        _err(str(exc))


@cli.command("mouse-drag", context_settings=CONTEXT_SETTINGS)
@click.argument("x1", type=float)
@click.argument("y1", type=float)
@click.argument("x2", type=float)
@click.argument("y2", type=float)
def cmd_mouse_drag(x1: float, y1: float, x2: float, y2: float) -> None:
    """Drag the mouse from (X1, Y1) to (X2, Y2)."""
    try:
        _ok(send_command("mouse_drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2}))
    except Exception as exc:
        _err(str(exc))


@cli.command("mouse-down", context_settings=CONTEXT_SETTINGS)
@click.option("--button", default="left",
              type=click.Choice(["left", "right", "middle"], case_sensitive=False),
              help="Mouse button to press (default: left).")
def cmd_mouse_down(button: str) -> None:
    """Press and hold a mouse button."""
    try:
        _ok(send_command("mouse_down", {"button": button}))
    except Exception as exc:
        _err(str(exc))


@cli.command("mouse-up", context_settings=CONTEXT_SETTINGS)
@click.option("--button", default="left",
              type=click.Choice(["left", "right", "middle"], case_sensitive=False),
              help="Mouse button to release (default: left).")
def cmd_mouse_up(button: str) -> None:
    """Release a held mouse button."""
    try:
        _ok(send_command("mouse_up", {"button": button}))
    except Exception as exc:
        _err(str(exc))


# ── Wait ──────────────────────────────────────────────────────────────────────

@cli.command("wait", context_settings=CONTEXT_SETTINGS)
@click.argument("value")
@click.option("--gone", is_flag=True, default=False,
              help="Wait for VALUE text to disappear instead of appear.")
def cmd_wait(value: str, gone: bool) -> None:
    """Wait for SECONDS or until TEXT appears (--gone: disappears).

    \b
    If VALUE is a number, wait that many seconds (max 60).
    Otherwise, wait until VALUE text appears on the page.
    With --gone, wait until VALUE text disappears.

    \b
    Examples:
        bridgic-browser wait 3          # wait 3 seconds
        bridgic-browser wait "Submit"   # wait for text to appear
        bridgic-browser wait --gone "Loading"  # wait for text to disappear
    """
    try:
        # Try to parse as a number for time-based wait
        try:
            seconds = float(value)
        except ValueError:
            seconds = None

        if seconds is not None and not gone:
            _ok(send_command("wait", {"seconds": seconds}))
        elif gone:
            _ok(send_command("wait", {"text_gone": value}))
        else:
            _ok(send_command("wait", {"text": value}))
    except Exception as exc:
        _err(str(exc))


# ── Tabs ──────────────────────────────────────────────────────────────────────

@cli.command("tabs", context_settings=CONTEXT_SETTINGS)
def cmd_tabs() -> None:
    """List all open tabs."""
    try:
        # Read-only query: do not auto-spawn a new browser session.
        _ok(send_command("tabs", start_if_needed=False))
    except Exception as exc:
        _err(str(exc))


@cli.command("new-tab", context_settings=CONTEXT_SETTINGS)
@click.argument("url", required=False, default=None)
def cmd_new_tab(url: str | None) -> None:
    """Open a new tab, optionally navigating to URL."""
    try:
        _ok(send_command("new_tab", {"url": url}))
    except Exception as exc:
        _err(str(exc))


@cli.command("switch-tab", context_settings=CONTEXT_SETTINGS)
@click.argument("page_id")
def cmd_switch_tab(page_id: str) -> None:
    """Switch to a tab by its page_id (from 'tabs' command)."""
    try:
        _ok(send_command("switch_tab", {"page_id": page_id}))
    except Exception as exc:
        _err(str(exc))


@cli.command("close-tab", context_settings=CONTEXT_SETTINGS)
@click.argument("page_id", required=False, default=None)
def cmd_close_tab(page_id: str | None) -> None:
    """Close a tab by page_id, or the current tab if omitted."""
    try:
        _ok(send_command("close_tab", {"page_id": page_id}))
    except Exception as exc:
        _err(str(exc))


# ── Capture ───────────────────────────────────────────────────────────────────

@cli.command("screenshot", context_settings=CONTEXT_SETTINGS)
@click.argument("path")
@click.option("--full-page", is_flag=True, default=False,
              help="Capture the full scrollable page.")
def cmd_screenshot(path: str, full_page: bool) -> None:
    """Save a screenshot to PATH."""
    try:
        abs_path = os.path.abspath(path)
        _ok(send_command("screenshot", {"path": abs_path, "full_page": full_page}))
    except Exception as exc:
        _err(str(exc))


@cli.command("pdf", context_settings=CONTEXT_SETTINGS)
@click.argument("path")
def cmd_pdf(path: str) -> None:
    """Save the current page as a PDF."""
    try:
        abs_path = os.path.abspath(path)
        _ok(send_command("pdf", {"path": abs_path}))
    except Exception as exc:
        _err(str(exc))


# ── Network ───────────────────────────────────────────────────────────────────

@cli.command("network-start", context_settings=CONTEXT_SETTINGS)
def cmd_network_start() -> None:
    """Start capturing network requests."""
    try:
        _ok(send_command("network_start"))
    except Exception as exc:
        _err(str(exc))


@cli.command("network-stop", context_settings=CONTEXT_SETTINGS)
def cmd_network_stop() -> None:
    """Stop capturing network requests."""
    try:
        _ok(send_command("network_stop"))
    except Exception as exc:
        _err(str(exc))


@cli.command("network", context_settings=CONTEXT_SETTINGS)
@click.option("--static", "include_static", is_flag=True, default=False,
              help="Include static resources (images, scripts, stylesheets).")
@click.option("--no-clear", is_flag=True, default=False,
              help="Keep requests after reading (default: clear after read).")
def cmd_network(include_static: bool, no_clear: bool) -> None:
    """Get captured network requests."""
    try:
        _ok(send_command("network", {"include_static": include_static, "clear": not no_clear}))
    except Exception as exc:
        _err(str(exc))


@cli.command("wait-network", context_settings=CONTEXT_SETTINGS)
@click.option("--timeout", default=30000, type=float,
              help="Maximum wait time in milliseconds (default: 30000).")
def cmd_wait_network(timeout: float) -> None:
    """Wait until the network is idle."""
    try:
        _ok(send_command("wait_network", {"timeout": timeout}))
    except Exception as exc:
        _err(str(exc))


# ── Dialog ────────────────────────────────────────────────────────────────────

@cli.command("dialog-setup", context_settings=CONTEXT_SETTINGS)
@click.option("--action", default="accept",
              type=click.Choice(["accept", "dismiss"], case_sensitive=False),
              help="Action to take on dialogs (default: accept).")
@click.option("--text", default=None, help="Text to enter for prompt() dialogs.")
def cmd_dialog_setup(action: str, text: str | None) -> None:
    """Set up automatic dialog handling for all future dialogs."""
    try:
        _ok(send_command("dialog_setup", {"action": action, "text": text}))
    except Exception as exc:
        _err(str(exc))


@cli.command("dialog", context_settings=CONTEXT_SETTINGS)
@click.option("--dismiss", is_flag=True, default=False,
              help="Dismiss the dialog (default: accept).")
@click.option("--text", default=None, help="Text to enter for prompt() dialogs.")
def cmd_dialog(dismiss: bool, text: str | None) -> None:
    """Handle the next dialog that appears."""
    try:
        _ok(send_command("dialog", {"dismiss": dismiss, "text": text}))
    except Exception as exc:
        _err(str(exc))


@cli.command("dialog-remove", context_settings=CONTEXT_SETTINGS)
def cmd_dialog_remove() -> None:
    """Remove the automatic dialog handler."""
    try:
        _ok(send_command("dialog_remove"))
    except Exception as exc:
        _err(str(exc))


# ── Storage ───────────────────────────────────────────────────────────────────

@cli.command("storage-save", context_settings=CONTEXT_SETTINGS)
@click.argument("path")
def cmd_storage_save(path: str) -> None:
    """Save browser storage state (cookies, localStorage) to PATH."""
    try:
        abs_path = os.path.abspath(path)
        _ok(send_command("storage_save", {"path": abs_path}))
    except Exception as exc:
        _err(str(exc))


@cli.command("storage-load", context_settings=CONTEXT_SETTINGS)
@click.argument("path")
def cmd_storage_load(path: str) -> None:
    """Restore browser storage state from PATH."""
    try:
        abs_path = os.path.abspath(path)
        _ok(send_command("storage_load", {"path": abs_path}))
    except Exception as exc:
        _err(str(exc))


@cli.command("cookies-clear", context_settings=CONTEXT_SETTINGS)
def cmd_cookies_clear() -> None:
    """Clear all cookies from the browser context."""
    try:
        _ok(send_command("cookies_clear"))
    except Exception as exc:
        _err(str(exc))


@cli.command("cookies", context_settings=CONTEXT_SETTINGS)
@click.option("--url", default=None, help="Filter cookies by URL.")
def cmd_cookies(url: str | None) -> None:
    """Get cookies from the browser context."""
    try:
        _ok(send_command("cookies", {"url": url}))
    except Exception as exc:
        _err(str(exc))


@cli.command("cookie-set", context_settings=CONTEXT_SETTINGS)
@click.argument("name")
@click.argument("value")
@click.option("--url", default=None, help="URL to associate the cookie with.")
@click.option("--domain", default=None, help="Cookie domain.")
@click.option("--path", "cookie_path", default="/", help="Cookie path (default: /).")
@click.option("--expires", default=None, type=float, help="Unix timestamp when the cookie expires.")
@click.option("--http-only", is_flag=True, default=False, help="Set HttpOnly flag.")
@click.option("--secure", is_flag=True, default=False, help="Set Secure flag.")
@click.option("--same-site", default=None,
              type=click.Choice(["Strict", "Lax", "None"], case_sensitive=True),
              help="SameSite attribute.")
def cmd_cookie_set(
    name: str, value: str, url: str | None, domain: str | None, cookie_path: str,
    expires: float | None, http_only: bool, secure: bool, same_site: str | None,
) -> None:
    """Set a cookie in the browser context."""
    try:
        _ok(send_command("cookie_set", {
            "name": name, "value": value, "url": url, "domain": domain,
            "path": cookie_path, "expires": expires, "http_only": http_only,
            "secure": secure, "same_site": same_site,
        }))
    except Exception as exc:
        _err(str(exc))


# ── Verify ────────────────────────────────────────────────────────────────────

@cli.command("verify-visible", context_settings=CONTEXT_SETTINGS)
@click.argument("role")
@click.argument("name")
@click.option("--timeout", default=5000, type=float,
              help="Maximum wait time in milliseconds (default: 5000).")
def cmd_verify_visible(role: str, name: str, timeout: float) -> None:
    """Verify an element with ROLE and NAME is visible on the page."""
    try:
        _ok(send_command("verify_visible", {"role": role, "name": name, "timeout": timeout}))
    except Exception as exc:
        _err(str(exc))


@cli.command("verify-text", context_settings=CONTEXT_SETTINGS)
@click.argument("text")
@click.option("--exact", is_flag=True, default=False,
              help="Match TEXT exactly (default: substring match).")
@click.option("--timeout", default=5000, type=float,
              help="Maximum wait time in milliseconds (default: 5000).")
def cmd_verify_text(text: str, exact: bool, timeout: float) -> None:
    """Verify that TEXT is visible on the page."""
    try:
        _ok(send_command("verify_text", {"text": text, "exact": exact, "timeout": timeout}))
    except Exception as exc:
        _err(str(exc))


@cli.command("verify-value", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
@click.argument("expected")
def cmd_verify_value(ref: str, expected: str) -> None:
    """Verify that the value of REF element matches EXPECTED."""
    try:
        _ok(send_command("verify_value", {"ref": _strip_ref(ref), "expected": expected}))
    except Exception as exc:
        _err(str(exc))


@cli.command("verify-state", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
@click.argument("state")
def cmd_verify_state(ref: str, state: str) -> None:
    """Verify the STATE of a REF element (visible, hidden, enabled, disabled, checked, unchecked)."""
    try:
        _ok(send_command("verify_state", {"ref": _strip_ref(ref), "state": state}))
    except Exception as exc:
        _err(str(exc))


@cli.command("verify-url", context_settings=CONTEXT_SETTINGS)
@click.argument("url")
@click.option("--exact", is_flag=True, default=False,
              help="Match URL exactly (default: substring match).")
def cmd_verify_url(url: str, exact: bool) -> None:
    """Verify the current page URL matches URL."""
    try:
        _ok(send_command("verify_url", {"url": url, "exact": exact}))
    except Exception as exc:
        _err(str(exc))


@cli.command("verify-title", context_settings=CONTEXT_SETTINGS)
@click.argument("title")
@click.option("--exact", is_flag=True, default=False,
              help="Match title exactly (default: substring match).")
def cmd_verify_title(title: str, exact: bool) -> None:
    """Verify the current page title matches TITLE."""
    try:
        _ok(send_command("verify_title", {"title": title, "exact": exact}))
    except Exception as exc:
        _err(str(exc))


# ── Evaluate ──────────────────────────────────────────────────────────────────

@cli.command("eval", context_settings=CONTEXT_SETTINGS)
@click.argument("code")
def cmd_eval(code: str) -> None:
    """Evaluate JavaScript in the page context and print the result."""
    try:
        _ok(send_command("eval", {"code": code}))
    except Exception as exc:
        _err(str(exc))


@cli.command("eval-on", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
@click.argument("code")
def cmd_eval_on(ref: str, code: str) -> None:
    """Evaluate JavaScript with a REF element as the argument."""
    try:
        _ok(send_command("eval_on", {"ref": _strip_ref(ref), "code": code}))
    except Exception as exc:
        _err(str(exc))


# ── Developer ─────────────────────────────────────────────────────────────────

@cli.command("console-start", context_settings=CONTEXT_SETTINGS)
def cmd_console_start() -> None:
    """Start capturing browser console output."""
    try:
        _ok(send_command("console_start"))
    except Exception as exc:
        _err(str(exc))


@cli.command("console-stop", context_settings=CONTEXT_SETTINGS)
def cmd_console_stop() -> None:
    """Stop capturing browser console output."""
    try:
        _ok(send_command("console_stop"))
    except Exception as exc:
        _err(str(exc))


@cli.command("console", context_settings=CONTEXT_SETTINGS)
@click.option("--filter", "type_filter", default=None,
              type=click.Choice(["log", "debug", "info", "error", "warning", "dir", "trace"],
                                case_sensitive=False),
              help="Filter messages by type.")
@click.option("--no-clear", is_flag=True, default=False,
              help="Keep messages after reading (default: clear after read).")
def cmd_console(type_filter: str | None, no_clear: bool) -> None:
    """Get captured console messages."""
    try:
        _ok(send_command("console", {"filter": type_filter, "clear": not no_clear}))
    except Exception as exc:
        _err(str(exc))


@cli.command("trace-start", context_settings=CONTEXT_SETTINGS)
@click.option("--no-screenshots", is_flag=True, default=False,
              help="Disable screenshot capture during trace.")
@click.option("--no-snapshots", is_flag=True, default=False,
              help="Disable DOM snapshot capture during trace.")
def cmd_trace_start(no_screenshots: bool, no_snapshots: bool) -> None:
    """Start browser tracing."""
    try:
        _ok(send_command("trace_start", {
            "no_screenshots": no_screenshots,
            "no_snapshots": no_snapshots,
        }))
    except Exception as exc:
        _err(str(exc))


@cli.command("trace-stop", context_settings=CONTEXT_SETTINGS)
@click.argument("path")
def cmd_trace_stop(path: str) -> None:
    """Stop tracing and save the trace to PATH (.zip)."""
    try:
        abs_path = os.path.abspath(path)
        _ok(send_command("trace_stop", {"path": abs_path}))
    except Exception as exc:
        _err(str(exc))


@cli.command("trace-chunk", context_settings=CONTEXT_SETTINGS)
@click.argument("title")
def cmd_trace_chunk(title: str) -> None:
    """Add a named chunk/annotation to the current trace."""
    try:
        _ok(send_command("trace_chunk", {"title": title}))
    except Exception as exc:
        _err(str(exc))


@cli.command("video-start", context_settings=CONTEXT_SETTINGS)
@click.option("--width", default=None, type=int, help="Video width in pixels.")
@click.option("--height", default=None, type=int, help="Video height in pixels.")
def cmd_video_start(width: int | None, height: int | None) -> None:
    """Start video recording."""
    try:
        _ok(send_command("video_start", {"width": width, "height": height}))
    except Exception as exc:
        _err(str(exc))


@cli.command("video-stop", context_settings=CONTEXT_SETTINGS)
@click.argument("path", required=False, default=None)
def cmd_video_stop(path: str | None) -> None:
    """Stop video recording and save to PATH (optional)."""
    try:
        abs_path = os.path.abspath(path) if path else None
        _ok(send_command("video_stop", {"path": abs_path}))
    except Exception as exc:
        _err(str(exc))


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@cli.command("close", context_settings=CONTEXT_SETTINGS)
def cmd_close() -> None:
    """Close the browser and stop the daemon."""
    try:
        _ok(send_command("close", {}, start_if_needed=False))
    except Exception as exc:
        _err(str(exc))


@cli.command("resize", context_settings=CONTEXT_SETTINGS)
@click.argument("width", type=int)
@click.argument("height", type=int)
def cmd_resize(width: int, height: int) -> None:
    """Resize the browser viewport to WIDTH × HEIGHT pixels."""
    try:
        _ok(send_command("resize", {"width": width, "height": height}))
    except Exception as exc:
        _err(str(exc))
