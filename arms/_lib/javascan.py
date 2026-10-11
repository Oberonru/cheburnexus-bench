"""Plain-text Java scanning shared by the Java sister arms (grep-java, repowise-java).

No parser and no oracle: a character state machine that blanks comments and string/char literals
(keeping every newline, so line numbers hold), and a brace-counting scan that names the types a file
declares in the oracle's spelling (`pkg.Outer/Inner`, see oracle/java/README.md).
"""

from __future__ import annotations

import re

TYPE_RE = re.compile(
    r"^\s*(?:@\w+(?:\([^()]*\))?\s+)*(?:(?:public|private|protected|static|final|abstract|sealed|"
    r"non-sealed|strictfp)\s+)*(?:class|interface|enum|record|@interface)\s+(?P<name>[A-Za-z_$][\w$]*)")
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)


def strip_java(text: str) -> str:
    """Comments, string literals, text blocks and char literals -> spaces; newlines kept."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j])); i = j
        elif text.startswith('"""', i):
            j = i + 3
            while j < n and not text.startswith('"""', j):
                j += 2 if text[j] == "\\" else 1
            j = min(n, j + 3)
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j])); i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(n, j + 1)
            out.append(" " * (j - i)); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def package_of(clean: str) -> str:
    m = PACKAGE_RE.search(clean)
    return m.group(1) if m else ""


def type_spelling(package: str, chain: list[str]) -> str:
    path = "/".join(chain)
    return f"{package}.{path}" if package else path


def in_packages(type_key: str, packages) -> bool:
    return any(type_key == p or type_key.startswith(p + ".") for p in packages)


class TypeTracker:
    """Feed lines in order; `chain` is the enclosing named-type path at the current point."""

    def __init__(self) -> None:
        self.depth = 0
        self.stack: list[tuple[str, int]] = []
        self.pending: str | None = None

    @property
    def chain(self) -> list[str]:
        return [n for n, _ in self.stack]

    def start_line(self, line: str) -> None:
        if self.pending is None:
            m = TYPE_RE.match(line)
            if m:
                self.pending = m.group("name")

    def end_line(self, line: str) -> list[str]:
        """Process braces of the line. Returns the chains of types opened on it."""
        opened: list[str] = []
        for ch in line:
            if ch == "{":
                self.depth += 1
                if self.pending is not None:
                    self.stack.append((self.pending, self.depth))
                    opened.append("/".join(self.chain))
                    self.pending = None
            elif ch == "}":
                if self.stack and self.stack[-1][1] == self.depth:
                    self.stack.pop()
                self.depth = max(0, self.depth - 1)
        return opened


def declared_types(clean: str) -> tuple[str, dict[str, list[str]]]:
    """(package, simple name -> [oracle-spelled type keys]) for every named type in a file."""
    pkg = package_of(clean)
    tr = TypeTracker()
    found: dict[str, list[str]] = {}
    for line in clean.split("\n"):
        tr.start_line(line)
        for chain in tr.end_line(line):
            simple = chain.rsplit("/", 1)[-1]
            found.setdefault(simple, []).append(type_spelling(pkg, chain.split("/")))
    return pkg, found
