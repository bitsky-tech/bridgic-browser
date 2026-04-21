"""Central timeout constants for bridgic-browser.

Every timeout here is the total budget for one logical operation. They are
named after *what* the budget covers, not *where* the call site is, so the
same constant can be reused from SDK, CLI daemon, and tests without drift.

Guidelines:
  - All values are **seconds** (``float``) unless the suffix is ``_MS``.
  - Names use the pattern ``<SCOPE>_<ACTION>_S`` so a ``grep`` for ``_S`` in
    this module enumerates the full budget list.
  - Where a value is overridable via an environment variable the override is
    resolved here (never in the consuming module) so that documentation and
    the runtime value cannot diverge.
  - Call sites should import the named constant rather than hard-code a
    number. Inline ``timeout=5.0`` kwargs on ``locator.*`` and similar
    Playwright calls intentionally stay inline — their value is
    operation-specific and does not belong in a shared module.

The ``_CLOSE`` section is deliberately short: the shutdown pipeline is the
place where a drifting magic number is most dangerous (a dead browser blocks
the daemon), so those budgets are named and documented even when used once.
"""

from __future__ import annotations

import os


def _float_env(name: str, default: float) -> float:
    """Return ``float(os.environ[name])`` or ``default`` on missing/invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Shutdown / close pipeline (SDK-side)
# ---------------------------------------------------------------------------
# These are applied one-per-step inside ``Browser.close()``. They are short
# on purpose — if any of them saturates we fall through to the daemon-level
# watchdog (_DAEMON_STOP_S) and finally a force-kill of the Playwright driver.

PAGE_CLOSE_S: float = 5.0
"""Budget for a single ``page.close()`` call during shutdown."""

TRACE_STOP_S: float = 30.0
"""Budget for ``context.tracing.stop()`` — writes the trace zip to disk."""

CONTEXT_CLOSE_S: float = 15.0
"""Budget for a single ``context.close()`` call."""

BROWSER_CLOSE_S: float = 15.0
"""Budget for ``browser.close()`` (non-persistent mode)."""

PLAYWRIGHT_STOP_S: float = 15.0
"""Budget for stopping the Playwright Node driver."""

VIDEO_PREPARE_STOP_S: float = 15.0
"""Budget for one recorder ``prepare_stop()`` — stops the CDP screencast."""

VIDEO_FINALIZE_S: float = 30.0
"""Budget for one recorder ``finalize()`` — flushes ffmpeg to the output file."""


# ---------------------------------------------------------------------------
# Video recorder internals
# ---------------------------------------------------------------------------

VIDEO_PREPARE_STOP_FALLBACK_S: float = 10.0
"""``finalize()`` safety fallback when ``prepare_stop`` wasn't called first."""

VIDEO_FFMPEG_EXIT_S: float = 15.0
"""Wait for ffmpeg to exit on its own after stdin is closed."""

VIDEO_FFMPEG_KILL_REAP_S: float = 2.0
"""Wait after ``ffmpeg.kill()`` to reap the child and avoid a zombie."""

VIDEO_STDERR_DRAIN_S: float = 2.0
"""Wait for the stderr reader task to finish after ffmpeg exits."""


# ---------------------------------------------------------------------------
# CLI daemon
# ---------------------------------------------------------------------------

DAEMON_READ_S: float = 60.0
"""Max wait for a JSON command line from a connected client."""

DAEMON_STOP_S: float = _float_env("BRIDGIC_DAEMON_STOP_TIMEOUT", 60.0)
"""Global safety-net budget for ``browser.close()`` inside the daemon.

This is a watchdog — the per-step timeouts above should finish first under
normal operation. Lowered from the historical 300 s so a hung shutdown does
not make the user think the CLI has frozen: individual steps already cap at
≤ 30 s and we fall through to force-kill after this.

Override with ``BRIDGIC_DAEMON_STOP_TIMEOUT`` if you have a legitimate
long-running close (e.g. finalizing a multi-gigabyte video on a slow disk).
"""

SLOW_COMMAND_S: float = 60.0
"""Threshold above which ``_dispatch`` emits a warning instead of an info log."""

CDP_RECONNECT_BACKOFF_S: float = 0.5
"""Sleep before each automatic CDP reconnect attempt.

Keeps a tight loop from pounding on an already-dead CDP endpoint while we
wait for the target to come back up (e.g. after user killed Chrome).
"""

CDP_PROBE_S: float = _float_env("BRIDGIC_CDP_PROBE_TIMEOUT", 1.5)
"""Per-probe TCP connect budget when checking if a CDP endpoint is alive."""


# ---------------------------------------------------------------------------
# CLI client
# ---------------------------------------------------------------------------

CLIENT_RESPONSE_S: float = _float_env("BRIDGIC_DAEMON_RESPONSE_TIMEOUT", 90.0)
"""Default socket read budget on the client side.

Raised above typical tool latency so the daemon has time to execute the
command and return structured errors. Commands whose natural runtime can
exceed this fall back to ``CLIENT_LONG_COMMAND_S``; commands that carry an
explicit ``timeout`` arg extend it by ``CLIENT_RESPONSE_BUFFER_S``.
"""

CLIENT_RESPONSE_BUFFER_S: float = _float_env(
    "BRIDGIC_DAEMON_RESPONSE_TIMEOUT_BUFFER", 30.0
)
"""Extra client-side window above any arg-supplied ``timeout``.

Without this, ``wait --timeout 120`` would race the 90 s client default and
the client would abort while the daemon was still working, orphaning the
in-flight task and confusing the next CLI invocation.
"""

CLIENT_READY_S: float = _float_env("BRIDGIC_DAEMON_READY_TIMEOUT", 30.0)
"""How long the client waits for a freshly spawned daemon to emit READY."""

CLIENT_LONG_COMMAND_S: float = 300.0
"""Fallback response budget for ``_LONG_COMMANDS`` (downloads, video-stop…)."""


# ---------------------------------------------------------------------------
# Dispatch heartbeat (Part C)
# ---------------------------------------------------------------------------

DISPATCH_HEARTBEAT_S: float = 5.0
"""Interval between heartbeat log lines while a command is in flight.

Short enough that an operator watching the daemon log sees progress within
a few seconds, long enough that a normally-fast command (snapshot, click)
never emits one.
"""


# ---------------------------------------------------------------------------
# Locator interaction ceiling
# ---------------------------------------------------------------------------

CLICK_S: float = _float_env("BRIDGIC_CLICK_TIMEOUT", 10.0)
"""Hard ceiling for a single ``locator.click / dblclick / check / uncheck``.

Playwright defaults to 30 s and retries ``visible, enabled, stable`` up to
the deadline. On Vue/React SPA pages Chrome can judge a freshly-scrolled
element as *still* outside viewport (sticky header, transform, animation),
and the retry loop spins for the full 30 s — blocking every other CLI
command queued on the daemon. Capping at 10 s keeps the CLI responsive.

The SDK default and the CLI daemon default are the same 10 s. Raise it via
``BRIDGIC_CLICK_TIMEOUT`` when a test needs to accommodate a slow-starting
SPA; lower it for tighter bail-out.
"""
