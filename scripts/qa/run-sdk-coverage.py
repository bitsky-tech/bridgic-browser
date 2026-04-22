#!/usr/bin/env python3
"""SDK differential coverage for bridgic-browser.

Exercises the ~11 SDK methods on Browser that are NOT exposed through the CLI
(see scripts/qa/cli-command-matrix.md and bridgic/browser/_cli_catalog.py).

Intended to be invoked by run-mode-matrix.sh for variants V1, V3, V5 — the
other variants (headed / V7 smoke) re-use the same SDK path so one headless
verification per connection-mode is sufficient.

Usage:
    python3 scripts/qa/run-sdk-coverage.py --variant V1
    python3 scripts/qa/run-sdk-coverage.py --variant V3
    python3 scripts/qa/run-sdk-coverage.py --variant V5 --cdp 9222

Output:
    $QA_DIR/sdk-coverage/<variant>/results.tsv       — per-method PASS/FAIL/N/A
    $QA_DIR/sdk-coverage/<variant>/run.log           — exception tracebacks
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

_PAGE_ID_RE = re.compile(r"(page_[0-9a-fA-F]+)")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bridgic.browser.session._browser import Browser  # noqa: E402
from bridgic.browser.session._snapshot import EnhancedSnapshot  # noqa: E402


PLAYGROUND = f"file://{ROOT}/scripts/qa/cli-full-coverage.html"


def _qa_dir() -> Path:
    qa = os.environ.get("QA_DIR")
    if qa:
        return Path(qa)
    return Path("/tmp") / "bridgic-qa-sdk-manual"


def _browser_kwargs(variant: str, cdp: Optional[str]) -> dict[str, Any]:
    if variant == "V1":
        return {"headless": True, "stealth": True}
    if variant == "V3":
        return {"headless": True, "stealth": True, "clear_user_data": True}
    if variant == "V5":
        if not cdp:
            raise SystemExit("V5 requires --cdp PORT_OR_URL")
        return {"cdp": cdp, "headless": True}
    raise SystemExit(f"unsupported variant: {variant}")


class Recorder:
    def __init__(self, variant: str) -> None:
        self.report_dir = _qa_dir() / "sdk-coverage" / variant
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.results_path = self.report_dir / "results.tsv"
        self.log_path = self.report_dir / "run.log"
        self._results_fh = self.results_path.open("w", encoding="utf-8")
        self._results_fh.write("method\tstatus\tnote\n")
        self._log_fh = self.log_path.open("w", encoding="utf-8")

    def record(self, method: str, status: str, note: str = "") -> None:
        self._results_fh.write(f"{method}\t{status}\t{note}\n")
        self._results_fh.flush()
        self._log_fh.write(f"[{status}] {method}  {note}\n")
        self._log_fh.flush()

    def record_exception(self, method: str, exc: BaseException) -> None:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        self._log_fh.write(f"[FAIL] {method}\n")
        self._log_fh.writelines(tb)
        self._log_fh.write("\n")
        self._log_fh.flush()
        short = (str(exc) or type(exc).__name__).splitlines()[0][:200]
        self._results_fh.write(f"{method}\tFAIL\t{short}\n")
        self._results_fh.flush()

    def close(self) -> None:
        self._results_fh.close()
        self._log_fh.close()


async def _run_case(
    rec: Recorder,
    name: str,
    fn: Callable[[], Any],
    *,
    expect: Callable[[Any], Optional[str]] | None = None,
) -> None:
    """Run a single SDK test case.

    ``expect`` returns ``None`` when result looks OK, or a short string with
    the reason the result is wrong.
    """
    try:
        result = await fn()
    except Exception as exc:  # noqa: BLE001 — we want to log anything
        rec.record_exception(name, exc)
        return
    if expect is not None:
        reason = expect(result)
        if reason:
            rec.record(name, "FAIL", f"assertion: {reason}")
            return
    rec.record(name, "PASS")


async def _exercise_sdk(browser: Browser, rec: Recorder) -> None:
    # Navigate first so subsequent snapshot-driven calls have a live page.
    await browser.navigate_to(PLAYGROUND)

    # 1. get_snapshot (raw object)
    snap_holder: dict[str, Any] = {}

    async def _get_snap() -> EnhancedSnapshot:
        snap = await browser.get_snapshot(interactive=False)
        snap_holder["snap"] = snap
        return snap

    def _check_snap(snap: EnhancedSnapshot) -> Optional[str]:
        if not getattr(snap, "tree", None):
            return ".tree empty"
        if not isinstance(getattr(snap, "refs", None), dict):
            return ".refs is not a dict"
        return None

    await _run_case(rec, "get_snapshot", _get_snap, expect=_check_snap)

    # 2. get_element_by_ref — pick any ref from the snapshot
    async def _get_elem():
        snap: EnhancedSnapshot = snap_holder.get("snap")  # type: ignore[assignment]
        if snap is None or not snap.refs:
            raise RuntimeError("no snapshot refs available")
        ref = next(iter(snap.refs.keys()))
        locator = await browser.get_element_by_ref(ref)
        return (ref, locator)

    def _check_elem(result: Any) -> Optional[str]:
        _ref, loc = result
        if loc is None:
            return "locator is None"
        return None

    await _run_case(rec, "get_element_by_ref", _get_elem, expect=_check_elem)

    # 3. get_page_desc
    await _run_case(
        rec, "get_page_desc",
        lambda: browser.get_page_desc(),
        expect=lambda r: None if r is not None else "returned None",
    )

    # 4. get_all_page_descs
    await _run_case(
        rec, "get_all_page_descs",
        lambda: browser.get_all_page_descs(),
        expect=lambda r: None if isinstance(r, list) and r else "empty or wrong type",
    )

    # 5. get_page_size_info
    await _run_case(
        rec, "get_page_size_info",
        lambda: browser.get_page_size_info(),
        expect=lambda r: None if r is not None else "returned None",
    )

    # 6. get_current_page
    await _run_case(
        rec, "get_current_page",
        lambda: browser.get_current_page(),
        expect=lambda r: None if r is not None else "returned None",
    )

    # 7. get_current_page_title
    await _run_case(
        rec, "get_current_page_title",
        lambda: browser.get_current_page_title(),
        expect=lambda r: None if isinstance(r, str) and r else "empty title",
    )

    # 8. get_full_page_info
    await _run_case(
        rec, "get_full_page_info",
        lambda: browser.get_full_page_info(),
        expect=lambda r: None if r is not None else "returned None",
    )

    # 9. switch_to_page — open a second tab, switch back and forth.
    # new_tab() returns a human message like "Opened new tab page_abc123 at ...",
    # so we extract the page_id with a regex rather than passing the string whole.
    async def _switch_tab_roundtrip():
        result = await browser.new_tab("about:blank")
        m = _PAGE_ID_RE.search(result)
        if not m:
            raise RuntimeError(f"could not parse page_id from new_tab result: {result!r}")
        new_page_id = m.group(1)
        ok, msg = await browser.switch_to_page(new_page_id)
        if not ok:
            raise RuntimeError(f"switch_to_page failed: {msg}")
        await browser.close_tab(new_page_id)
        return (new_page_id, msg)

    await _run_case(rec, "switch_to_page", _switch_tab_roundtrip)

    # 10. scroll_to_text — the playground HTML has "Offscreen Target" far below
    await _run_case(
        rec, "scroll_to_text",
        lambda: browser.scroll_to_text("Offscreen Target"),
        expect=lambda r: None if isinstance(r, str) else "non-string result",
    )

    # 11. get_element_by_prompt — LLM-dependent, always N/A here
    rec.record(
        "get_element_by_prompt",
        "N/A",
        "requires OPENAI_API_KEY + OpenAILlm; excluded from non-LLM SDK pass",
    )


async def _async_main(variant: str, cdp: Optional[str]) -> int:
    rec = Recorder(variant)
    try:
        kwargs = _browser_kwargs(variant, cdp)
        # 12. async with Browser(...) as b: — exercised implicitly here.
        async with Browser(**kwargs) as browser:
            rec.record("async_with_Browser", "PASS", "context-manager entered")
            await _exercise_sdk(browser, rec)
    except Exception as exc:  # noqa: BLE001
        rec.record_exception("async_with_Browser", exc)
    finally:
        rec.close()
    # Print summary
    pass_count = fail_count = na_count = 0
    with open(rec.results_path, encoding="utf-8") as f:
        next(f, None)  # header
        for line in f:
            status = line.split("\t")[1] if "\t" in line else ""
            if status == "PASS":
                pass_count += 1
            elif status == "FAIL":
                fail_count += 1
            elif status == "N/A":
                na_count += 1
    print(f"[sdk-coverage {variant}] PASS={pass_count} FAIL={fail_count} N/A={na_count}")
    print(f"[sdk-coverage {variant}] results: {rec.results_path}")
    return 0 if fail_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="bridgic-browser SDK differential coverage")
    parser.add_argument("--variant", required=True, choices=["V1", "V3", "V5"])
    parser.add_argument("--cdp", default=None, help="CDP port or URL (V5 only)")
    args = parser.parse_args()
    return asyncio.run(_async_main(args.variant, args.cdp))


if __name__ == "__main__":
    raise SystemExit(main())
