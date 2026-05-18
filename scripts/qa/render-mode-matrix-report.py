#!/usr/bin/env python3
"""Aggregate per-variant CLI + SDK coverage TSVs into a single mode-matrix report.

Reads:
    <qa_dir>/cli-full-coverage/V?/coverage-results.tsv
    <qa_dir>/sdk-coverage/V?/results.tsv

Writes to stdout (redirect into mode-matrix-report.md):

    # Mode Matrix Report
    ## Summary (counts per variant)
    ## CLI Coverage Matrix
    ## SDK Differential Matrix
"""

from __future__ import annotations

import csv
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, List

ALL_VARIANTS: List[str] = ["V1", "V2", "V3", "V4", "V5", "V6", "V7"]
SDK_VARIANTS: List[str] = ["V1", "V3", "V5"]

VARIANT_LABELS: Dict[str, str] = {
    "V1": "Persistent × Headless × Stealth=on",
    "V2": "Persistent × Headed × Stealth=on",
    "V3": "Ephemeral × Headless × Stealth=on",
    "V4": "Ephemeral × Headed × Stealth=on",
    "V5": "CDP × Headless × Stealth=on",
    "V6": "CDP × Headed × Stealth=on",
    "V7": "Persistent × Headless × Stealth=off (smoke)",
}


def _read_tsv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"))


def _status_cell(rows_by_key: Dict[str, str], key: str) -> str:
    status = rows_by_key.get(key)
    if status is None:
        return "—"
    if status == "PASS":
        return "✅"
    if status == "FAIL":
        return "❌"
    if status == "N/A":
        return "➖"
    return status


def _note_cell(notes_by_key: Dict[str, str], key: str) -> str:
    note = notes_by_key.get(key, "")
    if not note:
        return ""
    return note.replace("|", "\\|").replace("\n", " ")[:60]


def _cli_matrix(qa_dir: Path) -> List[str]:
    out: List[str] = ["## CLI Coverage Matrix", ""]

    per_variant_rows: Dict[str, List[dict]] = OrderedDict()
    for v in ALL_VARIANTS:
        per_variant_rows[v] = _read_tsv(qa_dir / "cli-full-coverage" / v / "coverage-results.tsv")

    # union of command keys across variants, preserving insertion order from V1
    keys_in_order: List[str] = []
    seen = set()
    for v in ALL_VARIANTS:
        for row in per_variant_rows[v]:
            cmd = row.get("command", "")
            if cmd and cmd not in seen:
                keys_in_order.append(cmd)
                seen.add(cmd)

    if not keys_in_order:
        out.append("_(no CLI coverage TSVs found)_")
        out.append("")
        return out

    header_cells = ["Command"] + ALL_VARIANTS
    out.append("| " + " | ".join(header_cells) + " |")
    out.append("|" + "|".join(["---"] * len(header_cells)) + "|")

    for cmd in keys_in_order:
        status_row = [f"`{cmd}`"]
        for v in ALL_VARIANTS:
            rows = per_variant_rows[v]
            status_map = {r["command"]: r["status"] for r in rows}
            status_row.append(_status_cell(status_map, cmd))
        out.append("| " + " | ".join(status_row) + " |")

    out.append("")
    out.append("Legend: ✅ PASS · ❌ FAIL · ➖ N/A · — not run")
    out.append("")
    return out


