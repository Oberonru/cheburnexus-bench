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

    # Polly: build_command built only one of four declared product_assemblies, and the drop was
    # invisible — assemblies_for() silently shrank the answer key while the manifest still claimed
    # to cover the repository. missing_out must report exactly what was dropped, so a build_command
    # that doesn't build everything corpus.json declares shows up instead of being read as the arm
    # inventing false positives.
    missing: list[str] = []
    got = assemblies_for(entry_missing, checkout, "without-tests", missing)
    if got:
        failures.append("missing_out variant still selected a nonexistent assembly")
    if missing != ["src/App/bin/Release/net8.0/Absent.dll"]:
        failures.append(f"missing_out = {missing!r}, expected the one absent declared assembly")

    # A present assembly must never be reported as missing alongside a genuinely absent one.
    entry_mixed = dict(entry, product_assemblies=[
        "src/App/bin/Release/net8.0/App.dll",
        "src/App/bin/Release/net8.0/Absent.dll",
    ])
    missing_mixed: list[str] = []
    assemblies_for(entry_mixed, checkout, "without-tests", missing_mixed)
    if missing_mixed != ["src/App/bin/Release/net8.0/Absent.dll"]:
        failures.append(f"missing_out = {missing_mixed!r} for a mixed present/absent list, "
                        "expected only the absent one")
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

    # `source_files` returns platform-native separators (never a comparison key downstream) —
    # normalize to "/" so the hardcoded expectations below hold on both POSIX and Windows.
    without = {p.relative_to(repo).as_posix() for p in armkit.source_files(repo, "without-tests")}
    with_tests = {p.relative_to(repo).as_posix() for p in armkit.source_files(repo, "with-tests")}

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
    from run import CellNotMeasurable, build_oracle_csharp

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
        build_oracle_csharp(entry, checkout, "with-tests", tmp / "oracle-out")
        failures.append("with-tests was accepted although no test assembly exists — the cell would "
                        "publish a number produced by the harness rather than by any tool")
    except CellNotMeasurable:
        pass
    except Exception as unexpected:  # noqa: BLE001 - any other failure is still a failure to report
        failures.append(f"expected CellNotMeasurable, got {type(unexpected).__name__}: {unexpected}")
    return failures


