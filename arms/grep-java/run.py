#!/usr/bin/env python3
"""The grep-java arm: what an agent with only text search can know about Java calls.

Sister of arms/grep (same two passes, same candidate fan-out, same rules in spirit), re-spelled for
Java syntax because the C# arm is a state machine over C# syntax and is not edited for another
language. Written from the C# arm and the oracle's key spelling (oracle/java/README.md) before any
Java number was seen; nothing project-specific.

Pass 1, declarations: per file, `package`, nesting of named types by brace counting, and every
method-shaped declaration (modifiers, optional generics, return type, name, `(`) plus constructors
(a name equal to the enclosing type) and `static {` blocks (`.cctor`). Pass 2, call sites: every
`name(` that is not a declaration becomes one edge PER candidate declaration sharing the name; `new
Foo(` becomes one `.ctor` edge per type named Foo. The caller is the nearest declaration above the
call line (lambda bodies therefore fold into their method, as the oracle does; anonymous-class
bodies fold into the enclosing method, which the oracle does not).

Scope: the corpus entry's `java_packages` (the oracle's own definition of a project class) decide
which files are read, for declarations and calls alike. Library methods are not candidates, so no
edge into a library type is made (the oracle drops those too).

Known blind spots, left as they are: no implicit default constructors or implicit `super()` edges,
no method references, no field initializers (they attach to whatever declaration is above them),
no overload resolution (the key collapses overloads anyway).
"""

from __future__ import annotations

import re
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import armkit  # noqa: E402
import javascan  # noqa: E402

MODS = ("public|private|protected|static|final|abstract|synchronized|native|default|strictfp|"
        "transient|volatile")
RET = r"[A-Za-z_$][\w$.]*(?:<[^(){};=]*>)?(?:\[\])*"
DECL_RE = re.compile(
    r"^\s*(?:@\w+(?:\([^()]*\))?\s+)*"
    rf"(?P<mods>(?:(?:{MODS})\s+)*)"
    r"(?:<[^(){};=]*>\s+)?"
    rf"(?:(?P<ret>{RET})\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"\((?P<params>[^()]*)(?:\)|$)"
)
STATIC_INIT_RE = re.compile(r"^\s*static\s*\{")
STATEMENT_LEAD = {"return", "throw", "yield", "else", "case", "new", "assert", "do", "try",
                  "class", "interface", "enum", "record", "package", "import", "extends",
                  "implements", "instanceof"}
CONTROL = {"if", "while", "for", "switch", "catch", "synchronized", "return", "new", "this",
           "super", "try", "assert", "throw", "else", "do", "yield", "case"}
CALL_RE = re.compile(r"(?<![\w$@])([A-Za-z_$][\w$]*)\s*\(")
NEW_RE = re.compile(r"\bnew\s+([A-Za-z_$][\w$.]*)\s*(?:<[^(){};]*>)?\s*\(")


@dataclass
class Decl:
    line: int
    type_key: str
    method: str
    name_col: int


@dataclass
class FileScan:
    lines: list[str]
    decls: list[Decl]
    decl_cols: dict[int, int]
    types: set[tuple[str, str]]


def scan_file(clean: str) -> FileScan:
    lines = clean.split("\n")
    pkg = javascan.package_of(clean)
    tr = javascan.TypeTracker()
    decls: list[Decl] = []
    cols: dict[int, int] = {}
    types: set[tuple[str, str]] = set()
    for idx, line in enumerate(lines):
        no = idx + 1
        tr.start_line(line)
        chain = tr.chain
        if chain:
            tkey = javascan.type_spelling(pkg, chain)
            if STATIC_INIT_RE.match(line):
                decls.append(Decl(no, tkey, ".cctor", 0))
            m = DECL_RE.match(line)
            if m and tr.pending is None:
                name, ret = m.group("name"), m.group("ret")
                mods = m.group("mods").split()
                simple = chain[-1]
                if name not in CONTROL and ret not in STATEMENT_LEAD:
                    if name == simple and ret is None:
                        decls.append(Decl(no, tkey, ".ctor", m.start("name"))); cols[no] = m.start("name")
                    elif ret is not None or mods:
                        decls.append(Decl(no, tkey, name, m.start("name"))); cols[no] = m.start("name")
        for opened in tr.end_line(line):
            parts = opened.split("/")
            types.add((javascan.type_spelling(pkg, parts), parts[-1]))
    return FileScan(lines, decls, cols, types)


def emit_calls(rel: str, scan: FileScan, methods, types) -> Iterator[armkit.Edge]:
    starts = [d.line for d in scan.decls]
    for idx, line in enumerate(scan.lines):
        no = idx + 1
        if "(" not in line:
            continue
        i = bisect_right(starts, no) - 1
        if i < 0:
            continue
        caller = scan.decls[i]
        ckey = f"{caller.type_key}::{caller.method}"
        skip: set[int] = set()
        if no in scan.decl_cols:
            skip.add(scan.decl_cols[no])
        for m in NEW_RE.finditer(line):
            skip.add(m.start(1))
            for tkey in sorted(types.get(m.group(1).rsplit(".", 1)[-1], ())):
                yield armkit.Edge(ckey, f"{tkey}::.ctor", rel, no)
        for m in CALL_RE.finditer(line):
            name = m.group(1)
            if m.start(1) in skip or name in CONTROL:
                continue
            for tkey in sorted(methods.get(name, ())):
                yield armkit.Edge(ckey, f"{tkey}::{name}", rel, no)


def corpus_packages(repo_root: Path):
    for entry in armkit._load_corpus_entries():
        if str(entry.get("name", "")).split("/")[-1] == repo_root.name:
            return tuple(entry.get("java_packages") or ()) or None
    return None


def collect(repo_root: Path, cell: str) -> Iterator[armkit.Edge]:
    packages = corpus_packages(repo_root)
    scans: dict[Path, FileScan] = {}
    methods: dict[str, set[str]] = defaultdict(set)
    types: dict[str, set[str]] = defaultdict(set)
    for path in armkit.source_files(repo_root, cell, ".java"):
        try:
            clean = javascan.strip_java(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        pkg = javascan.package_of(clean)
        if packages and not javascan.in_packages(pkg, packages):
            continue
        scan = scan_file(clean)
        scans[path] = scan
        for d in scan.decls:
            if d.method not in (".ctor", ".cctor"):
                methods[d.method].add(d.type_key)
        for tkey, simple in scan.types:
            types[simple].add(tkey)
    for path, scan in scans.items():
        yield from emit_calls(path.relative_to(repo_root).as_posix(), scan, methods, types)


def version() -> str:
    return "pure-python-java-1"


if __name__ == "__main__":
    sys.exit(armkit.main(name="grep-java", version=version, collect=collect))
