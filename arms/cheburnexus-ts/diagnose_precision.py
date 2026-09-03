#!/usr/bin/env python3
"""Diagnostic (not part of the arm contract): classify cheburnexus-ts's primary-cell false
positives against class-validator's TS oracle, to answer one question the naive 0.586 precision
cannot answer on its own — the oracle is a RUNTIME key (see oracle/typescript/README.md,
manifest.json's `oracle_coverage: "executed-tests-only"`): it only records a call that actually
executed during the 806-test run. An arm edge whose CALLER function was never invoked by any test
has no oracle row to match against it, and grade.py has no scope mechanism to tell "never executed"
apart from "executed and wrong" — both currently score as a false positive.

This script does not change the grader and does not fence anything in code. It reads the same
oracle.jsonl / edges.jsonl / overrides.json / first_party the grader reads, reproduces grade.py's
own primary-cell classification byte-for-byte (imported from grade.py, not re-implemented), and then
inspects the SOURCE at each unmatched edge's caller/callee line to bucket it by hand-checkable
evidence:

  (a) caller absent from the oracle's raw Caller set entirely -> the key is SILENT (never observed
      this function execute at all) -> not a contradiction, a coverage gap in the key.
  (b) caller IS present as an oracle Caller somewhere (so it did execute) but this specific edge has
      no oracle row -> a genuine disagreement. Manually sub-split by reading the source.
  (c) near-miss: an oracle row exists whose caller and callee match ours on every field EXCEPT the
      embedded line number -> would falsify the line-number-equivalence assumption in run.py's
      header.

Usage:
    python arms/cheburnexus-ts/diagnose_precision.py <run_dir>
where <run_dir> is e.g. results/_ts-diag/class-validator/with-tests
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "grader"))
import grade  # noqa: E402


def main() -> int:
    run_dir = Path(sys.argv[1])
    oracle_path = run_dir / "_oracle" / "oracle.jsonl"
    overrides_path = run_dir / "_oracle" / "overrides.json"
    arm_dir = run_dir / "cheburnexus-ts"
    edges_path = arm_dir / "edges.jsonl"
    manifest = json.loads((arm_dir / "manifest.json").read_text(encoding="utf-8"))
    first_party = set(manifest["first_party"])

    cells, _, stats = grade.load_oracle(str(oracle_path), first_party)
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    overrides = {k: set(v) for k, v in overrides.items()}
    arm_edges, arm_dropped = grade.load_arm(str(edges_path))

    print(f"oracle rows: {stats}")
    print(f"arm edges loaded (post caller-normalize): {len(arm_edges)}, dropped as unattributable: {arm_dropped}")

    callee_cell = grade.build_callee_cell(cells)
    primary = cells["primary"]

    # Reproduce the grader's own cell split of the arm's edges: which of arm_edges land in 'primary'.
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

    print(f"arm_edges_in_primary_cell: {len(arm_primary)}  matched: {len(matched)}  unmatched: {len(unmatched)}")
    naive_precision = len(matched) / len(arm_primary) if arm_primary else None
    print(f"naive precision: {naive_precision}")

    # ── raw oracle caller/callee sets, RAW (all rows, all cells — a caller proven to have executed
    # even if its callee landed in an excluded cell is still proof the CALLER executed) ───────────
    raw_rows = []
    with open(oracle_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw_rows.append(json.loads(line))

    executed_callers_raw: set[str] = set()   # exact Caller string as the oracle wrote it (pre method_key)
    executed_callers_norm: set[str] = set()  # after method_key + normalize_caller, matching arm_edges' own caller space
    for row in raw_rows:
        executed_callers_raw.add(row["Caller"])
        mk = grade.method_key(row["Caller"])
        if mk is not None:
            nk, ok = grade.normalize_caller(mk)
            if ok:
                executed_callers_norm.add(nk)

    # For near-miss (bucket c): index oracle primary+all-cell rows by (caller-without-line,
    # callee-without-line) so we can find "same call, different line" candidates.
    def strip_line(key: str) -> tuple[str, str]:
        """key form: 'Type::Class.member:LINE[(get|set)]' or 'Type::name:LINE'. Split off the
        trailing ':LINE' (and any '(get)'/'(set)' suffix) so two keys that differ ONLY in the
        embedded line number compare equal."""
        suffix = ""
        for tag in ("(get)", "(set)"):
            if key.endswith(tag):
                key = key[: -len(tag)]
                suffix = tag
                break
        head, _, tail = key.rpartition(":")
        if tail.isdigit():
            return head, suffix
        return key, suffix  # no trailing :LINE at all — leave as-is

    all_oracle_rows_norm: list[tuple[str, str]] = []
    for row in raw_rows:
        mk_c = grade.method_key(row["Caller"])
        mk_e = grade.method_key(row["Callee"])
        if mk_c is None or mk_e is None:
            continue
        nc, ok = grade.normalize_caller(mk_c)
        if not ok:
            continue
        all_oracle_rows_norm.append((nc, mk_e))

    lineless_index: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for c, e in all_oracle_rows_norm:
        c2, csuf = strip_line(c)
        e2, esuf = strip_line(e)
        lineless_index.setdefault((c2, e2), []).append((c, e))

    bucket_a, bucket_b, bucket_c = [], [], []
    for edge in sorted(unmatched):
        caller, callee = edge
        if caller not in executed_callers_norm:
            bucket_a.append(edge)
            continue
        c2, _ = strip_line(caller)
        e2, _ = strip_line(callee)
        candidates = lineless_index.get((c2, e2))
        if candidates:
            bucket_c.append((edge, candidates))
            continue
        bucket_b.append(edge)

    print(f"\nbucket (a) caller never observed executing at all: {len(bucket_a)}")
    for e in bucket_a[:25]:
        print("   ", e)
    print(f"\nbucket (c) near-miss: same caller+callee modulo embedded line: {len(bucket_c)}")
    for e, cands in bucket_c[:25]:
        print("   arm:", e)
        print("   oracle candidates:", cands)
    print(f"\nbucket (b) caller executed, no line-fuzzy match either — genuine disagreement: {len(bucket_b)}")
    for e in bucket_b:
        print("   ", e)

    # ── fenced precision: only count arm primary-cell edges whose CALLER is in the executed set ──
    fenced_num = sum(1 for e in arm_primary if e in matched)
    fenced_den = sum(1 for e in arm_primary if e[0] in executed_callers_norm)
    fenced_precision = fenced_num / fenced_den if fenced_den else None
    print(f"\nfenced precision (denominator = arm primary-cell edges whose caller DID execute per "
          f"the key): {fenced_num}/{fenced_den} = {fenced_precision}")
    print(f"naive precision (denominator = all arm primary-cell edges): {len(matched)}/{len(arm_primary)} = {naive_precision}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
