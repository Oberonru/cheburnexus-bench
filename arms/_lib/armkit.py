"""Shared plumbing for arms, so that three implementations cannot drift into three dialects.

An arm's job is to answer one question — which calls does this tool believe exist — and nothing
here decides that. What lives here is the boring part every arm would otherwise reimplement
slightly differently: the CLI shape, the cell split, the row writer, and the `--describe` line.

Nothing in this module may read the oracle, the override map, or any graded result. An arm that
could see the answer key would be able to score against it instead of being measured by it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

CELLS = ("with-tests", "without-tests")

# Directory names that hold test code in every C# repository we have looked at. Kept as a shared
# default so all arms split a repository the same way; a corpus entry may override it.
TEST_DIR_MARKERS = ("test", "tests", "spec", "specs", "benchmark", "benchmarks", "samples")


@dataclass(frozen=True)
class Edge:
    """One call the arm believes exists. Fields it cannot fill are left out, never guessed."""

    caller: str
    callee: str
    caller_file: str | None = None
    caller_line: int | None = None

    def to_row(self) -> dict:
        row: dict = {"caller": self.caller, "callee": self.callee}
        if self.caller_file is not None:
            row["caller_file"] = self.caller_file
        if self.caller_line is not None:
            row["caller_line"] = self.caller_line
        return row


def _is_test_dir_name(part: str) -> bool:
    """True when one lowercased path segment names a test directory.

    Two shapes count, both directory-naming conventions rather than anything file-specific:
    the whole segment (`test/`, `spec/`), or its final dot-delimited component (a project folder
    named `FluentValidation.Tests` or `FluentValidation.Tests.Benchmarks`, where the marker is a
    dotted suffix on the project name rather than the whole directory name). A plain substring
    check would also catch `Contest/` or `Latest/`, which are not test directories at all — this
    only matches at a `.`-boundary or the start of the segment, never mid-word.
    """
    return part in TEST_DIR_MARKERS or part.rsplit(".", 1)[-1] in TEST_DIR_MARKERS


def is_test_path(path: Path, repo_root: Path) -> bool:
    """True when a file belongs to the repository's tests.

    Judged on directory names rather than file names: `FooTests.cs` sitting in `src/` is production
    code that happens to be named awkwardly, while everything under `test/` is not. The rule is
    shared by every arm so that the two cells mean the same thing in all three columns.

    2026-08-24: extended to also catch a dot-suffixed project directory name (`X.Tests`,
    `X.Tests.Benchmarks`) alongside the original whole-segment match (`test/`) — see the
    "Deviations" entry dated 2026-08-24 in `PREREGISTRATION-e1-csharp-edge-precision.md` for why.
    """
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        return False
    return any(_is_test_dir_name(part.lower()) for part in relative.parts[:-1])


def source_files(repo_root: Path, cell: str, suffix: str = ".cs") -> Iterator[Path]:
    """Every source file this cell should see, in a stable order.

    Sorted, because an arm that walks the filesystem in directory order produces different output
    on different machines, and a diff between two runs must mean a real change.
    """
    if cell not in CELLS:
        raise ValueError(f"unknown cell {cell!r}; expected one of {CELLS}")

    for path in sorted(repo_root.rglob(f"*{suffix}")):
        parts = {p.lower() for p in path.parts}
        if "obj" in parts or "bin" in parts or ".git" in parts:
            continue
        if cell == "without-tests" and is_test_path(path, repo_root):
            continue
        yield path


def write_edges(out_path: Path, edges: Iterable[Edge]) -> int:
    """Write the contract rows, deduplicated and ordered. Returns how many were written.

    Deduplicated because an arm reporting the same call twice is telling us one fact, not two, and
    the grader compares sets. Ordered so two runs of the same arm on the same input produce
    byte-identical files — which is what makes a published row set checkable.
    """
    unique = {(e.caller, e.callee, e.caller_file, e.caller_line) for e in edges}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        for caller, callee, file, line in sorted(
            unique, key=lambda t: (t[0], t[1], t[2] or "", t[3] or 0)
        ):
            handle.write(json.dumps(Edge(caller, callee, file, line).to_row()) + "\n")
    return len(unique)


@dataclass(frozen=True)
class Coverage:
    """What the arm knows about the limits of its own answer.

    Precision and recall alone cannot tell two very different behaviours apart. A tool that saw a
    call site and could not resolve the target, and a tool that never noticed the site at all, both
    score the same missing edge — yet only one of them told the truth about its own ignorance.

    Our engine already draws this distinction internally (`CallGraphCoverage.cs`: `is_exact`,
    `reason`, `unresolved_call_sites`, `budget_exhausted`), and the edge contract was throwing it
    away. Reported as its own column, never folded into recall: a low recall that comes with a
    declared unresolved count is a different claim from a low recall that comes with silence.

    Declared here BEFORE our own arm has produced a single number, so that it cannot be mistaken
    for a category invented to rescue a score.
    """

    is_exact: bool | None = None
    reason: str = ""
    unresolved_call_sites: int | None = None
    budget_exhausted_sites: int | None = None
    note: str = ""

    def to_row(self) -> dict:
        row: dict = {}
        for key, value in (("is_exact", self.is_exact), ("reason", self.reason),
                           ("unresolved_call_sites", self.unresolved_call_sites),
                           ("budget_exhausted_sites", self.budget_exhausted_sites),
                           ("note", self.note)):
            if value not in (None, ""):
                row[key] = value
        return row


def coverage_path(out_path: Path) -> Path:
    """Where an arm's coverage sidecar lives: beside its edges, same stem."""
    return out_path.with_suffix(out_path.suffix + ".coverage.json")


