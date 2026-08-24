#!/usr/bin/env python3
"""The grep arm: the honest baseline an agent falls back to when it has no compiler-grade index.

Two regex passes over every source file the cell includes, and nothing else:

  Pass 1 — DECLARATIONS. Walk each file top to bottom, tracking namespace/type nesting by counting
  braces (not parsing C#), and record every method-shaped declaration: its declaring type
  (`Namespace.Type`, nested types as `Outer/Inner`) and its method name.

  Pass 2 — CALL SITES. Walk the same files again for `Name(` occurrences that are not themselves a
  declaration. Grep has no type system, so it cannot know which declaration of `Name` a given call
  site resolves to — it can only tell you "somewhere in this repository, N things are named `Name`".
  So every occurrence becomes one edge PER CANDIDATE: one edge to every declaration in the repo
  that shares the callee's method name (or, for `new Foo(...)`, every type named `Foo`). That
  candidate explosion is not a bug to be tuned away — it is the whole point of measuring this arm:
  it is exactly the junk an agent gets handed when it has nothing better than grep.

See RULES.md for the exact, pre-registered rules and every documented deviation from a literal
reading of the task brief.
"""

from __future__ import annotations

import re
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import armkit  # noqa: E402


# ── Pass 0: strip comments and string/char literals ─────────────────────────────────────────────
#
# A single character-level state machine over the WHOLE file (not per line), so that multi-line
# block comments and verbatim strings are handled correctly. Every stripped character is replaced
# by a space (newlines are kept as newlines) so that line numbers — and therefore `caller_line` —
# never shift relative to the original file.

def strip_comments_and_strings(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    state = "code"  # code | line_comment | block_comment | string | verbatim_string | char
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state, i = "line_comment", i + 2
                out.append("  ")
                continue
            if c == "/" and nxt == "*":
                state, i = "block_comment", i + 2
                out.append("  ")
                continue
            if c == "@" and nxt == '"':
                state, i = "verbatim_string", i + 2
                out.append("  ")
                continue
            if c == '"':
                state, i = "string", i + 1
                out.append(" ")
                continue
            if c == "'":
                state, i = "char", i + 1
                out.append(" ")
                continue
            out.append(c)
            i += 1
        elif state == "line_comment":
            out.append(c if c == "\n" else " ")
            if c == "\n":
                state = "code"
            i += 1
        elif state == "block_comment":
            if c == "*" and nxt == "/":
                out.append("  ")
                state, i = "code", i + 2
                continue
            out.append(c if c == "\n" else " ")
            i += 1
        elif state == "string":
            if c == "\\" and i + 1 < n:
                out.append("  ")
                i += 2
                continue
            out.append(c if c == "\n" else " ")
            if c == '"':
                state = "code"
            i += 1
        elif state == "verbatim_string":
            if c == '"' and nxt == '"':
                out.append("  ")
                i += 2
                continue
            out.append(c if c == "\n" else " ")
            if c == '"':
                state = "code"
            i += 1
        elif state == "char":
            if c == "\\" and i + 1 < n:
                out.append("  ")
                i += 2
                continue
            out.append(c if c == "\n" else " ")
            if c == "'":
                state = "code"
            i += 1
    return "".join(out)


# ── Pass 1: declarations ─────────────────────────────────────────────────────────────────────────

MODIFIERS = (
    "public|private|protected|internal|static|virtual|override|abstract|sealed|"
    "async|unsafe|extern|new|partial|readonly|volatile|explicit|implicit"
)
# A return type "token": identifier/dots/?/[] plus at most one generic argument list, which may
# itself contain spaces and commas (`Dictionary<string, object>`) — but not parens or braces, so
# we never run past the method's own parameter list.
RET_TOKEN = r"[A-Za-z_][\w.?\[\]]*(?:<[^(){}]*>)?"
# A declaration name: usually a plain identifier, but an explicit interface implementation is
# written `Interface.Method` — we accept the dotted chain and keep only the last segment (see
# RULES.md "Explicit interface implementations").
NAME_CHAIN = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"

DECL_RE = re.compile(
    r"^\s*(?:\[[^\[\]]*\]\s*)*"
    rf"(?P<mods>(?:(?:{MODIFIERS})\s+)*)"
    rf"(?:(?P<ret>{RET_TOKEN})\s+)?"
    rf"(?P<name>{NAME_CHAIN})"
    r"\s*(?:<[^(){}]*>)?\s*"
    r"\((?P<params>[^()]*)\)"
)

FILE_SCOPED_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)\s*;\s*$")
NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)")
TYPE_RE = re.compile(
    rf"^\s*(?:\[[^\[\]]*\]\s*)*(?:(?:{MODIFIERS})\s+)*"
    r"(?:class|struct|interface|record|enum)\s+"
    r"(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<(?P<generics>[^(){}]*)>)?"
)


