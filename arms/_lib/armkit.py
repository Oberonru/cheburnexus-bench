"""Shared plumbing for arms, so that three implementations cannot drift into three dialects.

An arm's job is to answer one question — which calls does this tool believe exist — and nothing
here decides that. What lives here is the boring part every arm would otherwise reimplement
slightly differently: the CLI shape, the cell split, the row writer, and the `--describe` line.

Nothing in this module may read the oracle, the override map, or any graded result. An arm that
could see the answer key would be able to score against it instead of being measured by it.
`corpus.json`'s `product_projects`/`test_assemblies_projects` fields are the one documented
exception (see `corpus_scope_dirs` below): they are the corpus's own structural description of
which directories are its product, not a graded result an arm could tune itself against.
"""

from __future__ import annotations

import argparse
import functools
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


# ── first-party scope, derived from corpus.json ─────────────────────────────────────────────────
#
# Defect #10 (found 2026-08-24): every arm walked the WHOLE checkout. `is_test_path` keeps test
# code out of the without-tests cell, but nothing kept a sibling, non-first-party project out of
# EITHER cell. Polly's pinned checkout carries `src/Polly/` — the legacy pre-Polly.Core library,
# a different assembly, excluded from the corpus (see `corpus.json`'s `build_caveat`) — sharing
# the `Polly` namespace with `Polly.Core`. Every arm scanned it anyway: on the 2026-08-24 run, 369
# of the cheburnexus arm's 1005 emitted edges originated in `src/Polly/`, all with a `Polly`-
# prefixed callee, so the grader's key-based cell split could not tell them from genuine
# `Polly.Core` callees and routed all 369 into the primary comparable cell as forced false
# positives (716 primary-cell edges, 371 matched -> 345 false positives, against 369 candidates —
# the numbers line up). All three arms collapsed on Polly for exactly this reason.
#
# The fix is not "exclude src/Polly" — that would be hand-picking a directory for one repository.
# `corpus.json` already declares, per repository, which projects ARE the product
# (`product_projects`) and which are its tests (`test_assemblies_projects`); the arms just never
# consulted that declaration when deciding which source to read. This makes that declaration the
# scope, for every repository the same way, with no per-repo special case.

CORPUS_JSON_PATH = Path(__file__).resolve().parents[2] / "corpus.json"


def _strip_trailing_note(entry: str) -> str:
    """corpus.json sometimes appends a parenthetical aside after a path
    (`"test/TestDummies/TestDummies.csproj (test helper, not itself a suite)"`). Strip it back to
    the bare path so a directory can be derived from it."""
    return entry.split(" (", 1)[0].strip()


def _project_dir(csproj_relative: str) -> str | None:
    """The repo-relative directory a listed `.csproj` path implies, or None when it has none.

    A project file living directly at the repository root has no directory prefix this scheme can
    express (an empty prefix would match every file in the checkout) — that project is reported as
    unscopable by its caller rather than silently granted, or silently denied, the run of the repo.
    None of the three corpus repositories hit this today; every listed project lives at least one
    directory below the checkout root.
    """
    cleaned = _strip_trailing_note(csproj_relative).replace("\\", "/")
    if "/" not in cleaned:
        return None
    return cleaned.rsplit("/", 1)[0]


def scope_dirs_from_entry(entry: dict, cell: str) -> list[str] | None:
    """The repo-relative directories this corpus entry declares as first-party, for one cell.

    Always the parent directories of `product_projects`. The `with-tests` cell additionally
    includes `test_assemblies_projects`, so test sources stay in scope where the cell means to
    grade them — whether TEST code is then excluded from a file is still `is_test_path`'s job; this
    function only says which projects belong to the repository's own declared surface at all, as
    opposed to a vendored, legacy, or sample project the corpus never claimed.

    Returns None when corpus.json does not record enough to answer. No invented rule, no directory
    guess: `product_projects` absent (or present but empty) means the caller must say so and fall
    back to unscoped, not silently narrow a repository nobody described.
    """
    product = entry.get("product_projects") or None
    if not product:
        return None

    dirs = {d for d in (_project_dir(p) for p in product) if d is not None}
    if cell == "with-tests":
        dirs |= {d for d in (_project_dir(p) for p in entry.get("test_assemblies_projects") or ())
                 if d is not None}
    return sorted(dirs)


def in_declared_scope(path: Path, repo_root: Path, scope_dirs: Iterable[str] | None) -> bool:
    """True when `path` sits inside one of `scope_dirs` — or unconditionally True when
    `scope_dirs` is None, meaning no scope could be derived (see `scope_dirs_from_entry`).

    Compared by path COMPONENT, never by string prefix: `scope_dirs=["src/Polly.Core"]` must not
    also match `src/Polly.Core.Tests`, and — the actual shape of defect #10 — `"src/Polly"` (a
    sibling project, same string prefix as every declared Polly.* directory, different assembly)
    must not match `src/Polly.Core/...` either. A naive `str.startswith` gets both of these wrong.
    """
    if scope_dirs is None:
        return True
    try:
        directory_parts = path.relative_to(repo_root).parts[:-1]  # directories only, not the file
    except ValueError:
        return False
    for scope in scope_dirs:
        scope_parts = Path(scope).parts
        if tuple(directory_parts[:len(scope_parts)]) == scope_parts:
            return True
    return False


@functools.lru_cache(maxsize=None)
def _load_corpus_entries() -> tuple[dict, ...]:
    try:
        raw = json.loads(CORPUS_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(raw if isinstance(raw, list) else raw.values())


_SCOPE_WARNED: set[tuple[str, str]] = set()


def _warn_scope_once(repo_name: str, reason: str) -> None:
    key = (repo_name, reason)
    if key in _SCOPE_WARNED:
        return
    _SCOPE_WARNED.add(key)
    print(f"armkit: cannot derive a first-party scope for {repo_name!r} ({reason}) — scanning "
          f"the checkout unscoped", file=sys.stderr)


@functools.lru_cache(maxsize=None)
def corpus_scope_dirs(repo_root: Path, cell: str) -> tuple[str, ...] | None:
    """`scope_dirs_from_entry`, looked up by matching `repo_root`'s directory name against
    `corpus.json`'s `name` field (the same match the runner uses: `entry["name"].split("/")[-1]`).

    A repository this cannot find in corpus.json at all — or finds but which has no
    `product_projects` recorded — is reported once on stderr and left unscoped: this function
    never invents a directory rule, it only reads the one corpus.json already ships with.
    """
    name = repo_root.name
    for entry in _load_corpus_entries():
        if str(entry.get("name", "")).split("/")[-1] == name:
            scope = scope_dirs_from_entry(entry, cell)
            if scope is None:
                _warn_scope_once(name, "corpus.json has no product_projects recorded for it")
                return None
            return tuple(scope)
    _warn_scope_once(name, "no entry named for it in corpus.json")
    return None


def in_scope(path: Path, repo_root: Path, cell: str) -> bool:
    """`in_declared_scope`, with the scope looked up from corpus.json for this repo and cell."""
    return in_declared_scope(path, repo_root, corpus_scope_dirs(repo_root, cell))


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
        if not in_scope(path, repo_root, cell):
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
