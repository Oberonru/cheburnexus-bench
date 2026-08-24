#!/usr/bin/env python3
"""Tests for our own arm's conversion, and for the one behaviour we must never get wrong.

Our engine's output has to be reshaped into the edge contract, and this arm is the one place where a
generous mistake would flatter us. It is also the arm that currently cannot run without a licence,
which makes the second half of this file the important half: being refused must stay distinguishable
from finding nothing.

No engine run is needed — these are the pure decisions.

    python3 arms/cheburnexus/test_cheburnexus.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "arms" / "_lib"))
sys.path.insert(0, str(HERE))

import armkit  # noqa: E402
from run import _parse_raw_key, _to_repo_relative, backtick_arity  # noqa: E402


def check_generic_arity() -> list[str]:
    """Arity is kept, closed arguments go. The oracle spells `Cache`1`, so we must too."""
    failures = []
    cases = [
        ("Cache<T>", "Cache`1", "one parameter"),
        ("Pair<K,V>", "Pair`2", "two parameters"),
        ("Map<K,List<V>>", "Map`2", "a nested argument is one parameter, not two"),
        ("Plain", "Plain", "a non-generic name is untouched"),
        ("Cache`1", "Cache`1", "already-suffixed input must not be suffixed twice"),
    ]
    for raw, expected, why in cases:
        got = backtick_arity(raw)
        if got != expected:
            failures.append(f"backtick_arity({raw!r}) = {got!r}, expected {expected!r} — {why}")
    return failures


def check_key_parsing() -> list[str]:
    """The engine's own key shape, split only on the separator it actually inserts."""
    failures = []

    parsed = _parse_raw_key("src/App/Guard.cs::App.Guard::NotNull(System.String)")
    if parsed != ("src/App/Guard.cs", "App.Guard", "NotNull"):
        failures.append(f"ordinary key parsed as {parsed!r}")

    # A Windows-style path carrying a drive letter still splits on '::' alone.
    parsed = _parse_raw_key(r"C:/repo/src/A.cs::App.A::Run()")
    if parsed is None or parsed[2] != "Run":
        failures.append(f"a drive-lettered path broke parsing: {parsed!r}")

    if _parse_raw_key("not-a-key") is not None:
        failures.append("a malformed key was parsed instead of rejected — a bad row must be "
                        "dropped, never turned into an edge that scores")
    return failures


def check_paths_are_repo_relative() -> list[str]:
    """Anchors must be comparable across machines, and never invented when they are not."""
    failures = []
    repo = Path("/home/u/repo")

    got = _to_repo_relative("/home/u/repo/src/App/Service.cs", repo)
    if got != "src/App/Service.cs":
        failures.append(f"a path under the checkout was not made relative: {got!r}")

    got = _to_repo_relative(r"\\home\\u\\repo\\src\\App\\Service.cs".replace("\\\\", "\\"), repo)
    if "\\" in got:
        failures.append(f"backslashes survived into the anchor: {got!r} — rows must be portable")

    outside = _to_repo_relative("/elsewhere/Other.cs", repo)
    if outside != "/elsewhere/Other.cs":
        failures.append(f"a path outside the checkout was rewritten to {outside!r} — better an "
                        "honest absolute path than an invented relative one")
    return failures


def check_refused_is_not_empty() -> list[str]:
    """The licence wall must surface as `blocked`, never as an arm that looked and found nothing.

    This is the assertion that matters most in this file. Our engine analyses a repository cleanly
    and then withholds the call graph, because the graph is the paid feature. Reporting that as zero
    edges would publish `recall 0.000` beside our own product — a claim that it searched and failed,
    when it was never allowed to search.
    """
    failures = []
    source = (HERE / "run.py").read_text(encoding="utf-8")
    if "armkit.Blocked" not in source:
        failures.append("the arm no longer raises armkit.Blocked — a licence refusal would be "
                        "graded as a genuine zero")
    if "calls-counts.json" not in source:
        failures.append("the arm no longer recognises the counts-only output that marks the wall")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        probe = tmp / "arm.py"
        probe.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'arms' / '_lib')!r})\n"
            "import armkit\n"
            "def collect(repo, cell):\n"
            "    raise armkit.Blocked('analyzer.semantic is a Pro feature')\n"
            "sys.exit(armkit.main('cheburnexus', lambda: 'test', collect))\n",
            encoding="utf-8")
        (tmp / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(probe), "--repo", str(tmp / "repo"),
             "--cell", "without-tests", "--out", str(tmp / "out.jsonl")],
            capture_output=True, text=True)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(f"a refused arm exited {result.returncode}, expected {armkit.EXIT_BLOCKED}")
        if (tmp / "out.jsonl").exists():
            failures.append("a refused arm produced an output file, which the grader would score")
    return failures