def generic_arity(generics: str | None) -> int:
    """Count of type parameters in a type DECLARATION's own `<...>` list, e.g. `Cache<T>` -> 1,
    `Pair<K, V>` -> 2. Only ever called on the declaration itself, never on a use site — a use site
    like `new Cache<int>()` tells you nothing about how many parameters `Cache` was declared with,
    it only tells you how many were supplied here, which is not the same question and is not what
    this counts. Variance keywords (`in T`, `out T`) and attributes on a parameter don't change the
    count, so a plain top-level comma split is enough — a type parameter list never itself contains
    a nested `<...>` (that only happens in constraint clauses, which live outside this capture).
    """
    if not generics or not generics.strip():
        return 0
    return len([p for p in generics.split(",") if p.strip()])

# Names DECL_RE could capture that are really control-flow keywords, not declarations — either as
# the "name" (a bare `if (...)`) or as the mistaken "return type" of the next token (`return Foo()`
# would otherwise read as return-type `return`, name `Foo`).
# Tokens that can stand where a return type stands but can never BE one. Without this check
# `return Get(key);` parses as a declaration of a method named `Get`: the regex sees `return` as a
# one-word return type and `Get(` as the declarator. That invents declarations, and every invented
# declaration becomes another candidate for every call site sharing the name — inflating this arm's
# output with junk it never actually found. The error runs in our own favour, which is exactly why
# it has to go.
#
# Deliberately conservative: `ref` is excluded from this set because `ref int Foo()` is a real
# return type, and rejecting it would delete true declarations to kill false ones.
STATEMENT_LEAD_TOKENS = {
    "return", "throw", "yield", "await", "else", "case", "goto", "break", "continue",
    "in", "is", "as", "and", "or", "not",
}

CONTROL_WORDS = {
    "if", "while", "for", "foreach", "switch", "catch", "lock", "using", "return", "nameof",
    "typeof", "sizeof", "new", "else", "do", "fixed", "checked", "unchecked", "yield", "throw",
    "await", "when", "delegate", "this", "base",
}


@dataclass
class Decl:
    line: int          # 1-based
    type_key: str       # "Namespace.Type" or "Namespace.Outer/Inner"
    method: str          # bare method name ('.ctor' / '.cctor' for constructors)
    name_col: int        # 0-based char offset of the captured name, for call-site self-exclusion


@dataclass
class FileScan:
    lines: list[str]           # comment/string-stripped, one entry per source line (1:1 with file)
    decls: list[Decl]          # in ascending line order
    decl_lines: dict[int, int]  # line -> name_col, for excluding a decl's own "Name(" from calls
    types: set[tuple[str, str]]  # (type_key, simple name) for every type declared in this file


