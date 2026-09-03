#!/usr/bin/env python3
"""Second-pass recall breakdown for zod, built on top of diagnose_recall.py's buckets.

2026-09-03 CORRECTION: the previous version of this script had two proven bugs, found by
hand-reading 20 sampled "caller absent" edges against the real source and the arm's own
`architecture.methods.json`:

  (a) "node existence" was computed from EDGE ENDPOINTS of edges.jsonl (`arm_callers |
      arm_callees`), not from the arm's actual node list. That conflates "the arm never
      emitted a node for this caller" with "the arm emitted the node but no static edge
      reaches it" -- ALL 20 hand-sampled "absent" callers turned out to exist as real nodes
      in `architecture.methods.json`. Fixed below: node existence is now decided from
      `arm_nodes_all` (every method key in architecture.methods.json, translated through
      this arm's OWN `run.py` Translator/TypeIndex -- the exact same code path `run.py`
      uses to build edges.jsonl, so the join key shape is guaranteed consistent). The old
      edge-endpoint set is KEPT, but renamed `arm_nodes_with_edges`, so the two facts can
      never be silently conflated again.
  (b) `shape()` decided "anon-arrow/callback" from the substring `anonL` in the LABEL,
      never from syntax -- 7 of 20 hand-sampled were anonymous FUNCTION EXPRESSIONS, not
      arrows. Fixed below: `classify_anon_syntax()` reads the actual source line and
      distinguishes arrow / function-expression / unclear-syntax; the caller-shape bucket
      names are renamed to say what was actually observed, never "arrow" without evidence.

This script goes one level deeper than diagnose_recall.py to answer the exact question asked
for the zod recall write-up: 100% of the misses, in mutually exclusive categories --

  1. callee-is-anon-closure-with-NO-arm-node-at-all  (structural: arm never emits ANY node,
     as caller or callee, for that closure)
  2. caller absent from arm entirely, split by caller SHAPE, source-syntax verified where
     the source is readable (anon-arrow / anon-function-expression / anon-syntax-unclear /
     named-function / class-method)
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

PART 2 (statically-unknowable classification) additionally splits `missing` into "genuinely
missing" vs "statically unknowable" for two mechanisms found by hand-reading: (1) dynamic /
computed property call (`locales[locale]!()`) and (2) test-runner-invoked callback (the
2nd argument to Vitest `test`/`it`/`describe`/... -- the runner calls it, no call
EXPRESSION for that invocation exists anywhere in source). Both are detected from SOURCE
TEXT, conservatively -- unsure cases stay "genuinely missing", never inflating recall. This
NEVER changes `recall on primary` (the headline number `runner/run.py` reports); it is
reported purely as an additional `recall_excluding_unknowable` metric, always printed
alongside the original and both denominators.

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
sys.path.insert(0, str(HERE))
import grade  # noqa: E402
import run as ts_run  # noqa: E402  -- reuses run.py's OWN TypeIndex/Translator/_parse_raw_key,
# the exact code that builds edges.jsonl's keys, so the node-existence join key shape is
# guaranteed identical instead of being independently (and possibly wrongly) reimplemented here.


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


def load_arm_all_nodes(work_dir: Path, checkout_root: Path) -> tuple[set[str], set[str], dict]:
    """The arm's REAL node list -- every method key `architecture.methods.json` records,
    translated through `run.py`'s OWN `TypeIndex`/`Translator` (the identical code that turns
    TsAnalyzer's raw keys into edges.jsonl's oracle-shape keys). Returns
    (full_keys, lineless_keys, translator_drop_stats). A raw key that `Translator.translate`
    drops (no owner fqn, no methods-sidecar line, etc.) is simply absent from the result --
    counted in the returned drop-stats dict, never guessed into existence.
    """
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


_TESTRUNNER_RE = re.compile(
    r"\b(describe|it|test|beforeEach|afterEach|beforeAll|afterAll)"
    r"(?:\s*\.\s*(?:only|skip|each\([^)]*\)))?\s*\(\s*(?:[\'\"][^\'\"]*[\'\"]\s*,)?\s*$"
)
# matches a test-runner call whose opening `(` (and, for the common shape, its first string-
# literal name argument) is the LAST thing on the line -- i.e. the callback argument starts on
# the following line(s). Combined with a same-line inline check in `is_testrunner_callback`.

_TESTRUNNER_INLINE_RE = re.compile(
    r"\b(describe|it|test|beforeEach|afterEach|beforeAll|afterAll)"
    r"(?:\s*\.\s*(?:only|skip|each\([^)]*\)))?\s*\(\s*(?:[\'\"][^\'\"]*[\'\"]\s*,\s*)?"
    r"(?:async\s+)?(?:\(|function\b)"
)

_DYNAMIC_PROP_CALL_RE = re.compile(
    r"\[\s*(?!['\"])[^\[\]'\"]{1,60}\]\s*!?\s*\("
)
# A bracket-indexed call whose bracket contents are NOT a quoted string literal, e.g.
# `locales[locale]!()`. `obj["fixedName"]()` is deliberately EXCLUDED -- a string literal key
# is statically known and any engine miss on it is a genuine resolve gap, not an unknowable.


def classify_anon_syntax(src_line: str | None) -> str:
    """Actual SOURCE SYNTAX of an anonymous callable's own declaration line -- never inferred
    from the oracle label. Deliberately conservative: only calls it "arrow" or
    "function-expression" when the line itself says so; anything else is
    "anon-syntax-unclear" rather than a guess."""
    if src_line is None:
        return "anon-syntax-unreadable"
    s = src_line.strip()
    if re.search(r"\bfunction\b\s*\*?\s*[\w$]*\s*\(", s):
        return "anon-function-expression"
    if "=>" in s:
        return "anon-arrow"
    return "anon-syntax-unclear"


def is_testrunner_callback(checkout_root: Path, callee_key: str) -> bool:
    """Is `callee_key`'s own declaration line the direct 2nd-argument callback of a Vitest
    `test`/`it`/`describe`/`beforeEach`/... call -- i.e. the runner invokes it at RUNTIME with
    no static call EXPRESSION anywhere in source for that specific invocation? Checked from
    SOURCE, on the callee's own line and the two lines immediately before it (covers both the
    common one-liner `it('x', () => {...})` and the wrapped `it('x',\\n  () => {...})` shape).
    Conservative: any line it cannot read counts as "no", never "yes"."""
    if checkout_root is None:
        return False
    fpath = file_of(callee_key)
    ln = line_of(callee_key)
    if ln is None:
        return False
    if fpath not in _source_cache:
        p = checkout_root / fpath
        try:
            _source_cache[fpath] = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            _source_cache[fpath] = []
    lines = _source_cache[fpath]
    if not (1 <= ln <= len(lines)):
        return False
    own_line = lines[ln - 1]
    if _TESTRUNNER_INLINE_RE.search(own_line):
        return True
    for back in (1, 2):
        idx = ln - 1 - back
        if idx < 0:
            break
        if _TESTRUNNER_RE.search(lines[idx]):
            return True
    return False


def has_dynamic_property_call_in_body(checkout_root: Path, caller_key: str, callee_key: str,
                                       window: int = 15) -> bool:
    """Does the caller's body (a bounded window of `window` lines starting at its own decl
    line -- we have no end-of-function boundary from the key alone) contain a computed/dynamic
    property call such as `locales[locale]!()`, AND is the callee itself under a `/locales/`
    directory -- the ONE shape hand-verified for this corpus (a runtime-string-indexed default
    export factory)? Deliberately narrow: an unrestricted body-window scan false-positived
    badly in testing (it flagged unrelated missing edges to `any()`/`object()`/`safeParse`
    simply because SOME OTHER bracket call existed nearby in the same function) -- tying the
    match to the specific corpus shape that was hand-verified avoids inflating this bucket with
    matches that have no real connection to the missing edge. Conservative: unreadable source,
    or no callee-path match, means "no"."""
    if checkout_root is None:
        return False
    # Narrowed further after inspection: `/locales/` alone also matched the UNRELATED test
    # helper `packages/zod/src/v4/core/tests/locales/parity.test.ts` directory (false
    # positives spotted by hand -- callees `asFormat`/`asSize`, plain local closures with no
    # dynamic-call relationship at all). Require the actual locale-DEFINITION path.
    if "packages/zod/src/v4/locales/" not in callee_key.replace("\\", "/"):
        return False
    fpath = file_of(caller_key)
    ln = line_of(caller_key)
    if ln is None:
        return False
    if fpath not in _source_cache:
        p = checkout_root / fpath
        try:
            _source_cache[fpath] = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            _source_cache[fpath] = []
    lines = _source_cache[fpath]
    lo = max(0, ln - 1)
    hi = min(len(lines), lo + window)
    for line in lines[lo:hi]:
        if _DYNAMIC_PROP_CALL_RE.search(line):
            return True
    return False


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
    print(f"arm edges: {len(arm_edges)} (dropped {arm_dropped}) -- this is the pre-denominator grader")
    print(f"  discard the task asks to measure, never assumed zero; see grade.load_arm for what it drops.")
    print(f"oracle primary: {len(primary)}  matched: {len(matched)}  MISSING (denominator for everything below): {total_misses}")
    print(f"recall on primary (HEADLINE -- UNCHANGED, matches runner/run.py): {len(matched)/len(primary):.4f}\n")

    # ---- node existence: TWO SEPARATE FACTS, never conflated ---------------------------
    # (a) arm_nodes_with_edges -- anything the arm emitted an EDGE to/from (the OLD, WRONG
    #     proxy for "does this node exist" -- kept, renamed, for comparison only).
    arm_callers = {c for c, _ in arm_edges}
    arm_callees = {e for _, e in arm_edges}
    arm_nodes_with_edges = arm_callers | arm_callees
    arm_nodes_with_edges_lineless = {strip_line(n) for n in arm_nodes_with_edges}
    arm_callers_lineless = {strip_line(c) for c in arm_callers}
    arm_lineless = {(strip_line(c), strip_line(e)) for c, e in arm_edges}

    # (b) arm_nodes_all -- the arm's REAL node list, from architecture.methods.json via
    #     run.py's own Translator. THIS is what "does a node exist for this key" must mean.
    if checkout_root is None:
        print("FATAL: checkout_root is required to load the arm's real node list "
              "(architecture.methods.json translation needs it for repo-relative paths).")
        return 1
    cell = manifest.get("cell", "with-tests")
    work_dir = HERE / ".work" / checkout_root.name / cell
    arm_nodes_all, arm_nodes_all_lineless, node_drop_stats = load_arm_all_nodes(work_dir, checkout_root)
    print(f"arm's REAL node list (architecture.methods.json translated via run.py's own "
          f"Translator, work dir {work_dir}):")
    print(f"  raw method keys: {node_drop_stats['_raw_keys_total']}  translated ok: "
          f"{node_drop_stats['_translated_ok']}  drop reasons: "
          f"{ {k: v for k, v in node_drop_stats.items() if not k.startswith('_') and v} }")
    print(f"  arm_nodes_all: {len(arm_nodes_all)}   arm_nodes_with_edges (OLD proxy): "
          f"{len(arm_nodes_with_edges)}\n")

    # ---- ordered, mutually exclusive classification -----------------------------------
    # Node existence below uses arm_nodes_all_lineless (the arm's REAL node list) -- NEVER
    # the edge-endpoint proxy. arm_nodes_with_edges is printed separately, for comparison,
    # further down, and never feeds a classification decision.
    cat_nearmiss = []
    cat_named_arrow_field_defect = []
    cat_anon_callee_no_node = []
    cat_caller_absent: dict[str, list] = {}
    cat_true_resolve_gap = []
    cat_named_callee_absent = []
    cat_unclassified = []

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

        # 1) callee is an anon closure the arm has NO REAL NODE for at all (checked against
        #    arm_nodes_all_lineless -- architecture.methods.json, not edge endpoints).
        if is_anon(callee) and strip_line(callee) not in arm_nodes_all_lineless:
            cat_anon_callee_no_node.append(edge)
            continue

        # 2) caller missing from the arm's REAL node list entirely (line-insensitive), split
        #    by caller shape -- shape() now reads actual source syntax for anon callables.
        caller_is_real_node = caller in arm_nodes_all or strip_line(caller) in arm_nodes_all_lineless
        if not caller_is_real_node:
            s = shape(caller, checkout_root)
            cat_caller_absent.setdefault(s, []).append(edge)
            continue

        # 3) caller is a real node AND named callee is a real node, but no edge -- true gap.
        callee_is_real_node = strip_line(callee) in arm_nodes_all_lineless
        if callee_is_real_node:
            cat_true_resolve_gap.append(edge)
            continue

        # 3b) caller is a real node, callee is a NAMED (non-anon) member the arm never emits
        #     a node for at all -- typically a property-accessor read (e.g. `this.boolean`
        #     reading a `get boolean()`) that the arm's call-detector doesn't treat as a call
        #     site. Distinct from (3): here the callee itself is missing, not just the edge.
        if not is_anon(callee):
            cat_named_callee_absent.append(edge)
            continue

        cat_unclassified.append(edge)

    caller_absent_total = sum(len(v) for v in cat_caller_absent.values())
    buckets = [
        ("5_near_miss_line", cat_nearmiss),
        ("4_named_arrow_field_mislabeled_anonL (oracle artifact)", [e for e, _, _ in cat_named_arrow_field_defect]),
        ("1_anon_callee_no_arm_node_at_all", cat_anon_callee_no_node),
    ] + [
        (f"2_caller_absent__shape={s}", lst) for s, lst in sorted(cat_caller_absent.items())
    ] + [
        ("3_true_resolve_gap_both_known", cat_true_resolve_gap),
        ("3b_named_callee_absent_from_arm", cat_named_callee_absent),
        ("0_unclassified", cat_unclassified),
    ]
    print(f"(caller-absent bucket 2 total, all shapes combined: {caller_absent_total} -- "
          f"{100*caller_absent_total/total_misses:.2f}% -- compare against the OLD bucket "
          f"'2a_caller_absent_anon_arrow' reported by the pre-fix version of this script)")

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

    # ---- PART 2: statically-unknowable misses, as an ADDITIONAL metric -----------------
    # This NEVER changes `missing`, `matched`, `primary`, or the headline "recall on primary"
    # printed above -- that number stays exactly what runner/run.py reports. This is purely
    # an extra lens on the SAME `missing` set, classifying each edge into "genuinely missing"
    # vs "statically unknowable" for two mechanisms found by hand-reading source. Conservative
    # by construction: an edge is excluded ONLY on a positive source-text match; anything
    # ambiguous or unreadable stays "genuinely missing".
    print("\n" + "=" * 88)
    print("PART 2 -- statically-unknowable misses (additional metric, does not touch recall above)")
    print("=" * 88)

    unknowable_testrunner = []
    unknowable_dynamic_prop = []
    genuinely_missing = []
    for edge in sorted(missing):
        caller, callee = edge
        if is_testrunner_callback(checkout_root, callee):
            unknowable_testrunner.append(edge)
            continue
        if has_dynamic_property_call_in_body(checkout_root, caller, callee):
            unknowable_dynamic_prop.append(edge)
            continue
        genuinely_missing.append(edge)

    n_test = len(unknowable_testrunner)
    n_dyn = len(unknowable_dynamic_prop)
    n_genuine = len(genuinely_missing)
    assert n_test + n_dyn + n_genuine == total_misses

    print(f"\nexclusion counts (out of {total_misses} missing edges):")
    print(f"  test-runner-invoked callback (mechanism 2, checked on CALLEE's decl line): "
          f"{n_test}  ({100*n_test/total_misses:.2f}%)")
    print(f"  dynamic/computed property call in caller body, callee under /locales/ (mechanism 1, "
          f"checked in a 15-line window from CALLER's decl line): {n_dyn}  ({100*n_dyn/total_misses:.2f}%)")
    print(f"  genuinely missing (everything else): {n_genuine}  ({100*n_genuine/total_misses:.2f}%)")

    denom_excl_unknowable = len(primary) - n_test - n_dyn
    recall_orig = len(matched) / len(primary)
    recall_excl = len(matched) / denom_excl_unknowable if denom_excl_unknowable else float("nan")
    print(f"\nrecall on primary (ORIGINAL, headline, UNCHANGED): {len(matched)} / {len(primary)} = {recall_orig:.4f}")
    print(f"recall_excluding_unknowable (ADDITIONAL metric):   {len(matched)} / {denom_excl_unknowable} "
          f"= {recall_excl:.4f}   (denominator reduced by {n_test + n_dyn} statically-unknowable rows)")

    print(f"\n-- spot-check: 5 test-runner-callback exclusions (mechanism 2), by hand --")
    for edge in unknowable_testrunner[:5]:
        caller, callee = edge
        csrc = read_line(checkout_root, caller) if checkout_root else None
        esrc = read_line(checkout_root, callee) if checkout_root else None
        print(f"   caller {caller}  src: {csrc.strip() if csrc else '(unreadable)'}")
        print(f"   callee {callee}  src: {esrc.strip() if esrc else '(unreadable)'}")
        print()

    print(f"-- spot-check: 5 dynamic-property-call exclusions (mechanism 1), by hand --")
    for edge in unknowable_dynamic_prop[:5]:
        caller, callee = edge
        csrc = read_line(checkout_root, caller) if checkout_root else None
        esrc = read_line(checkout_root, callee) if checkout_root else None
        print(f"   caller {caller}  src: {csrc.strip() if csrc else '(unreadable)'}")
        print(f"   callee {callee}  src: {esrc.strip() if esrc else '(unreadable)'}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
