#!/usr/bin/env python3
"""grade_implements.py must not introduce error of its own, and must keep the three axes it exists
to separate — first-party direct declarations, external targets, and generic targets/types whose
arity the engine's sidecar cannot preserve — from bleeding into each other.

    python3 test_implements.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

# One row per cell this axis recognizes.
FIXTURE = [
    # first-party inherits — primary
    dict(Type="App.Dog", Target="App.Animal", Kind="inherits",
         TypeFile="Dog.cs", TypeLine=3, TypeAssembly="App", TargetAssembly="App", TargetExternal=False),
    # first-party implements — primary
    dict(Type="App.FileSink", Target="App.ISink", Kind="implements",
         TypeFile="FileSink.cs", TypeLine=5, TypeAssembly="App", TargetAssembly="App", TargetExternal=False),
    # external target — BCL
    dict(Type="App.Widget", Target="System.IDisposable", Kind="implements",
         TypeFile="Widget.cs", TypeLine=1, TypeAssembly="App", TargetAssembly="System.Private.CoreLib",
         TargetExternal=True),
    # generic target — arity survives on the oracle side only
    dict(Type="App.IntBox", Target="App.Box`1", Kind="inherits",
         TypeFile="IntBox.cs", TypeLine=8, TypeAssembly="App", TargetAssembly="App", TargetExternal=False),
]


def run_grader(oracle: Path, arm: Path, out: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(HERE / "grade_implements.py"), "--oracle", str(oracle), "--arm", str(arm),
         "--json", str(out)],
        capture_output=True, text=True, check=True)
    sys.stdout.write(result.stdout)
    return json.loads(out.read_text())


def cell(report: dict, prefix: str) -> dict:
    for entry in report["cells"]:
        if entry["cell"].startswith(prefix):
            return entry
    raise AssertionError(f"no cell starting with {prefix!r}")


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def check_generic_key_detection() -> list[str]:
    sys.path.insert(0, str(HERE))
    from grade_implements import is_generic_key

    failures = []
    cases = [
        ("App.Box`1", True),
        ("HedgingExecutionContext`1/ExecutionInfo`1", True),
        ("App.Animal", False),
        ("App.ISink", False),
    ]
    for name, expected in cases:
        if is_generic_key(name) != expected:
            failures.append(f"is_generic_key({name!r}) = {is_generic_key(name)}, expected {expected}")
    return failures


def main() -> int:
    failures: list[str] = check_generic_key_detection()

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        oracle = tmp / "oracle.jsonl"
        write(oracle, FIXTURE)

        # 1. identity — the answer key graded against itself must score 1.000 in every non-empty cell
        arm = tmp / "arm.jsonl"
        write(arm, FIXTURE)
        report = run_grader(oracle, arm, tmp / "identity.json")

        for name in ("primary", "excluded — target outside", "excluded — generic"):
            entry = cell(report, name)
            if entry["oracle_edges"] == 0:
                failures.append(f"fixture covers no edges for cell {entry['cell']!r}")
            elif entry["precision"] != 1.0 or entry["recall"] != 1.0:
                failures.append(
                    f"identity broken in {entry['cell']!r}: "
                    f"precision={entry['precision']} recall={entry['recall']}")

        primary = cell(report, "primary")
        if primary["oracle_edges"] != 2:
            failures.append(f"expected 2 primary edges (inherits + implements), got {primary['oracle_edges']}")

        generic = cell(report, "excluded — generic")
        if generic["oracle_edges"] != 1:
            failures.append(f"expected 1 generic-target edge, got {generic['oracle_edges']}")

        external = cell(report, "excluded — target outside")
        if external["oracle_edges"] != 1:
            failures.append(f"expected 1 external-target edge, got {external['oracle_edges']}")

        # 2. the test must be able to fail: a missing edge has to cost recall
        write(arm, [r for r in FIXTURE if r["Type"] != "App.FileSink"])
        report = run_grader(oracle, arm, tmp / "missing.json")
        if cell(report, "primary")["recall"] >= 1.0:
            failures.append("dropping a real primary edge did not reduce recall — the check is inert")

        # 3. an invented edge has to cost precision
        write(arm, FIXTURE + [dict(Type="App.Ghost", Target="App.Nowhere", Kind="implements",
                                    TargetAssembly="App", TargetExternal=False)])
        report = run_grader(oracle, arm, tmp / "invented.json")
        if cell(report, "primary")["precision"] >= 1.0:
            failures.append("an invented edge to nobody's target did not reduce precision")

        # 4. an arm that names the SIMPLE-NAME-WRONG target (the by-simple-name resolution
        # hypothesis this whole axis exists to measure) must show up as a primary-cell miss, not
        # silently vanish into some other cell or match by accident.
        write(arm, [dict(Type="App.Dog", Target="Other.Ns.Animal", Kind="inherits",
                          TargetAssembly="App", TargetExternal=False)] +
              [r for r in FIXTURE if r["Type"] != "App.Dog"])
        report = run_grader(oracle, arm, tmp / "wrong-namespace.json")
        primary = cell(report, "primary")
        if primary["matched"] != 1:  # only the FileSink row still matches
            failures.append(
                f"wrong-namespace target should leave exactly 1 primary match (FileSink), "
                f"got {primary['matched']}")

        # 5. an arm's edge to a target the oracle classified as generic must land in the generic
        # cell (via target_cell), not be silently scored as a primary-cell false positive just
        # because the arm's own key never carries the arity suffix.
        write(arm, FIXTURE)
        report = run_grader(oracle, arm, tmp / "generic-fallback.json")
        if cell(report, "excluded — generic")["arm_edges_in_cell"] != 1:
            failures.append("arm edge to a generic-classified target did not land in the generic cell")

    if failures:
        print("\nFAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\nOK — implements grader is neutral, separates its cells, and the check can fail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
