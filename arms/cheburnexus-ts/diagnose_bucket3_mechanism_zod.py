#!/usr/bin/env python3
"""Diagnose the CALL-SITE MECHANISM behind bucket 3 (`3_true_resolve_gap_both_known`) of the
zod recall breakdown -- both caller and callee ARE arm nodes, but no edge connects them.

reclass_bucket3_zod.py classified the SHAPE of the caller/callee DECLARATIONS. That tells us
nothing about why resolution failed -- the call EXPRESSION in the caller's body does. This
script locates that call site (brace-balanced scan of the caller's body, source-text search for
the callee's member name) and classifies the call mechanism into one of nine buckets (M1-M9),
mutually exclusive, summing to exactly 9827.

Loading/translation/bucket-3 recomputation copied VERBATIM from reclass_bucket3_zod.py so the
bucket-3 edge set here is identical to that script's (9827 edges).

Usage: python arms/cheburnexus-ts/diagnose_bucket3_mechanism_zod.py <run_dir> <checkout_root>
e.g.:  python arms/cheburnexus-ts/diagnose_bucket3_mechanism_zod.py results/session-qualified-zod/zod/with-tests D:\\DEV\\TsTest\\zod
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
# Verbatim helpers from reclass_bucket3_zod.py (only what's needed to rebuild bucket 3)
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


def read_lines(checkout_root: Path, fpath: str) -> list[str]:
    if fpath not in _source_cache:
        p = checkout_root / fpath
        try:
            _source_cache[fpath] = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            _source_cache[fpath] = []
    return _source_cache[fpath]


def read_line(checkout_root: Path, key: str) -> str | None:
    fpath = file_of(key)
    ln = line_of(key)
    if ln is None:
        return None
    lines = read_lines(checkout_root, fpath)
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


def shape(key: str, checkout_root: Path | None = None) -> str:
    m = member_of(key)
    if key.endswith("(get)") or key.endswith("(set)"):
        return "accessor"
    if "anonL" in m or "<anon" in m:
        return "anon-unclassified-no-checkout"
    last = m.rpartition(".")[2]
    if last in ("constructor", "ctor", ".ctor"):
        return "constructor"
    if last in ("describe", "it", "test", "beforeEach", "afterEach", "beforeAll", "afterAll", "expect"):
        return "jest-frame"
    if "." not in m:
        return "module-level/function"
    return "named-method"


def rebuild_bucket3(run_dir: Path, checkout_root: Path) -> list[tuple[str, str]]:
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

    arm_lineless = {(strip_line(c), strip_line(e)) for c, e in arm_edges}

    cell = manifest.get("cell", "with-tests")
    work_dir = HERE / ".work" / checkout_root.name / cell
    arm_nodes_all, arm_nodes_all_lineless, _ = load_arm_all_nodes(work_dir, checkout_root)

    cat_true_resolve_gap = []
    for edge in sorted(missing):
        caller, callee = edge
        if (strip_line(caller), strip_line(callee)) in arm_lineless:
            continue
        if is_anon(callee):
            src = read_line(checkout_root, callee)
            hit, _name = looks_like_named_arrow_field(src)
            if hit:
                continue
        if is_anon(callee) and strip_line(callee) not in arm_nodes_all_lineless:
            continue
        caller_is_real_node = caller in arm_nodes_all or strip_line(caller) in arm_nodes_all_lineless
        if not caller_is_real_node:
            continue
        callee_is_real_node = strip_line(callee) in arm_nodes_all_lineless
        if callee_is_real_node:
            cat_true_resolve_gap.append(edge)
            continue
        # else: bucket 3b or unclassified -- not part of bucket 3
    return cat_true_resolve_gap


# ---------------------------------------------------------------------------------------
# NEW: locate the call site in the caller's body and classify the call MECHANISM.
# ---------------------------------------------------------------------------------------

BODY_CAP_LINES = 400

HOF_CALLBACK_RE_TMPL = (
    r"\.(?:map|forEach|filter|then|catch|reduce|reduceRight|sort|find|findIndex|some|every|flatMap)"
    r"\(\s*{name}\s*[,)]"
)
BARE_FRAME_CALLBACK_RE_TMPL = (
    r"(?<![\w$])(?:expect|describe|it|test|beforeEach|afterEach|beforeAll|afterAll)\(\s*{name}\s*[,)]"
)


def extract_body(lines: list[str], start_ln: int, cap: int = BODY_CAP_LINES) -> str | None:
    """Brace-balanced scan starting at 1-indexed start_ln, capped at `cap` lines. Best-effort:
    ignores string/comment/regex-literal content (heuristic, not a real tokenizer)."""
    n = len(lines)
    start_idx = start_ln - 1
    if start_idx < 0 or start_idx >= n:
        return None
    depth = 0
    started = False
    collected = []
    end = min(n, start_idx + cap)
    for i in range(start_idx, end):
        line = lines[i]
        collected.append(line)
        for ch in line:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        if started and depth <= 0:
            return "\n".join(collected)
    return "\n".join(collected) if collected else None


def split_top_commas(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def parse_param_names(param_text: str) -> list[str]:
    """Handle destructuring ({a,b}/[a,b]), renames ({a: b}), defaults (x = y), rest (...x)."""
    p = param_text.strip().lstrip(".").strip()  # tolerate leftover '...' from rest params
    if p.startswith("{"):
        inner = p[1 : p.rfind("}")] if "}" in p else p[1:]
        names = []
        for part in split_top_commas(inner):
            part = part.split("=")[0].strip()
            if ":" in part:
                part = part.split(":")[-1].strip()
            part = part.lstrip(".").strip()
            m = re.match(r"[A-Za-z_$][\w$]*", part)
            if m:
                names.append(m.group(0))
        return names
    if p.startswith("["):
        inner = p[1 : p.rfind("]")] if "]" in p else p[1:]
        names = []
        for part in split_top_commas(inner):
            part = part.split("=")[0].strip().lstrip(".").strip()
            m = re.match(r"[A-Za-z_$][\w$]*", part)
            if m:
                names.append(m.group(0))
        return names
    base = p.split(":")[0].split("=")[0].strip()
    m = re.match(r"[A-Za-z_$][\w$]*", base)
    return [m.group(0)] if m else []


def extract_params(body_head: str) -> list[str]:
    i = body_head.find("(")
    if i == -1:
        return []
    depth = 0
    j = -1
    for k in range(i, len(body_head)):
        c = body_head[k]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                j = k
                break
    if j == -1:
        return []
    inner = body_head[i + 1 : j]
    names = []
    for part in split_top_commas(inner):
        names.extend(parse_param_names(part))
    return names


def line_no_for_offset(body: str, offset: int, start_ln: int) -> int:
    return start_ln + body.count("\n", 0, offset)


def line_text_at(body: str, offset: int) -> str:
    lo = body.rfind("\n", 0, offset) + 1
    hi = body.find("\n", offset)
    if hi == -1:
        hi = len(body)
    return body[lo:hi].strip()


def classify_call_site(body: str, name: str, params: list[str]):
    """Return (mechanism, matched_line_no_offset_in_body, snippet) or None if no direct call
    expression `name(` is found."""
    call_re = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
    for m in call_re.finditer(body):
        start = m.start()
        pre = body[max(0, start - 80) : start].rstrip()
        snippet = line_text_at(body, start)
        if re.search(r"(?<![\w$])new\s*$", pre):
            return "M7", start, snippet
        if pre.endswith("."):
            recv_m = re.search(r"([\w$]+(?:\.[\w$]+)*)\.$", pre)
            recv = recv_m.group(1) if recv_m else ""
            if recv == "this" or recv.startswith("this."):
                return "M2", start, snippet
            return "M3", start, snippet
        if pre.endswith("]"):
            return "M5", start, snippet
        # bare identifier call
        if name in params:
            return "M4", start, snippet
        return "M1", start, snippet
    return None


def classify_callback_pass(body: str, name: str):
    for tmpl in (HOF_CALLBACK_RE_TMPL, BARE_FRAME_CALLBACK_RE_TMPL):
        rex = re.compile(tmpl.format(name=re.escape(name)))
        m = rex.search(body)
        if m:
            return "M6", m.start(), line_text_at(body, m.start())
    return None


WORD_RE_TMPL = r"(?<![\w$]){name}(?![\w$])"

# ---------------------------------------------------------------------------------------
# ROUND 2 -- caller-side structural classification.
#
# The round-1 method searched the CALLER's body for the CALLEE's member-name TEXT. That is
# systematically blind to any dispatch where the function is reached through a binding whose
# name differs from the callee's own label (a parameter, a local variable holding a function,
# a reassigned property) -- which is exactly the higher-order / indirection cases this task
# exists to measure. Round 2 instead enumerates EVERY call expression actually present in the
# caller's body and classifies the edge by what KIND of call site is available, without
# requiring the callee's name to appear at all.
# ---------------------------------------------------------------------------------------

JS_KEYWORDS_AS_CALL_BASE = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof", "in", "of",
    "new", "await", "yield", "throw", "delete", "void", "instanceof", "super", "constructor",
}

CALL_ANY_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*)*)\s*\(")

IMPORT_RE = re.compile(
    r"^\s*import\s+(?:type\s+)?(?:\*\s+as\s+([A-Za-z_$][\w$]*)"
    r"|\{([^}]*)\}(?:\s*,\s*\{([^}]*)\})?"
    r"|([A-Za-z_$][\w$]*))\s+from",
    re.MULTILINE,
)

LOCAL_DECL_RE = re.compile(r"(?<![\w$])(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")

_imports_cache: dict[str, set[str]] = {}


def imports_of(checkout_root: Path, fpath: str) -> set[str]:
    if fpath in _imports_cache:
        return _imports_cache[fpath]
    lines = read_lines(checkout_root, fpath)
    text = "\n".join(lines)
    names: set[str] = set()
    for m in IMPORT_RE.finditer(text):
        ns, named1, named2, default = m.groups()
        if ns:
            names.add(ns)
        if default:
            names.add(default)
        for named in (named1, named2):
            if not named:
                continue
            for part in named.split(","):
                part = part.strip()
                if not part:
                    continue
                # `Orig as Alias` -> the LOCAL binding is the alias
                if " as " in part:
                    part = part.split(" as ")[-1].strip()
                m2 = re.match(r"[A-Za-z_$][\w$]*", part)
                if m2:
                    names.add(m2.group(0))
    _imports_cache[fpath] = names
    return names


def local_bindings_near(lines: list[str], start_ln: int, body_line_count: int) -> set[str]:
    """`const`/`let`/`var` NAMEs declared in a window spanning up to 200 lines BEFORE the
    caller's own declaration (enclosing-closure bindings, e.g. `const originalRun = ...`
    captured by a nested arrow/function) plus the caller's own body (locals declared and
    called within itself)."""
    lo = max(0, start_ln - 1 - 200)
    hi = min(len(lines), start_ln - 1 + body_line_count)
    window = "\n".join(lines[lo:hi])
    return {m.group(1) for m in LOCAL_DECL_RE.finditer(window)}


def get_all_calls(body: str) -> list[tuple[str, int]]:
    """[(base_identifier, offset), ...] for every call expression in `body`, in source order.
    base_identifier = the first segment of the (possibly dotted/optional-chained) callee text,
    e.g. `inst._zod.run(` -> `inst`, `mapper(` -> `mapper`, `new Foo(` -> `Foo`."""
    normalized = body.replace("?.", ".")
    out = []
    for m in CALL_ANY_RE.finditer(normalized):
        chain = m.group(1)
        base = chain.split(".")[0]
        if base in JS_KEYWORDS_AS_CALL_BASE:
            continue
        out.append((base, m.start()))
    return out


def diagnose_edge(caller: str, callee: str, checkout_root: Path):
    """Return dict with mechanism, matched source line/snippet (or reason for a residual)."""
    name = member_of(callee).rpartition(".")[2] or member_of(callee)
    c_ln = line_of(caller)
    c_file = file_of(caller)
    if c_ln is None:
        return {"mech": "M9", "reason": "caller_key_has_no_line_number", "snippet": None}
    lines = read_lines(checkout_root, c_file)
    if not lines:
        return {"mech": "M9", "reason": "caller_source_file_missing_or_unreadable", "snippet": None}
    body = extract_body(lines, c_ln)
    if body is None:
        return {"mech": "M9", "reason": "caller_body_not_resolvable_bad_start_line", "snippet": None}

    header_end = body.find("{")
    header = body[: header_end + 1] if header_end != -1 else body[:200]
    params = extract_params(header)

    # -- PRIORITY 1: direct NAME match (high confidence -- the callee's own label is the text
    # actually written at the call site). Covers M1/M2/M3/M5/M6/M7 exactly as round 1.
    direct = classify_call_site(body, name, params)
    if direct is not None:
        mech, offset, snippet = direct
        ln = line_no_for_offset(body, offset, c_ln)
        return {"mech": mech, "line": ln, "snippet": snippet}

    cb = classify_callback_pass(body, name)
    if cb is not None:
        mech, offset, snippet = cb
        ln = line_no_for_offset(body, offset, c_ln)
        return {"mech": "M6", "line": ln, "snippet": snippet}

    # -- No name match. Fall back to STRUCTURAL classification: what kind of binding is being
    # called in this body at all, regardless of whether its text matches the callee's label.
    body_line_count = body.count("\n") + 1
    calls = get_all_calls(body)

    if not calls:
        return {"mech": "M12", "reason": "caller_body_has_no_call_expression_at_all", "snippet": None}

    # PRIORITY 2: M4 -- call of the caller's OWN parameter (true higher-order: the function
    # arrived as an argument, so its identity is invisible from the caller's declaration text).
    for base, offset in calls:
        if base in params:
            ln = line_no_for_offset(body, offset, c_ln)
            return {"mech": "M4", "line": ln, "snippet": line_text_at(body, offset)}

    # PRIORITY 3: M10 -- call through a local function-valued variable (declared with const/let
    # either inside the caller's own body, or in the immediately enclosing scope -- the `const
    # originalRun = inst._zod?.run; ... const shim = (...) => { ... originalRun(...) }` shape).
    locals_ = local_bindings_near(lines, c_ln, body_line_count)
    for base, offset in calls:
        if base in locals_:
            ln = line_no_for_offset(body, offset, c_ln)
            return {"mech": "M10", "line": ln, "snippet": line_text_at(body, offset)}

    # PRIORITY 4: M11 -- call through an imported binding (namespace import, named import, or
    # default import) whose own name doesn't match the callee's label.
    imports = imports_of(checkout_root, c_file)
    for base, offset in calls:
        if base in imports:
            ln = line_no_for_offset(body, offset, c_ln)
            return {"mech": "M11", "line": ln, "snippet": line_text_at(body, offset)}

    # PRIORITY 5 (residual): the body DOES contain call expressions, but none of them route
    # through a parameter, a nearby local binding, or an import -- e.g. calls only on `this`,
    # on values, or on names we can't place. Honest residual, not a story.
    base, offset = calls[0]
    ln = line_no_for_offset(body, offset, c_ln)
    return {
        "mech": "M8",
        "reason": "caller_body_has_calls_but_none_route_through_param_local_or_import",
        "snippet": line_text_at(body, offset),
        "line": ln,
    }


MECH_LABELS = {
    "M1": "bare identifier call            foo(...)  [name-matched]",
    "M2": "property call on `this`         this.foo(...) / this._zod.foo(...)  [name-matched]",
    "M3": "property call on a value        x.foo(...)  [name-matched]",
    "M4": "call of a PARAMETER             callee reached via caller's own param (true higher-order)",
    "M5": "call through element access     x[k](...) / computed key  [name-matched]",
    "M6": "call of an immediately-passed callback   arr.map(foo) / expect(foo)  [name-matched]",
    "M7": "constructor / new X(...)  [name-matched]",
    "M8": "body HAS call expressions, but none route through param/local/import (RESIDUAL)",
    "M9": "caller source unreadable / body not resolvable",
    "M10": "call through a LOCAL function-valued variable (const/let, own body or enclosing scope)",
    "M11": "call through an IMPORTED binding (namespace/named/default import)",
    "M12": "caller body contains NO call expression at all (oracle/arm caller-attribution mismatch)",
}
MECH_ORDER = ["M1", "M2", "M3", "M5", "M6", "M7", "M4", "M10", "M11", "M12", "M8", "M9"]
PRIORITY_NOTE = (
    "Classification priority (first match wins, mutually exclusive):\n"
    "  1. M9  caller source/body unresolvable\n"
    "  2. name-match against the callee's own label text -> M1/M2/M3/M5/M6/M7\n"
    "  3. M4  call routes through one of the caller's OWN PARAMETERS\n"
    "  4. M10 call routes through a LOCAL const/let binding (own body or enclosing scope, up to 200 lines back)\n"
    "  5. M11 call routes through an IMPORTED binding\n"
    "  6. M12 caller body contains no call expression at all\n"
    "  7. M8  caller body has calls, but none satisfy 3-5 (residual)"
)


def main() -> int:
    run_dir = Path(sys.argv[1])
    checkout_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if checkout_root is None:
        print("FATAL: checkout_root required")
        return 1

    bucket3 = rebuild_bucket3(run_dir, checkout_root)
    total = len(bucket3)
    print(f"bucket 3 (3_true_resolve_gap_both_known): {total}")
    assert total == 9827, f"bucket 3 size drifted from expected 9827: got {total}"

    print("\n" + "=" * 90)
    print("ROUND 2: CALLER-SIDE STRUCTURAL CLASSIFICATION")
    print("=" * 90)
    print(PRIORITY_NOTE)

    # -- sanity check demanded before reporting anything: M4 MUST fire on the known
    # ZodError.flatten(mapper) -> anonL116 example (the exact case round 1 mis-swept into M8).
    known_caller = "packages/zod/src/v3/ZodError.ts::ZodError.flatten:308"
    known_callee = "packages/zod/src/v3/tests/all-errors.test.ts::anonL116:116"
    sanity = diagnose_edge(known_caller, known_callee, checkout_root)
    print(f"\nSANITY CHECK -- {known_caller} -> {known_callee}")
    print(f"  round-2 mechanism: {sanity['mech']}  line={sanity.get('line')}  snippet={sanity.get('snippet')!r}")
    assert sanity["mech"] == "M4", (
        f"REGRESSION: expected M4 (mapper is flatten's own parameter), got {sanity['mech']} -- "
        f"the structural fallback is broken, do not trust the table below"
    )
    print("  PASS -- M4 fires as required.\n")

    counts: Counter = Counter()
    m8_reasons: Counter = Counter()
    m8_reason_examples: dict[str, list] = {}
    m12_examples: list = []
    examples: dict[str, list] = {m: [] for m in MECH_ORDER}

    for caller, callee in bucket3:
        result = diagnose_edge(caller, callee, checkout_root)
        mech = result["mech"]
        counts[mech] += 1
        if mech == "M8":
            reason = result["reason"]
            m8_reasons[reason] += 1
            if len(m8_reason_examples.setdefault(reason, [])) < 15:
                m8_reason_examples[reason].append((caller, callee))
        if mech == "M12" and len(m12_examples) < 15:
            m12_examples.append((caller, callee))
        if len(examples[mech]) < 8:
            examples[mech].append((caller, callee, result.get("line"), result.get("snippet"), result.get("reason")))

    check = sum(counts.values())
    assert check == 9827, f"mechanism counts do not sum to 9827: got {check}"

    print("=" * 90)
    print(f"CALL-SITE MECHANISM TABLE (ROUND 2) -- {total} bucket-3 edges")
    print("=" * 90)
    for mech in MECH_ORDER:
        n = counts.get(mech, 0)
        pct = 100 * n / total
        print(f"  {mech:4s}{n:6d}  ({pct:5.2f}%)  {MECH_LABELS[mech]}")
    print(f"\nSUM: {check} (must equal {total})")
    residual_n = counts.get("M8", 0) + counts.get("M9", 0)
    print(f"\nRESIDUAL (M8+M9, still genuinely unexplained by this heuristic): {residual_n}  "
          f"({100*residual_n/total:.2f}% of bucket 3)")
    print(f"M12 (caller body has NO call expression at all -- oracle/arm caller-attribution "
          f"mismatch, a separate real signal, not folded into the residual above): "
          f"{counts.get('M12', 0)}  ({100*counts.get('M12', 0)/total:.2f}%)")

    if m12_examples:
        print("\n15 examples of M12 (caller body has no call expression at all):")
        for caller, callee in m12_examples:
            csrc = read_line(checkout_root, caller)
            esrc = read_line(checkout_root, callee)
            print(f"  CALLER: {caller}")
            print(f"    decl: {csrc.strip() if csrc else '(unreadable)'}")
            print(f"  CALLEE: {callee}")
            print(f"    decl: {esrc.strip() if esrc else '(unreadable)'}")
            print()

    if m8_reasons:
        m8_total = sum(m8_reasons.values())
        print("\n" + "=" * 90)
        print(f"M8 RESIDUAL REASON BREAKDOWN -- {m8_total} edges ({100*m8_total/total:.2f}% of bucket 3)")
        print("=" * 90)
        for reason, n in m8_reasons.most_common():
            print(f"  {n:6d}  ({100*n/m8_total:5.2f}%)  {reason}")
        print("\n15 examples per M8 reason:")
        for reason, _ in m8_reasons.most_common():
            print(f"\n--- reason: {reason} ---")
            for caller, callee in m8_reason_examples.get(reason, [])[:15]:
                csrc = read_line(checkout_root, caller)
                esrc = read_line(checkout_root, callee)
                print(f"  CALLER: {caller}")
                print(f"    decl: {csrc.strip() if csrc else '(unreadable)'}")
                print(f"  CALLEE: {callee}")
                print(f"    decl: {esrc.strip() if esrc else '(unreadable)'}")
                print()

    print("\n" + "=" * 90)
    print("8 EXAMPLES PER MECHANISM")
    print("=" * 90)
    for mech in MECH_ORDER:
        print(f"\n--- {mech}: {MECH_LABELS[mech]} ---")
        if not examples[mech]:
            print("  (no examples -- 0 edges in this mechanism)")
            continue
        for caller, callee, ln, snippet, reason in examples[mech]:
            print(f"  CALLER: {caller}")
            print(f"  CALLEE: {callee}")
            if snippet is not None:
                print(f"  CALL SITE (line {ln}): {snippet}")
            elif reason is not None:
                print(f"  REASON: {reason}")
            print()

    print("DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