def check_test_scope_guard(tmp: Path) -> list[str]:
    """`test_assemblies_projects` (what the arms read) and `test_assemblies` (what the answer key is
    built from) must describe the same set of projects for `with-tests`.

    This shipped once for real: serilog's `test_assemblies_projects` listed five test projects —
    the arms' read scope — while `test_assemblies` listed only two — the answer key's scope.
    Serilog.PerformanceTests, TestDummies and AotTestApp were read but never judgeable, so every
    call with a caller in one of them was junk by construction: the compiler recorded it, nobody put
    it in the key, and the arm was charged for reporting it correctly (e8, 2026-08-28).
    """
    from run import CellNotMeasurable, build_oracle_csharp, unmatched_test_scope_projects

    failures = []

    # The PRE-FIX shape: exactly serilog's corpus.json entry before e8.
    mismatched = {
        "target_framework_probed": "net8.0",
        "test_assemblies": [
            "test/Serilog.ApprovalTests/bin/Release/net8.0/Serilog.ApprovalTests.dll",
            "test/Serilog.Tests/bin/Release/net8.0/Serilog.Tests.dll",
        ],
        "test_assemblies_projects": [
            "test/Serilog.Tests/Serilog.Tests.csproj",
            "test/Serilog.ApprovalTests/Serilog.ApprovalTests.csproj",
            "test/Serilog.PerformanceTests/Serilog.PerformanceTests.csproj",
            "test/TestDummies/TestDummies.csproj (test helper, not itself a suite)",
            "test/AotTestApp/AotTestApp.csproj (AOT smoke-test app, not itself a suite)",
        ],
    }
    expected_unmatched = [
        "test/AotTestApp/AotTestApp.csproj",
        "test/Serilog.PerformanceTests/Serilog.PerformanceTests.csproj",
        "test/TestDummies/TestDummies.csproj",
    ]

    got = unmatched_test_scope_projects(mismatched, "with-tests")
    if got != expected_unmatched:
        failures.append(f"pre-fix shape: expected {expected_unmatched} unmatched, got {got}")
    if unmatched_test_scope_projects(mismatched, "without-tests"):
        failures.append("without-tests must never be affected — it never reads "
                        "test_assemblies_projects at all")

    # The guard must fire through build_oracle, the actual caller, not just exist as an unused
    # pure function. Assembly files are empty stand-ins: the raise happens before either is opened.
    checkout = tmp / "mismatched-repo"
    for relative in mismatched["test_assemblies"]:
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    entry = dict(mismatched, product_assemblies=[])
    try:
        build_oracle_csharp(entry, checkout, "with-tests", tmp / "oracle-out-mismatched")
        failures.append("build_oracle accepted a scope mismatch instead of refusing it")
    except CellNotMeasurable as why:
        message = str(why)
        print(f"    guard message (pre-fix shape): {message}")
        for project in expected_unmatched:
            if project not in message:
                failures.append(f"guard message did not name {project}: {message}")
    except Exception as unexpected:  # noqa: BLE001 - any other failure is still a failure to report
        failures.append(f"expected CellNotMeasurable, got {type(unexpected).__name__}: {unexpected}")

    # The POST-FIX shape: corpus.json's actual serilog entry today must be silent.
    corpus = json.loads((ROOT / "corpus.json").read_text(encoding="utf-8"))
    serilog = next(e for e in corpus if e["name"] == "serilog/serilog")
    still_unmatched = unmatched_test_scope_projects(serilog, "with-tests")
    print(f"    unmatched_test_scope_projects on corpus.json's serilog entry: {still_unmatched!r}")
    if still_unmatched:
        failures.append(f"corpus.json's serilog entry still has an unmatched scope: {still_unmatched}")

    # A repository whose test_assemblies is EMPTY (Polly, FluentValidation) must keep getting the
    # older, more specific "no test assembly was found on disk" message — this guard must not paper
    # over that refusal with a different one.
    checkout_empty = tmp / "empty-test-assemblies-repo"
    product_dll = checkout_empty / "src/App/bin/Release/net8.0/App.dll"
    product_dll.parent.mkdir(parents=True, exist_ok=True)
    product_dll.write_bytes(b"")
    empty_test_assemblies = dict(
        mismatched, test_assemblies=[],
        product_assemblies=["src/App/bin/Release/net8.0/App.dll"],
    )
    try:
        build_oracle_csharp(empty_test_assemblies, checkout_empty, "with-tests", tmp / "oracle-out-empty")
        failures.append("build_oracle accepted a with-tests cell with no test assembly at all")
    except CellNotMeasurable as why:
        if "no test assembly was found on disk" not in str(why):
            failures.append(f"the empty-test_assemblies case must keep raising the older, more "
                            f"specific message, got: {why}")
    except Exception as unexpected:  # noqa: BLE001
        failures.append(f"expected CellNotMeasurable, got {type(unexpected).__name__}: {unexpected}")

    return failures


# ── language axis, seam 2: corpus.json's `language` field must route to the right oracle, default
# to csharp when absent, and fail loudly (never a silent skip) for a language nobody registered ──

