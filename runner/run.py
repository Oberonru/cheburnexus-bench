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


def assemblies_for(entry: dict, checkout: Path, cell: str,
                    missing_out: list[str] | None = None) -> list[Path]:
    """The assemblies the answer key is built from, for this cell.

    Only the probed target framework is used. A multi-targeted repository produces the same types
    several times over, and counting net6.0 and net8.0 copies of one method as different edges would
    inflate the answer key with duplicates no arm could ever match twice.

    A declared assembly (matching the probed framework) that isn't on disk is dropped silently from
    the return value, same as always — an arm still needs to run against whatever actually built.
    But a build_command that doesn't build everything corpus.json declares must not vanish without a
    trace: that's exactly what happened to Polly (build_command built only Polly.Core while
    product_assemblies listed four DLLs, so the answer key covered one of them and the other three
    silently became "false positives" against arms that correctly analyzed all four). Pass
    `missing_out` to collect the dropped relative paths instead of losing them.
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
            elif missing_out is not None:
                missing_out.append(relative)
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
    missing: list[str] = []
    assemblies = assemblies_for(entry, checkout, cell, missing)
    if missing:
        print(
            f"WARNING: {entry.get('name', '<unnamed>')}/{cell}: build_command did not produce "
            f"{len(missing)} declared assembly(ies) — dropped from the answer key, NOT from what "
            f"the arms analyze: {', '.join(missing)}",
            file=sys.stderr,
        )
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


ORACLE_TS_DIR = ROOT / "oracle" / "typescript"


def _ts_key(label: str) -> str:
    """Translate a shadow-stack label (`file:Class.member:line`, or the root frame `<module>`)
    into the `Type::Method` shape grade.py's `method_key()` already parses (built for Cecil's
    `Ns.Type::Method` names, reused unmodified here — see build_oracle_typescript's docstring for
    why this is safe rather than coincidental).

    `<module>` — no wrapped function was on the shadow stack, i.e. the call came from code the
    transformer never touched (top-level module evaluation, or a callable shape the transformer
    still skips, e.g. a generator body per oracle/typescript/README.md "Still open") — becomes
    `<module>::<entry>:0`. That string contains a literal `<`, so grade.py's own
    `normalize_caller`/`enclosing_user_method` already treats it exactly like an unresolvable
    compiler-mangled C# caller: dropped, counted in `unremappable_caller`, charged to nobody. No
    grader change needed — this reuses an existing rule instead of adding a new one.
    """
    label = label.strip()
    if label == "<module>":
        return "<module>::<entry>:0"
    if ":" not in label:
        # Defensive only — every real label the transformer emits has a file prefix. A future
        # label shape change would land here instead of crashing the whole oracle build.
        return f"<unknown>::{label}:0"
    file_part, rest = label.split(":", 1)
    return f"{file_part}::{rest}"


def build_oracle_typescript(entry: dict, checkout: Path, cell: str, out_dir: Path) -> tuple[Path, Path, list[str]] | None:
    """Extract the answer key for one TypeScript repository, via the shadow-stack RUNTIME oracle
    in `oracle/typescript/` (see its README.md for the mechanism). Registered in ORACLE_BUILDERS
    as the "typescript" language.

    ── Why this can only ever be a with-tests oracle ──────────────────────────────────────────
    The key is deliberately a RUNTIME trace, not tsc/ts-morph static analysis: our own arm
    (`TsAnalyzer`) IS ts-morph, so a static-analysis key would grade the arm against a copy of
    itself. A runtime key only exists while code is actually running, so there is no such thing as
    a TypeScript answer key that was not produced by running the test suite — `without-tests` is
    not merely unbuilt, it is not a coherent concept for this oracle, so it is refused as
    CellNotMeasurable exactly like the C# `with-tests` guards above refuse a cell whose assumptions
    don't hold, just mirrored: there the risk was a stale/mismatched build silently reusing the
    without-tests key; here the cell simply has no key-producing mechanism at all.

    ── The recall caveat, represented in code, not just here ─────────────────────────────────
    The oracle only records an edge for a callee that was actually ENTERED at least once. A real
    call an arm reports into code the test suite never exercised has no corresponding oracle row —
    not because it is wrong, but because this oracle cannot see it. That is a structural ceiling on
    RECALL (recall is only meaningful on the executed slice, per the README), and it is written
    into `manifest.json` via `oracle_coverage` (see main()) rather than left as a comment nobody
    reads at grading time. It is NOT specially fenced out of the PRECISION denominator: doing that
    would need a mechanism grade.py doesn't have (a "reachable but never executed" scope, distinct
    from "outside the corpus" — CalleeAssembly-style first_party filtering is per-module, not
    per-function, and the C# oracle has no analogous need since it enumerates every compiled call
    site whether or not any test reaches it). Building that mechanism was judged out of scope for
    this session; flagged here as a required follow-up rather than silently approximated.
    """
    if cell != "with-tests":
        raise CellNotMeasurable(
            "the TypeScript oracle is a runtime shadow stack: it only records an edge while the "
            "code runs, so a 'without-tests' answer key is not a smaller version of the real one, "
            "it is nothing — there is no way to produce it. Only 'with-tests' is measurable here."
        )

    harness = entry.get("typescript_harness")
    if harness not in ("ts-jest", "vitest"):
        raise SystemExit(
            f"{entry['name']}: language: typescript entries need a typescript_harness field in "
            f"corpus.json, either 'ts-jest' or 'vitest' (got {harness!r}) — harness selection is "
            f"never auto-detected, see build_oracle_typescript's docstring"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "raw_edges.txt"
    if raw.exists():
        raw.unlink()

    env = {**os.environ, "ORACLE_TS_CORPUS_ROOT": str(checkout), "EDGE_OUT": str(raw)}

    if harness == "ts-jest":
        jest_bin = ORACLE_TS_DIR / "node_modules" / "jest" / "bin" / "jest.js"
        if not jest_bin.is_file():
            raise SystemExit(f"{jest_bin} missing — run `npm install` in {ORACLE_TS_DIR}")
        cmd = ["node", str(jest_bin),
               "--config", str(ORACLE_TS_DIR / "jest.instrumented.config.js"),
               "--runInBand", "--no-cache"]
    else:
        # typescript_test_filter is for a monorepo with NO per-package vitest config (vue: one
        # root vitest.config.ts with a `test.projects` fan-out) — the root is what vitest must be
        # rooted at, and typescript_project_dir alone (used unscoped, per-package config) would
        # point --root at a directory with no vitest config of its own. In that shape the answer
        # key is narrowed instead with a positional test-path filter passed straight through to
        # vitest, while typescript_project_dir keeps doing its other job of scoping the ARM's own
        # source-file scan (armkit.scope_dirs_from_entry) to the same package.
        test_filter = entry.get("typescript_test_filter")
        rel = entry.get("typescript_project_dir")
        project_dir = checkout if test_filter else (checkout / rel if rel else checkout)
        env["ORACLE_TS_PROJECT_DIR"] = str(project_dir)
        npx = "npx.cmd" if os.name == "nt" else "npx"
        cmd = [npx, "vitest", "run", "--root", str(project_dir),
               "--config", str(ORACLE_TS_DIR / "vitest.instrumented.config.mts")]
        if test_filter:
            cmd.append(test_filter)

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(checkout), env=env)
    (out_dir / "harness.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (out_dir / "harness.stderr.txt").write_text(result.stderr, encoding="utf-8")

    # A config mistake (wrong `ts` instance, wrong tsconfig, wrong corpus root) must not look like
    # a legitimate empty answer key — see oracle/typescript/README.md "Two silent no-ops".
    if "instrumented 0 functions" in result.stderr:
        raise SystemExit(
            f"{entry['name']}: shadow-stack instrumented ZERO functions — a config mistake, not a "
            f"real empty answer key. See {out_dir / 'harness.stderr.txt'}"
        )
    if not raw.is_file() or raw.stat().st_size == 0:
        raise SystemExit(
            f"{entry['name']}: TS harness produced no edges at all (exit {result.returncode}) — "
            f"see {out_dir / 'harness.stdout.txt'} / harness.stderr.txt"
        )
    if result.returncode != 0:
        # Real corpora have pre-existing red tests (class-validator/zod both do, per
        # oracle/typescript/README.md). A nonzero exit alongside real recorded edges is a fact
        # worth printing, not grounds to throw the run away — the zero-wrap guard above is what
        # catches the actually-dangerous silent-no-op case.
        print(f"note: {entry['name']} TS harness exited {result.returncode} (edges were recorded; "
              f"likely some tests failed) — see {out_dir / 'harness.stderr.txt'}", file=sys.stderr)

    module_id = repo_dir_name(entry)
    edges_path = out_dir / "oracle.jsonl"
    overrides_path = out_dir / "overrides.json"
    # No override/virtual-dispatch concept exists for this oracle (that is a C#/IL notion — a
    # declared interface/base-class target vs. the implementation an arm names). Empty, not
    # absent: grade.py's --overrides flag expects a file.
    overrides_path.write_text("{}", encoding="utf-8")

    seen: set[tuple[str, str]] = set()
    written = 0
    with edges_path.open("w", encoding="utf-8") as out:
        for line in raw.read_text(encoding="utf-8", errors="replace").splitlines():
            if " -> " not in line:
                continue
            caller_label, callee_label = line.split(" -> ", 1)
            caller_key, callee_key = _ts_key(caller_label), _ts_key(callee_label)
            pair = (caller_key, callee_key)
            if pair in seen:
                continue  # grade.py loads the oracle into a set anyway; dedup here just shrinks the file
            seen.add(pair)
            out.write(json.dumps({
                "Caller": caller_key,
                "Callee": callee_key,
                "Op": "call",  # the shadow stack does not distinguish call/callvirt/newobj/ldftn
                "CalleeAssembly": module_id,
                "CallerCompilerGenerated": False,
                "CalleeCompilerGenerated": False,
                "NoDebugInfo": False,
            }) + "\n")
            written += 1

    if written == 0:
        raise SystemExit(f"{entry['name']}: raw edges existed but none parsed into oracle.jsonl rows")

    return edges_path, overrides_path, [module_id]


# Which oracle builds the answer key for a corpus.json `language`. Routing through this registry
# rather than a hardcoded `dotnet run --project oracle/csharp` is what lets a second language add
# itself (`oracle/typescript` -> `build_oracle_typescript`) without touching this dispatch again.
# An unregistered language must fail loudly, not silently skip the cell — see its use in main().
ORACLE_BUILDERS: dict[str, Callable[[dict, Path, str, Path], tuple[Path, Path, list[str]] | None]] = {
    "csharp": build_oracle_csharp,
    "typescript": build_oracle_typescript,
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


def grade(oracle: Path, overrides: Path, arm: ArmRun, first_party: list[str], out_dir: Path,
          executed_only: bool = False) -> dict | None:
    if arm.edges_path is None:
        return None
    report = out_dir / "result.json"
    # grade.py's cell_of() is deterministic on the data now (fixed-precedence tie-break, see
    # PREREGISTRATION deviations, G3), but PYTHONHASHSEED is pinned here too as a second,
    # independent guard — belt and braces — so a published number never again depends on which
    # hash seed a Python process happened to start with.
    cmd = [
        sys.executable, str(GRADER),
        "--oracle", str(oracle), "--arm", str(arm.edges_path),
        "--overrides", str(overrides),
        "--first-party", *first_party,
        "--json", str(report),
    ]
    unity_components = oracle.parent / "unity-components.json"
    if unity_components.is_file():
        # Written by the C# oracle only when it found MonoBehaviour-derived types, so a repository
        # without Unity code never gets the extra cell. See grade.py UNITY_MESSAGES.
        cmd += ["--unity-components", str(unity_components)]
    if executed_only:
        # RUNTIME ORACLES ONLY. The caller sets this from corpus.json's `language`, never guessed
        # here — see grade.py's --executed-only help for why this must never reach the C# cell
        # (its IL key is exhaustive-static; there is no "never observed executing" there).
        cmd.append("--executed-only")
    result = run(cmd, env={**os.environ, "PYTHONHASHSEED": "0"})
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
    # ABSOLUTE for the same reason as `checkout` below: the TS oracle passes EDGE_OUT into a
    # test process whose cwd is the CHECKOUT, so a relative --out resolved there and every
    # append threw ENOENT inside the suite -- the run finished 'successfully' with no key.
    out_root = (args.out or (ROOT / "results" / stamp)).absolute()

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
        # ABSOLUTE, always: the TS oracle hands this path to a Vitest config that resolves it
        # with a different cwd than ours, so a relative --checkouts (the documented usage,
        # `--checkouts corpus`) silently looked for the corpus config under the wrong root and
        # the harness produced zero edges. `.absolute()` and not `.resolve()` on purpose: a
        # checkout may be a junction/symlink to where the working tree really lives, and the
        # oracle and the arm must agree on ONE spelling of the root or every repo-relative key
        # they emit stops joining.
        checkout = (args.checkouts / repo_key).absolute()
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
            missing_assemblies: list[str] = []
            oracle_assemblies = [str(a.relative_to(checkout))
                                  for a in assemblies_for(entry, checkout, cell, missing_assemblies)]

            for arm_name in args.arms:
                arm_dir = cell_dir / arm_name
                arm_dir.mkdir(parents=True, exist_ok=True)
                arm = run_arm(arm_name, checkout, cell, arm_dir, repo_key)
                # executed-only is a RUNTIME-ORACLE-ONLY filter (see grade()'s docstring / grade.py
                # --executed-only help): TypeScript's shadow-stack key only has an opinion on code
                # a test actually ran, C#'s Cecil/IL key is exhaustive-static and sees everything
                # compiled whether it ran or not. Gated on corpus.json's own `language` field, not
                # guessed from the arm, so it can never silently drift onto the C# cell.
                report = grade(oracle, overrides, arm, first_party, arm_dir,
                                executed_only=(entry.get("language", DEFAULT_LANGUAGE) == "typescript"))

                (arm_dir / "manifest.json").write_text(json.dumps({
                    "repo": entry["name"],
                    "sha": entry["sha"],
                    "cell": cell,
                    "arm": arm.describe or {"name": arm_name},
                    "mode": arm.mode,
                    "note": arm.note,
                    "seconds": arm.seconds,
                    "coverage_declared_by_arm": arm.coverage,
                    # csharp: the answer key enumerates every compiled call site, run or not —
                    # "exhaustive-static". typescript: the shadow-stack key only records a call
                    # that actually executed — "executed-tests-only", a strict precision oracle and
                    # a recall oracle only on the slice the test suite reached. See
                    # build_oracle_typescript's docstring in this file.
                    "oracle_coverage": ("executed-tests-only"
                                        if entry.get("language", DEFAULT_LANGUAGE) == "typescript"
                                        else "exhaustive-static"),
                    "first_party": first_party,
                    "oracle_assemblies": oracle_assemblies,
                    # Declared in corpus.json's product_assemblies/test_assemblies (at the probed
                    # framework) but not found on disk — build_command didn't build them, so they
                    # were dropped from the answer key above. Non-empty here means the oracle is
                    # under-covering relative to what corpus.json claims; see assemblies_for's
                    # docstring for why this must be visible instead of silent.
                    "missing_declared_assemblies": missing_assemblies,
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
