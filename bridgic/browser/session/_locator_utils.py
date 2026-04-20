"""Playwright locator helpers shared by the ``Browser`` class.

These are intentionally module-level (rather than methods on ``Browser``) so
they can be reused independently and mocked in unit tests without constructing
a live browser. Each one is written to be safe under CDP borrowed mode, where
``locator.evaluate()`` / ``page.evaluate()`` can hang because Playwright's
``_mainContext()`` never resolves for pre-existing tabs.
"""

import asyncio
import logging
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)


def _get_page_key(page) -> str:
    """Get a unique key for a page."""
    return str(id(page))


def _get_context_key(context) -> str:
    """Get a unique key for a context."""
    return str(id(context))


def _css_attr_equals(name: str, value: str) -> str:
    """Build a CSS attribute selector with basic quote escaping."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"[{name}='{escaped}']"


async def _filter_visible_locators(locators: list) -> list:
    """Return only locators confirmed visible; [] when none are.

    Used in dropdown-option resolution where hidden candidates (e.g., the shadow
    ``<select><option>`` that Arco/AntD-style widgets embed for a11y/form posting)
    must be dropped. Those hidden options receive dispatched clicks without
    side-effect, producing silent no-op selections.
    """
    if not locators:
        return []
    results = await asyncio.gather(
        *[locator.is_visible() for locator in locators],
        return_exceptions=True,
    )
    return [loc for loc, r in zip(locators, results) if r is True]


async def _safe_tag_name(locator) -> str:
    """Return lowercase tagName or "" on failure/timeout.

    Mirrors the CDP-safe pattern used in ``select_dropdown_option_by_ref``:
    ``locator.evaluate`` can hang in CDP-borrowed mode, so we bound the call
    with ``asyncio.wait_for`` and swallow any exception.
    """
    try:
        return await asyncio.wait_for(
            locator.evaluate("el => el.tagName.toLowerCase()"),
            timeout=1.0,
        )
    except Exception:
        return ""


async def _get_dropdown_option_locators(page, locator) -> list:
    """Resolve option locators for native, embedded, and portalized dropdowns.

    Strategy:
      (A) Native ``<select>``: return ``<option>`` children as-is. Closed
          ``<select>`` hides options from ``is_visible()`` but Playwright's
          ``select_option`` still selects them correctly.
      (B) Custom combobox: skip ``locator.locator("option")`` entirely — in
          Arco/AntD/Element Plus/Headless UI components those are a hidden
          shadow ``<select>`` used for form-posting/a11y only, and clicking
          them is a silent no-op. Instead:
            B1. ``aria-controls`` / ``aria-owns`` target → visible options
            B2. Combobox subtree ``[role='option']`` → visible
            B3. Exactly one visible ``[role='listbox']`` on page → its options

    No page-wide ``[role='option']`` fallback: when multiple listboxes are
    visible it's ambiguous which one belongs to this trigger, and a wrong
    guess silently selects from an unrelated widget.
    """
    tag = await _safe_tag_name(locator)
    if tag == "select":
        return await locator.locator("option").all()

    if page is None:
        return []

    # B1. aria-controls / aria-owns — portalized listbox container.
    # Trust the aria-controls relationship: shadow-<select> wrappers don't set
    # aria-controls, so any id target here points at the real listbox. Do NOT
    # gate on container-level visibility — virtualized dropdowns (AntD
    # rc-virtual-list, react-window, …) render the container with a 0×0 bbox
    # while absolutely-positioning visible option rows inside, and Playwright's
    # is_visible() rejects that container. Filter at the option level instead.
    controlled_ids: list[str] = []
    for attr_name in ("aria-controls", "aria-owns"):
        attr_value = await locator.get_attribute(attr_name)
        if attr_value:
            controlled_ids.extend(part for part in attr_value.split() if part)
    for controlled_id in controlled_ids:
        container = page.locator(_css_attr_equals("id", controlled_id))
        if await container.count() == 0:
            continue
        candidates = await container.locator("[role='option'], option").all()
        visible = await _filter_visible_locators(candidates)
        if visible:
            return visible
        # AntD/rc-virtual-list pattern: the aria-controls target is a shadow
        # a11y listbox containing 0-width ``role='option'`` ghosts, while the
        # actual rendered menu items (``.ant-select-item-option`` etc.) are
        # siblings inside the same portal shell (``.ant-select-dropdown``).
        # Ascend to the target's parent and search broader class-based
        # patterns covering AntD / Arco / Element Plus / Vuetify / Headless UI.
        shell = container.locator("xpath=..")
        shell_candidates = await shell.locator(
            "[role='option'], option, "
            "[class*='select-item-option'], [class*='select-option'], "
            "[class*='select-item'], [class*='menu-item-option'], "
            "[class*='dropdown-item'], [class*='menu-item']"
        ).all()
        visible = await _filter_visible_locators(shell_candidates)
        if visible:
            return visible

    # B2. [role='option'] descendants of the trigger (e.g., when listbox is
    # a sibling/descendant rather than portalized). Strict visibility filter
    # is required here: shadow-<select> wrappers nest their hidden <option>
    # inside the trigger's subtree, and only per-option filtering can drop them.
    candidates = await locator.locator("[role='option']").all()
    visible = await _filter_visible_locators(candidates)
    if visible:
        return visible

    # B3. Exactly one visible listbox anywhere on the page. Already disambiguated
    # by container visibility, so skip per-option filtering for the same reason
    # as B1.
    listboxes = await page.locator("[role='listbox']").all()
    visible_listboxes = await _filter_visible_locators(listboxes)
    if len(visible_listboxes) == 1:
        candidates = await visible_listboxes[0].locator("[role='option'], option").all()
        if candidates:
            return candidates

    return []


async def _is_native_checkbox_or_radio(locator) -> bool:
    """Return True when locator points to <input type=checkbox|radio>.

    Uses ``get_attribute("type")`` instead of ``evaluate()`` to avoid
    Playwright's ``_mainContext()`` hang on pre-existing CDP tabs.
    Only ``<input type=checkbox|radio>`` elements carry those type values, so
    the tagName check is redundant. A custom element with an explicit
    ``type="checkbox"`` attribute would be misidentified, but this is
    vanishingly rare in practice.
    """
    try:
        input_type = (await locator.get_attribute("type") or "").strip().lower()
        return input_type in {"checkbox", "radio"}
    except Exception:
        return False


async def _is_checked(locator) -> bool:
    """Check both native .checked and aria-checked state.

    Uses ``is_checked()`` (CDP-backed, has timeout) plus ``get_attribute``
    instead of ``evaluate()`` to avoid the ``_mainContext()`` hang on
    pre-existing CDP tabs.
    """
    try:
        if await locator.is_checked():
            return True
    except Exception:
        pass
    try:
        aria = (await locator.get_attribute("aria-checked") or "").strip().lower()
        return aria == "true"
    except Exception:
        return False


async def _cdp_evaluate_on_element(cdp_context, page, locator, code: str) -> Any:
    """Evaluate *code* (an arrow function ``el => ...``) on the DOM element
    identified by *locator*, using a raw CDPSession.

    Resolves the element via bounding-box coordinates + ``document.elementFromPoint``
    so it bypasses Playwright's ``_mainContext()`` which hangs on pre-existing
    CDP-borrowed tabs. Raises on any failure (caller must handle).

    Scroll-race detection: the locator's bbox is re-acquired after the
    ``elementFromPoint`` call and compared with the pre-call bbox. If the
    page scrolled in between, the coordinates resolved to a different
    element — we raise a clear error so the caller can retry instead of
    silently executing JS on the wrong node.
    """
    bbox = await locator.bounding_box()
    if bbox is None:
        raise RuntimeError("Element has no bounding box — cannot resolve via CDPSession")
    cx = int(bbox["x"] + bbox["width"] / 2)
    cy = int(bbox["y"] + bbox["height"] / 2)
    session = await cdp_context.new_cdp_session(page)
    try:
        elem_result = await asyncio.wait_for(
            session.send("Runtime.evaluate", {
                "expression": f"document.elementFromPoint({cx},{cy})",
                "returnByValue": False,
            }),
            timeout=5.0,
        )
        object_id = elem_result.get("result", {}).get("objectId")
        if not object_id:
            raise RuntimeError("No element found at coordinates via CDPSession")
        bbox_after = await locator.bounding_box()
        # M4: CSS `scroll-behavior: smooth` animates `scrollIntoViewIfNeeded`
        # across multiple frames. The first post-check can catch the element
        # mid-animation; if so, wait a short beat and re-probe once.
        _bbox_changed = (
            bbox_after is None
            or abs(bbox_after["x"] - bbox["x"]) > 1
            or abs(bbox_after["y"] - bbox["y"]) > 1
            or abs(bbox_after["width"] - bbox["width"]) > 1
            or abs(bbox_after["height"] - bbox["height"]) > 1
        )
        if _bbox_changed:
            await asyncio.sleep(0.1)
            bbox_after = await locator.bounding_box()
        if bbox_after is None:
            raise RuntimeError(
                "Element disappeared during CDP resolution — possible scroll race"
            )
        if (
            abs(bbox_after["x"] - bbox["x"]) > 1
            or abs(bbox_after["y"] - bbox["y"]) > 1
            or abs(bbox_after["width"] - bbox["width"]) > 1
            or abs(bbox_after["height"] - bbox["height"]) > 1
        ):
            raise RuntimeError(
                f"Element moved during CDP resolution — scroll race detected "
                f"(bbox before={bbox}, after={bbox_after})"
            )
        call_result = await asyncio.wait_for(
            session.send("Runtime.callFunctionOn", {
                "functionDeclaration": code,
                "objectId": object_id,
                "arguments": [{"objectId": object_id}],
                "returnByValue": True,
                "awaitPromise": True,
            }),
            timeout=30.0,
        )
        if call_result.get("exceptionDetails"):
            raise RuntimeError(f"JS exception: {call_result['exceptionDetails']}")
        return call_result.get("result", {}).get("value")
    finally:
        try:
            await session.detach()
        except Exception:
            pass


_DEFAULT_CLICK_TIMEOUT_MS = 10000
"""Hard ceiling for locator.click / dblclick / check / uncheck.

