#!/usr/bin/env python3
"""Ceiling of an ideal output filter on grep's edges, measured in bytes.

QUESTION: if a perfect oracle filtered grep's output before an agent ever read it, how many
BYTES would disappear? This is the CEILING an output filter could reach — an ideal filter, not
our engine — and it exists to decide whether building a real one is worth anything at all.

This script does NOT reimplement the matching rule. It imports grader/grade.py and reuses its
identity function (`method_key` + `normalize_caller`), its oracle-cell split (`load_oracle`),
and its match test (the body of `Cell.score`'s inner loop, lifted verbatim into
`edge_confirmed`). The only piece not exposed as an importable function is the callee→cell
classifier `cell_of()`, which lives inline inside grade.py's `main()`; it is reproduced here
byte-for-byte (see `cell_of` below) rather than re-derived, so the two scripts cannot drift
apart on what counts as which cell.

Scope: `without-tests` cells only, `serilog` and `Polly` only. FluentValidation is EXCLUDED —
a recorded defect (2026-08-24) means its two cells scanned identical files including test code,
so its rows are contaminated and would not be comparable to the other two repos' numbers.

The published grader operates on grep's edges after they collapse into a SET of distinct
(caller, callee) pairs (see grade.load_arm). This script does not use that set directly: an
agent reads raw emitted LINES, and grep frequently repeats one (caller, callee) pair across many
lines (Polly: 8234 raw rows collapse to 5447 distinct pairs). So classification (confirmed /
contradicted / excluded / unremappable) is computed once per distinct normalised edge, using the
grader's own rule, and then applied to every raw row that edge appears in — bytes are summed at
the ROW level, because that is what actually reaches an agent's context window.

Usage:
    python3 grader/ceiling_grep_filter.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "grader"))
import grade  # noqa: E402  (reuse identity + oracle-cell logic; see module docstring)

RESULTS_ROOT = ROOT / "results" / "full-matrix-2026-08-24"
CELL = "without-tests"
REPOS = ["serilog", "Polly"]  # FluentValidation excluded — see module docstring
EXCLUDED_REPO_NOTE = (
    "FluentValidation is excluded from this measurement: a defect recorded 2026-08-24 means its "
    "with-tests and without-tests cells scanned identical files including test code, so its rows "
    "are contaminated and not comparable to serilog/Polly here."
)


def build_callee_cell(cells: dict) -> dict[str, str]:
    """Which oracle cell first claims each callee. Mirrors grade.py main() lines ~294-297."""
    callee_cell: dict[str, str] = {}
    for name, cell in cells.items():
        for _, callee in cell.oracle:
            callee_cell.setdefault(callee, name)
    return callee_cell


def cell_of(callee: str, callee_cell: dict[str, str], overrides: dict[str, set[str]]) -> str:
    """Verbatim reproduction of the `cell_of` closure inside grade.py's `main()`.

    Not importable as-is (it is a closure over locals built inside main()), so it is copied here
    exactly rather than re-derived, to keep this script's classification identical to the
    published grader's. See grade.py for the reasoning comments this omits for brevity.
    """
    known = callee_cell.get(callee)
    if known is not None:
        return known
    for declared in overrides.get(callee, ()):
        declared_cell = callee_cell.get(declared)
        if declared_cell is not None:
            return declared_cell
    method = callee.rpartition("::")[2]
    if method.startswith(("get_", "set_")):
        return "accessor"
    if method in grade.ENUMERATOR_PROTOCOL:
        return "enumerator"
    return "primary"


def edge_confirmed(edge: tuple[str, str], primary_oracle: set, overrides: dict[str, set[str]]) -> bool:
    """The match test lifted verbatim from grade.Cell.score()'s inner loop, for one edge."""
    if edge in primary_oracle:
        return True
    caller, callee = edge
    for declared in overrides.get(callee, ()):
        if (caller, declared) in primary_oracle:
            return True
    return False


