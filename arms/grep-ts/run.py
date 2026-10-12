#!/usr/bin/env python3
"""The grep-ts arm: what an agent with only text search can know about TypeScript calls.

Sister of arms/grep-java (same two passes, same candidate fan-out), re-spelled for TypeScript and
the oracle's key `{file}::{Class.}{name}:{declaration line}[(get|set)]`. Written without looking at
any TypeScript score; nothing project-specific.

Pass 1, declarations (per file, comments/strings blanked, braces counted): `function name(`,
`const|let|var name = [async] function|(...) =>|x =>`, class members `[modifiers] name(`,
`name = (...) =>` class fields, `get|set name(` (tagged `(get)`/`(set)`), `constructor(`; the class
qualifier is the innermost open `class X`. Pass 2, call sites: every `name(` / `.name(` that is not
a declaration becomes one edge PER declaration sharing the bare name (text search cannot tell which
`validate` is meant); `new Foo(` becomes an edge to Foo's `constructor` declaration if it has one.
The caller is the innermost open declaration by brace depth, else `{file}::module-scope:0`.

Known blind spots, left as they are: anonymous closures fold into the enclosing declaration (the
oracle keys them `anonL<line>`), no type resolution, no overloads, no imports/aliases, no decorators
as calls, `.vue` files read as their `<script>` blocks only, a class without an explicit
constructor has no `new` target.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import armkit  # noqa: E402

SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue")
SKIP_PARTS = {"node_modules", "dist", "build", "coverage", ".git"}
CONTROL = {"if", "for", "while", "switch", "catch", "return", "function", "typeof", "await", "new",
           "super", "import", "require", "throw", "else", "do", "void", "delete", "in", "of",
           "yield", "case", "with", "get", "set", "async"}
IDENT = r"[A-Za-z_$][\w$]*"
MODS = r"(?:(?:public|private|protected|static|readonly|abstract|override|async|declare|export|default)\s+)*"
CLASS_RE = re.compile(rf"\bclass\s+({IDENT})")
FUNC_RE = re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*({IDENT})\s*[<(]")
ARROW = rf"(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|{IDENT}\s*=>)"
VAR_RE = re.compile(rf"^\s*(?:export\s+)?(?:const|let|var)\s+({IDENT})\s*(?::[^=]+)?=\s*{ARROW}")
MEMBER_RE = re.compile(rf"^\s*(?:@\w+(?:\([^)]*\))?\s*)*{MODS}(?P<acc>get\s+|set\s+)?\*?(?P<name>{IDENT})\s*\??\s*(?:<[^()]*>)?\(")
FIELD_FN_RE = re.compile(rf"^\s*{MODS}(?P<name>{IDENT})\s*(?::[^=]+)?=\s*{ARROW}")
CALL_RE = re.compile(rf"(?<![\w$])({IDENT})\s*(?:<[^()<>]*>)?\s*\(")
NEW_RE = re.compile(rf"\bnew\s+({IDENT})")


def blank(text: str) -> str:
    """Comments, string and template literal contents -> spaces; newlines kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                if c != "`" and text[j] == "\n":
                    break
                j += 2 if text[j] == "\\" else 1
            j = min(j, n)
            out.append(c + re.sub(r"[^\n]", " ", text[i + 1:j]) + (c if j < n and text[j] == c else ""))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


@dataclass
class Decl:
    line: int
    name: str
    cls: str | None
    tag: str  # "" | "(get)" | "(set)"

    @property
    def key_tail(self) -> str:
        return f"{self.cls + '.' if self.cls else ''}{self.name}:{self.line}{self.tag}"


def script_only(path: Path, text: str) -> str:
    if path.suffix != ".vue":
        return text
    out = [""] * (text.count("\n") + 1)
    for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", text, re.S):
        start = text.count("\n", 0, m.start(1))
        for k, ln in enumerate(m.group(1).split("\n")):
            out[start + k] = ln
    return "\n".join(out)


