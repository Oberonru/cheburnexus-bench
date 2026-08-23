#!/usr/bin/env python3
"""Grade one arm's call edges against the compiler-derived answer key.

The rules this file implements were fixed in the pre-registration BEFORE any measured run. They are
kept in one place, named, and printed with the result, so that a reader can see the boundary that
produced a number instead of inferring it. Changing a rule changes every published figure, so a
change belongs in the pre-registration's deviations section, not in a quiet edit here.

Usage:
    grade.py --oracle oracle.jsonl --arm arm.jsonl [--overrides overrides.json] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

# ── Identity ──────────────────────────────────────────────────────────────────
# Cecil writes "System.Void Ns.Type::Method(System.Int32)". Arms write "Ns.Type::Method". Both
# collapse to the same key: type + method name, no return type, no parameter types. EDGE_FORMAT.md
# explains what that costs and why it is still the right trade for the question being asked.

_CECIL_FULLNAME = re.compile(r"^(?:[\w.<>`\[\],&*+ ]+\s+)?(?P<type>[^\s]+)::(?P<method>[^(]+)")

# Roslyn's mangled names for code it moved out of the method a human wrote:
#   <Write>b__4_0   lambda        <Emit>d__7    async / iterator state machine
#   <>c__DisplayClass9_0          closure holder
_MANGLED_ENCLOSING = re.compile(r"<([^>]+)>[bdgf]__")

ENUMERATOR_PROTOCOL = frozenset({"GetEnumerator", "MoveNext", "get_Current", "Dispose"})
COMPARABLE_OPS = frozenset({"call", "callvirt", "newobj", "ldftn"})


def method_key(raw: str) -> str | None:
    """Canonical `Namespace.Type::Method`, or None when the string is not a method reference."""
    match = _CECIL_FULLNAME.match(raw.strip())
    if not match:
        return None
    type_name = match.group("type").strip()
    method = match.group("method").strip()
    # Cecil spells constructors `.ctor`; source-level tools usually spell them as the type name.
    if method in (".ctor", ".cctor"):
        method = ".ctor"
    return f"{type_name}::{method}"


def enclosing_user_method(key: str) -> tuple[str, bool]:
    """Remap a compiler-generated caller onto the user method it was lifted out of.

    Returns (key, remapped). A caller we cannot resolve confidently is reported, never silently
    attributed to a method that may not be the right one.
    """
    type_name, _, method = key.partition("::")

    hit = _MANGLED_ENCLOSING.search(method)
    if hit:
        outer_type = type_name.split("/")[0]
        return f"{outer_type}::{hit.group(1)}", True

    hit = _MANGLED_ENCLOSING.search(type_name)
    if hit:
        outer_type = type_name.split("/")[0]
        return f"{outer_type}::{hit.group(1)}", True

    # A generated type whose name reveals no enclosing method (`<>c`, `<>f__AnonymousType0`).
    if "<" in type_name or "<" in method:
        return key, False

    return key, False


@dataclass
class Cell:
    """One published number plus the material behind it."""

    name: str
    oracle: set[tuple[str, str]] = field(default_factory=set)

    def score(self, arm: set[tuple[str, str]], overrides: dict[str, set[str]]) -> dict:
        """Precision is computed on the arm edges that BELONG to this cell, never on all of them.

        An arm that correctly reports a property read or a call into the standard library has not
        made a mistake — that edge simply lives in a different cell. Dividing by the arm's whole
        output would punish every arm for being more complete than the cell it is judged in, and
        would punish the most complete arm hardest.
        """
        matched = set()
        for edge in arm:
            if edge in self.oracle:
                matched.add(edge)
                continue
            # Accept an override of the declared target: IL names the declaration, a source-level
            # tool may name an implementation. Reduction happens on the arm side only.
            caller, callee = edge
            for declared in overrides.get(callee, ()):
                if (caller, declared) in self.oracle:
                    matched.add((caller, declared))
                    break

        true_positive = len(matched)
        precision = true_positive / len(arm) if arm else 0.0
        recall = true_positive / len(self.oracle) if self.oracle else 0.0
        return {
            "cell": self.name,
            "oracle_edges": len(self.oracle),
            "arm_edges_in_cell": len(arm),
            "matched": true_positive,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
        }


def load_oracle(path: str, first_party: set[str] | None) -> tuple[dict[str, Cell], dict[str, set[str]], dict]:
    """Split the answer key into the primary comparable cell and the excluded cells."""
    cells = {
        "primary": Cell("primary — first-party ordinary calls and constructors"),
        "external": Cell("excluded — callee outside the corpus"),
        "accessor": Cell("excluded — property accessors"),
        "enumerator": Cell("excluded — foreach protocol"),
        "generated": Cell("excluded — compiler-generated callee"),
        "op_excluded": Cell("excluded — indirect call op (calli / ldvirtftn)"),
    }
    overrides: dict[str, set[str]] = defaultdict(set)
    stats = {"rows": 0, "unremappable_caller": 0, "no_source_anchor": 0}
    # Callers the answer key itself could not trace back to a human-written method. Handed to the
    # arm loader so both sides drop the same edges: the arm cannot see a CompilerGeneratedAttribute,
    # only a mangled name, so without this list the two sides disagree and the grader manufactures
    # false positives out of its own asymmetry.
    unremappable: set[str] = set()

    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            stats["rows"] += 1

            caller = method_key(row["Caller"])
            callee = method_key(row["Callee"])
            if caller is None or callee is None:
                continue

            if row.get("CallerCompilerGenerated"):
                caller, remapped = enclosing_user_method(caller)
                if not remapped:
                    stats["unremappable_caller"] += 1
                    unremappable.add(caller)
                    continue
            if row.get("NoDebugInfo"):
                stats["no_source_anchor"] += 1

            edge = (caller, callee)
            callee_method = callee.rpartition("::")[2]

            if row.get("Op") not in COMPARABLE_OPS:
                # Kept as its own cell rather than dropped, so that the callee stays classifiable.
                # A callee the answer key silently forgot would resurface as an arm's false
                # positive — the grader would invent junk that the arm never produced.
                cells["op_excluded"].oracle.add(edge)
            elif first_party is not None and row.get("CalleeAssembly") not in first_party:
                cells["external"].oracle.add(edge)
            elif callee_method.startswith(("get_", "set_")):
                cells["accessor"].oracle.add(edge)
            elif callee_method in ENUMERATOR_PROTOCOL:
                cells["enumerator"].oracle.add(edge)
            elif row.get("CalleeCompilerGenerated"):
                cells["generated"].oracle.add(edge)
            else:
                cells["primary"].oracle.add(edge)

    return cells, overrides, stats, unremappable


def load_arm(path: str, unremappable: set[str]) -> tuple[set[tuple[str, str]], int]:
    """Load an arm's edges under exactly the caller discipline the answer key uses.

    The two sides must drop the same things. An edge whose caller is compiler-generated and cannot
    be traced back to the method a human wrote counts for nobody — so it leaves the arm's output
    too. Applying the rule to one side only turns the grader itself into a source of error, which
    an identity run (grading the answer key against itself) exposes immediately.
    """
    edges: set[tuple[str, str]] = set()
    dropped = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            caller = method_key(row.get("caller") or row.get("Caller") or "")
            callee = method_key(row.get("callee") or row.get("Callee") or "")
            if caller is None or callee is None:
                continue
            original = caller
            mangled = "<" in caller
            caller, remapped = enclosing_user_method(caller)
            if original in unremappable or (mangled and not remapped):
                dropped += 1
                continue
            edges.add((caller, callee))
    return edges, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--first-party", nargs="*", default=None,
                        help="assembly names of the corpus; omit to treat every callee as in-corpus")
    parser.add_argument("--overrides", default=None,
                        help="JSON map {override_key: [declared_key, ...]} emitted by the oracle")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    first_party = set(args.first_party) if args.first_party else None
    cells, overrides, stats, unremappable = load_oracle(args.oracle, first_party)

    if args.overrides:
        with open(args.overrides, encoding="utf-8") as handle:
            raw = json.load(handle)
        overrides = {key: set(value) for key, value in raw.items()}

    arm, arm_dropped = load_arm(args.arm, unremappable)

    # Which cell does each callee belong to? Built from the answer key itself: every method the
    # compiler ever records a call to is classified once, and an arm's edge is judged in that same
    # cell. A callee the compiler never records anywhere cannot be defended as "a different cell" —
    # it is an edge to something the built code does not call, so it lands in the primary cell as
    # junk, which is exactly the quantity this benchmark exists to measure.
    callee_cell: dict[str, str] = {}
    for name, cell in cells.items():
        for _, callee in cell.oracle:
            callee_cell.setdefault(callee, name)

    def cell_of(callee: str) -> str:
        known = callee_cell.get(callee)
        if known is not None:
            return known
        method = callee.rpartition("::")[2]
        if method.startswith(("get_", "set_")):
            return "accessor"
        if method in ENUMERATOR_PROTOCOL:
            return "enumerator"
        return "primary"

    arm_by_cell: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for edge in arm:
        arm_by_cell[cell_of(edge[1])].add(edge)

    results = [cell.score(arm_by_cell[name], overrides) for name, cell in cells.items()]

    width = max(len(r["cell"]) for r in results)
    print(f"{'cell':<{width}}  {'oracle':>7} {'arm':>7} {'match':>7} {'prec':>7} {'recall':>7}")
    for r in results:
        print(f"{r['cell']:<{width}}  {r['oracle_edges']:>7} {r['arm_edges_in_cell']:>7} "
              f"{r['matched']:>7} {r['precision']:>7.3f} {r['recall']:>7.3f}")

    print(f"\noracle rows read      : {stats['rows']}")
    print(f"caller not remappable : {stats['unremappable_caller']}  (counted for nobody)")
    print(f"no source anchor      : {stats['no_source_anchor']}")
    print(f"arm rows dropped      : {arm_dropped}  (same caller rule as the answer key)")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"cells": results, "stats": {**stats, "arm_dropped": arm_dropped}}, handle, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
