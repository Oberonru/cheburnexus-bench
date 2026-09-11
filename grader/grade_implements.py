#!/usr/bin/env python3
"""Grade one arm's implements.jsonl against the compiler-derived answer key, for the TYPE-LEVEL
axis: "which types implement interface X / inherit base Y" (direct declarations only, no
transitive closure — both sides are direct edges, see oracle/csharp/Program.cs and
arms/cheburnexus/run.py).

This is a SIBLING module to grade.py, not an axis switch bolted onto it. grade.py's machinery
(method_key, the Cecil-signature regex, the caller-remap for lambdas/closures, generic-METHOD-
ARGUMENT stripping, the override map for virtual dispatch) is entirely about resolving CALL
EDGES between methods. None of it applies here: an (Type, Target, Kind) row has no caller to
remap, no method signature to parse, and no override map — a type either declares a base/interface
or it does not, there is nothing to disambiguate. Forcing this into grade.py's shape would mean
threading an unused "is this an edge or a type-relationship" flag through every function above;
a sibling file that reuses the CELL/SCORE SHAPE (see Cell.score in grade.py:227-254, the
precision/recall computation) keeps both files readable on their own terms.

Usage:
    grade_implements.py --oracle implements.jsonl --arm implements.jsonl [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

# IL/Cecil spells a generic type's own arity on the type name itself: `Box\`1`, or nested,
# `HedgingExecutionContext\`1/ExecutionInfo\`1`. The engine's classdeps sidecar strips this before
# the cheburnexus arm ever sees an edge (ClassDepsSidecarBuilder.Canon() — see the CAVEAT comment
# above arms/cheburnexus/run.py's `_build_type_facts`), on BOTH the Type and the Target side of an
# edge. An arm's key for that same relationship can therefore never carry a backtick-arity suffix,
# so any oracle row that does is structurally incomparable — not a miss, a different question the
# arm's key shape cannot even ask. See EXCLUDED_GENERIC below.
_ARITY_SUFFIX = re.compile(r"`\d+")


def is_generic_key(name: str) -> bool:
    """True if `name` (a Type or Target string) carries IL generic arity anywhere in its chain."""
    return bool(_ARITY_SUFFIX.search(name))


def edge_key(row: dict) -> tuple[str, str, str] | None:
    """(Type, Target, Kind) — the whole comparable identity of one declared relationship.

    Unlike grade.py's method_key, there is no parsing here: the oracle and the arm are both
    already required to write this contract's field names verbatim (oracle/csharp/Program.cs's
    ImplementsRow; arms/cheburnexus/run.py's _implements_rows_for_run). A row missing any of the
    three fields cannot be judged and is dropped, counted for nobody, exactly like grade.py drops
    an unparseable Caller/Callee.
    """
    type_name, target, kind = row.get("Type"), row.get("Target"), row.get("Kind")
    if not type_name or not target or not kind:
        return None
    return (type_name, target, kind)


CELL_PRECEDENCE = ("generic", "external", "primary")


@dataclass
class Cell:
    """One published number plus the material behind it. Same shape as grade.py's Cell so a
    result.json from this axis reads the same way as the call-edge axis's does."""

    name: str
    oracle: set[tuple[str, str, str]] = field(default_factory=set)

    def score(self, arm: set[tuple[str, str, str]]) -> dict:
        matched = arm & self.oracle
        true_positive = len(matched)
        precision = true_positive / len(arm) if arm else None
        recall = true_positive / len(self.oracle) if self.oracle else None
        return {
            "cell": self.name,
            "oracle_edges": len(self.oracle),
            "arm_edges_in_cell": len(arm),
            "matched": true_positive,
            "precision": None if precision is None else round(precision, 4),
            "recall": None if recall is None else round(recall, 4),
        }


def classify_oracle_row(row: dict) -> str:
    """Which cell an ORACLE row belongs to, from the row's own fields — no lookup needed, unlike
    grade.py's callee_cell (there, a callee's cell depends on OTHER rows; here, a (Type, Target,
    Kind) row is fully self-describing)."""
    if is_generic_key(row.get("Type", "")) or is_generic_key(row.get("Target", "")):
        return "generic"
    if row.get("TargetExternal"):
        return "external"
    return "primary"