def _variant_summary(qa_dir: Path) -> List[str]:
    out: List[str] = ["## Summary", ""]
    out.append("| Variant | Description | CLI PASS | CLI FAIL | CLI N/A | SDK PASS | SDK FAIL | SDK N/A |")
    out.append("|---|---|---|---|---|---|---|---|")

    for v in ALL_VARIANTS:
        cli_rows = _read_tsv(qa_dir / "cli-full-coverage" / v / "coverage-results.tsv")
        cli_c = Counter(r.get("status", "") for r in cli_rows)
        sdk_rows = _read_tsv(qa_dir / "sdk-coverage" / v / "results.tsv") if v in SDK_VARIANTS else []
        sdk_c = Counter(r.get("status", "") for r in sdk_rows)
        sdk_cells = (
            [sdk_c.get("PASS", 0), sdk_c.get("FAIL", 0), sdk_c.get("N/A", 0)]
            if sdk_rows
            else ["—", "—", "—"]
        )
        out.append(
            "| **{v}** | {label} | {p} | {f} | {na} | {sp} | {sf} | {sna} |".format(
                v=v,
                label=VARIANT_LABELS[v],
                p=cli_c.get("PASS", 0),
                f=cli_c.get("FAIL", 0),
                na=cli_c.get("N/A", 0),
                sp=sdk_cells[0],
                sf=sdk_cells[1],
                sna=sdk_cells[2],
            )
        )

    out.append("")
    return out


def _failure_details(qa_dir: Path) -> List[str]:
    out: List[str] = ["## Failures (CLI + SDK)", ""]
    found = False
    for v in ALL_VARIANTS:
        cli_rows = _read_tsv(qa_dir / "cli-full-coverage" / v / "coverage-results.tsv")
        sdk_rows = _read_tsv(qa_dir / "sdk-coverage" / v / "results.tsv") if v in SDK_VARIANTS else []

        cli_fails = [r for r in cli_rows if r.get("status") == "FAIL"]
        sdk_fails = [r for r in sdk_rows if r.get("status") == "FAIL"]

        if not cli_fails and not sdk_fails:
            continue
        found = True
        out.append(f"### {v} — {VARIANT_LABELS[v]}")
        out.append("")
        for r in cli_fails:
            out.append(f"- CLI `{r.get('command', '')}` — note: `{r.get('note', '')}` — evidence: `{r.get('evidence', '')}`")
        for r in sdk_fails:
            out.append(f"- SDK `{r.get('method', '')}` — note: `{r.get('note', '')}`")
        out.append("")

    if not found:
        out.append("_No failures across any variant._")
        out.append("")
    return out


def _sdk_matrix(qa_dir: Path) -> List[str]:
    out: List[str] = ["## SDK Differential Matrix", ""]
    per_variant_rows: Dict[str, List[dict]] = OrderedDict()
    for v in SDK_VARIANTS:
        per_variant_rows[v] = _read_tsv(qa_dir / "sdk-coverage" / v / "results.tsv")

    keys: List[str] = []
    seen = set()
    for v in SDK_VARIANTS:
        for r in per_variant_rows[v]:
            m = r.get("method", "")
            if m and m not in seen:
                keys.append(m)
                seen.add(m)

    if not keys:
        out.append("_(no SDK coverage TSVs found)_")
        out.append("")
        return out

    header = ["Method"] + SDK_VARIANTS + ["Note"]
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "|".join(["---"] * len(header)) + "|")
    for m in keys:
        row = [f"`{m}`"]
        last_note = ""
        for v in SDK_VARIANTS:
            rs = per_variant_rows[v]
            sm = {r["method"]: r["status"] for r in rs}
            nm = {r["method"]: r.get("note", "") for r in rs}
            row.append(_status_cell(sm, m))
            if not last_note and nm.get(m):
                last_note = nm[m]
        row.append(_note_cell({m: last_note}, m))
        out.append("| " + " | ".join(row) + " |")

    out.append("")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render-mode-matrix-report.py <qa_dir>", file=sys.stderr)
        return 2
    qa_dir = Path(sys.argv[1])
    if not qa_dir.exists():
        print(f"qa_dir does not exist: {qa_dir}", file=sys.stderr)
        return 1

    lines: List[str] = [
        "# bridgic-browser Mode Matrix Report",
        "",
        f"QA directory: `{qa_dir}`",
        "",
    ]
    lines += _variant_summary(qa_dir)
    lines += _failure_details(qa_dir)
    lines += _cli_matrix(qa_dir)
    lines += _sdk_matrix(qa_dir)

    sys.stdout.write("\n".join(lines))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
