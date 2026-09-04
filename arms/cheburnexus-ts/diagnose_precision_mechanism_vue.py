#!/usr/bin/env python3
"""Diagnose the FALSE POSITIVE set of cheburnexus-ts on vuejs/core packages/reactivity
(precision 0.8701, the lowest ever recorded on this bench) by CALL-SITE MECHANISM.

Reuses grade.py's own primary-cell classification (imported, not re-implemented) to build the
exact false-positive set: arm edges landing in the primary cell whose caller DID execute per the
runtime oracle (the "fenced" denominator result.json already reports, 1494 arm edges / 1300
matched / 194 false), then locates the CALL SITE the caller's body actually contains for that
callee's member name (or, when no name match exists, the structural fallback: parameter / local
binding / import), reusing the exact classifier from diagnose_bucket3_mechanism_zod.py (same
regexes, same priority order) since that script is the precedent for "bucket by call mechanism,
not by proxy".

Usage:
    python arms/cheburnexus-ts/diagnose_precision_mechanism_vue.py <run_dir> <checkout_root>
e.g.:
    python arms/cheburnexus-ts/diagnose_precision_mechanism_vue.py results/vue_check/core/with-tests D:\\DEV\\TsTest\\core
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "grader"))
sys.path.insert(0, str(HERE))
import grade  # noqa: E402

# Reuse the mechanism classifier verbatim from the zod recall-mechanism script.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "diagnose_bucket3_mechanism_zod", HERE / "diagnose_bucket3_mechanism_zod.py"
)
b3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b3)


def build_false_positive_set(run_dir: Path):
    oracle_path = run_dir / "_oracle" / "oracle.jsonl"
    overrides_path = run_dir / "_oracle" / "overrides.json"
    arm_dir = run_dir / "cheburnexus-ts"
    edges_path = arm_dir / "edges.jsonl"
    manifest = json.loads((arm_dir / "manifest.json").read_text(encoding="utf-8"))
    first_party = set(manifest["first_party"])

    cells, _, stats = grade.load_oracle(str(oracle_path), first_party)
    overrides = {k: set(v) for k, v in json.loads(overrides_path.read_text(encoding="utf-8")).items()}
    arm_edges, arm_dropped = grade.load_arm(str(edges_path))

    callee_cell = grade.build_callee_cell(cells)
    primary = cells["primary"]

    arm_primary = {e for e in arm_edges if grade.cell_of(e[1], callee_cell, overrides) == "primary"}

    matched = set()
    for edge in arm_primary:
        if edge in primary.oracle:
            matched.add(edge)
            continue
        caller, callee = edge
        for declared in sorted(overrides.get(callee, ())):
            if (caller, declared) in primary.oracle:
                matched.add(edge)
                break
    unmatched = arm_primary - matched

    # Fence: same denominator result.json's "primary" cell reports -- caller DID execute per the
    # runtime oracle (drop the 28-edge "unjudged" cell separately, that's a coverage gap, not a
    # false positive).
    raw_rows = []
    with open(oracle_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                raw_rows.append(json.loads(line))
    executed_callers_norm: set[str] = set()
    for row in raw_rows:
        mk = grade.method_key(row["Caller"])
        if mk is not None:
            nk, ok = grade.normalize_caller(mk)
            if ok:
                executed_callers_norm.add(nk)

    fenced_primary = {e for e in arm_primary if e[0] in executed_callers_norm}
    fenced_matched = fenced_primary & matched
    fenced_false = fenced_primary - matched

    print(f"oracle stats: {stats}")
    print(f"arm edges (post caller-normalize): {len(arm_edges)}, dropped unattributable: {arm_dropped}")
    print(f"arm_primary (all, unfenced): {len(arm_primary)}  matched: {len(arm_primary & matched)}")
    print(f"arm_primary (fenced, caller executed): {len(fenced_primary)}  matched: {len(fenced_matched)}"
          f"  FALSE: {len(fenced_false)}")
    print(f"fraction false of fenced primary: {len(fenced_false)}/{len(fenced_primary)} = "
          f"{len(fenced_false)/len(fenced_primary):.4f}")
    print(f"(cross-check vs result.json: 194/1494 = {194/1494:.4f})")

    return sorted(fenced_false)


def main() -> int:
    run_dir = Path(sys.argv[1])
    checkout_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if checkout_root is None:
        print("FATAL: checkout_root required")
        return 1

    false_edges = build_false_positive_set(run_dir)
    total = len(false_edges)
    print(f"\nFALSE POSITIVE SET: {total} edges\n")

    print("=" * 90)
    print("CALL-SITE MECHANISM CLASSIFICATION (reusing diagnose_bucket3_mechanism_zod.classify)")
    print("=" * 90)
    print(b3.PRIORITY_NOTE)

    counts: Counter = Counter()
    m8_reasons: Counter = Counter()
    examples: dict[str, list] = {m: [] for m in b3.MECH_ORDER}

    for caller, callee in false_edges:
        # here the FALSE-POSITIVE callee is the one the ARM claims; we look in the caller's body
        # for how that name is invoked (or reached structurally) to see the mechanism that misled
        # the resolver.
        result = b3.diagnose_edge(caller, callee, checkout_root)
        mech = result["mech"]
        counts[mech] += 1
        if mech == "M8":
            m8_reasons[result["reason"]] += 1
        if len(examples[mech]) < 10:
            examples[mech].append((caller, callee, result.get("line"), result.get("snippet"), result.get("reason")))

    check = sum(counts.values())
    assert check == total, f"mechanism counts do not sum to {total}: got {check}"

    print("\n" + "=" * 90)
    print(f"CALL-SITE MECHANISM TABLE -- {total} false-positive edges")
    print("=" * 90)
    for mech in b3.MECH_ORDER:
        n = counts.get(mech, 0)
        pct = 100 * n / total
        print(f"  {mech:4s}{n:6d}  ({pct:5.2f}%)  {b3.MECH_LABELS[mech]}")
    print(f"\nSUM: {check} (must equal {total}, i.e. 100%)")

    print("\n" + "=" * 90)
    print("EXAMPLES PER MECHANISM (up to 10 each)")
    print("=" * 90)
    for mech in b3.MECH_ORDER:
        print(f"\n--- {mech}: {b3.MECH_LABELS[mech]}  (n={counts.get(mech,0)}) ---")
        if not examples[mech]:
            print("  (none)")
            continue
        for caller, callee, ln, snippet, reason in examples[mech]:
            print(f"  ARM CALLER: {caller}")
            print(f"  ARM CALLEE (claimed, false): {callee}")
            if snippet is not None:
                print(f"  CALL SITE (line {ln}): {snippet}")
            elif reason is not None:
                print(f"  REASON: {reason}")
            print()

    print("DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
