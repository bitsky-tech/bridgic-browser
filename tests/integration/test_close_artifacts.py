"""
Integration tests for browser close with active trace/video sessions.

Two scenarios:
  A) close() called directly while trace + video are still active
     → artifacts auto-finalized, paths returned in close() result
  B) stop_tracing() + stop_video() called first, then close()
     → artifacts already saved before close(), close() should be clean

Note: close-report.json is written by the daemon (CLI path) only.
      These tests cover the Python SDK path (direct browser.close()).

Run with:
    uv run pytest tests/integration/test_close_artifacts.py -v -s
"""

import re
import time
from pathlib import Path

import pytest

from bridgic.browser.session import Browser


TEST_URL = "https://www.example.com"


async def _do_browsing(browser: Browser) -> None:
    """Shared browsing actions used by both test scenarios."""
    await browser.navigate_to(TEST_URL)
    await browser.get_snapshot()
    await browser.take_screenshot()


def _parse_paths(text: str, suffix: str) -> list[str]:
    """Extract file paths with the given suffix from a result string."""
    return [p for p in re.findall(r"[^\s]+", text) if p.endswith(suffix)]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_close_direct_with_active_trace_and_video():
    """Scenario A: stop() while trace + video are still active.

    Verifies:
    - inspect_pending_close_artifacts() pre-allocates trace path
    - stop() completes and returns a result containing artifact paths
    - Trace file exists on disk and is non-empty
    - Video file exists on disk
    """
    browser = Browser(headless=True, stealth=False)

    await browser.navigate_to(TEST_URL)
    await browser.start_tracing()
    await browser.start_video()
    await _do_browsing(browser)

    # Pre-allocate paths (daemon calls this before responding to client)
    artifacts = browser.inspect_pending_close_artifacts()
    print(f"\n  session_dir:       {artifacts['session_dir']}")
    print(f"  pre-alloc trace:   {artifacts['trace']}")
    print(f"  pre-alloc video_dir: {artifacts.get('video_dir')}")

    assert artifacts["trace"], "Expected a pre-allocated trace path"
    pre_trace = Path(artifacts["trace"][0])
    assert pre_trace.exists(), "Pre-allocated trace.zip should be created on disk"

    t0 = time.monotonic()
    result = await browser.close()
    elapsed = time.monotonic() - t0
    print(f"\n  stop() elapsed: {elapsed:.3f}s")
    print(f"  stop() result:\n{result}")

    # Trace file must exist and be non-empty
    trace_paths = _parse_paths(result, ".zip")
    assert trace_paths, f"Expected trace path in stop() result:\n{result}"
    for tp in trace_paths:
        p = Path(tp)
        assert p.exists(), f"Trace file missing: {p}"
        assert p.stat().st_size > 0, f"Trace file is empty: {p}"
        print(f"  trace file OK: {p.name} ({p.stat().st_size // 1024} KB)")

    # Video file must exist
    video_paths = _parse_paths(result, ".webm")
    assert video_paths, f"Expected video path in stop() result:\n{result}"
    for vp in video_paths:
        p = Path(vp)
        assert p.exists(), f"Video file missing: {p}"
        print(f"  video file OK: {p.name} ({p.stat().st_size // 1024} KB)")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_close_after_explicit_stop_trace_and_video():
    """Scenario B: stop_tracing() + stop_video() called before stop().

    Verifies:
    - stop_tracing() returns a valid trace path and file is written immediately
    - stop_video() returns without error
    - inspect_pending_close_artifacts() reports no active trace (already stopped)
    - stop() completes cleanly, no duplicate artifact paths
    """
    browser = Browser(headless=True, stealth=False)

    await browser.navigate_to(TEST_URL)
    await browser.start_tracing()
    await browser.start_video()
    await _do_browsing(browser)

    # Explicitly stop before close
    trace_result = await browser.stop_tracing()
    video_result = await browser.stop_video()
    print(f"\n  stop_tracing() → {trace_result}")
    print(f"  stop_video()   → {video_result}")

    # Trace file should already be on disk
    trace_paths = _parse_paths(trace_result, ".zip")
    assert trace_paths, f"Expected trace path in stop_tracing() result: {trace_result}"
    early_trace = Path(trace_paths[0])
    assert early_trace.exists(), f"Trace file not written yet: {early_trace}"
    assert early_trace.stat().st_size > 0, f"Trace file is empty: {early_trace}"
    print(f"  early trace OK: {early_trace.name} ({early_trace.stat().st_size // 1024} KB)")

    # No active trace at close time
    artifacts = browser.inspect_pending_close_artifacts()
    print(f"  session_dir:     {artifacts['session_dir']}")
    print(f"  pre-alloc trace: {artifacts['trace']} (should be empty)")
    assert artifacts["trace"] == [], (
        f"Expected no pre-allocated trace (already stopped), got: {artifacts['trace']}"
    )

    t0 = time.monotonic()
    result = await browser.close()
    elapsed = time.monotonic() - t0
    print(f"\n  stop() elapsed: {elapsed:.3f}s")
    print(f"  stop() result:\n{result}")

    # stop() should NOT mention another trace file (already saved earlier)
    new_trace_paths = _parse_paths(result, ".zip")
    assert new_trace_paths == [], (
        f"stop() should not auto-save another trace, got: {new_trace_paths}"
    )
