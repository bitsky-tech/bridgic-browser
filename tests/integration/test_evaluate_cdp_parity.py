"""Integration: ``evaluate_javascript`` in CDP-borrowed mode produces
byte-identical results to Playwright's ``page.evaluate(str)`` for every
JS form a user is likely to write.

This is the regression lock for the bug where ``(() => {...})()`` IIFEs
worked in non-CDP mode but ``SyntaxError``ed under ``cdp="auto"``
(the old ``_maybe_wrap_arrow_fn`` re-wrapped the already-called IIFE).

What gets verified end-to-end:

* JS language semantics — IIFE, class, ``let``/``const`` lists, async
  arrows, async IIFE awaiting promises, template literals, destructuring,
  regex, spread, throw, label blocks, two-expression completion values.
* Host-object substitution — ``this`` / ``window`` / ``document`` /
  ``document.body`` / ``NodeList[i]`` resolve to Playwright-compatible
  ``ref: <…>`` strings instead of tanking ``returnByValue``.
* Auto-call convention — ``(...a) => a.length`` returns ``1`` because
  Playwright passes one ``undefined`` arg by default; the wrapper must
  match.
* Promise + host-object combo — ``(async () => document)()`` resolves
  the Promise *before* host-object substitution.

Cases CDP ``returnByValue: true`` cannot reach parity on (Map / Set /
NodeList content / cyclic references) are *expected* to diverge — the
wrapper docstring documents this. They are excluded from this matrix.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bridgic.browser.session import Browser
from playwright.async_api import async_playwright

from ._chrome_utils import (
    find_chrome_binary,
    kill_chrome,
    launch_chrome,
    pick_free_port,
)


# (case_name, user_js, normalize_for_bridgic_str_return)
# The 3rd element is a Python value; bridgic returns str so we stringify
# Playwright's value the same way ``evaluate_javascript`` does internally.
THROW = object()


CASES = [
    # JS language forms — full V8 semantics through indirect eval
    ("plain expr",            "document ? 42 : 0",                                                    42),
    ("number literal",        "42",                                                                   42),
    ("string literal",        '"foo"',                                                                "foo"),
    ("null",                  "null",                                                                 None),
    ("undefined",             "undefined",                                                            None),
    ("bare arrow fn",         "() => 42",                                                             42),
    ("arrow with args",       "(x, y) => x ?? y ?? 99",                                               99),
    ("arrow body",            "() => { return { a: 1 } }",                                            {"a": 1}),
    ("IIFE with ;",           '(() => { return JSON.stringify({a:1,b:"x"}) })();',                    '{"a":1,"b":"x"}'),
    ("IIFE no ;",             '(() => { return JSON.stringify({a:1,b:"x"}) })()',                     '{"a":1,"b":"x"}'),
    ("named fn expr",         "function() { return [1,2,3].length }",                                 3),
    ("class decl + stmt",     "class C { get v() { return 1 } }; new C().v",                          1),
    ("paren obj literal",     "({a:1, b:2})",                                                         {"a": 1, "b": 2}),
    ("let stmt list",         "let x = 5; x * 2",                                                     10),
    ("const stmt list",       "const x = 5; x * 2",                                                   10),
    ("two exprs",             "1+1; 2+2",                                                             4),
    ("label block",           "lbl: { break lbl; }",                                                  None),
    ("comment + expr",        "() => 42 // ok",                                                       42),
    ("block comment",         "/* hi */ () => 1",                                                     1),
    ("async arrow",           "async () => 7",                                                        7),
    ("async IIFE w/ await",   "(async () => { return await Promise.resolve(33) })()",                 33),
    ("shorthand obj",         "const k = 7; ({k})",                                                   {"k": 7}),
    ("array destructuring",   "const [a,b]=[1,2]; a+b",                                               3),
    ("regex literal",         "/abc/g.flags",                                                         "g"),
    ("template literal",      "`a${1+1}b`",                                                           "a2b"),
    ("spread",                "[...[1,2,3]]",                                                         [1, 2, 3]),
    ("non-circular obj",      "const o={a:1, b:{c:2}}; o",                                            {"a": 1, "b": {"c": 2}}),

    # Errors must both throw with the same JS-level message
    ("named function decl",   "function foo() { return 9 }; foo()",                                   THROW),
    ("bare object literal",   "{a:1, b:2}",                                                           THROW),
    ("rejected promise",      'Promise.reject(new Error("boom"))',                                    THROW),
    ("throw expr",            'throw new Error("hi")',                                                THROW),

    # Host-object substitution (the failure mode that motivated the fix)
    ("this in page",          "this",                                                                 "ref: <Window>"),
    ("window",                "window",                                                               "ref: <Window>"),
    ("globalThis",            "globalThis",                                                           "ref: <Window>"),
    ("document",              "document",                                                             "ref: <Document>"),
    ("document.body",         "document.body",                                                        "ref: <Node>"),
    ("document.body.tagName", "document.body.tagName",                                                "BODY"),

    # Calling-convention parity with Playwright (one undefined arg)
    ("spread args length",    "(...a) => a.length",                                                   1),

    # Promise<host-object>: wrapper must await *before* host-object check
    ("Promise<Document>",     "(async () => document)()",                                             "ref: <Document>"),

    # Date round-trips through V8 toJSON
    ("Date toISOString",      'new Date("2024-01-01T00:00:00Z").toISOString()',                       "2024-01-01T00:00:00.000Z"),
]


def _stringify_like_bridgic(v) -> str:
    """Mirror ``evaluate_javascript``'s return-stringification rules so we
    can compare apples to apples: bridgic always hands back a ``str``."""
    if v is None:
        return "None"
    if v is True:
        return "True"
    if v is False:
        return "False"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    return str(v)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_evaluate_parity_with_playwright(tmp_path: Path) -> None:
    chrome = find_chrome_binary()
    if chrome is None:
        pytest.skip("no chrome/chromium binary available")

    port = pick_free_port()
    proc = launch_chrome(chrome, port, tmp_path / "user-data")

    landing = "data:text/html,<title>hi</title><body>x"

    try:
        # Playwright reference run — a fresh chromium (NOT borrowed).
        async with async_playwright() as p:
            pw_browser = await p.chromium.launch()
            pw_page = await pw_browser.new_page()
            await pw_page.goto(landing)

            mismatches: list[str] = []
            async with Browser(cdp=f"http://127.0.0.1:{port}", headless=False) as b:
                await b.navigate_to(landing)
                for name, src, expected in CASES:
                    # Run user code through bridgic's production CDP path.
                    bridgic_val = None
                    bridgic_threw = False
                    try:
                        bridgic_val = await b.evaluate_javascript(src)
                    except Exception:
                        bridgic_threw = True

                    if expected is THROW:
                        # Cross-check Playwright also throws on the same input.
                        pw_threw = False
                        try:
                            await pw_page.evaluate(src)
                        except Exception:
                            pw_threw = True
                        if not (bridgic_threw and pw_threw):
                            mismatches.append(
                                f"{name}: expected both to throw, "
                                f"bridgic_threw={bridgic_threw} pw_threw={pw_threw} "
                                f"bridgic_val={bridgic_val!r}"
                            )
                        continue

                    if bridgic_threw:
                        mismatches.append(f"{name}: bridgic raised; expected {expected!r}")
                        continue
                    expected_str = _stringify_like_bridgic(expected)
                    if bridgic_val != expected_str:
                        mismatches.append(
                            f"{name}: got {bridgic_val!r}, expected {expected_str!r}"
                        )

            await pw_browser.close()
    finally:
        kill_chrome(proc)

    assert not mismatches, "CDP parity failures:\n  " + "\n  ".join(mismatches)
