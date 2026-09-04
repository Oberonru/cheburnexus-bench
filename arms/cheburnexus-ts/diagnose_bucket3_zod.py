#!/usr/bin/env python3
"""Decompose bucket 3 (`3_true_resolve_gap_both_known`) and bucket 3b
(`3b_named_callee_absent_from_arm`) from diagnose_recall_zod.py with no remainder.

Loading/translation/classification helpers copied VERBATIM from diagnose_recall_zod.py so the
bucket recomputation here matches that script's numbers exactly (same join keys, same ordering
of classification rules).

Usage: python arms/cheburnexus-ts/diagnose_bucket3_zod.py <run_dir> <checkout_root>
e.g.:  python arms/cheburnexus-ts/diagnose_bucket3_zod.py results/session-qualified-zod/zod/with-tests D:\\DEV\\TsTest\\zod
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
import run as ts_run  # noqa: E402


# ---------------------------------------------------------------------------------------
# Verbatim helpers from diagnose_recall_zod.py
# ---------------------------------------------------------------------------------------

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
    if src_line is None:
        return False, "?"
    s = src_line.strip()
    m = NAMED_FIELD_ARROW_RE.match(s)
    if m:
        return True, m.group(1)
    return False, ""


def load_arm_all_nodes(work_dir: Path, checkout_root: Path) -> tuple[set[str], set[str], dict]:
    arch_path = work_dir / "architecture.json"
    methods_path = work_dir / "architecture.methods.json"
    arch = json.loads(arch_path.read_text(encoding="utf-8"))
    methods_env = json.loads(methods_path.read_text(encoding="utf-8"))
    methods_data = methods_env.get("Data") or {}

    types = ts_run.TypeIndex(arch)
    translator = ts_run.Translator(methods_data, types, checkout_root)
    full: set[str] = set()
    for raw_key in methods_data:
        translated = translator.translate(raw_key)
        if translated is not None:
            full.add(translated)
    lineless = {strip_line(k) for k in full}
    drop_stats = dict(translator.drops)
    drop_stats["_translated_ok"] = translator.ok
    drop_stats["_raw_keys_total"] = len(methods_data)
    return full, lineless, drop_stats


def classify_anon_syntax(src_line: str | None) -> str:
    if src_line is None:
        return "anon-syntax-unreadable"
    s = src_line.strip()
    if re.search(r"\bfunction\b\s*\*?\s*[\w$]*\s*\(", s):
        return "anon-function-expression"
    if "=>" in s:
        return "anon-arrow"
    return "anon-syntax-unclear"


def shape(key: str, checkout_root: Path | None = None) -> str:
    m = member_of(key)
    if key.endswith("(get)") or key.endswith("(set)"):
        return "accessor"
    if "anonL" in m or "<anon" in m:
        if checkout_root is not None:
            return classify_anon_syntax(read_line(checkout_root, key))
        return "anon-unclassified-no-checkout"
    last = m.rpartition(".")[2]
    if last in ("constructor", "ctor", ".ctor"):
        return "constructor"
    if last in ("describe", "it", "test", "beforeEach", "afterEach", "beforeAll", "afterAll", "expect"):
        return "jest-frame"
    if "." not in m:
        return "module-level/function"
    return "named-method"


# ---------------------------------------------------------------------------------------
# New: callee/caller "shape" for the 5x5 decomposition of bucket 3.
# Categories (mutually exclusive, checked in this order against the SOURCE declaration line):
#   (a) anonymous          -- label member matches anonL\d+ (or <anon...)
#   (b) named-local-closure -- decl line matches `const|let|var NAME =` or `NAME: (` /
#                              `NAME: async (` -- i.e. a function-valued variable/property,
#                              NOT a class member and NOT a top-level `function` decl.
#   (c) class-method/accessor -- key ends in (get)/(set), OR member has a `.` (Class.member)
#                                 and the decl line looks like a method/property shorthand
#                                 (no `function`, no `=`, no leading const/let/var).
#   (d) top-level function decl -- decl line matches `function NAME(` (module scope, member
#                                 has no `.`).
#   (e) other -- unreadable source or none of the above match; examples printed.
# ---------------------------------------------------------------------------------------

_NAMED_LOCAL_CLOSURE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*(?::\s*[^=]+)?=\s*(?:async\s*)?"
    r"(?:\(|function\b|[A-Za-z_$][\w$]*\s*=>)"
)
_NAMED_LOCAL_CLOSURE_PROP_RE = re.compile(
    r"^\s*[A-Za-z_$][\w$]*\s*:\s*(?:async\s*)?\(.*?(?:=>|\)\s*:?.*\{)"
)
_TOPLEVEL_FUNCTION_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b\s*\*?\s*[A-Za-z_$]")
_CLASS_METHOD_SHAPE_RE = re.compile(
    r"^\s*(?:public|private|protected|readonly|static|override|async|get|set|abstract|\*)?\s*"
    r"(?:public|private|protected|readonly|static|override|async|get|set|\*)?\s*"
    r"[A-Za-z_$][\w$]*\s*\("
)


def callee_caller_shape(key: str, checkout_root: Path) -> str:
    """5-way shape classifier over the actual source declaration line. Order matters:
    checked most-specific-first so every key lands in exactly one bucket."""
    if key.endswith("(get)") or key.endswith("(set)"):
        return "c_class_method_accessor"
    m = member_of(key)
    src = read_line(checkout_root, key)
    if "anonL" in m or "<anon" in m:
        return "a_anonymous"
    if src is None:
        return "e_other_unreadable"
    s = src.strip()
    has_dot = "." in m
    if _NAMED_LOCAL_CLOSURE_RE.match(s) or _NAMED_LOCAL_CLOSURE_PROP_RE.match(s):
        return "b_named_local_closure"
    if not has_dot and _TOPLEVEL_FUNCTION_RE.match(s):
        return "d_toplevel_function"
    if has_dot and _CLASS_METHOD_SHAPE_RE.match(s) and "=" not in s.split("(")[0]:
        return "c_class_method_accessor"
    if not has_dot:
        # module-level, no dot in member name, but doesn't match toplevel-function or
        # named-local-closure patterns cleanly -- still likely a function decl; try loosely.
        if _TOPLEVEL_FUNCTION_RE.search(s):
            return "d_toplevel_function"
        return "e_other_unreadable"
    return "e_other_unreadable"


def main() -> int:
    run_dir = Path(sys.argv[1])
    checkout_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if checkout_root is None:
        print("FATAL: checkout_root required")
        return 1
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

    arm_callers = {c for c, _ in arm_edges}
    arm_callees = {e for _, e in arm_edges}
    arm_lineless = {(strip_line(c), strip_line(e)) for c, e in arm_edges}

    cell = manifest.get("cell", "with-tests")
    work_dir = HERE / ".work" / checkout_root.name / cell
    arm_nodes_all, arm_nodes_all_lineless, node_drop_stats = load_arm_all_nodes(work_dir, checkout_root)

    print(f"oracle primary: {len(primary)}  matched: {len(matched)}  missing: {total_misses}")

    # ---- recompute the SAME ordered classification as diagnose_recall_zod.py ----------
    cat_nearmiss = []
    cat_named_arrow_field_defect = []
    cat_anon_callee_no_node = []
    cat_caller_absent: dict[str, list] = {}
    cat_true_resolve_gap = []
    cat_named_callee_absent = []
    cat_unclassified = []

    for edge in sorted(missing):
        caller, callee = edge
        if (strip_line(caller), strip_line(callee)) in arm_lineless:
            cat_nearmiss.append(edge)
            continue
        if is_anon(callee):
            src = read_line(checkout_root, callee)
            hit, name = looks_like_named_arrow_field(src)
            if hit:
                cat_named_arrow_field_defect.append(edge)
                continue
        if is_anon(callee) and strip_line(callee) not in arm_nodes_all_lineless:
            cat_anon_callee_no_node.append(edge)
            continue
        caller_is_real_node = caller in arm_nodes_all or strip_line(caller) in arm_nodes_all_lineless
        if not caller_is_real_node:
            s = shape(caller, checkout_root)
            cat_caller_absent.setdefault(s, []).append(edge)
            continue
        callee_is_real_node = strip_line(callee) in arm_nodes_all_lineless
        if callee_is_real_node:
            cat_true_resolve_gap.append(edge)
            continue
        if not is_anon(callee):
            cat_named_callee_absent.append(edge)
            continue
        cat_unclassified.append(edge)

    n_bucket3 = len(cat_true_resolve_gap)
    n_bucket3b = len(cat_named_callee_absent)
    print(f"bucket 3 (3_true_resolve_gap_both_known): {n_bucket3}")
    print(f"bucket 3b (3b_named_callee_absent_from_arm): {n_bucket3b}")

    # =====================================================================================
    # BUCKET 3 DECOMPOSITION
    # =====================================================================================
    print("\n" + "=" * 90)
    print(f"BUCKET 3 DECOMPOSITION -- {n_bucket3} edges (caller AND named callee both real arm nodes, no edge)")
    print("=" * 90)

    ROWS = ["a_anonymous", "b_named_local_closure", "c_class_method_accessor", "d_toplevel_function", "e_other_unreadable"]
    matrix: dict[tuple[str, str], int] = Counter()
    callee_examples: dict[str, list] = {r: [] for r in ROWS}
    caller_examples: dict[str, list] = {r: [] for r in ROWS}

    callee_shape_of: dict[tuple, str] = {}
    caller_shape_of: dict[tuple, str] = {}

    for edge in cat_true_resolve_gap:
        caller, callee = edge
        cs = callee_caller_shape(callee, checkout_root)
        ks = callee_caller_shape(caller, checkout_root)
        callee_shape_of[edge] = cs
        caller_shape_of[edge] = ks
        matrix[(ks, cs)] += 1  # (caller_shape, callee_shape)

    check = sum(matrix.values())
    assert check == n_bucket3, f"5x5 matrix does not sum to bucket3: {check} != {n_bucket3}"

    header = "caller\\callee".ljust(28) + "".join(c[2:16].ljust(16) for c in ROWS) + "row_total"
    print("\n5x5 matrix (rows=CALLER shape, cols=CALLEE shape):\n")
    print(header)
    for r in ROWS:
        row_total = sum(matrix[(r, c)] for c in ROWS)
        line = r.ljust(28) + "".join(str(matrix[(r, c)]).ljust(16) for c in ROWS) + str(row_total)
        print(line)
    col_totals_line = "col_total".ljust(28) + "".join(str(sum(matrix[(r, c)] for r in ROWS)).ljust(16) for c in ROWS)
    print(col_totals_line + f"  grand_total={check}")

    print("\ncaller-shape marginals:")
    for r in ROWS:
        n = sum(matrix[(r, c)] for c in ROWS)
        print(f"  {r:28s} {n:6d}  ({100*n/n_bucket3:.2f}%)")
    print("\ncallee-shape marginals:")
    for c in ROWS:
        n = sum(matrix[(r, c)] for r in ROWS)
        print(f"  {c:28s} {n:6d}  ({100*n/n_bucket3:.2f}%)")

    # top 25 callee nodes by miss count
    callee_counts = Counter(callee for caller, callee in cat_true_resolve_gap)
    print(f"\ntop 25 CALLEE nodes by miss count (out of {len(callee_counts)} distinct callees):")
    for callee, n in callee_counts.most_common(25):
        src = read_line(checkout_root, callee)
        print(f"  {n:4d}  {callee}   src: {src.strip() if src else '(unreadable)'}")

    caller_counts = Counter(caller for caller, callee in cat_true_resolve_gap)
    print(f"\ntop 25 CALLER nodes by miss count (out of {len(caller_counts)} distinct callers):")
    for caller, n in caller_counts.most_common(25):
        src = read_line(checkout_root, caller)
        print(f"  {n:4d}  {caller}   src: {src.strip() if src else '(unreadable)'}")

    # top 15 (caller-file -> callee-file) pairs
    file_pair_counts = Counter((file_of(caller), file_of(callee)) for caller, callee in cat_true_resolve_gap)
    print(f"\ntop 15 (caller-file -> callee-file) pairs (out of {len(file_pair_counts)} distinct pairs):")
    for (cf, ef), n in file_pair_counts.most_common(15):
        print(f"  {n:4d}  {cf}  ->  {ef}")

    # same-file vs different-file
    same_file = sum(1 for caller, callee in cat_true_resolve_gap if file_of(caller) == file_of(callee))
    diff_file = n_bucket3 - same_file
    assert same_file + diff_file == n_bucket3
    print(f"\ncallee declared lexically INSIDE caller's file: {same_file}  ({100*same_file/n_bucket3:.2f}%)")
    print(f"callee declared in a DIFFERENT file:              {diff_file}  ({100*diff_file/n_bucket3:.2f}%)")

    # higher-order probe: anonymous-or-local-closure callee whose decl site is NOT lexically
    # inside the caller's BODY. We approximate "inside caller's body" as: same file AND callee's
    # declaration line falls within a bounded window after the caller's declaration line (we
    # have no end-of-function boundary from the key alone -- conservative window of 200 lines,
    # matching the scale of typical zod functions). Anything outside that window, or in a
    # different file entirely, counts as "arrived as an argument or via a variable" (higher-order).
    HO_WINDOW = 200
    ho_count = 0
    ho_examples = []
    for edge in cat_true_resolve_gap:
        caller, callee = edge
        cs = callee_shape_of[edge]
        if cs not in ("a_anonymous", "b_named_local_closure"):
            continue
        caller_ln = line_of(caller)
        callee_ln = line_of(callee)
        same_f = file_of(caller) == file_of(callee)
        inside_body = False
        if same_f and caller_ln is not None and callee_ln is not None:
            if caller_ln <= callee_ln <= caller_ln + HO_WINDOW:
                inside_body = True
        if not inside_body:
            ho_count += 1
            if len(ho_examples) < 10:
                csrc = read_line(checkout_root, caller)
                esrc = read_line(checkout_root, callee)
                ho_examples.append((caller, csrc, callee, esrc))

    print(f"\nHIGHER-ORDER PROBE: anon/local-closure callee whose decl site is NOT lexically inside "
          f"caller's file within a {HO_WINDOW}-line window (i.e. arrived as an argument or via a "
          f"variable, not a plain nested closure call):")
    print(f"  count: {ho_count}  ({100*ho_count/n_bucket3:.2f}% of bucket 3)")
    print("\n  10 examples (caller src / callee src):")
    for caller, csrc, callee, esrc in ho_examples:
        print(f"    caller {caller}")
        print(f"      src: {csrc.strip() if csrc else '(unreadable)'}")
        print(f"    callee {callee}")
        print(f"      src: {esrc.strip() if esrc else '(unreadable)'}")
        print()

    # =====================================================================================
    # BUCKET 3b DECOMPOSITION
    # =====================================================================================
    print("\n" + "=" * 90)
    print(f"BUCKET 3b DECOMPOSITION -- {n_bucket3b} edges (caller real node, NAMED callee has NO arm node at all)")
    print("=" * 90)

    # arm's set of analyzed files -- derived from arm_nodes_all (every file that produced at
    # least one real node), used to distinguish (b) "file never analyzed" from
    # (c) "file analyzed but this specific method absent from architecture.methods.json".
    analyzed_files = {file_of(k) for k in arm_nodes_all}

    b3b_accessor = []
    b3b_file_not_analyzed = []
    b3b_method_absent_in_analyzed_file = []
    b3b_other = []

    for edge in cat_named_callee_absent:
        caller, callee = edge
        if callee.endswith("(get)") or callee.endswith("(set)"):
            b3b_accessor.append(edge)
            continue
        cf = file_of(callee)
        if cf not in analyzed_files:
            b3b_file_not_analyzed.append(edge)
            continue
        # file WAS analyzed (produced other nodes) but this exact callee key isn't among them
        b3b_method_absent_in_analyzed_file.append(edge)

    check3b = len(b3b_accessor) + len(b3b_file_not_analyzed) + len(b3b_method_absent_in_analyzed_file) + len(b3b_other)
    assert check3b == n_bucket3b, f"3b split does not sum: {check3b} != {n_bucket3b}"

    print(f"\n(a) callee is an accessor get/set:                                  {len(b3b_accessor):6d}  "
          f"({100*len(b3b_accessor)/n_bucket3b:.2f}%)")
    print(f"(b) callee's file never produced ANY arm node (file never analyzed): {len(b3b_file_not_analyzed):6d}  "
          f"({100*len(b3b_file_not_analyzed)/n_bucket3b:.2f}%)")
    print(f"(c) callee's file WAS analyzed, but this method is absent from       {len(b3b_method_absent_in_analyzed_file):6d}  "
          f"({100*len(b3b_method_absent_in_analyzed_file)/n_bucket3b:.2f}%)\n    architecture.methods.json")
    print(f"(d) other:                                                          {len(b3b_other):6d}  "
          f"({100*len(b3b_other)/n_bucket3b:.2f}%)")
    print(f"SUM: {check3b} (must equal {n_bucket3b})")

    top_files = Counter(file_of(callee) for caller, callee in b3b_file_not_analyzed)
    print(f"\ntop files for (b) 'file never analyzed' (out of {len(top_files)} distinct files):")
    for f, n in top_files.most_common(25):
        print(f"  {n:4d}  {f}")

    print(f"\ntop 20 MISSING CALLEE labels for bucket 3b overall, with counts:")
    callee_3b_counts = Counter(callee for caller, callee in cat_named_callee_absent)
    for callee, n in callee_3b_counts.most_common(20):
        src = read_line(checkout_root, callee)
        print(f"  {n:4d}  {callee}   src: {src.strip() if src else '(unreadable)'}")

    print("\nDONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