def load_overrides(path: Path) -> dict[str, set[str]]:
    """Same canonicalisation grade.py's main() applies to the overrides map before use."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    overrides: dict[str, set[str]] = defaultdict(set)
    for override, declarations in raw.items():
        key = grade.method_key(override)
        if key is None:
            continue
        for declaration in declarations:
            declared_key = grade.method_key(declaration)
            if declared_key is not None and declared_key != key:
                overrides[key].add(declared_key)
    return overrides


def render_line(row: dict) -> str:
    """The realistic agent-visible form of one grep edge: `caller_file:caller_line: <callee>`."""
    return f"{row.get('caller_file')}:{row.get('caller_line')}: {row.get('callee', '')}\n"


def analyze_repo(repo_dir_name: str) -> dict:
    base = RESULTS_ROOT / repo_dir_name / CELL
    edges_path = base / "grep" / "edges.jsonl"
    oracle_path = base / "_oracle" / "oracle.jsonl"
    overrides_path = base / "_oracle" / "overrides.json"
    manifest_path = base / "grep" / "manifest.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_party = set(manifest["first_party"])

    cells, _oracle_overrides_unused, oracle_stats = grade.load_oracle(str(oracle_path), first_party)
    overrides = load_overrides(overrides_path)
    callee_cell = build_callee_cell(cells)
    primary_oracle = cells["primary"].oracle

    rows: list[dict] = []
    with open(edges_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    total_edges = len(rows)
    total_jsonl_bytes = os.path.getsize(edges_path)  # reference only; not used in the ceiling

    counts = {"confirmed": 0, "contradicted": 0, "excluded": 0, "unremappable_caller": 0, "malformed": 0}
    byte_sums = {"confirmed": 0, "contradicted": 0, "excluded": 0, "unremappable_caller": 0, "malformed": 0}
    total_rendered_bytes = 0

    # (caller_file, caller_line) -> set of classification labels seen at that location
    loc_labels: dict[tuple, set] = defaultdict(set)

    # cache the per-distinct-edge classification so repeated (caller,callee) pairs across many
    # rows are not re-walked through the oracle sets every time
    edge_label_cache: dict[tuple, str] = {}

    for row in rows:
        rendered = render_line(row)
        rb = len(rendered.encode("utf-8"))
        total_rendered_bytes += rb

        loc_key = (row.get("caller_file"), row.get("caller_line"))
        raw_caller = row.get("caller") or ""
        raw_callee = row.get("callee") or ""

        caller_key = grade.method_key(raw_caller)
        callee_key = grade.method_key(raw_callee)
        if caller_key is None or callee_key is None:
            label = "malformed"
        else:
            caller_norm, attributable = grade.normalize_caller(caller_key)
            if not attributable:
                label = "unremappable_caller"
            else:
                edge = (caller_norm, callee_key)
                cached = edge_label_cache.get(edge)
                if cached is not None:
                    label = cached
                else:
                    cell_name = cell_of(callee_key, callee_cell, overrides)
                    if cell_name != "primary":
                        label = "excluded"
                    elif edge_confirmed(edge, primary_oracle, overrides):
                        label = "confirmed"
                    else:
                        label = "contradicted"
                    edge_label_cache[edge] = label

        counts[label] += 1
        byte_sums[label] += rb
        loc_labels[loc_key].add(label)

    # ── Ceilings ────────────────────────────────────────────────────────────────
    # Naive ceiling: drop everything the oracle does not affirmatively confirm in the primary
    # cell (contradicted + excluded-cell + unremappable-caller + malformed).
    naive_dropped_bytes = total_rendered_bytes - byte_sums["confirmed"]
    # Honest ceiling: drop ONLY primary-cell edges the oracle contradicts. Keep excluded-cell
    # edges (the oracle simply has no opinion there — property accessors, foreach protocol,
    # external callees, compiler-generated, indirect ops) and keep unremappable-caller /
    # malformed rows too, for the same reason: the grader never puts these in a position to be
    # proven wrong, so an ideal filter built to remove only PROVEN-WRONG edges must leave them.
    honest_dropped_bytes = byte_sums["contradicted"]

    # ── Distinct locations ─────────────────────────────────────────────────────
    loc_total = len(loc_labels)
    loc_confirmed = sum(1 for labels in loc_labels.values() if "confirmed" in labels)
    loc_survive_honest = sum(
        1 for labels in loc_labels.values() if labels != {"contradicted"}
    )

    return {
        "repo": repo_dir_name,
        "cell": CELL,
        "first_party": sorted(first_party),
        "total_edges_raw_rows": total_edges,
        "total_jsonl_bytes": total_jsonl_bytes,
        "total_rendered_bytes": total_rendered_bytes,
        "counts": counts,
        "byte_sums": byte_sums,
        "naive_ceiling": {
            "dropped_bytes": naive_dropped_bytes,
            "pct_of_rendered": round(100 * naive_dropped_bytes / total_rendered_bytes, 2),
            "kept_bytes": total_rendered_bytes - naive_dropped_bytes,
        },
        "honest_ceiling": {
            "dropped_bytes": honest_dropped_bytes,
            "pct_of_rendered": round(100 * honest_dropped_bytes / total_rendered_bytes, 2),
            "kept_bytes": total_rendered_bytes - honest_dropped_bytes,
        },
        "gap_naive_minus_honest": {
            "bytes": naive_dropped_bytes - honest_dropped_bytes,
            "pct_points_of_rendered": round(
                100 * (naive_dropped_bytes - honest_dropped_bytes) / total_rendered_bytes, 2
            ),
        },
        "distinct_locations": {
            "total": loc_total,
            "confirmed_by_oracle": loc_confirmed,
            "survive_naive_ceiling": loc_confirmed,
            "survive_honest_ceiling": loc_survive_honest,
            "dropped_naive": loc_total - loc_confirmed,
            "dropped_honest": loc_total - loc_survive_honest,
            "pct_dropped_naive": round(100 * (loc_total - loc_confirmed) / loc_total, 2),
            "pct_dropped_honest": round(100 * (loc_total - loc_survive_honest) / loc_total, 2),
        },
        "oracle_stats": oracle_stats,
    }


def print_table(results: list[dict]) -> None:
    print(f"\n{EXCLUDED_REPO_NOTE}\n")
    print(f"{'repo':<12}{'edges':>8}{'rendered B':>12}{'jsonl B':>10}"
          f"{'confirmed':>10}{'contrad.':>10}{'excluded':>10}{'unremap':>9}")
    for r in results:
        c = r["counts"]
        print(f"{r['repo']:<12}{r['total_edges_raw_rows']:>8}{r['total_rendered_bytes']:>12}"
              f"{r['total_jsonl_bytes']:>10}{c['confirmed']:>10}{c['contradicted']:>10}"
              f"{c['excluded']:>10}{c['unremappable_caller']:>9}")

    print(f"\n{'repo':<12}{'naive ceiling':>16}{'honest ceiling':>16}{'gap (pts)':>12}")
    for r in results:
        n = r["naive_ceiling"]["pct_of_rendered"]
        h = r["honest_ceiling"]["pct_of_rendered"]
        gap = r["gap_naive_minus_honest"]["pct_points_of_rendered"]
        print(f"{r['repo']:<12}{n:>14.2f}%{h:>15.2f}%{gap:>11.2f}p")

    print(f"\n{'repo':<12}{'loc total':>10}{'loc conf.':>10}{'loc drop naive':>16}{'loc drop honest':>17}")
    for r in results:
        d = r["distinct_locations"]
        print(f"{r['repo']:<12}{d['total']:>10}{d['confirmed_by_oracle']:>10}"
              f"{d['dropped_naive']:>13} ({d['pct_dropped_naive']:.1f}%)"
              f"{d['dropped_honest']:>14} ({d['pct_dropped_honest']:.1f}%)")
    print()


def main() -> int:
    results = [analyze_repo(repo) for repo in REPOS]
    print_table(results)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_path = ROOT / "results" / f"ceiling-grep-filter-{stamp}.json"
    payload = {
        "question": "bytes removed if a perfect oracle filtered grep's output before an agent read it",
        "scope": {"cells": [CELL], "repos": REPOS, "excluded_repo": "FluentValidation",
                   "excluded_repo_reason": EXCLUDED_REPO_NOTE},
        "method": {
            "rendered_line_form": "{caller_file}:{caller_line}: {callee}\\n",
            "matching_rule_source": "grader/grade.py (method_key, normalize_caller, load_oracle, "
                                     "Cell.score match test) — imported and reused, not reimplemented",
            "cell_of_source": "verbatim copy of the cell_of() closure inside grade.py main() "
                               "(not importable as a standalone function)",
            "unit_of_classification": "distinct normalised (caller,callee) edge, cached and applied "
                                       "to every raw row sharing that edge",
            "unit_of_bytes": "raw row (one line an agent would actually read)",
        },
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