def scan(path: Path, text: str):
    """-> (declarations, call occurrences [(line, kind, name, caller Decl | None)])"""
    lines = blank(script_only(path, text)).split("\n")
    depth = 0
    stack: list[tuple[str, object, int]] = []   # (kind 'class'|'decl', Class name | Decl, body depth)
    pending: tuple[str, object] | None = None
    pending_age = 0
    decls: list[Decl] = []
    calls = []
    for idx, line in enumerate(lines):
        no = idx + 1
        cls = next((o for k, o, _ in reversed(stack) if k == "class"), None)
        top_is_class_body = bool(stack) and stack[-1][0] == "class" and stack[-1][2] == depth
        d = None
        col = -1
        m = FUNC_RE.match(line)
        if m:
            d, col = Decl(no, m.group(1), cls, ""), m.start(1)
        else:
            m = VAR_RE.match(line)
            if m:
                d, col = Decl(no, m.group(1), cls, ""), m.start(1)
            elif top_is_class_body:
                m = MEMBER_RE.match(line)
                if m and m.group("name") not in CONTROL:
                    acc = (m.group("acc") or "").strip()
                    d, col = Decl(no, m.group("name"), cls, f"({acc})" if acc else ""), m.start("name")
                else:
                    m = FIELD_FN_RE.match(line)
                    if m:
                        d, col = Decl(no, m.group("name"), cls, ""), m.start("name")
        cm = CLASS_RE.search(line)
        if d is not None:
            decls.append(d)
        outer = next((o for k, o, _ in reversed(stack) if k == "decl"), None)
        skip = {col} if col >= 0 else set()
        for nm in NEW_RE.finditer(line):
            skip.add(nm.start(1))
            calls.append((no, "new", nm.group(1), d if d is not None and nm.start() > col else outer))
        for cmm in CALL_RE.finditer(line):
            name = cmm.group(1)
            if cmm.start(1) in skip or name in CONTROL or (cm and cm.start(1) == cmm.start(1)):
                continue
            calls.append((no, "call", name, d if d is not None and cmm.start() > col else outer))
        if cm:
            pending, pending_age = ("class", cm.group(1)), 0
        elif d is not None:
            pending, pending_age = ("decl", d), 0
        for ch in line:
            if ch == "{":
                depth += 1
                if pending is not None:
                    stack.append((pending[0], pending[1], depth))
                    pending = None
            elif ch == "}":
                while stack and stack[-1][2] == depth:
                    stack.pop()
                depth -= 1
        if pending is not None:
            pending_age += 1
            if line.rstrip().endswith(";") or pending_age > 8 or (pending[0] == "decl" and "=>" in line and "{" not in line.split("=>", 1)[1]):
                pending = None
    return decls, calls


def collect(repo_root: Path, cell: str) -> Iterator[armkit.Edge]:
    scanned = []
    by_name: dict[str, list[str]] = defaultdict(list)
    ctors: dict[str, list[str]] = defaultdict(list)
    for path in armkit.source_files(repo_root, cell, SUFFIXES):
        rel_parts = path.relative_to(repo_root).parts
        if SKIP_PARTS & set(rel_parts) or path.name.endswith(".d.ts"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(repo_root).as_posix()
        decls, calls = scan(path, text)
        scanned.append((rel, calls))
        for d in decls:
            k = f"{rel}::{d.key_tail}"
            if d.name == "constructor" and d.cls:
                ctors[d.cls].append(k)
            else:
                by_name[d.name].append(k)
    for rel, calls in scanned:
        for no, kind, name, caller in calls:
            ck = f"{rel}::{caller.key_tail}" if caller else f"{rel}::module-scope:0"
            targets = ctors.get(name, ()) if kind == "new" else by_name.get(name, ())
            for t in targets:
                yield armkit.Edge(ck, t, rel, no)


def version() -> str:
    return "pure-python-ts-1"


if __name__ == "__main__":
    sys.exit(armkit.main(name="grep-ts", version=version, collect=collect))