def check_describe_carries_identity() -> list[str]:
    """A published row set is worthless if it cannot be tied to the version that produced it."""
    failures = []
    result = subprocess.run([sys.executable, str(HERE / "run.py"), "--describe"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        failures.append(f"--describe failed: {result.stderr.strip()[:200]}")
        return failures
    try:
        described = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        failures.append(f"--describe did not print one JSON object: {result.stdout[:200]!r}")
        return failures
    if described.get("name") != "cheburnexus":
        failures.append(f"--describe reports name {described.get('name')!r}")
    if not described.get("version") or described["version"] in ("unknown", ""):
        failures.append("--describe reports no engine version — results could not be traced to a build")
    return failures


# ── defect #9 fixtures: a controllable fake engine + a throwaway repo ──────────────────────────
# These four tests reproduce the harness defect found 2026-08-24: a stale architecture.calls.json
# left in arms/cheburnexus/.work/<repo>/<cell>/<project>/ from a PREVIOUS run was silently reported
# as THIS run's result when the engine refused (exit 2, wrote nothing) or exited 0 without writing
# anything. _invoke() checked only arch.is_file() — never the exit code, never the file's age
# against the start of this run — and the per-project scratch directory was never cleaned between
# runs. See the PREREGISTRATION doc's 2026-08-24 "Отклонения от плана" entry for the full account.
#
# The fake engine below stands in for arch-computer.exe: given a --solution/--project target whose
# path contains one of three markers, it behaves the way the real engine was observed to behave in
# each of the three situations that mattered:
#   "RefuseRestore"    — exit 2, writes nothing, stderr is a real (non-entitlement) refusal message
#   "ExitZeroNoOutput"  — exit 0, writes nothing (a clean run that simply produced no sidecar)
#   "Good"              — exit 0, writes a genuine architecture.json + architecture.calls.json
_FAKE_ENGINE_SOURCE = '''#!/usr/bin/env python3
import json
import pathlib
import sys

args = sys.argv[1:]
target = args[1]
out_path = pathlib.Path(args[args.index("--output") + 1])

if "RefuseRestore" in target:
    sys.stderr.write(
        "[warn] no restore output for RefuseRestore.csproj "
        "(expected .../RefuseRestore/obj/project.assets.json)\\n"
    )
    sys.stderr.write(
        "Refusing to analyze: at least one project has no restore output "
        "(obj/project.assets.json). Run 'dotnet restore' first — without it every referenced "
        "package type is unresolvable and the call graph would be silently incomplete.\\n"
    )
    sys.exit(2)

if "ExitZeroNoOutput" in target:
    sys.exit(0)

if "Good" in target:
    out_path.write_text(json.dumps({
        "Files": [{"Namespace": "App", "Types": [{"Name": "Widget", "Methods": []}]}]
    }))
    out_path.with_name("architecture.calls.json").write_text(json.dumps({
        "Data": {
            "src/Good/Widget.cs::App.Widget::Run": {
                "Calls": [{"Target": "src/Good/Widget.cs::App.Widget::Helper()", "Line": 10}]
            }
        }
    }))
    sys.exit(0)

sys.stderr.write(f"fake-engine: unrecognized target {target!r}\\n")
sys.exit(1)
'''


def _write_fake_engine(tmp: Path) -> Path:
    engine = tmp / "fake_engine.py"
    engine.write_text(_FAKE_ENGINE_SOURCE, encoding="utf-8")
    engine.chmod(0o755)
    return engine


def _make_fake_repo(tmp: Path, project_names: list[str]) -> Path:
    # Named after tmp's own (already-unique) basename, not a fixed "repo" — WORK_ROOT is keyed by
    # repo_root.name alone, and a fixed name would let one test's scratch directory collide with
    # another's inside the shared arms/cheburnexus/.work/ tree.
    repo = tmp / f"repo-{tmp.name}"
    for name in project_names:
        proj_dir = repo / "src" / name
        proj_dir.mkdir(parents=True)
        (proj_dir / f"{name}.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"></Project>', encoding="utf-8")
        (proj_dir / "Widget.cs").write_text("namespace App { class Widget {} }", encoding="utf-8")
    return repo


def _plant_stale_artifacts(repo: Path, cell: str, project_stem: str, files: dict[str, str]) -> Path:
    """Drop files into the exact per-project scratch path run.py will use, backdated three days —
    reproducing a leftover from a previous run that a fixed arm must never read as this run's own."""
    stale_dir = HERE / ".work" / repo.name / cell / project_stem
    stale_dir.mkdir(parents=True, exist_ok=True)
    old = time.time() - 3 * 24 * 3600
    for name, content in files.items():
        p = stale_dir / name
        p.write_text(content, encoding="utf-8")
        os.utime(p, (old, old))
    return stale_dir


def _run_arm_cli(repo: Path, cell: str, out: Path, engine: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CHEBURNEXUS_ENGINE"] = str(engine)
    return subprocess.run(
        [sys.executable, str(HERE / "run.py"), "--repo", str(repo), "--cell", cell, "--out", str(out)],
        capture_output=True, text=True, env=env)


_STALE_ARCH = json.dumps({"Files": [{"Namespace": "Stale", "Types": [{"Name": "Ghost", "Methods": []}]}]})
_STALE_CALLS = json.dumps({
    "Data": {"stale/Ghost.cs::Stale.Ghost::Run": {
        "Calls": [{"Target": "stale/Ghost.cs::Stale.Ghost::Boo()", "Line": 1}]
    }}
})
_STALE_COUNTS = json.dumps({"Data": {}})


def check_stale_artifact_with_nonzero_exit_is_not_reported() -> list[str]:
    """Defect #9, mode 1: the engine exits non-zero and writes nothing, but a stale
    architecture.calls.json from three days ago is still sitting in the scratch directory. A fixed
    arm must not read it — it must report the cell as blocked, not as a set of (stale) edges."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["RefuseRestore"])
        _plant_stale_artifacts(repo, "without-tests", "RefuseRestore",
                               {"architecture.json": _STALE_ARCH, "architecture.calls.json": _STALE_CALLS})
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(
                f"engine exited 2 with a stale calls.json present: arm exited {result.returncode}, "
                f"expected {armkit.EXIT_BLOCKED} (BLOCKED) — stderr tail: {result.stderr[-500:]!r}")
        if out.exists():
            failures.append(
                "engine exited 2 with a stale calls.json present: arm wrote edges.jsonl anyway "
                f"(the stale 3-day-old edge leaked through) — {out.read_text(encoding='utf-8')[:300]!r}")
    return failures


def check_freshness_catches_exit_zero_with_no_output() -> list[str]:
    """Defect #9, mode 2: the engine exits 0 (a "successful" run by exit-code alone) but writes
    nothing new — belt-and-braces for when the exit-code check alone is not enough, since a stale
    architecture.json sitting in the scratch dir would otherwise look like this run's own output."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["ExitZeroNoOutput"])
        _plant_stale_artifacts(repo, "without-tests", "ExitZeroNoOutput",
                               {"architecture.json": _STALE_ARCH, "architecture.calls.json": _STALE_CALLS})
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(
                f"engine exited 0 but wrote nothing, with a stale calls.json present: arm exited "
                f"{result.returncode}, expected {armkit.EXIT_BLOCKED} — the freshness check did not "
                f"catch the stale file. stderr tail: {result.stderr[-500:]!r}")
        if out.exists():
            failures.append(
                "engine exited 0 but wrote nothing, with a stale calls.json present: arm wrote "
                f"edges.jsonl from the stale file — {out.read_text(encoding='utf-8')[:300]!r}")
    return failures


def check_non_entitlement_refusal_is_not_mislabelled() -> list[str]:
    """Defect #9, mode 3: the engine refuses for an ordinary reason (missing dotnet restore) — not
    the licence wall. A stale calls-counts.json (no calls.json) is also sitting there, which is
    exactly the shape the old code mistook for "entitlement wall, not a failure". The reported
    reason must quote the engine's own refusal message, and must never say "entitlement wall"."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["RefuseRestore"])
        _plant_stale_artifacts(repo, "without-tests", "RefuseRestore",
                               {"architecture.json": _STALE_ARCH, "architecture.calls-counts.json": _STALE_COUNTS})
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        combined = result.stdout + result.stderr
        if "entitlement wall" in combined.lower():
            failures.append(
                f"a plain 'no restore output' refusal was reported as the entitlement wall — "
                f"stderr: {result.stderr[-800:]!r}")
        if "Refusing to analyze: at least one project has no restore output" not in combined:
            failures.append(
                "the engine's own refusal message did not survive into the arm's report — "
                f"stderr: {result.stderr[-800:]!r}")
        if out.exists():
            failures.append("a refused project produced edges.jsonl anyway")
    return failures


def check_partial_project_coverage_fails_the_cell_loudly() -> list[str]:
    """Defect #9, mode 4 (the masking bug): one project analyzes cleanly and produces real edges,
    a second refuses outright. The old code graded the cell on the one project that worked and
    never mentioned the one that didn't — "378 edges" printed as though the run were complete. A
    fixed arm must fail the whole cell rather than publish a partial result as if it were whole."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["Good", "RefuseRestore"])
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(
                f"1 of 2 projects produced no call graph, but arm exited {result.returncode} "
                f"(expected {armkit.EXIT_BLOCKED}) — a partial analysis must fail the cell, not be "
                f"graded as complete. stderr tail: {result.stderr[-800:]!r}")
        if out.exists():
            failures.append(
                "1 of 2 projects produced no call graph, but the arm still wrote edges.jsonl from "
                f"the project that succeeded — {out.read_text(encoding='utf-8')[:300]!r}")
    return failures


def main() -> int:
    failures = check_generic_arity()
    failures += check_key_parsing()
    failures += check_paths_are_repo_relative()
    failures += check_refused_is_not_empty()
    failures += check_describe_carries_identity()
    failures += check_stale_artifact_with_nonzero_exit_is_not_reported()
    failures += check_freshness_catches_exit_zero_with_no_output()
    failures += check_non_entitlement_refusal_is_not_mislabelled()
    failures += check_partial_project_coverage_fails_the_cell_loudly()

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok — key shape, arity, anchors, identity, refusal-is-not-emptiness, staleness, exit "
          "codes, honest refusal reasons, and partial-coverage masking all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
