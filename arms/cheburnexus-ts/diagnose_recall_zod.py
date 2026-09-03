#!/usr/bin/env python3
"""Second-pass recall breakdown for zod, built on top of diagnose_recall.py's buckets.

diagnose_recall.py already splits the 20107 missing zod edges into 4 mechanical buckets
(near-miss line / caller absent / caller line-shifted / caller known, callee missing).
This script goes one level deeper to answer the exact question asked for the zod recall
write-up: 100% of the misses, in 5 mutually exclusive categories --

  1. callee-is-anon-closure-with-NO-arm-node-at-all  (structural: arm never emits ANY node,
     as caller or callee, for that closure)
  2. caller absent from arm entirely, split by caller SHAPE (anon-arrow / named-function /
     class-method)
  3. caller present AND named callee present as arm nodes, but no edge between them --
     the genuine resolve gap. 10 concrete examples pulled with source text.
  4. known oracle-labelling defect: a NAMED arrow assigned to a class field/var (e.g.
     `Mocker.pick = (...) => ...`) that the oracle mislabels as `Class.anonLN` -- detected by
     reading the source line and checking it is the arrow's OWN declaration line, assigned to
     an identifier, not a bare callback passed as an argument.
  5. near-miss by line number (same call, oracle/arm line differs by 1-2) -- reuses
     diagnose_recall.py's own near-miss bucket, which already requires an EXACT non-line match.

Anything that cannot be classified by one of the above (parser could not read the source
line, e.g.) is counted separately as "unclassified" and never silently folded into another
bucket.

Usage: python arms/cheburnexus-ts/diagnose_recall_zod.py <run_dir> <checkout_root>
e.g.:  python arms/cheburnexus-ts/diagnose_recall_zod.py results/zod-anon-fix-2026-09-03/zod/with-tests D:\\DEV\\TsTest\\zod
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


def file_of(key: str) -> str:
    return key.split("::", 1)[0]


def line_of(key: str) -> int | None:
    k = key
    for tag in ("(get)", "(set)"):
        if k.endswith(tag):
            k = k[: -len(tag)]
            break
    head, _, tail = k.rpartition(":")
    return int(tail) if tail.isdigit() else None


def member_of(key: str) -> str:
    return strip_line(key).rpartition(":")[2] or strip_line(key)


def is_anon(key: str) -> bool:
    m = member_of(key)
    return "anonL" in m


NAMED_FIELD_ARROW_RE = re.compile(
    r"^\s*(?:public|private|protected|readonly|static|export|const|let|var|declare|override|async)?\s*"
    r"(?:public|private|protected|readonly|static|export|const|let|var|declare|override|async)?\s*"
    r"([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?\(.*?(?:=>|\)\s*:?.*\{)"
)


_source_cache: dict[str, list[str]] = {}


def read_line(checkout_root: Path, key: str) -> str | None:
    fpath = file_of(key)
    ln = line_of(key)
    if ln is None:
        return None
    if fpath not in _source_cache:
        p = checkout_root / fpath
        try:
            _source_cache[fpath] = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            _source_cache[fpath] = []
    lines = _source_cache[fpath]
    if 1 <= ln <= len(lines):
        return lines[ln - 1]
    return None


def looks_like_named_arrow_field(src_line: str | None) -> tuple[bool, str]:
    """Is this the declaration line of a NAMED field/var initialized to an arrow (the
    known oracle defect: the anon label should have carried the field's real name)?"""
    if src_line is None:
        return False, "?"
    s = src_line.strip()
    m = NAMED_FIELD_ARROW_RE.match(s)
    if m:
        return True, m.group(1)
    return False, ""


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
    checkout_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
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

    total_misses = len(missing)
    print(f"oracle rows: {stats}")
    print(f"arm edges: {len(arm_edges)} (dropped {arm_dropped})")
    print(f"oracle primary: {len(primary)}  matched: {len(matched)}  MISSING (denominator for everything below): {total_misses}")
    print(f"recall on primary: {len(matched)/len(primary):.4f}\n")

    arm_callers = {c for c, _ in arm_edges}
    arm_callees = {e for _, e in arm_edges}
    arm_nodes = arm_callers | arm_callees               # anything the arm emitted an edge to/from
    arm_nodes_lineless = {strip_line(n) for n in arm_nodes}
    arm_callers_lineless = {strip_line(c) for c in arm_callers}
    arm_lineless = {(strip_line(c), strip_line(e)) for c, e in arm_edges}

    # ---- ordered, mutually exclusive classification -----------------------------------
    cat_nearmiss = []
    cat_named_arrow_field_defect = []
    cat_anon_callee_no_node = []
    cat_caller_absent_anon = []
    cat_caller_absent_named_fn = []
    cat_caller_absent_method = []
    cat_true_resolve_gap = []
    cat_named_callee_absent = []
    cat_unclassified = []

    defect_names: Counter[str] = Counter()

    for edge in sorted(missing):
        caller, callee = edge

        # 5) near-miss by line: exact match once line is stripped from BOTH ends.
        if (strip_line(caller), strip_line(callee)) in arm_lineless:
            cat_nearmiss.append(edge)
            continue

        # 4) known oracle defect: callee is an anonL label whose declaration line is
        #    actually a NAMED field/var arrow assignment (e.g. `Mocker.pick = (...) => ...`).
        if is_anon(callee):
            src = read_line(checkout_root, callee) if checkout_root else None
            hit, name = looks_like_named_arrow_field(src)
            if hit:
                cat_named_arrow_field_defect.append((edge, name, src.strip() if src else ""))
                continue

        # 1) callee is an anon closure the arm has NO node for at all (never appears as
        #    caller or callee anywhere in the arm's own edge set, line-insensitive).
        if is_anon(callee) and strip_line(callee) not in arm_nodes_lineless:
            cat_anon_callee_no_node.append(edge)
            continue

        # 2) caller missing from the arm entirely (line-insensitive), split by caller shape.
        if strip_line(caller) not in arm_nodes_lineless and caller not in arm_callers:
            s = shape(caller)
            if s == "anon-arrow/callback":
                cat_caller_absent_anon.append(edge)
            elif s == "named-method":
                cat_caller_absent_method.append(edge)
            else:
                cat_caller_absent_named_fn.append(edge)
            continue

        # 3) caller known AND callee known (named) as arm nodes, but no edge -- true gap.
        caller_known = caller in arm_callers or strip_line(caller) in arm_callers_lineless
        if caller_known and strip_line(callee) in arm_nodes_lineless:
            cat_true_resolve_gap.append(edge)
            continue

        # 3b) caller known, callee is a NAMED (non-anon) member the arm never emits a node
        #     for at all -- typically a property-accessor read (e.g. `this.boolean` reading
        #     a `get boolean()`) that the arm's call-detector doesn't treat as a call site.
        #     Distinct from (3): here the callee itself is missing, not just the edge.
        if caller_known and not is_anon(callee):
            cat_named_callee_absent.append(edge)
            continue

        cat_unclassified.append(edge)

    buckets = [
        ("5_near_miss_line", cat_nearmiss),
        ("4_named_arrow_field_mislabeled_anonL (oracle artifact)", cat_named_arrow_field_defect and [e for e, _, _ in cat_named_arrow_field_defect]),
        ("1_anon_callee_no_arm_node_at_all", cat_anon_callee_no_node),
        ("2a_caller_absent_anon_arrow", cat_caller_absent_anon),
        ("2b_caller_absent_named_function", cat_caller_absent_named_fn),
        ("2c_caller_absent_class_method", cat_caller_absent_method),
        ("3_true_resolve_gap_both_known", cat_true_resolve_gap),
        ("3b_named_callee_absent_from_arm", cat_named_callee_absent),
        ("0_unclassified", cat_unclassified),
    ]

    print(f"{'category':55s} {'n':>7s} {'% of misses':>12s}")
    check_sum = 0
    for name, lst in buckets:
        n = len(lst)
        check_sum += n
        print(f"{name:55s} {n:7d} {100*n/total_misses:11.2f}%")
    print(f"{'SUM (must equal denominator above)':55s} {check_sum:7d}")
    assert check_sum == total_misses, f"partition is not exhaustive: {check_sum} != {total_misses}"

    print("\n-- category 4 examples (named field/var arrow mislabeled anonLN) --")
    for edge, name, src in cat_named_arrow_field_defect[:15]:
        print(f"   {edge}  ->  real name guess: {name!r}  src: {src}")

    print(f"\n-- category 3 (true resolve gap): {len(cat_true_resolve_gap)} total, breakdown by path --")

    def is_classic(edge):
        caller, callee = edge
        return "packages/zod/src/v4/classic/" in caller or "packages/zod/src/v4/classic/" in callee
    classic_n = sum(1 for e in cat_true_resolve_gap if is_classic(e))
    other_n = len(cat_true_resolve_gap) - classic_n
    print(f"   src/v4/classic (either end): {classic_n}  ({100*classic_n/max(1,len(cat_true_resolve_gap)):.1f}%)")
    print(f"   everything else:             {other_n}  ({100*other_n/max(1,len(cat_true_resolve_gap)):.1f}%)")

    print("\n-- category 3: 10 concrete examples with source --")
    shown = 0
    for edge in cat_true_resolve_gap:
        if shown >= 10:
            break
        caller, callee = edge
        csrc = read_line(checkout_root, caller) if checkout_root else None
        esrc = read_line(checkout_root, callee) if checkout_root else None
        print(f"  [{shown+1}] caller {caller}")
        print(f"        src: {csrc.strip() if csrc else '(unreadable)'}")
        print(f"      callee {callee}")
        print(f"        src: {esrc.strip() if esrc else '(unreadable)'}")
        shown += 1

    print("\n-- raw sample edges per bucket (first 5 each) --")
    for name, lst in buckets:
        print(f"\n[{name}]")
        for e in lst[:5]:
            print("   ", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
