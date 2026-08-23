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
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORACLE_PROJECT = ROOT / "oracle" / "csharp"
GRADER = ROOT / "grader" / "grade.py"
PUBLISHED = ROOT / "results" / "published"
CELLS = ("without-tests", "with-tests")


@dataclass
class ArmRun:
    name: str
    mode: str              # live | replay | unavailable
    edges_path: Path | None = None
    describe: dict = field(default_factory=dict)
    seconds: float | None = None
    note: str = ""


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def load_corpus(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else list(data.values())


def repo_dir_name(entry: dict) -> str:
    return entry["name"].split("/")[-1]


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
            if wanted and f"/{wanted}/" not in relative.replace("\\", "/"):
                continue
            candidate = checkout / relative
            if candidate.is_file():
                chosen.append(candidate)
    return chosen


def build_oracle(entry: dict, checkout: Path, cell: str, out_dir: Path) -> tuple[Path, Path, list[str]] | None:
    """Extract the answer key for one repository and cell. Returns (edges, overrides, first_party)."""
    assemblies = assemblies_for(entry, checkout, cell)
    if not assemblies:
        return None

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
            return ArmRun(name, describe.get("mode", "live"), edges, describe, seconds)

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


def grade(oracle: Path, overrides: Path, arm: ArmRun, first_party: list[str], out_dir: Path) -> dict | None:
    if arm.edges_path is None:
        return None
    report = out_dir / "result.json"
    result = run([
        sys.executable, str(GRADER),
        "--oracle", str(oracle), "--arm", str(arm.edges_path),
        "--overrides", str(overrides),
        "--first-party", *first_party,
        "--json", str(report),
    ])
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
            print(f"skip {entry['name']}: no checkout at {checkout} — clone it from corpus.json",
                  file=sys.stderr)
            continue

        for cell in args.cells:
            cell_dir = out_root / repo_key / cell
            built = build_oracle(entry, checkout, cell, cell_dir / "_oracle")
            if built is None:
                print(f"skip {entry['name']} / {cell}: no built assemblies for this cell",
                      file=sys.stderr)
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
                    "first_party": first_party,
                    "oracle_assemblies": [str(a.relative_to(checkout))
                                          for a in assemblies_for(entry, checkout, cell)],
                    "host": {"platform": platform.platform(), "python": platform.python_version(),
                             "dotnet_sdk": sdk},
                    "utc": stamp,
                }, indent=2), encoding="utf-8")

                rows.append((repo_key, cell, arm_name, arm.mode, primary(report), arm.note))

    print(f"\n{'repo':<18}{'cell':<15}{'arm':<14}{'mode':<12}{'prec':>8}{'recall':>8}   note")
    for repo_key, cell, arm_name, mode, cellrow, note in rows:
        if cellrow is None:
            print(f"{repo_key:<18}{cell:<15}{arm_name:<14}{mode:<12}{'—':>8}{'—':>8}   {note}")
            continue
        fmt = lambda v: "  n/a" if v is None else f"{v:8.3f}"
        print(f"{repo_key:<18}{cell:<15}{arm_name:<14}{mode:<12}"
              f"{fmt(cellrow['precision'])}{fmt(cellrow['recall'])}   {note}")

    print(f"\nresults: {out_root}")
    print("A `replay` row was NOT executed here — it regrades published rows. See arms/ARCHITECTURE.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
