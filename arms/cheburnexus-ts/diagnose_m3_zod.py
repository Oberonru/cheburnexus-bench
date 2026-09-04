#!/usr/bin/env python3
"""Decompose mechanism M3 ("property call on a value, x.foo(...), callee name matches") of the
zod bucket-3 mechanism table -- 2780 edges -- along two axes (callee declaration container,
receiver expression shape) plus a cross-tab and a concentration table.

Loading/translation/classification reused VERBATIM (by import, not copy-paste) from
diagnose_bucket3_mechanism_zod.py so the M3 edge set here is EXACTLY the same 2780 edges that
script reports. Every split below is asserted to sum to exactly 2780.

Usage: python arms/cheburnexus-ts/diagnose_m3_zod.py <run_dir> <checkout_root>
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import diagnose_bucket3_mechanism_zod as m3  # noqa: E402  -- verbatim loader/classifier reuse

# ---------------------------------------------------------------------------------------
# Rebuild the exact M3 edge set (2780 edges), capturing call-site line + snippet + receiver text.
# ---------------------------------------------------------------------------------------


def find_call_site_recv(body: str, name: str):
    """Re-derive what classify_call_site found for THIS specific call (the first name( match
    that is a property-call, i.e. pre ends with '.'), returning (recv_chain, start_offset)."""
    call_re = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
    for mm in call_re.finditer(body):
        start = mm.start()
        pre = body[max(0, start - 80) : start].rstrip()
        if re.search(r"(?<![\w$])new\s*$", pre):
            continue
        if pre.endswith("."):
            recv_m = re.search(r"([\w$]+(?:\.[\w$]+)*)\.$", pre)
            recv = recv_m.group(1) if recv_m else ""
            if recv == "this" or recv.startswith("this."):
                continue
            return recv, start
        # any other shape (bracket, bare, etc.) -- not M3, keep scanning in case a LATER
        # occurrence of the same name is the M3 one (classify_call_site itself takes the
        # FIRST occurrence overall, so in practice this loop only ever returns on the first
        # iteration when mech==M3; kept as a loop for robustness).
        continue
    return None, None


def gather_m3(bucket3, checkout_root: Path):
    out = []
    for caller, callee in bucket3:
        result = m3.diagnose_edge(caller, callee, checkout_root)
        if result["mech"] != "M3":
            continue
        name = m3.member_of(callee).rpartition(".")[2] or m3.member_of(callee)
        c_ln = m3.line_of(caller)
        c_file = m3.file_of(caller)
        lines = m3.read_lines(checkout_root, c_file)
        body = m3.extract_body(lines, c_ln)
        header_end = body.find("{")
        header = body[: header_end + 1] if header_end != -1 else body[:200]
        params = extract_own_params(header)
        recv, offset = find_call_site_recv(body, name)
        call_ln = m3.line_no_for_offset(body, offset, c_ln) if offset is not None else result.get("line")
        snippet = m3.line_text_at(body, offset) if offset is not None else result.get("snippet")
        out.append(
            {
                "caller": caller,
                "callee": callee,
                "recv": recv,
                "call_ln": call_ln,
                "snippet": snippet,
                "params": params,
                "c_file": c_file,
                "c_ln": c_ln,
                "header": header,
                "body_line_count": body.count("\n") + 1,
            }
        )
    return out


# ---------------------------------------------------------------------------------------
# AXIS A -- callee's declaration container.
# ---------------------------------------------------------------------------------------

METHOD_SHORTHAND_RE = re.compile(r"^\s*(?:get|set)?\s*[\w$]+\s*\([^()]*\)\s*(?::\s*[^={]+)?\s*\{?\s*$")
ARROW_PROP_RE = re.compile(r"^\s*[\'\"]?[\w$]+[\'\"]?\s*:\s*(?:async\s*)?\([^()]*\)\s*(?::[^=]+)?=>")
FUNC_PROP_RE = re.compile(r"^\s*[\'\"]?[\w$]+[\'\"]?\s*:\s*(?:async\s*)?function\b")
PROP_ASSIGN_CLOSURE_RE = re.compile(
    r"^\s*[\w$]+(?:\.[\w$]+)+\s*=\s*(?:async\s*)?(?:\([^()]*\)\s*=>|function\b)"
)
TOP_FUNC_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+[\w$]+")
CLASS_DECL_RE = re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+[\w$]+")
OBJ_OPEN_RE = re.compile(r"(?:=|return)\s*\{?\s*$")


def find_enclosing_brace_line(lines: list[str], decl_ln_1idx: int) -> int | None:
    """Scan backward from just before the declaration line's own body-open, character by
    character, tracking brace depth, to find the line holding the UNMATCHED enclosing '{'.
    Returns a 0-indexed line number, or None if not found within a reasonable window."""
    end_idx = decl_ln_1idx - 1  # 0-idx of decl line itself
    start_idx = max(0, end_idx - 300)
    text = "\n".join(lines[start_idx:end_idx])  # everything STRICTLY BEFORE the decl line
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                nl_before = text.rfind("\n", 0, i)
                return start_idx + text.count("\n", 0, i)
            depth -= 1
    return None


def classify_container(callee: str, checkout_root: Path):
    src = m3.read_line(checkout_root, callee)
    fpath = m3.file_of(callee)
    ln = m3.line_of(callee)
    if src is None or ln is None:
        return "other", "callee source/line unreadable"
    s = src.strip()

    if PROP_ASSIGN_CLOSURE_RE.match(s):
        return "prop_assigned_closure", s

    if TOP_FUNC_RE.match(s):
        member = m3.member_of(callee)
        if "." in member:
            return "toplevel_fn_as_namespace_prop", s
        return "toplevel_fn_as_namespace_prop", s

    is_method_shorthand = bool(METHOD_SHORTHAND_RE.match(s)) and "(" in s and not s.startswith(
        ("if", "for", "while", "switch", "function", "class")
    )
    is_arrow_prop = bool(ARROW_PROP_RE.match(s)) or bool(FUNC_PROP_RE.match(s))

    if not (is_method_shorthand or is_arrow_prop):
        return "other", s

    lines = m3.read_lines(checkout_root, fpath)
    enc_idx = find_enclosing_brace_line(lines, ln)
    if enc_idx is None:
        return "other", s + "  [no enclosing brace found]"
    enc_line = lines[enc_idx].strip()
    # walk further back over consecutive short lines to find the real header if the immediate
    # enclosing line is just a bare '{' or 'return {' etc.
    probe_idx = enc_idx
    probe = enc_line
    hops = 0
    while hops < 5 and (probe == "{" or probe == "" or probe.endswith("(")):
        probe_idx -= 1
        if probe_idx < 0:
            break
        probe = lines[probe_idx].strip()
        hops += 1

    combined = enc_line + " || " + probe
    if CLASS_DECL_RE.search(combined) or re.search(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s", probe):
        return "class_method", s
    if re.search(r"^\s*(?:public|private|protected|static|readonly|abstract|override)\s", enc_line):
        return "class_method", s
    if OBJ_OPEN_RE.search(enc_line) or OBJ_OPEN_RE.search(probe) or enc_line.endswith("{") and (
        "=" in probe or "return" in probe
    ):
        return "object_literal_method", s
    if enc_line == "{" or enc_line.endswith("({") or enc_line.endswith(", {") or re.search(r":\s*\{$", enc_line):
        return "object_literal_method", s
    return "other", s + f"  [enclosing: {enc_line!r} / {probe!r}]"


# ---------------------------------------------------------------------------------------
# AXIS B -- receiver expression shape.
# ---------------------------------------------------------------------------------------


LOCAL_DECL_TYPED_RE = re.compile(r"(?<![\w$])(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=(?!=)")
ARROW_SIG_RE = re.compile(r"\(([^()]*)\)\s*(?::\s*[^=]+)?=>")
FUNC_SIG_RE = re.compile(r"function\s*[\w$]*\s*\(([^()]*)\)")


def extract_own_params(header: str) -> list[str]:
    """Fix for the mechanism module's extract_params: that one grabs the FIRST '(' in the
    header, which is wrong for a caller whose declaration line is an arrow function nested
    inside an outer call expression -- e.g. `.preprocess(async (data, ctx) => {` -- the first
    '(' there belongs to `.preprocess(`, not to the arrow's own parameter list. Find the
    nearest `(...) =>` signature instead (works for both bare and wrapped arrows); fall back to
    a `function name(...)` signature; fall back to the original (possibly wrong) extractor only
    if neither matches."""
    m = ARROW_SIG_RE.search(header)
    if m:
        names = []
        for part in m3.split_top_commas(m.group(1)):
            names.extend(m3.parse_param_names(part))
        return names
    m = FUNC_SIG_RE.search(header)
    if m:
        names = []
        for part in m3.split_top_commas(m.group(1)):
            names.extend(m3.parse_param_names(part))
        return names
    return m3.extract_params(header)


def param_annotated(header: str, base: str) -> bool:
    m = ARROW_SIG_RE.search(header) or FUNC_SIG_RE.search(header)
    if not m:
        return False
    inner = m.group(1)
    for part in m3.split_top_commas(inner):
        names = m3.parse_param_names(part)
        if base in names:
            return ":" in part.split("=")[0]
    return False


def classify_receiver(rec: dict, checkout_root: Path):
    recv = rec["recv"] or ""
    if not recv:
        return "other", False
    segs = recv.split(".")
    base = segs[0]
    if len(segs) > 1:
        return "chain_ge3" if len(segs) >= 3 else "chain_2seg", None
    # single segment
    if base == "this":
        return "this_field", None  # should not occur -- excluded structurally by M2
    if base in rec["params"]:
        annotated = param_annotated(rec["header"], base)
        return "param", annotated
    locals_ = m3.local_bindings_near(m3.read_lines(checkout_root, rec["c_file"]), rec["c_ln"], rec["body_line_count"])
    locals_ |= set(LOCAL_DECL_TYPED_RE.findall(
        "\n".join(m3.read_lines(checkout_root, rec["c_file"])[max(0, rec["c_ln"] - 1 - 200): rec["c_ln"] - 1 + rec["body_line_count"]])
    ))
    if base in locals_:
        return "local_const_let", None
    imports = m3.imports_of(checkout_root, rec["c_file"])
    if base in imports:
        return "imported_namespace", None
    return "other", None


B_LABELS = {
    "param": "parameter of the caller",
    "local_const_let": "local const/let",
    "imported_namespace": "imported namespace/binding",
    "this_field": "this / a field",
    "chain_2seg": "longer chain (2 segments, a.b.foo())",
    "chain_ge3": "longer chain (>=3 segments, a.b.c.foo())",
    "other": "other",
}
B_ORDER = ["param", "local_const_let", "imported_namespace", "this_field", "chain_2seg", "chain_ge3", "other"]

A_LABELS = {
    "object_literal_method": "method of an OBJECT LITERAL",
    "class_method": "class method / accessor",
    "prop_assigned_closure": "property assigned a closure after the fact",
    "toplevel_fn_as_namespace_prop": "top-level function referenced as namespace/module property",
    "other": "other",
}
A_ORDER = ["object_literal_method", "class_method", "prop_assigned_closure", "toplevel_fn_as_namespace_prop", "other"]


def main() -> int:
    run_dir = Path(sys.argv[1])
    checkout_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if checkout_root is None:
        print("FATAL: checkout_root required")
        return 1

    bucket3 = m3.rebuild_bucket3(run_dir, checkout_root)
    assert len(bucket3) == 9827, f"bucket3 drifted: {len(bucket3)}"

    m3_edges = gather_m3(bucket3, checkout_root)
    total = len(m3_edges)
    print(f"M3 edges (property call on a value, x.foo(...), name-matched): {total}")
    assert total == 2780, f"M3 count drifted from expected 2780: got {total}"

    # ---- AXIS A ----
    a_counts: Counter = Counter()
    a_examples: dict[str, Counter] = {k: Counter() for k in A_ORDER}
    a_label_of: dict[tuple, str] = {}
    for rec in m3_edges:
        cat, detail = classify_container(rec["callee"], checkout_root)
        rec["A"] = cat
        a_counts[cat] += 1
        a_examples[cat][rec["callee"]] += 1

    assert sum(a_counts.values()) == total, f"axis A does not sum to {total}: {sum(a_counts.values())}"

    print("\n" + "=" * 90)
    print(f"AXIS A -- callee's declaration container ({total} edges)")
    print("=" * 90)
    for cat in A_ORDER:
        n = a_counts.get(cat, 0)
        pct = 100 * n / total
        print(f"  {cat:32s}{n:6d}  ({pct:5.2f}%)  {A_LABELS[cat]}")
        top15 = a_examples[cat].most_common(15)
        for callee, c in top15:
            print(f"      {c:5d}x  {callee}")
    print(f"\nSUM (axis A): {sum(a_counts.values())} (must equal {total})")

    if a_counts.get("other", 0) > 0:
        print("\n-- 'other' (axis A) raw examples (up to 15) --")
        shown = 0
        for rec in m3_edges:
            if rec["A"] == "other" and shown < 15:
                cat, detail = classify_container(rec["callee"], checkout_root)
                print(f"  CALLEE: {rec['callee']}")
                print(f"    detail: {detail}")
                shown += 1

    # ---- AXIS B ----
    b_counts: Counter = Counter()
    b_param_annotated = 0
    b_param_total = 0
    for rec in m3_edges:
        cat, annotated = classify_receiver(rec, checkout_root)
        rec["B"] = cat
        b_counts[cat] += 1
        if cat == "param":
            b_param_total += 1
            if annotated:
                b_param_annotated += 1

    assert sum(b_counts.values()) == total, f"axis B does not sum to {total}: {sum(b_counts.values())}"

    print("\n" + "=" * 90)
    print(f"AXIS B -- receiver expression `x` in x.foo(...) ({total} edges)")
    print("=" * 90)
    for cat in B_ORDER:
        n = b_counts.get(cat, 0)
        pct = 100 * n / total
        extra = ""
        if cat == "param":
            extra = f"  [of these, {b_param_annotated}/{b_param_total} carry an explicit type annotation, {b_param_total - b_param_annotated} do not]"
        print(f"  {cat:20s}{n:6d}  ({pct:5.2f}%)  {B_LABELS[cat]}{extra}")
    print(f"\nSUM (axis B): {sum(b_counts.values())} (must equal {total})")

    if b_counts.get("other", 0) > 0:
        print("\n-- 'other' (axis B) raw examples (up to 15) --")
        shown = 0
        for rec in m3_edges:
            if rec["B"] == "other" and shown < 15:
                print(f"  CALLER: {rec['caller']}  recv={rec['recv']!r}  snippet={rec['snippet']!r}")
                shown += 1

    # ---- C: cross-tab ----
    cross: Counter = Counter()
    for rec in m3_edges:
        cross[(rec["A"], rec["B"])] += 1
    assert sum(cross.values()) == total

    print("\n" + "=" * 90)
    print(f"AXIS A x AXIS B CROSS-TAB ({total} edges)")
    print("=" * 90)
    header_row = "container \\ receiver".ljust(32) + "".join(f"{b:>16s}" for b in B_ORDER) + f"{'TOTAL':>10s}"
    print(header_row)
    for a in A_ORDER:
        row = a.ljust(32)
        rowsum = 0
        for b in B_ORDER:
            n = cross.get((a, b), 0)
            rowsum += n
            row += f"{n:>16d}"
        row += f"{rowsum:>10d}"
        print(row)
    colsums = "TOTAL".ljust(32) + "".join(f"{sum(cross.get((a,b),0) for a in A_ORDER):>16d}" for b in B_ORDER)
    colsums += f"{total:>10d}"
    print(colsums)

    largest = cross.most_common(1)[0]
    (la, lb), lcount = largest
    print(f"\nLARGEST CELL: A={la} x B={lb} -> {lcount} edges ({100*lcount/total:.2f}%)")
    print("15 examples:")
    shown = 0
    for rec in m3_edges:
        if rec["A"] == la and rec["B"] == lb and shown < 15:
            callee_ln = m3.line_of(rec["callee"])
            callee_src = m3.read_line(checkout_root, rec["callee"])
            print(f"  CALLER: {rec['caller']}")
            print(f"  CALLEE: {rec['callee']}")
            print(f"  CALL SITE (line {rec['call_ln']}): {rec['snippet']}")
            print(f"  CALLEE DECL (line {callee_ln}): {callee_src.strip() if callee_src else '(unreadable)'}")
            print()
            shown += 1

    # ---- D: concentration ----
    callee_counts: Counter = Counter()
    for rec in m3_edges:
        callee_counts[rec["callee"]] += 1
    assert sum(callee_counts.values()) == total

    print("\n" + "=" * 90)
    print(f"AXIS D -- CONCENTRATION: top 25 callee nodes by edge count ({total} edges)")
    print("=" * 90)
    cum = 0
    for i, (callee, n) in enumerate(callee_counts.most_common(25), start=1):
        cum += n
        pct = 100 * n / total
        cumpct = 100 * cum / total
        print(f"  {i:3d}. {n:5d}  ({pct:5.2f}%)  cum={cumpct:6.2f}%   {callee}")

    sorted_counts = [n for _, n in callee_counts.most_common()]
    running = 0
    n50 = n80 = None
    for i, n in enumerate(sorted_counts, start=1):
        running += n
        if n50 is None and running >= 0.5 * total:
            n50 = i
        if n80 is None and running >= 0.8 * total:
            n80 = i
    print(f"\nDistinct callee nodes needed to cover 50% of M3: {n50}  (out of {len(sorted_counts)} distinct callees)")
    print(f"Distinct callee nodes needed to cover 80% of M3: {n80}  (out of {len(sorted_counts)} distinct callees)")
    print(f"Total distinct callee nodes in M3: {len(sorted_counts)}  (max edge count for one callee: {sorted_counts[0]})")

    print("\nDONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
