#!/usr/bin/env python3
"""The grader must not introduce error of its own.

Graded against itself, the answer key has to score exactly 1.000 — anything less means the two
sides of the comparison disagree about which edges are in play, and that disagreement would be
charged to whichever arm is being measured. This is the one property that has to hold before a
single published number can be trusted, so it is asserted on a fixture that carries every case
known to break symmetry: a lambda caller, a caller no name can be traced back to, a property
accessor, a callee outside the corpus, and an indirect call op.

The last two checks prove the test can fail: remove an edge and recall must drop; invent one and
precision must drop. A green test that cannot go red proves nothing.

    python3 test_identity.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

# Each row is what the C# oracle emits, with the flags that drive classification.
FIXTURE = [
    # ordinary first-party call — the primary cell
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.Repo::Load()",
         Op="call", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # constructor — also primary
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.Repo::.ctor()",
         Op="newobj", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # real call written inside a lambda: caller is generated, but must be remapped, not dropped
    dict(Caller="System.Void App.Service/<>c__DisplayClass3_0::<Run>b__0()",
         Callee="System.Void App.Repo::Save()",
         Op="callvirt", CalleeAssembly="App", VirtualDispatch=True,
         CallerCompilerGenerated=True, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # generated caller whose name reveals no enclosing method — counts for nobody, both sides
    dict(Caller="System.Void App.Service/<>c::.cctor()", Callee="System.Void App.Repo::Init()",
         Op="call", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=True, CalleeCompilerGenerated=False, NoDebugInfo=True),
    # property accessor — excluded cell
    dict(Caller="System.Void App.Service::Run()", Callee="System.String App.Repo::get_Name()",
         Op="callvirt", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # outside the corpus — excluded cell
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void System.Console::WriteLine()",
         Op="call", CalleeAssembly="System.Private.CoreLib", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # indirect op — excluded cell, but the callee stays classifiable
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.Repo::Hook()",
         Op="calli", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
]


def run_grader(oracle: Path, arm: Path, out: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(HERE / "grade.py"), "--oracle", str(oracle), "--arm", str(arm),
         "--first-party", "App", "--json", str(out)],
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


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        oracle = tmp / "oracle.jsonl"
        write(oracle, FIXTURE)

        # 1. identity — the answer key graded against itself
        arm = tmp / "arm.jsonl"
        write(arm, FIXTURE)
        report = run_grader(oracle, arm, tmp / "identity.json")

        for name in ("primary", "excluded — callee outside", "excluded — property"):
            entry = cell(report, name)
            if entry["oracle_edges"] == 0:
                failures.append(f"fixture covers no edges for cell {entry['cell']!r}")
            elif entry["precision"] != 1.0 or entry["recall"] != 1.0:
                failures.append(
                    f"identity broken in {entry['cell']!r}: "
                    f"precision={entry['precision']} recall={entry['recall']}")

        primary = cell(report, "primary")
        if primary["oracle_edges"] != 3:
            failures.append(
                f"expected 3 primary edges (call, ctor, lambda-remapped), got {primary['oracle_edges']}")

        # 2. the test must be able to fail: a missing edge has to cost recall
        write(arm, [r for r in FIXTURE if "Load" not in r["Callee"]])
        report = run_grader(oracle, arm, tmp / "missing.json")
        if cell(report, "primary")["recall"] >= 1.0:
            failures.append("dropping a real edge did not reduce recall — the check is inert")

        # 3. and an invented edge has to cost precision
        write(arm, FIXTURE + [dict(caller="App.Service::Run", callee="App.Ghost::Never")])
        report = run_grader(oracle, arm, tmp / "invented.json")
        if cell(report, "primary")["precision"] >= 1.0:
            failures.append("an edge to a method nobody calls did not reduce precision")

    if failures:
        print("\nFAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\nOK — grader is neutral, and the check can fail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
