"""
Bridgic Browser CLI commands (click-based).

Usage examples:
    bridgic-browser open https://example.com
    bridgic-browser snapshot
    bridgic-browser click @e2
    bridgic-browser fill @e3 "test@example.com"
    bridgic-browser get text @e1
    bridgic-browser screenshot page.png
    bridgic-browser navigate https://example.com
    bridgic-browser back / forward / reload
    bridgic-browser info
    bridgic-browser search "python tutorials"
    bridgic-browser hover @e5
    bridgic-browser select @e4 "Option A"
    bridgic-browser check @e6 / uncheck @e6
    bridgic-browser double-click @e3
    bridgic-browser press "Control+A"
    bridgic-browser type "hello world"
    bridgic-browser scroll --dy 300
    bridgic-browser scroll --dy -200
    bridgic-browser wait 2.5
    bridgic-browser wait-for "Done"
    bridgic-browser tabs
    bridgic-browser new-tab https://example.com
    bridgic-browser switch-tab page_1234
    bridgic-browser close-tab
    bridgic-browser eval "() => document.title"
    bridgic-browser pdf report.pdf
    bridgic-browser close
"""
from __future__ import annotations

import os
import sys

import click

from ._client import send_command

# Support both -h and --help for every command
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


class SectionedGroup(click.Group):
    """A Click Group that renders help output grouped into named sections."""

    # Ordered list of (section_title, [command_names]).
    # Commands not listed here fall into a trailing "Other" section.
    SECTIONS: list[tuple[str, list[str]]] = [
        ("Navigation",          ["open", "navigate", "back", "forward", "reload", "search", "info"]),
        ("Snapshot",            ["snapshot"]),
        ("Element Interaction", ["click", "double-click", "hover", "focus",
                                 "fill", "select", "check", "uncheck", "get"]),
        ("Keyboard",            ["press", "type"]),
        ("Mouse",               ["scroll"]),
        ("Wait",                ["wait", "wait-for"]),
        ("Tabs",                ["tabs", "new-tab", "switch-tab", "close-tab"]),
        ("Capture",             ["screenshot", "pdf"]),
        ("Developer",           ["eval"]),
        ("Lifecycle",           ["close"]),
    ]

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        assigned: set[str] = set()
        for section_title, cmd_names in self.SECTIONS:
            rows = []
            for name in cmd_names:
                cmd = self.commands.get(name)
                if cmd is None or cmd.hidden:
                    continue
                rows.append((name, cmd.get_short_help_str(limit=formatter.width)))
                assigned.add(name)
            if rows:
                with formatter.section(section_title):
                    formatter.write_dl(rows)

        # Any command registered but not assigned to a section goes here
        leftover = [
            (name, self.commands[name].get_short_help_str(limit=formatter.width))
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
    """Bridgic Browser CLI — control a persistent browser session."""


# ── Navigation ────────────────────────────────────────────────────────────────

@cli.command("open", context_settings=CONTEXT_SETTINGS)
@click.argument("url")
def cmd_open(url: str) -> None:
    """Navigate to URL (starts browser if needed)."""
    try:
        _ok(send_command("open", {"url": url}))
    except Exception as exc:
        _err(str(exc))


@cli.command("navigate", context_settings=CONTEXT_SETTINGS)
@click.argument("url")
def cmd_navigate(url: str) -> None:
    """Navigate to URL in the current tab."""
    try:
        _ok(send_command("navigate", {"url": url}))
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


# ── Element interaction ───────────────────────────────────────────────────────

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
    """Check a checkbox by ref."""
    try:
        _ok(send_command("check", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("uncheck", context_settings=CONTEXT_SETTINGS)
@click.argument("ref")
def cmd_uncheck(ref: str) -> None:
    """Uncheck a checkbox by ref."""
    try:
        _ok(send_command("uncheck", {"ref": _strip_ref(ref)}))
    except Exception as exc:
        _err(str(exc))


@cli.command("get", context_settings=CONTEXT_SETTINGS)
@click.argument("property", metavar="PROPERTY")
@click.argument("ref")
def cmd_get(property: str, ref: str) -> None:
    """Get a property of an element by ref.  PROPERTY is currently 'text'."""
    if property.lower() != "text":
        _err(f"Unsupported property {property!r}. Supported: text")
    else:
        try:
            _ok(send_command("get_text", {"ref": _strip_ref(ref)}))
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
def cmd_type(text: str) -> None:
    """Type text into the currently focused element."""
    try:
        _ok(send_command("type", {"text": text}))
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


# ── Wait ──────────────────────────────────────────────────────────────────────

@cli.command("wait", context_settings=CONTEXT_SETTINGS)
@click.argument("seconds", type=float)
def cmd_wait(seconds: float) -> None:
    """Wait for SECONDS seconds (accepts decimals, max 60)."""
    try:
        _ok(send_command("wait", {"seconds": seconds}))
    except Exception as exc:
        _err(str(exc))


@cli.command("wait-for", context_settings=CONTEXT_SETTINGS)
@click.argument("text")
@click.option("--gone", is_flag=True, default=False,
              help="Wait for TEXT to disappear instead of appear.")
def cmd_wait_for(text: str, gone: bool) -> None:
    """Wait until TEXT appears on the page (or disappears with --gone)."""
    try:
        if gone:
            _ok(send_command("wait", {"text_gone": text}))
        else:
            _ok(send_command("wait", {"text": text}))
    except Exception as exc:
        _err(str(exc))


# ── Tabs ──────────────────────────────────────────────────────────────────────

@cli.command("tabs", context_settings=CONTEXT_SETTINGS)
def cmd_tabs() -> None:
    """List all open tabs."""
    try:
        _ok(send_command("tabs"))
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
    """Save the current page as a PDF (headless mode only)."""
    try:
        abs_path = os.path.abspath(path)
        _ok(send_command("pdf", {"path": abs_path}))
    except Exception as exc:
        _err(str(exc))


# ── Developer ─────────────────────────────────────────────────────────────────

@cli.command("eval", context_settings=CONTEXT_SETTINGS)
@click.argument("code")
def cmd_eval(code: str) -> None:
    """Evaluate JavaScript in the page context and print the result."""
    try:
        _ok(send_command("eval", {"code": code}))
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