def classify_decl(m: re.Match, current_type: str | None, simple_type: str | None) -> str | None:
    """Return the bare method name this line declares, or None if it is not a declaration."""
    name_chain = m.group("name")
    name = name_chain.rsplit(".", 1)[-1]
    if name in CONTROL_WORDS:
        return None
    if current_type is None:
        return None  # a method-shaped line with no enclosing type — grep has nothing to attach it to

    mods = m.group("mods") or ""
    mod_tokens = mods.split()
    ret = m.group("ret")

    if ret in STATEMENT_LEAD_TOKENS:
        return None  # `return Get(key);`, `await Run();`, `yield Foo();` — statements, not declarations

    if name == simple_type and "." not in name_chain:
        # `new Foo(...)` as a discarded-result statement is also `mods="new "`, `ret=None`,
        # `name==Foo` when written inside Foo itself — indistinguishable from a constructor by
        # shape alone. A real constructor never carries a return type, and C# never spells a bare
        # `new` as the only word before an object-creation call either — but a lone `new` modifier
        # with no return type is never legal on a real declaration (ctors can't take `new`, and
        # every ordinary method needs a return type). So mods=={'new'} with ret absent is rejected.
        if mod_tokens == ["new"] and ret is None:
            return None
        return ".cctor" if "static" in mod_tokens else ".ctor"

    if mod_tokens == ["new"] and ret is None:
        return None  # see above: bare `new Foo();`, not a hiding-modifier declaration
    if not mod_tokens and not ret:
        return None  # bare `Name(...)` with nothing in front of it is a call statement, not a decl
    return name


def scan_file(cleaned_text: str) -> FileScan:
    lines = cleaned_text.split("\n")
    decls: list[Decl] = []
    decl_lines: dict[int, int] = {}
    types: set[tuple[str, str]] = set()  # (full type_key, simple name of the innermost type)

    brace_depth = 0
    namespace_stack: list[tuple[str, int]] = []  # (name, depth at which it was pushed)
    # (plain name as written on the constructor line, key-path name incl. `` `arity ``, depth)
    type_stack: list[tuple[str, str, int]] = []
    file_namespace: str | None = None
    # ('namespace', name) or ('type', plain_name, display_name) awaiting the next '{'
    pending: tuple[str, ...] | None = None

    def current_type_key() -> str | None:
        if not type_stack:
            return None
        ns_parts = [n for n, _ in namespace_stack]
        ns = file_namespace or (".".join(ns_parts) if ns_parts else None)
        types_path = "/".join(display for _, display, _ in type_stack)
        return f"{ns}.{types_path}" if ns else types_path

    def current_simple_type() -> str | None:
        # The PLAIN name — a source constructor is written `public Cache(...)`, never
        # `public Cache`1(...)`, so ctor detection must compare against the undecorated name.
        return type_stack[-1][0] if type_stack else None

    for idx, raw_line in enumerate(lines):
        line_no = idx + 1

        ns_match = FILE_SCOPED_NAMESPACE_RE.match(raw_line)
        if ns_match:
            file_namespace = ns_match.group(1)
        elif pending is None:
            m = NAMESPACE_RE.match(raw_line)
            if m:
                pending = ("namespace", m.group(1))
            else:
                m = TYPE_RE.match(raw_line)
                if m:
                    plain = m.group("name")
                    arity = generic_arity(m.group("generics"))
                    display = f"{plain}`{arity}" if arity else plain
                    pending = ("type", plain, display)

        decl_match = DECL_RE.match(raw_line)
        if decl_match:
            method = classify_decl(decl_match, current_type_key(), current_simple_type())
            if method is not None:
                tkey = current_type_key()
                assert tkey is not None
                decls.append(Decl(line_no, tkey, method, decl_match.start("name")))
                decl_lines[line_no] = decl_match.start("name")

        for ch in raw_line:
            if ch == "{":
                brace_depth += 1
                if pending is not None:
                    if pending[0] == "namespace":
                        namespace_stack.append((pending[1], brace_depth))
                    else:
                        _, plain, display = pending
                        type_stack.append((plain, display, brace_depth))
                        tkey = current_type_key()
                        if tkey:
                            types.add((tkey, plain))
                    pending = None
            elif ch == "}":
                if type_stack and type_stack[-1][2] == brace_depth:
                    type_stack.pop()
                elif namespace_stack and namespace_stack[-1][1] == brace_depth:
                    namespace_stack.pop()
                brace_depth = max(0, brace_depth - 1)

    return FileScan(lines, decls, decl_lines, types)


