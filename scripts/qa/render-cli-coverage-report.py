#!/usr/bin/env python3
"""Render a markdown coverage report from coverage-results.tsv."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: render-cli-coverage-report.py <coverage-results.tsv> <output.md>")
        return 2

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not in_path.exists():
        print(f"missing input: {in_path}", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(in_path.open(encoding="utf-8"), delimiter="\t"))
    counts = Counter(r["status"] for r in rows)
    failed = [r for r in rows if r["status"] == "FAIL"]
    na_rows = [r for r in rows if r["status"] == "N/A"]

    lines = [
        "# CLI Full Coverage Report",
        "",
        f"- Total commands: {len(rows)}",
        f"- PASS: {counts.get('PASS', 0)}",
        f"- FAIL: {counts.get('FAIL', 0)}",
        f"- N/A: {counts.get('N/A', 0)}",
        "",
        "## Failures",
    ]

    if failed:
        for row in failed:
            lines.append(
                f"- `{row['command']}` | evidence: `{row['evidence']}` | note: `{row['note']}`"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## N/A", ""])
    if na_rows:
        for row in na_rows:
            lines.append(f"- `{row['command']}` | note: `{row['note']}`")
    else:
        lines.append("- None")

    lines.extend(["", "## Full Result Table", "", "| Command | Status | Evidence | Note |", "|---|---|---|---|"])
    for row in rows:
        lines.append(
            f"| `{row['command']}` | {row['status']} | `{row['evidence']}` | {row['note']} |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
