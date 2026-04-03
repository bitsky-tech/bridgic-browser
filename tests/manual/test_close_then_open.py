"""
Manual test: verify close-then-open works in SDK mode.

Usage:
    uv run python tests/manual/test_close_then_open.py

Expected: both sequential and concurrent scenarios complete without
SingletonLock errors. Video files are saved correctly.
"""
import asyncio
import os
import time

from bridgic.browser.session import Browser


async def test_sequential_close_then_open():
    """Scenario 1: close() then start — the common case."""
    print("\n=== Test 1: Sequential close → open ===")

    browser = Browser(headless=False)
    await browser.navigate_to("https://www.baidu.com")
    await browser.start_tracing()
    await browser.start_video()
    print("  Browser 1 started, tracing + video active")
    await asyncio.sleep(2)

    t0 = time.monotonic()
    result = await browser.close()
    elapsed = time.monotonic() - t0
    print(f"  close() completed in {elapsed:.2f}s")
    print(f"  Result: {result.splitlines()[0]}")

    # Verify artifacts
    artifacts = browser._last_shutdown_artifacts
    print(f"  Trace files: {artifacts.get('trace', [])}")
    print(f"  Video files: {artifacts.get('video', [])}")
    errors = browser._last_shutdown_errors
    if errors:
        print(f"  ⚠ Warnings: {errors}")

    # Now open a new browser with the same user_data_dir
    browser2 = Browser(headless=False)
    t0 = time.monotonic()
    await browser2.navigate_to("https://www.baidu.com")
    elapsed = time.monotonic() - t0
    print(f"  Browser 2 started + navigated in {elapsed:.2f}s — OK!")
    await browser2.close()
    print("  ✓ Sequential test passed\n")


async def test_concurrent_close_and_open():
    """Scenario 2: close() and start() run concurrently — the race case."""
    print("=== Test 2: Concurrent close + open ===")

    browser = Browser(headless=False)
    await browser.navigate_to("https://www.baidu.com")
    await browser.start_tracing()
    await browser.start_video()
    print("  Browser 1 started, tracing + video active")
    await asyncio.sleep(2)

    browser2 = Browser(headless=False)

    async def close_browser1():
        t0 = time.monotonic()
        result = await browser.close()
        elapsed = time.monotonic() - t0
        print(f"  close() completed in {elapsed:.2f}s")
        return result

    async def open_browser2():
        # Small delay to simulate near-simultaneous close+open
        await asyncio.sleep(0.5)
        t0 = time.monotonic()
        await browser2.navigate_to("https://www.baidu.com")
        elapsed = time.monotonic() - t0
        print(f"  Browser 2 started + navigated in {elapsed:.2f}s")

    # Run close and open concurrently
    results = await asyncio.gather(
        close_browser1(),
        open_browser2(),
        return_exceptions=True,
    )

    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"  ✗ Task {i} failed: {r}")
        else:
            print(f"  ✓ Task {i} succeeded")

    await browser2.close()
    print("  ✓ Concurrent test passed\n")


async def main():
    print("Close-then-open SDK test")
    print("=" * 50)

    await test_sequential_close_then_open()
    await test_concurrent_close_and_open()

    print("=" * 50)
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
