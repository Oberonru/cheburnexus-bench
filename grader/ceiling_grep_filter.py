#!/usr/bin/env python3
"""Junk-per-answer: how many of an arm's BYTES are junk, measured the same way for two arms.

QUESTION: for the same repo and the same cell, how many BYTES of what an agent would actually
read are junk (contradicted / excluded-cell / unremappable-caller / malformed), and how does that
fraction compare between `grep` and `cheburnexus`? This is a COMPARATIVE measurement across two
arms, not a ceiling on one arm alone — it exists to show whether the engine's answer is smaller
and cleaner than grep's on the same ground, not just what an ideal filter could someday remove
from grep's output.

Both arms are rendered into the SAME canonical line form before anything is counted:
`{caller_file}:{caller_line}: {callee}\\n`. Same-form rendering is the fair choice — it isolates
what actually differs between the two arms (how many edges they emit, and how many times each one
repeats) from cosmetic formatting differences an agent would never actually see two arms present
identically anyway. Without a shared render, a byte comparison would just be measuring whose
line format happens to be more verbose, which answers a font question, not a junk question.

This script does NOT reimplement the matching rule. It imports grader/grade.py and reuses its
identity function (`method_key` + `normalize_caller`), its oracle-cell split (`load_oracle`), its
callee->cell classifier (`build_callee_cell` + `cell_of`), and its match test (the body of
`Cell.score`'s inner loop, lifted into `edge_confirmed`). `cell_of` used to live inline inside
grade.py's `main()` and was copied here byte-for-byte — until a grader fix (G3, 2026-08-25) rewrote
it and the copy silently drifted out of sync. It is now a module-level function in grade.py that
this script imports, so the two can no longer disagree on what counts as which cell.

Scope: `without-tests` cells only, `serilog` and `Polly` only. FluentValidation is EXCLUDED — the
contamination defect recorded 2026-08-24 (identical files scanned in both cells) was FIXED by
commits f6dcbd8/44ae8ab, but re-inclusion here is pending verification on a fresh full-matrix run.
We have not re-run the matrix, so we cannot confirm the fix actually produced clean, comparable
rows for this repo — leaving it excluded is the honest default until that verification exists.

The published grader operates on an arm's edges after they collapse into a SET of distinct
(caller, callee) pairs (see grade.load_arm). This script does not use that set directly: an
agent reads raw emitted LINES, and an arm frequently repeats one (caller, callee) pair across many
lines (grep on Polly: 8234 raw rows collapse to 5447 distinct pairs). So classification (confirmed
/ contradicted / excluded / unremappable) is computed once per distinct normalised edge, using the
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
ARMS = ["grep", "cheburnexus"]
EXCLUDED_REPO_NOTE = (
    "FluentValidation is excluded from this measurement: a defect recorded 2026-08-24 meant its "
    "with-tests and without-tests cells scanned identical files including test code, so its rows "
    "were contaminated and not comparable to serilog/Polly here. That defect was FIXED by commits "
    "f6dcbd8/44ae8ab, but re-inclusion is pending verification on a fresh full-matrix run — we have "
    "not re-run the matrix, so FluentValidation stays excluded until there is fresh data to check."
)


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
    """The realistic agent-visible form of one edge, SHARED by both arms: `caller_file:caller_line: <callee>`.

    Both arms emit rows through the same shared writer (arms/_lib/armkit.py's `Edge.to_row`), so
    the field names line up (`caller`, `callee`, `caller_file`, `caller_line`) without translation.
    """
    return f"{row.get('caller_file')}:{row.get('caller_line')}: {row.get('callee', '')}\n"


def analyze_arm(repo_dir_name: str, arm: str, results_root: Path = RESULTS_ROOT) -> dict:
    """Classify and byte-count one arm's raw rows for one repo, in the `without-tests` cell.

    `arm` is one of ARMS ("grep" or "cheburnexus"). The oracle is shared between both arms for a
    given repo — only the arm's own edges.jsonl/manifest.json differ.
    """
    base = results_root / repo_dir_name / CELL
    edges_path = base / arm / "edges.jsonl"
    oracle_path = base / "_oracle" / "oracle.jsonl"
    overrides_path = base / "_oracle" / "overrides.json"
    manifest_path = base / arm / "manifest.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_party = set(manifest["first_party"])

    cells, _oracle_overrides_unused, oracle_stats = grade.load_oracle(str(oracle_path), first_party)
    overrides = load_overrides(overrides_path)
    callee_cell = grade.build_callee_cell(cells)
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
                    cell_name = grade.cell_of(callee_key, callee_cell, overrides)
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

    # ── Distinct edges (from the cache, so only edges that reached full classification count) ──
    distinct_edge_counts: dict[str, int] = defaultdict(int)
    for label in edge_label_cache.values():
        distinct_edge_counts[label] += 1

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
        "arm": arm,
        "cell": CELL,
        "first_party": sorted(first_party),
        "total_edges_raw_rows": total_edges,
        "total_jsonl_bytes": total_jsonl_bytes,
        "total_rendered_bytes": total_rendered_bytes,
        "counts": counts,
        "byte_sums": byte_sums,
        "distinct_edge_counts": dict(distinct_edge_counts),
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


def comparative_summary(repo_dir_name: str, arm_results: dict[str, dict]) -> dict:
    """The headline comparison the article wants: junk-byte-fraction and total bytes, arm vs arm,
    same repo, same cell, same rendered line form."""
    per_arm = {}
    for arm, entry in arm_results.items():
        total = entry["total_rendered_bytes"]
        b = entry["byte_sums"]
        junk_bytes = b["contradicted"] + b["excluded"] + b["unremappable_caller"] + b["malformed"]
        confirmed_bytes = b["confirmed"]
        confirmed_distinct = entry["distinct_edge_counts"].get("confirmed", 0)
        per_arm[arm] = {
            "total_rendered_bytes": total,
            "junk_byte_fraction": round(junk_bytes / total, 4) if total else None,
            "confirmed_byte_fraction": round(confirmed_bytes / total, 4) if total else None,
            "bytes_per_confirmed_distinct_edge": (
                round(confirmed_bytes / confirmed_distinct, 2) if confirmed_distinct else None
            ),
        }
    return {"repo": repo_dir_name, "cell": CELL, "arms": per_arm}


def print_table(results: list[dict], comparisons: list[dict]) -> None:
    print(f"\n{EXCLUDED_REPO_NOTE}\n")
    print(f"{'repo':<12}{'arm':<12}{'edges':>8}{'rendered B':>12}{'jsonl B':>10}"
          f"{'confirmed':>10}{'contrad.':>10}{'excluded':>10}{'unremap':>9}")
    for r in results:
        c = r["counts"]
        print(f"{r['repo']:<12}{r['arm']:<12}{r['total_edges_raw_rows']:>8}{r['total_rendered_bytes']:>12}"
              f"{r['total_jsonl_bytes']:>10}{c['confirmed']:>10}{c['contradicted']:>10}"
              f"{c['excluded']:>10}{c['unremappable_caller']:>9}")

    print(f"\n{'repo':<12}{'arm':<12}{'naive ceiling':>16}{'honest ceiling':>16}{'gap (pts)':>12}")
    for r in results:
        n = r["naive_ceiling"]["pct_of_rendered"]
        h = r["honest_ceiling"]["pct_of_rendered"]
        gap = r["gap_naive_minus_honest"]["pct_points_of_rendered"]
        print(f"{r['repo']:<12}{r['arm']:<12}{n:>14.2f}%{h:>15.2f}%{gap:>11.2f}p")

    print(f"\n{'repo':<12}{'arm':<12}{'loc total':>10}{'loc conf.':>10}{'loc drop naive':>16}{'loc drop honest':>17}")
    for r in results:
        d = r["distinct_locations"]
        print(f"{r['repo']:<12}{r['arm']:<12}{d['total']:>10}{d['confirmed_by_oracle']:>10}"
              f"{d['dropped_naive']:>13} ({d['pct_dropped_naive']:.1f}%)"
              f"{d['dropped_honest']:>14} ({d['pct_dropped_honest']:.1f}%)")

    print(f"\n{'repo':<12}{'arm':<12}{'total bytes':>12}{'junk frac':>10}{'confirmed frac':>15}{'B/confirmed edge':>17}")
    for c in comparisons:
        for arm, s in c["arms"].items():
            jf = f"{s['junk_byte_fraction']:.4f}" if s["junk_byte_fraction"] is not None else "n/a"
            cf = f"{s['confirmed_byte_fraction']:.4f}" if s["confirmed_byte_fraction"] is not None else "n/a"
            bpe = f"{s['bytes_per_confirmed_distinct_edge']:.2f}" if s["bytes_per_confirmed_distinct_edge"] is not None else "n/a"
            print(f"{c['repo']:<12}{arm:<12}{s['total_rendered_bytes']:>12}{jf:>10}{cf:>15}{bpe:>17}")
    print()


def main() -> int:
    results: list[dict] = []
    comparisons: list[dict] = []
    for repo in REPOS:
        arm_results = {arm: analyze_arm(repo, arm) for arm in ARMS}
        results.extend(arm_results.values())
        comparisons.append(comparative_summary(repo, arm_results))

    print_table(results, comparisons)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_path = ROOT / "results" / f"junk-per-answer-{stamp}.json"
    payload = {
        "question": "for the same repo/cell, how many bytes of grep's vs cheburnexus's rendered "
                     "output are junk (contradicted/excluded/unremappable/malformed), and how does "
                     "the junk-byte-fraction compare between the two arms",
        "scope": {"cells": [CELL], "repos": REPOS, "arms": ARMS, "excluded_repo": "FluentValidation",
                   "excluded_repo_reason": EXCLUDED_REPO_NOTE},
        "method": {
            "rendered_line_form": "{caller_file}:{caller_line}: {callee}\\n",
            "same_form_rationale": "both arms are rendered into this one canonical form before any "
                                    "byte is counted, so the comparison isolates edge-count and "
                                    "repetition between arms from cosmetic formatting differences",
            "matching_rule_source": "grader/grade.py (method_key, normalize_caller, load_oracle, "
                                     "build_callee_cell, cell_of, Cell.score match test) — imported "
                                     "and reused, not reimplemented",
            "cell_of_source": "grade.cell_of — imported module-level function (shared with the "
                               "published grader, so the two cannot drift)",
            "unit_of_classification": "distinct normalised (caller,callee) edge, cached and applied "
                                       "to every raw row sharing that edge",
            "unit_of_bytes": "raw row (one line an agent would actually read)",
        },
        "results": results,
        "comparisons": comparisons,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