def write_coverage(out_path: Path, coverage: Coverage) -> None:
    coverage_path(out_path).write_text(json.dumps(coverage.to_row(), indent=2), encoding="utf-8")


class Blocked(Exception):
    """The tool ran but refused to produce the data — a licence wall, a disabled feature.

    This is NOT "found nothing". An arm that was refused and an arm that searched and came back
    empty are different facts, and collapsing them publishes a false statement about the tool: a
    recall of 0.000 next to our own engine would say it looked and failed, when it never looked.
    Raise this and the runner records a gap with the reason instead of a zero.
    """


EXIT_BLOCKED = 3


def main(
    name: str,
    version: Callable[[], str],
    collect: Callable[[Path, str], Iterable[Edge]],
    mode: str = "live",
) -> int:
    """The CLI every arm shares. `collect(repo_root, cell)` is the only part an arm writes itself."""
    parser = argparse.ArgumentParser(description=f"{name} arm")
    parser.add_argument("--repo", type=Path, help="checkout to read")
    parser.add_argument("--cell", choices=CELLS)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--describe", action="store_true",
                        help="print this arm's identity as one JSON object and exit")
    args = parser.parse_args()

    if args.describe:
        print(json.dumps({"name": name, "version": version(), "mode": mode}))
        return 0

    missing = [flag for flag, value in
               (("--repo", args.repo), ("--cell", args.cell), ("--out", args.out)) if value is None]
    if missing:
        parser.error("missing required arguments: " + ", ".join(missing))

    repo_root = args.repo.resolve()
    if not repo_root.is_dir():
        print(f"{name}: no such checkout: {repo_root}", file=sys.stderr)
        return 2

    try:
        produced = collect(repo_root, args.cell)
        # An arm may hand back edges alone, or edges plus what it knows about its own blind spots.
        if isinstance(produced, tuple):
            edges, coverage = produced
        else:
            edges, coverage = produced, None
        written = write_edges(args.out, edges)
        if coverage is not None:
            write_coverage(args.out, coverage)
    except Blocked as blocked:
        print(f"{name}: BLOCKED — {blocked}", file=sys.stderr)
        return EXIT_BLOCKED

    # An arm that finds nothing has answered the question — badly, but it has answered. Only an arm
    # that could not run at all fails, or a zero would be indistinguishable from a crash.
    print(f"{name}: {written} edges -> {args.out}", file=sys.stderr)
    return 0
