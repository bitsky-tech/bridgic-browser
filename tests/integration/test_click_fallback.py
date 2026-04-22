"""Integration: click() fallback dispatch_event stays within the 10 s cap.

Covers QA finding H03: on a continuously-animating button the fallback
``dispatch_event`` used to inherit Playwright's 30 s default, so a click that
nominally capped at 10 s really took ~40 s. The fix bounds the fallback via
:data:`bridgic.browser._timeouts.FALLBACK_DISPATCH_TIMEOUT_MS`; this test
proves the total click budget is bounded from end to end.

Run:
    uv run pytest tests/integration/test_click_fallback.py -v
"""

import pathlib
import time

import pytest

from bridgic.browser.errors import BridgicBrowserError
from bridgic.browser.session import Browser


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SHAKE_BUTTON = REPO_ROOT / "scripts" / "qa" / "shake-button.html"
STABLE_FLAP = REPO_ROOT / "scripts" / "qa" / "stable-flap.html"


# Budget = CLICK_S (10s) + FALLBACK_DISPATCH_TIMEOUT_MS (2s) + overhead.
# CI slow headroom bumps the headroom slightly; sharp failures (the 40 s
# pre-fix behaviour) blow past this by a wide margin.
_CLICK_BUDGET_S = 15.0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_name",
    [
        pytest.param("shake-button.html", id="shake"),
        pytest.param("stable-flap.html", id="stable-flap"),
    ],
)
async def test_click_on_animating_element_respects_budget(fixture_name):
    fixture = REPO_ROOT / "scripts" / "qa" / fixture_name
    assert fixture.exists(), f"missing QA fixture: {fixture}"

    async with Browser(headless=True, stealth=False) as browser:
        await browser.navigate_to(f"file://{fixture}")
        snapshot = await browser.get_snapshot(interactive=True)
        ref = next(
            (r for r, data in snapshot.refs.items() if data.role == "button"),
            None,
        )
        assert ref is not None, f"no button ref in snapshot of {fixture_name}"

        start = time.perf_counter()
        try:
            await browser.click_element_by_ref(ref)
        except BridgicBrowserError:
            # Expected when the fallback dispatch also times out — that is the
            # whole point of the bounded timeout. Either raising or succeeding
            # within budget is acceptable; the blocker is exceeding budget.
            pass
        elapsed = time.perf_counter() - start

    assert elapsed < _CLICK_BUDGET_S, (
        f"click() on {fixture_name} took {elapsed:.2f}s, exceeds "
        f"{_CLICK_BUDGET_S:.1f}s budget (H03 regression)"
    )
