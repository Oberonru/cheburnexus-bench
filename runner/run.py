#!/usr/bin/env python3
"""Drive the whole measurement: build the answer key, run every arm, grade, and report.

The runner is deliberately dumb about what an arm does. It hands over a checkout and a cell name,
takes back a file of edges, and never looks inside. Everything that decides a number lives either
in the oracle (what is true) or the grader (who matched it) — a runner that started making
judgement calls would be a third place to hide one.

    python3 runner/run.py --checkouts corpus --out results/2026-08-23
    python3 runner/run.py --checkouts corpus --only serilog --arms grep
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
ORACLE_PROJECT = ROOT / "oracle" / "csharp"
GRADER = ROOT / "grader" / "grade.py"
PUBLISHED = ROOT / "results" / "published"
BLOCKED_EXIT = 3  # armkit.EXIT_BLOCKED — the arm ran but was refused the data
CELLS = ("without-tests", "with-tests")
DEFAULT_LANGUAGE = "csharp"  # corpus.json entries written before the language axis existed

sys.path.insert(0, str(ROOT / "arms" / "_lib"))
import armkit  # noqa: E402  (path inserted above, matching the existing _lib/__pycache__ convention)


@dataclass
class ArmRun:
    name: str
    mode: str              # live | replay | unavailable
    edges_path: Path | None = None
    describe: dict = field(default_factory=dict)
    seconds: float | None = None
    note: str = ""
    coverage: dict = field(default_factory=dict)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def load_corpus(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else list(data.values())


def repo_dir_name(entry: dict) -> str:
    return entry["name"].split("/")[-1]


def targets_framework(relative: str, wanted: str) -> bool:
    """Does this output path belong to the target framework we probed?

    Not a substring test on `/net8.0/`: repositories using the Arcade layout put their output in
    `artifacts/bin/<project>/release_net8.0/`, and a naive match dropped every assembly Polly
    builds — a third of the corpus vanishing from the table with one line on stderr. A path segment
    counts when it IS the framework or ends with it after a separator.
    """
    for segment in relative.replace("\\", "/").split("/"):
        if segment == wanted or segment.endswith(f"_{wanted}") or segment.endswith(f".{wanted}"):
            return True
    return False


def assemblies_for(entry: dict, checkout: Path, cell: str) -> list[Path]:
    """The assemblies the answer key is built from, for this cell.

    Only the probed target framework is used. A multi-targeted repository produces the same types
    several times over, and counting net6.0 and net8.0 copies of one method as different edges would
    inflate the answer key with duplicates no arm could ever match twice.
    """
    wanted = entry.get("target_framework_probed")
    chosen: list[Path] = []

    groups = [entry.get("product_assemblies", [])]
    if cell == "with-tests":
        groups.append(entry.get("test_assemblies", []))

    for group in groups:
        for relative in group:
            if wanted and not targets_framework(relative, wanted):
                continue
            candidate = checkout / relative
            if candidate.is_file():
                chosen.append(candidate)
    return chosen


class CellNotMeasurable(Exception):
    """This repository and cell cannot be measured, and must be reported rather than approximated."""


def unmatched_test_scope_projects(entry: dict, cell: str) -> list[str]:
    """`test_assemblies_projects` entries the arms are scoped to read for which `test_assemblies`
    lists no matching assembly at the cell's probed framework.

    Only `with-tests` consults `test_assemblies_projects` at all (see
    `armkit.scope_dirs_from_entry`), so a mismatch cannot affect `without-tests` and this always
    returns `[]` for it.

    Matched by PROJECT, not by string: a `test_assemblies_projects` entry is a `.csproj` path,
    sometimes with a trailing parenthetical note (`_strip_trailing_note`); a `test_assemblies` entry
    is a built `.dll` path several directories deeper under the same project directory
    (`_project_dir`). A project counts as covered when some framework-filtered `test_assemblies`
    entry's path starts with its directory, compared by path COMPONENT so `test/Serilog.Tests` does
    not also match a hypothetical sibling `test/Serilog.Tests.Something`.
    """
    if cell != "with-tests":
        return []
    projects = entry.get("test_assemblies_projects") or []
    if not projects:
        return []

    wanted = entry.get("target_framework_probed")
    covered_parts = [
        Path(a.replace("\\", "/")).parts
        for a in entry.get("test_assemblies", [])
        if not wanted or targets_framework(a, wanted)
    ]

    unmatched = []
    for project in projects:
        project_dir = armkit._project_dir(project)
        if project_dir is None:
            continue  # same "cannot express a directory" case armkit._project_dir documents
        dir_parts = Path(project_dir).parts
        if not any(parts[:len(dir_parts)] == dir_parts for parts in covered_parts):
            unmatched.append(armkit._strip_trailing_note(project))
    return sorted(unmatched)


def build_oracle_csharp(entry: dict, checkout: Path, cell: str, out_dir: Path) -> tuple[Path, Path, list[str]] | None:
    """Extract the answer key for one repository and cell, via the C#/IL oracle. Returns
    (edges, overrides, first_party). Registered in ORACLE_BUILDERS as the "csharp" language."""
    assemblies = assemblies_for(entry, checkout, cell)
    if not assemblies:
        return None

    # A `with-tests` cell whose answer key contains no test assembly is not a with-tests cell. The
    # arms still read the test SOURCES, so their output grows while the answer key stays put, and
    # every extra edge is scored as junk against a key that was never asked about it. That produces
    # a number — serilog's grep precision fell 0.259 -> 0.044 this way — and the number means
    # nothing. Refuse it instead of publishing it.
    if cell == "with-tests":
        product = {p.resolve() for p in assemblies_for(entry, checkout, "without-tests")}
        if not [a for a in assemblies if a.resolve() not in product]:
            listed = len(entry.get("test_assemblies", []))
            raise CellNotMeasurable(
                f"no test assembly was found on disk ({listed} listed in corpus.json). The build "
                f"command only builds the product projects, so the answer key would be identical to "
                f"without-tests while the arms read the test sources — build the test projects, or "
                f"drop this cell for this repository."
            )

        # One level finer than the check above: some test assembly exists, but not one for every
        # project the arms are scoped to read. Those projects' calls are compiled, read by the arms,
        # and judged against nothing — silently junk by construction, the exact defect e8 fixed for
        # serilog (Serilog.PerformanceTests and AotTestApp were in scope but not in the answer key).
        unmatched = unmatched_test_scope_projects(entry, cell)
        if unmatched:
            wanted = entry.get("target_framework_probed")
            raise CellNotMeasurable(
                f"test_assemblies_projects and test_assemblies disagree for {wanted}: the arms are "
                f"scoped to read {', '.join(unmatched)}, but test_assemblies lists no matching "
                f"{wanted} assembly for {'it' if len(unmatched) == 1 else 'them'} — build the "
                f"project and add its assembly to test_assemblies, or drop it from "
                f"test_assemblies_projects."
            )

    edges = out_dir / "oracle.jsonl"
    overrides = out_dir / "overrides.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    result = run([
        "dotnet", "run", "-c", "Release", "--no-build", "--project", str(ORACLE_PROJECT), "--",
        *[str(a) for a in assemblies],
        "--source-root", str(checkout),
        "--out", str(edges),
        "--overrides", str(overrides),
    ])
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"oracle failed for {entry['name']} / {cell}")

    (out_dir / "oracle.stderr.txt").write_text(result.stderr, encoding="utf-8")
    first_party = sorted({a.stem for a in assemblies})
    return edges, overrides, first_party


# Which oracle builds the answer key for a corpus.json `language`. One entry today —
# `build_oracle_csharp` is exactly the old, unconditional `build_oracle` — but routing through this
# registry rather than a hardcoded `dotnet run --project oracle/csharp` is what lets a second
# language add itself (`oracle/typescript` -> `build_oracle_typescript`) without touching this
# dispatch again. An unregistered language must fail loudly, not silently skip the cell — see
# its use in main() below.
ORACLE_BUILDERS: dict[str, Callable[[dict, Path, str, Path], tuple[Path, Path, list[str]] | None]] = {
    "csharp": build_oracle_csharp,
}


def oracle_builder_for(entry: dict) -> Callable[[dict, Path, str, Path], tuple[Path, Path, list[str]] | None]:
    """The oracle builder for a corpus.json entry's `language`, defaulting to csharp when the
    field is absent (every entry written before the language axis existed). Raises SystemExit —
    loudly, never a silent skip — for a language nothing is registered for: that is a config
    mistake in corpus.json, not a legitimately-unmeasurable cell, and must not be folded into
    CellNotMeasurable."""
    language = entry.get("language", DEFAULT_LANGUAGE)
    builder = ORACLE_BUILDERS.get(language)
    if builder is None:
        raise SystemExit(
            f"{entry['name']}: unknown language {language!r} in corpus.json — no oracle "
            f"registered for it (known: {sorted(ORACLE_BUILDERS)})"
        )
    return builder


def run_arm(name: str, checkout: Path, cell: str, out_dir: Path, repo_key: str) -> ArmRun:
    """Execute an arm, or fall back to its published rows, or report it unavailable."""
    script = ROOT / "arms" / name / "run.py"
    published = PUBLISHED / name / repo_key / cell / "edges.jsonl"

    if script.is_file():
        described = run([sys.executable, str(script), "--describe"])
        describe = {}
        if described.returncode == 0 and described.stdout.strip():
            try:
                describe = json.loads(described.stdout.strip().splitlines()[-1])
            except json.JSONDecodeError:
                describe = {"name": name, "version": "unparsed"}

        edges = out_dir / "edges.jsonl"
        started = time.monotonic()
        result = run([sys.executable, str(script), "--repo", str(checkout),
                      "--cell", cell, "--out", str(edges)])
        seconds = time.monotonic() - started
        (out_dir / "arm.stderr.txt").write_text(result.stderr, encoding="utf-8")

        if result.returncode == 0 and edges.is_file():
            return ArmRun(name, describe.get("mode", "live"), edges, describe, seconds,
                          coverage=read_coverage(edges))

        if result.returncode == BLOCKED_EXIT:
            # The tool ran and was refused. Reported as a gap, never as a zero: a published 0.000
            # beside a tool that never got to look would be a false claim about that tool.
            reason = (result.stderr.strip().splitlines() or ["blocked with no message"])[-1]
            return ArmRun(name, "blocked", None, describe, seconds, reason)

        # The arm exists but could not run here — a licensed engine on an unlicensed machine, a
        # missing dependency. Published rows are the documented fallback; say which one was used.
        note = (result.stderr.strip().splitlines() or ["failed with no message"])[-1]
        if published.is_file():
            copied = out_dir / "edges.jsonl"
            shutil.copyfile(published, copied)
            return ArmRun(name, "replay", copied, describe, None,
                          f"could not run here ({note}); replayed published rows")
        return ArmRun(name, "unavailable", None, describe, None, note)

    if published.is_file():
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = out_dir / "edges.jsonl"
        shutil.copyfile(published, copied)
        return ArmRun(name, "replay", copied, {"name": name}, None, "replayed published rows")

    return ArmRun(name, "unavailable", None, {"name": name}, None, "no runner and no published rows")


def read_coverage(edges: Path) -> dict:
    """What the arm declared about its own blind spots, if anything.

    An arm that says "I saw 400 call sites I could not resolve" and an arm that says nothing both
    lose the same recall points. Only one of them is being honest about it, and a table that cannot
    show the difference invites the reader to assume the worse of the two.
    """
    sidecar = Path(str(edges) + ".coverage.json")
    if not sidecar.is_file():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def grade(oracle: Path, overrides: Path, arm: ArmRun, first_party: list[str], out_dir: Path) -> dict | None:
    if arm.edges_path is None:
        return None
    report = out_dir / "result.json"
    # grade.py's cell_of() is deterministic on the data now (fixed-precedence tie-break, see
    # PREREGISTRATION deviations, G3), but PYTHONHASHSEED is pinned here too as a second,
    # independent guard — belt and braces — so a published number never again depends on which
    # hash seed a Python process happened to start with.
    result = run([
        sys.executable, str(GRADER),
        "--oracle", str(oracle), "--arm", str(arm.edges_path),
        "--overrides", str(overrides),
        "--first-party", *first_party,
        "--json", str(report),
    ], env={**os.environ, "PYTHONHASHSEED": "0"})
    (out_dir / "grade.stdout.txt").write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        return None
    return json.loads(report.read_text(encoding="utf-8"))


def primary(report: dict | None) -> dict | None:
    if not report:
        return None
    for cell in report["cells"]:
        if cell["cell"].startswith("primary"):
            return cell
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus.json")
    parser.add_argument("--checkouts", type=Path, default=ROOT / "corpus")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--arms", nargs="*", default=["grep", "repowise", "cheburnexus"])
    parser.add_argument("--only", nargs="*", default=None, help="limit to these repository names")
    parser.add_argument("--cells", nargs="*", default=list(CELLS), choices=CELLS)
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_root = args.out or (ROOT / "results" / stamp)

    corpus = load_corpus(args.corpus)
    if args.only:
        wanted = {w.lower() for w in args.only}
        corpus = [e for e in corpus if repo_dir_name(e).lower() in wanted or e["name"].lower() in wanted]
    if not corpus:
        raise SystemExit("no corpus entries selected")

    sdk = run(["dotnet", "--version"]).stdout.strip()
    rows: list[tuple] = []

    for entry in corpus:
        repo_key = repo_dir_name(entry)
        checkout = args.checkouts / repo_key
        if not checkout.is_dir():
            for cell in args.cells:
                for arm_name in args.arms:
                    rows.append((repo_key, cell, arm_name, "no-checkout", None,
                                 f"no checkout at {checkout} — clone it from corpus.json", {}))
            continue

        builder = oracle_builder_for(entry)

        for cell in args.cells:
            cell_dir = out_root / repo_key / cell
            try:
                built = builder(entry, checkout, cell, cell_dir / "_oracle")
            except CellNotMeasurable as why:
                for arm_name in args.arms:
                    rows.append((repo_key, cell, arm_name, "not-measurable", None, str(why), {}))
                continue
            if built is None:
                # Never a quiet `continue`. A cell with no answer key must appear in the table as a
                # gap, or the published result silently shrinks to whatever happened to build — the
                # one failure mode the whole polygon is built to avoid.
                for arm_name in args.arms:
                    rows.append((repo_key, cell, arm_name, "no-oracle", None,
                                 "no built assemblies matched target_framework_probed", {}))
                continue
            oracle, overrides, first_party = built

            for arm_name in args.arms:
                arm_dir = cell_dir / arm_name
                arm_dir.mkdir(parents=True, exist_ok=True)
                arm = run_arm(arm_name, checkout, cell, arm_dir, repo_key)
                report = grade(oracle, overrides, arm, first_party, arm_dir)

                (arm_dir / "manifest.json").write_text(json.dumps({
                    "repo": entry["name"],
                    "sha": entry["sha"],
                    "cell": cell,
                    "arm": arm.describe or {"name": arm_name},
                    "mode": arm.mode,
                    "note": arm.note,
                    "seconds": arm.seconds,
                    "coverage_declared_by_arm": arm.coverage,
                    "first_party": first_party,
                    "oracle_assemblies": [str(a.relative_to(checkout))
                                          for a in assemblies_for(entry, checkout, cell)],
                    "host": {"platform": platform.platform(), "python": platform.python_version(),
                             "dotnet_sdk": sdk},
                    "utc": stamp,
                }, indent=2), encoding="utf-8")

                rows.append((repo_key, cell, arm_name, arm.mode, primary(report), arm.note,
                             arm.coverage))

    print(f"\n{'repo':<18}{'cell':<15}{'arm':<14}{'mode':<12}{'prec':>8}{'recall':>8}"
          f"{'declared':>10}   note")
    for repo_key, cell, arm_name, mode, cellrow, note, coverage in rows:
        # "declared" is what the arm itself admitted it could not resolve. Blank means the arm made
        # no such statement — which is not the same as having nothing unresolved.
        unresolved = coverage.get("unresolved_call_sites")
        declared = "—" if unresolved is None else str(unresolved)
        if cellrow is None:
            print(f"{repo_key:<18}{cell:<15}{arm_name:<14}{mode:<12}{'—':>8}{'—':>8}"
                  f"{declared:>10}   {note}")
            continue
        fmt = lambda v: "  n/a" if v is None else f"{v:8.3f}"
        print(f"{repo_key:<18}{cell:<15}{arm_name:<14}{mode:<12}"
              f"{fmt(cellrow['precision'])}{fmt(cellrow['recall'])}{declared:>10}   {note}")

    print(f"\nresults: {out_root}")
    print("A `replay` row was NOT executed here — it regrades published rows. See arms/ARCHITECTURE.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
