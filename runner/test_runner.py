#!/usr/bin/env python3
"""Tests for the parts of the harness that decide what gets measured at all.

These exist because of a specific failure. The runner selected assemblies with a substring test on
`/net8.0/`, Polly builds into Arcade's `release_net8.0`, and a third of the corpus disappeared from
the results table behind one line on stderr. Nothing was wrong with the oracle, the grader or any
arm — the measurement simply never happened for that repository, and the table looked complete.

A harness that can quietly measure less than it claims is more dangerous than one that is wrong out
loud, so the selection rules and the shared cell split are pinned here.

    python3 runner/test_runner.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "arms" / "_lib"))

import armkit  # noqa: E402
from run import assemblies_for, targets_framework  # noqa: E402


def check_framework_selection() -> list[str]:
    """The exact case that vanished, plus the neighbours that must NOT be swept in with it."""
    failures = []
    cases = [
        # (path, wanted, expected, why it matters)
        ("artifacts/bin/Polly.Core/release_net8.0/Polly.Core.dll", "net8.0", True,
         "Arcade layout — the case that silently dropped a third of the corpus"),
        ("src/Serilog/bin/Release/net8.0/Serilog.dll", "net8.0", True, "conventional layout"),
        ("artifacts/bin/X/debug_net8.0/X.dll", "net8.0", True, "Arcade debug configuration"),
        ("src/Serilog/bin/Release/net6.0/Serilog.dll", "net8.0", False,
         "a different framework must not be counted: its copies of the same methods would "
         "duplicate the answer key"),
        ("src/X/bin/Release/netstandard2.0/X.dll", "net8.0", False, "netstandard is not net8.0"),
        ("src/X/bin/Release/net8.0.1/X.dll", "net8.0", False,
         "a longer version string only prefixed by ours is a different framework"),
        (r"src\X\bin\Release\net8.0\X.dll", "net8.0", True, "Windows separators still resolve"),
    ]
    for path, wanted, expected, why in cases:
        got = targets_framework(path, wanted)
        if got is not expected:
            failures.append(f"targets_framework({path!r}, {wanted!r}) = {got}, expected {expected} — {why}")
    return failures


def check_assembly_selection(tmp: Path) -> list[str]:
    """with-tests adds the test assemblies; without-tests must not leak a single one."""
    failures = []
    checkout = tmp / "checkout"
    for relative in ("src/App/bin/Release/net8.0/App.dll",
                     "src/App/bin/Release/net6.0/App.dll",
                     "test/App.Tests/bin/Release/net8.0/App.Tests.dll"):
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    entry = {
        "target_framework_probed": "net8.0",
        "product_assemblies": ["src/App/bin/Release/net8.0/App.dll",
                               "src/App/bin/Release/net6.0/App.dll"],
        "test_assemblies": ["test/App.Tests/bin/Release/net8.0/App.Tests.dll"],
    }

    without = {p.name for p in assemblies_for(entry, checkout, "without-tests")}
    with_tests = {p.name for p in assemblies_for(entry, checkout, "with-tests")}

    if without != {"App.dll"}:
        failures.append(f"without-tests selected {sorted(without)}, expected just App.dll "
                        "(net6.0 copy excluded, test assembly excluded)")
    if with_tests != {"App.dll", "App.Tests.dll"}:
        failures.append(f"with-tests selected {sorted(with_tests)}, expected App.dll + App.Tests.dll")

    # A path listed in the corpus but never built must not be invented into the answer key.
    entry_missing = dict(entry, product_assemblies=["src/App/bin/Release/net8.0/Absent.dll"])
    if assemblies_for(entry_missing, checkout, "without-tests"):
        failures.append("an assembly that does not exist on disk was selected")
    return failures


def check_cell_split(tmp: Path) -> list[str]:
    """The cell split is shared by every arm, so a change here silently moves all three columns."""
    failures = []
    repo = tmp / "repo"
    files = {
        "src/App/Service.cs": False,
        "src/App/ServiceTests.cs": False,   # test-shaped NAME in production directory: not a test
        "test/App.Tests/ServiceTests.cs": True,
        "tests/Other/Thing.cs": True,
        "src/App/obj/Debug/Generated.cs": None,   # build output: excluded from both cells
        "src/App/bin/Release/Copy.cs": None,
    }
    for relative in files:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// x\n", encoding="utf-8")

    for relative, is_test in files.items():
        if is_test is None:
            continue
        got = armkit.is_test_path(repo / relative, repo)
        if got is not is_test:
            failures.append(f"is_test_path({relative}) = {got}, expected {is_test}")

    without = {str(p.relative_to(repo)) for p in armkit.source_files(repo, "without-tests")}
    with_tests = {str(p.relative_to(repo)) for p in armkit.source_files(repo, "with-tests")}

    if any("obj/" in p or "bin/" in p for p in without | with_tests):
        failures.append("build output leaked into a cell — generated code is not source under test")
    if "test/App.Tests/ServiceTests.cs" in without:
        failures.append("a test file survived into without-tests")
    if "test/App.Tests/ServiceTests.cs" not in with_tests:
        failures.append("with-tests lost a test file")
    if "src/App/ServiceTests.cs" not in without:
        failures.append("a production file was excluded for being named like a test")

    ordered = [str(p) for p in armkit.source_files(repo, "with-tests")]
    if ordered != sorted(ordered):
        failures.append("source_files is not sorted — two machines would produce different output")
    return failures


def check_row_writer(tmp: Path) -> list[str]:
    """Byte-identical output for the same facts: published rows are only checkable if stable."""
    failures = []
    edges = [
        armkit.Edge("B::b", "C::c", "b.cs", 2),
        armkit.Edge("A::a", "B::b", "a.cs", 1),
        armkit.Edge("A::a", "B::b", "a.cs", 1),   # the same fact stated twice
    ]
    first, second = tmp / "one.jsonl", tmp / "two.jsonl"
    written = armkit.write_edges(first, edges)
    armkit.write_edges(second, list(reversed(edges)))

    if written != 2:
        failures.append(f"write_edges reported {written} rows for 2 distinct edges — dedup is broken")
    if first.read_bytes() != second.read_bytes():
        failures.append("the same edges in a different order produced different bytes")
    rows = [json.loads(line) for line in first.read_text(encoding="utf-8").splitlines()]
    if rows and rows[0]["caller"] != "A::a":
        failures.append(f"rows are not sorted: first caller is {rows[0]['caller']!r}")
    if any("caller_file" in r and r["caller_file"] is None for r in rows):
        failures.append("a field the arm could not fill was written as null instead of omitted")
    return failures


def check_blocked_is_not_empty(tmp: Path) -> list[str]:
    """An arm refused the data must exit 3, never 0 with no rows.

    Refused and empty are different facts. Collapsing them prints recall 0.000 beside a tool that
    never got to look — a false statement about that tool, and the one we would have published
    about our own engine.
    """
    failures = []
    arm = tmp / "blocked_arm.py"
    arm.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'arms' / '_lib')!r})\n"
        "import armkit\n"
        "def collect(repo, cell):\n"
        "    raise armkit.Blocked('licence withheld the graph')\n"
        "sys.exit(armkit.main('blocked', lambda: 'test', collect))\n",
        encoding="utf-8")

    repo = tmp / "repo"
    repo.mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(arm), "--repo", str(repo), "--cell", "without-tests",
         "--out", str(tmp / "out.jsonl")],
        capture_output=True, text=True)

    if result.returncode != armkit.EXIT_BLOCKED:
        failures.append(f"a blocked arm exited {result.returncode}, expected {armkit.EXIT_BLOCKED}")
    if "licence withheld the graph" not in result.stderr:
        failures.append("the reason for being blocked did not reach stderr, so no table can show it")
    if (tmp / "out.jsonl").exists():
        failures.append("a blocked arm wrote an output file — it would be graded as a real zero")
    return failures


def check_with_tests_refuses_a_fake_cell(tmp: Path) -> list[str]:
    """A with-tests cell with no test assembly built must be refused, not scored.

    This shipped once: the corpus build command compiles only the product projects, so the answer
    key was byte-identical in both cells while the arms happily read the test sources. Serilog's
    grep precision "fell" from 0.259 to 0.044 — a real-looking number produced entirely by the
    harness comparing a grown output against an unchanged key.
    """
    from run import CellNotMeasurable, build_oracle

    failures = []
    checkout = tmp / "fake"
    built = checkout / "src/App/bin/Release/net8.0/App.dll"
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_bytes(b"")

    entry = {
        "target_framework_probed": "net8.0",
        "product_assemblies": ["src/App/bin/Release/net8.0/App.dll"],
        # listed in the corpus, never compiled — exactly the real situation
        "test_assemblies": ["test/App.Tests/bin/Release/net8.0/App.Tests.dll"],
    }
    try:
        build_oracle(entry, checkout, "with-tests", tmp / "oracle-out")
        failures.append("with-tests was accepted although no test assembly exists — the cell would "
                        "publish a number produced by the harness rather than by any tool")
    except CellNotMeasurable:
        pass
    except Exception as unexpected:  # noqa: BLE001 - any other failure is still a failure to report
        failures.append(f"expected CellNotMeasurable, got {type(unexpected).__name__}: {unexpected}")
    return failures


def main() -> int:
    failures: list[str] = check_framework_selection()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        failures += check_assembly_selection(tmp)
        failures += check_cell_split(tmp)
        failures += check_row_writer(tmp)
        failures += check_blocked_is_not_empty(tmp)
        failures += check_with_tests_refuses_a_fake_cell(tmp)

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok — selection, cell split, row writer and the blocked outcome all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