def load_oracle(path: str) -> tuple[dict[str, Cell], dict[str, str], dict]:
    cells = {
        "primary": Cell("primary — first-party direct inherits/implements"),
        "external": Cell("excluded — target outside the corpus (BCL/third-party)"),
        "generic": Cell("excluded — generic target/type: engine's sidecar strips arity "
                         "(ClassDepsSidecarBuilder.Canon), oracle's Cecil key keeps it — the two "
                         "sides' keys cannot agree by construction"),
    }
    # target -> cell, built from the oracle so an ARM row naming a target the oracle never
    # classified anywhere still lands somewhere principled (mirrors grade.py's callee_cell /
    # build_callee_cell / cell_of — see that module's comments for why "first classification
    # wins, by a fixed precedence" beats "last write wins" for determinism).
    target_cell: dict[str, str] = {}
    stats = {"rows": 0, "unparsed_row": 0}

    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            stats["rows"] += 1
            key = edge_key(row)
            if key is None:
                stats["unparsed_row"] += 1
                continue
            cell_name = classify_oracle_row(row)
            cells[cell_name].oracle.add(key)
            target = row["Target"]
            existing = target_cell.get(target)
            if existing is None or CELL_PRECEDENCE.index(cell_name) < CELL_PRECEDENCE.index(existing):
                target_cell[target] = cell_name

    return cells, target_cell, stats


def cell_of_arm_row(row: dict, target_cell: dict[str, str]) -> str:
    """Which cell an ARM edge is judged in. The oracle's own classification of that TARGET wins
    when known (an arm can never itself flag `generic`, since its own key shape has the arity
    already stripped — the oracle is the only side that can see the distinction). Falling back to
    the arm's own TargetExternal/generic-in-key checks when the oracle never mentions this target
    at all — a target the answer key has no opinion on is not proof it's wrong, but it is not
    proof it's right either, so it is judged by the same rule an oracle row would be."""
    target = row.get("Target", "")
    known = target_cell.get(target)
    if known is not None:
        return known
    if is_generic_key(row.get("Type", "")) or is_generic_key(target):
        return "generic"
    if row.get("TargetExternal"):
        return "external"
    return "primary"


def load_arm(path: str) -> tuple[set[tuple[str, str, str]], list[dict], int]:
    edges: set[tuple[str, str, str]] = set()
    rows: list[dict] = []
    dropped = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = edge_key(row)
            if key is None:
                dropped += 1
                continue
            edges.add(key)
            rows.append(row)
    return edges, rows, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    cells, target_cell, stats = load_oracle(args.oracle)
    arm_edges, arm_rows, arm_dropped = load_arm(args.arm)

    arm_by_cell: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in arm_rows:
        key = edge_key(row)
        arm_by_cell[cell_of_arm_row(row, target_cell)].add(key)

    results = [cell.score(arm_by_cell[name]) for name, cell in cells.items()]

    width = max(len(r["cell"]) for r in results)
    print(f"{'cell':<{width}}  {'oracle':>7} {'arm':>7} {'match':>7} {'prec':>7} {'recall':>7}")
    for r in results:
        fmt = lambda v: "    n/a" if v is None else f"{v:7.3f}"
        print(f"{r['cell']:<{width}}  {r['oracle_edges']:>7} {r['arm_edges_in_cell']:>7} "
              f"{r['matched']:>7} {fmt(r['precision'])} {fmt(r['recall'])}")

    print(f"\noracle rows read     : {stats['rows']}")
    print(f"oracle rows unparsed : {stats['unparsed_row']}  (missing Type/Target/Kind — dropped, "
          f"counted for nobody)")
    print(f"arm rows dropped     : {arm_dropped}  (same rule)")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"cells": results, "stats": {**stats, "arm_dropped": arm_dropped}}, handle, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