Playwright defaults to 30s and retries ``visible, enabled, stable`` up to the
deadline. On Vue/React SPA pages Chrome can judge a freshly-scrolled element
as *still* outside viewport (e.g. because a sticky header or transform occupies
the slot), and the retry loop spins for the full 30s — blocking every other
CLI command queued on the daemon. Capping at 10s keeps the CLI responsive;
the dispatch_event fallback below recovers the common case."""


async def _locator_action_with_fallback(
    locator,
    *,
    action: str,
    fallback_event: str = "click",
    timeout_ms: int = _DEFAULT_CLICK_TIMEOUT_MS,
) -> None:
    """Invoke ``locator.<action>`` with a hard timeout and dispatch_event fallback.

    Parameters
    ----------
    locator : Locator
        Playwright locator to act on.
    action : str
        Method name on the locator: ``"click"``, ``"dblclick"``, ``"check"``,
        or ``"uncheck"``.
    fallback_event : str, default ``"click"``
        DOM event to dispatch when the primary action times out. For ``check``
        and ``uncheck`` on custom ARIA widgets, ``"click"`` is the right event;
        ``dblclick`` uses ``"dblclick"``.
    timeout_ms : int, default :data:`_DEFAULT_CLICK_TIMEOUT_MS`
        Explicit timeout passed to Playwright. Shorter than the default 30s
        so a stuck actionability retry loop cannot freeze the CLI.

    Notes
    -----
    ``dispatch_event`` bypasses Playwright's actionability checks and directly
    fires the DOM event on the element. It is the right fallback when the
    element is logically interactive but geometrically confusing to
    Playwright (sticky/transform/absolute positioning, SPA layout quirks).
    """
    method = getattr(locator, action)
    try:
        await method(timeout=timeout_ms)
    except PlaywrightTimeoutError as e:
        logger.warning(
            "[_locator_action_with_fallback] %s timed out after %dms; "
            "falling back to dispatch_event(%r). Underlying: %s",
            action, timeout_ms, fallback_event, e,
        )
        await locator.dispatch_event(fallback_event)


async def _check_element_covered(locator, cx: float, cy: float, cdp_context=None) -> bool:
    """Return True when another element sits on top of (cx, cy).

    In CDP borrowed mode (``cdp_context`` provided) ``locator.evaluate()``
    hangs because Playwright's ``_mainContext()`` never resolves for
    pre-existing tabs. We return ``False`` immediately so callers fall
    through to ``locator.click()`` which uses the utility world and handles
    overlays internally.
    """
    if cdp_context is not None:
        return False
    try:
        return await asyncio.wait_for(
            locator.evaluate(
                f"(el) => {{ if (window.parent !== window) return false; "
                f"const t = document.elementFromPoint({cx}, {cy}); "
                f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
            ),
            timeout=10.0,
        )
    except Exception:
        return False


async def _click_covering_element(page, locator, cx: float, cy: float, cdp_context=None) -> None:
    """Click the element that covers position (cx, cy).

    In CDP borrowed mode (``cdp_context`` provided) uses a raw CDPSession
    ``Runtime.evaluate`` to click the topmost element at the coordinates,
    bypassing ``page.evaluate()`` which hangs on pre-existing tabs.
    Falls back to ``locator.dispatch_event("click")`` on any failure.
    """
    if cdp_context is not None:
        session = None
        try:
            session = await cdp_context.new_cdp_session(page)
            expr = f"document.elementFromPoint({cx}, {cy})?.click()"
            await asyncio.wait_for(
                session.send("Runtime.evaluate", {"expression": expr}),
                timeout=5.0,
            )
        except Exception:
            await locator.dispatch_event("click")
        finally:
            if session:
                try:
                    await session.detach()
                except Exception:
                    pass
        return
    try:
        await asyncio.wait_for(
            page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()"),
            timeout=10.0,
        )
    except Exception:
        await locator.dispatch_event("click")


async def _click_checkable_target(page, locator, bbox, cdp_context=None) -> None:
    """Click a checkable target with overlay handling and shadow DOM fallback."""
    if bbox is not None:
        cx = bbox["x"] + bbox["width"] / 2
        cy = bbox["y"] + bbox["height"] / 2
        if not await locator.is_visible():
            logger.debug("_click_checkable_target: bbox present but is_visible()=False; using dispatch_event click")
            await locator.dispatch_event("click")
            return

        covered = await _check_element_covered(locator, cx, cy, cdp_context=cdp_context)
        if covered:
            logger.debug("_click_checkable_target: covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
            if page:
                await _click_covering_element(page, locator, cx, cy, cdp_context=cdp_context)
            else:
                await locator.dispatch_event("click")
        else:
            await locator.click()
        return

    if await locator.is_visible():
        await locator.click()
    else:
        logger.debug("_click_checkable_target: no bbox and is_visible()=False; using dispatch_event click")
        await locator.dispatch_event("click")
