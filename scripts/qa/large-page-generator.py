#!/usr/bin/env python3
"""
scripts/qa/large-page-generator.py [--sections N] [--buttons-per M] [--out PATH]

Generates an HTML page with N sections × M buttons = N*M clickable refs,
to exercise the 3-phase snapshot batching (task.md §4.1).

Default: 50 sections × 100 buttons = 5000 refs.
"""

import argparse
import pathlib
import sys

TEMPLATE_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>QA: {total} refs (sections={sections} buttons={per})</title>
<style>
  body {{ font-family: sans-serif; padding: 1em; }}
  section {{ margin: 1em 0; padding: 0.5em; border: 1px solid #ccc; }}
  button {{ margin: 2px; padding: 4px 8px; font-size: 12px; }}
  .label {{ color: #666; }}
</style>
<h1>Large page: {total} clickable refs</h1>
<p>Sections: {sections}, buttons per section: {per}. Click any button to
   bump <span id="count">0</span> clicks.</p>
<script>
  window._count = 0;
  function bump(id) {{
    window._count += 1;
    document.getElementById('count').textContent = window._count;
    document.title = 'click #' + id;
  }}
</script>
"""

SECTION = """
<section id="sec-{i}">
  <h2>Section {i}</h2>
  {buttons}
</section>
"""

BUTTON = '<button data-sec="{i}" data-btn="{j}" aria-label="S{i}-B{j}" onclick="bump(\'{i}-{j}\')">S{i}-B{j}</button>'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sections", type=int, default=50)
    ap.add_argument("--buttons-per", type=int, default=100)
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("/tmp/bridgic-qa-large.html"))
    args = ap.parse_args()

    total = args.sections * args.buttons_per
    pieces: list[str] = [
        TEMPLATE_PAGE.format(sections=args.sections, per=args.buttons_per, total=total)
    ]
    for i in range(args.sections):
        buttons = "\n    ".join(
            BUTTON.format(i=i, j=j) for j in range(args.buttons_per)
        )
        pieces.append(SECTION.format(i=i, buttons=buttons))

    args.out.write_text("\n".join(pieces), encoding="utf-8")
    print(f"wrote {args.out} ({total} refs, {args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