# ── Pass 2: call sites ───────────────────────────────────────────────────────────────────────────

CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
NEW_RE = re.compile(r"\bnew\s+([A-Za-z_][\w.]*)\s*(?:<[^(){}]*>)?\s*\(")

# Per the task brief, plus a few syntactically identical control-flow keywords we ran into on the
# real corpus (documented as a deviation in RULES.md): checked/unchecked/fixed take `(...)` exactly
# like a call would.
CALL_KEYWORD_EXCLUDE = {
    "if", "while", "for", "foreach", "switch", "catch", "lock", "using", "return", "nameof",
    "typeof", "sizeof", "new", "checked", "unchecked", "fixed", "this", "base",
}


def nearest_caller(decls: list[Decl], line_no: int) -> Decl | None:
    """The declaration whose line is the closest one at-or-above `line_no` (grep's model of scope:
    scan upward to the nearest declaration, no brace matching needed for this half)."""
    lines = [d.line for d in decls]
    idx = bisect_right(lines, line_no) - 1
    return decls[idx] if idx >= 0 else None


def collect(repo_root: Path, cell: str) -> Iterator[armkit.Edge]:
    files = list(armkit.source_files(repo_root, cell))

    scans: dict[Path, FileScan] = {}
    method_registry: dict[str, set[str]] = defaultdict(set)   # method name -> {type_key}
    type_registry: dict[str, set[str]] = defaultdict(set)     # simple type name -> {type_key}

    # Pass 1 over every file first, so the candidate registries are complete repo-wide before any
    # call site is resolved — an arm may not use partial, file-order-dependent knowledge.
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scan = scan_file(strip_comments_and_strings(text))
        scans[path] = scan
        for d in scan.decls:
            if d.method not in (".ctor", ".cctor"):
                method_registry[d.method].add(d.type_key)
        for tkey, simple_name in scan.types:
            type_registry[simple_name].add(tkey)

    # Pass 2: call sites, resolved against the now-complete registries.
    for path in files:
        scan = scans.get(path)
        if scan is None:
            continue
        rel = path.relative_to(repo_root).as_posix()
        yield from emit_calls(rel, scan, method_registry, type_registry)


def emit_calls(
    rel_path: str,
    scan: FileScan,
    method_registry: dict[str, set[str]],
    type_registry: dict[str, set[str]],
) -> Iterator[armkit.Edge]:
    for idx, line in enumerate(scan.lines):
        line_no = idx + 1
        if "(" not in line:
            continue
        caller_decl = nearest_caller(scan.decls, line_no)
        if caller_decl is None:
            continue  # a call above any declaration grep found — nothing to attribute it to
        caller_key = f"{caller_decl.type_key}::{caller_decl.method}"

        exclude_cols: set[int] = set()
        own_col = scan.decl_lines.get(line_no)
        if own_col is not None:
            exclude_cols.add(own_col)

        for m in NEW_RE.finditer(line):
            exclude_cols.add(m.start(1))
            simple = m.group(1).rsplit(".", 1)[-1]
            for tkey in sorted(type_registry.get(simple, ())):
                yield armkit.Edge(caller_key, f"{tkey}::.ctor", rel_path, line_no)

        for m in CALL_RE.finditer(line):
            if m.start(1) in exclude_cols:
                continue
            name = m.group(1)
            if name in CALL_KEYWORD_EXCLUDE:
                continue
            for tkey in sorted(method_registry.get(name, ())):
                yield armkit.Edge(caller_key, f"{tkey}::{name}", rel_path, line_no)


def version() -> str:
    return "pure-python-1"


if __name__ == "__main__":
    sys.exit(armkit.main(name="grep", version=version, collect=collect))
