#!/usr/bin/env python3
"""Mirror of diagnose_precision.py on the RECALL side: bucket the oracle primary-cell edges the
arm did NOT produce, by hand-checkable evidence, so the dominant mechanism is measured rather than
guessed. Reuses grade.py's own loaders/classification (never re-implements them).

Usage: python arms/cheburnexus-ts/diagnose_recall.py <run_dir>
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "grader"))
import grade  # noqa: E402


def strip_line(key: str) -> str:
    for tag in ("(get)", "(set)"):
        if key.endswith(tag):
            key = key[: -len(tag)]
            break
    head, _, tail = key.rpartition(":")
    return head if tail.isdigit() else key


def member_of(key: str) -> str:
    """'file.ts:Class.member:12' -> 'Class.member'"""
    return strip_line(key).rpartition(":")[2] or strip_line(key)


def file_of(key: str) -> str:
    return key.split("::", 1)[0]


def shape(key: str) -> str:
    m = member_of(key)
    if key.endswith("(get)") or key.endswith("(set)"):
        return "accessor"
    if "anonL" in m or "<anon" in m:
        return "anon-arrow/callback"
    last = m.rpartition(".")[2]
    if last in ("constructor", "ctor", ".ctor"):
        return "constructor"
    if last in ("describe", "it", "test", "beforeEach", "afterEach", "beforeAll", "afterAll", "expect"):
        return "jest-frame"
    if "." not in m:
        return "module-level/function"
    return "named-method"


def main() -> int:
    run_dir = Path(sys.argv[1])
    oracle_path = run_dir / "_oracle" / "oracle.jsonl"
    overrides_path = run_dir / "_oracle" / "overrides.json"
    arm_dir = run_dir / "cheburnexus-ts"
    edges_path = arm_dir / "edges.jsonl"
    manifest = json.loads((arm_dir / "manifest.json").read_text(encoding="utf-8"))
    first_party = set(manifest["first_party"])

    cells, _, stats = grade.load_oracle(str(oracle_path), first_party)
    overrides = {k: set(v) for k, v in json.loads(overrides_path.read_text(encoding="utf-8")).items()}
    arm_edges, arm_dropped = grade.load_arm(str(edges_path))

    primary = cells["primary"].oracle
    matched = set()
    for edge in primary:
        caller, callee = edge
        if edge in arm_edges:
            matched.add(edge)
            continue
        for declared in sorted(overrides.get(callee, ())):
            if (caller, declared) in arm_edges:
                matched.add(edge)
                break
    missing = primary - matched
    print(f"oracle rows: {stats}")
    print(f"arm edges: {len(arm_edges)} (dropped {arm_dropped})")
    print(f"oracle primary: {len(primary)}  matched: {len(matched)}  MISSING: {len(missing)}")
    print(f"recall: {len(matched)/len(primary):.4f}\n")

    arm_callers = {c for c, _ in arm_edges}
    arm_callers_lineless = {strip_line(c) for c in arm_callers}
    arm_callees = {e for _, e in arm_edges}
    arm_lineless = {(strip_line(c), strip_line(e)) for c, e in arm_edges}
    arm_files = {file_of(c) for c in arm_callers} | {file_of(e) for e in arm_callees}

    b = {k: [] for k in ("c_nearmiss_line", "a_caller_absent", "a2_caller_line_shifted",
                         "b_caller_known_callee_missing")}
    for edge in sorted(missing):
        caller, callee = edge
        if (strip_line(caller), strip_line(callee)) in arm_lineless:
            b["c_nearmiss_line"].append(edge)
        elif caller in arm_callers:
            b["b_caller_known_callee_missing"].append(edge)
        elif strip_line(caller) in arm_callers_lineless:
            b["a2_caller_line_shifted"].append(edge)
        else:
            b["a_caller_absent"].append(edge)

    tot = len(missing)
    for k, v in b.items():
        print(f"{k:32s} {len(v):5d}  {100*len(v)/tot:5.1f}%")

    print("\n-- caller SHAPE of each bucket --")
    for k, v in b.items():
        if not v:
            continue
        print(f"\n[{k}]  n={len(v)}")
        for s, n in Counter(shape(c) for c, _ in v).most_common():
            print(f"    caller {s:24s} {n:5d}  {100*n/len(v):5.1f}%")
        for s, n in Counter(shape(e) for _, e in v).most_common():
            print(f"    callee {s:24s} {n:5d}  {100*n/len(v):5.1f}%")

    print("\n-- a_caller_absent: is the caller's FILE known to the arm at all? --")
    va = b["a_caller_absent"]
    if va:
        seen = Counter("file-known" if file_of(c) in arm_files else "file-UNKNOWN" for c, _ in va)
        for s, n in seen.most_common():
            print(f"    {s:16s} {n:5d}  {100*n/len(va):5.1f}%")
        print("    top absent callers:")
        for c, n in Counter(c for c, _ in va).most_common(20):
            print(f"      {n:4d}  {c}")

    print("\n-- samples --")
    for k, v in b.items():
        print(f"\n[{k}]")
        for e in v[:10]:
            print("   ", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