def check_oracle_builder_routing() -> list[str]:
    """`oracle_builder_for` is the one place `main()` decides which oracle answers a corpus.json
    entry. Exercised directly rather than through `main()` so this test needs no real checkout, no
    dotnet, and no grader."""
    from run import DEFAULT_LANGUAGE, ORACLE_BUILDERS, build_oracle_csharp, oracle_builder_for

    failures = []

    if DEFAULT_LANGUAGE != "csharp":
        failures.append(f"DEFAULT_LANGUAGE must be 'csharp' (every entry predating the language "
                        f"axis is a C# repo), got {DEFAULT_LANGUAGE!r}")

    explicit = oracle_builder_for({"name": "x/x", "language": "csharp"})
    if explicit is not build_oracle_csharp:
        failures.append("an entry that explicitly says language: csharp must route to "
                        "build_oracle_csharp")

    defaulted = oracle_builder_for({"name": "x/x"})
    if defaulted is not build_oracle_csharp:
        failures.append("an entry with no language field at all must default to csharp, not be "
                        "refused — every pre-existing corpus.json entry has no other language to "
                        "fall back to")

    try:
        oracle_builder_for({"name": "x/x", "language": "cobol"})
        failures.append("an unregistered language must raise SystemExit, not silently pick a "
                        "builder or return None — a corpus.json typo must not be swallowed into "
                        "CellNotMeasurable, which is reserved for a genuinely unmeasurable cell")
    except SystemExit as why:
        if "cobol" not in str(why) or "x/x" not in str(why):
            failures.append(f"the SystemExit message must name both the bad language and the "
                            f"repository so a config mistake is findable, got: {why}")

    # A second language's oracle now exists (oracle/typescript, shadow-stack runtime trace) — see
    # build_oracle_typescript in run.py. This assertion used to require exactly {"csharp"} "until a
    # second language's oracle exists"; that day arrived 2026-09-03, so the set check moves to
    # naming both known languages explicitly rather than being deleted outright.
    if set(ORACLE_BUILDERS) != {"csharp", "typescript"}:
        failures.append(f"ORACLE_BUILDERS should register exactly 'csharp' and 'typescript', "
                        f"got {sorted(ORACLE_BUILDERS)}")

    return failures


# ── typescript oracle: key translation + the with-tests-only guard, no node/npm required ──────

def check_typescript_key_and_guard() -> list[str]:
    """`_ts_key` and the without-tests refusal are pure Python and testable without a real TS
    corpus or any installed harness — the harness-invoking part of build_oracle_typescript is
    verified separately, by an actual run (see the session report, not this file)."""
    from run import CellNotMeasurable, ORACLE_BUILDERS, _ts_key, build_oracle_typescript

    failures = []

    if _ts_key("<module>") != "<module>::<entry>:0":
        failures.append("_ts_key('<module>') must map the root frame to a key containing a "
                        "literal '<' so grade.py's existing unresolvable-caller rule drops it")

    got = _ts_key("src/decorator/decorators.ts:ValidationTypes.isIn:42")
    want = "src/decorator/decorators.ts::ValidationTypes.isIn:42"
    if got != want:
        failures.append(f"_ts_key file:label:line -> Type::Method translation wrong: "
                        f"got {got!r}, want {want!r}")

    if "typescript" not in ORACLE_BUILDERS or ORACLE_BUILDERS["typescript"] is not build_oracle_typescript:
        failures.append("ORACLE_BUILDERS['typescript'] must route to build_oracle_typescript")

    try:
        build_oracle_typescript({"name": "x/x", "language": "typescript"}, Path("."),
                                 "without-tests", Path("."))
        failures.append("build_oracle_typescript('without-tests') must raise CellNotMeasurable — "
                        "a runtime oracle has no without-tests answer key to build")
    except CellNotMeasurable:
        pass
    except Exception as unexpected:  # noqa: BLE001
        failures.append(f"expected CellNotMeasurable for without-tests, got "
                        f"{type(unexpected).__name__}: {unexpected}")

    try:
        build_oracle_typescript({"name": "x/x", "language": "typescript"}, Path("."),
                                 "with-tests", Path("."))
        failures.append("build_oracle_typescript with no typescript_harness field must raise "
                        "SystemExit, not silently pick a default (harness is never auto-detected)")
    except SystemExit:
        pass
    except CellNotMeasurable as wrong:
        failures.append(f"missing typescript_harness is a config mistake (SystemExit), not an "
                        f"unmeasurable cell: {wrong}")

    return failures


def main() -> int:
    failures: list[str] = check_framework_selection()
    failures += check_oracle_builder_routing()
    failures += check_typescript_key_and_guard()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        failures += check_assembly_selection(tmp)
        failures += check_cell_split(tmp)
        failures += check_row_writer(tmp)
        failures += check_blocked_is_not_empty(tmp)
        failures += check_with_tests_refuses_a_fake_cell(tmp)
        failures += check_test_scope_guard(tmp)

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok — selection, cell split, row writer and the blocked outcome all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
