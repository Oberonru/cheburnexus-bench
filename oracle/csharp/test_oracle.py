#!/usr/bin/env python3
"""Tests for the answer key itself.

Everything else in the polygon is measured against this tool, so a defect here does not produce a
wrong number in one cell — it silently redefines the truth for every arm at once, and no amount of
grader neutrality can detect it. The grader's identity check deliberately cannot help: it compares
the oracle to itself.

So this file compares the oracle to something it cannot influence: a C# fixture whose call graph is
known by construction, and whose source lines are located by `// MARK:` comments rather than by
number, so editing the fixture cannot quietly invalidate an assertion.

    python3 oracle/csharp/test_oracle.py

Requires the .NET SDK. Builds the fixture on first run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "testdata" / "Fixture"
SOURCE = FIXTURE / "Fixture.cs"
ASSEMBLY = FIXTURE / "bin" / "Release" / "net8.0" / "OracleFixture.dll"


def mark_line(marker: str) -> int:
    """The 1-based line carrying `// MARK: <marker>`."""
    for number, text in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), start=1):
        if f"// MARK: {marker}" in text:
            return number
    raise AssertionError(f"fixture has no marker {marker!r} — the test and the fixture disagree")


def build_fixture() -> None:
    result = subprocess.run(
        ["dotnet", "build", "-c", "Release", "-v", "q", "--nologo", str(FIXTURE / "Fixture.csproj")],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit("fixture build failed:\n" + result.stdout[-3000:] + result.stderr[-2000:])


def run_oracle(assembly: Path, out_dir: Path) -> tuple[list[dict], dict, list[dict]]:
    edges = out_dir / "edges.jsonl"
    overrides = out_dir / "overrides.json"
    result = subprocess.run(
        ["dotnet", "run", "-c", "Release", "--no-build", "--project", str(HERE), "--",
         str(assembly), "--source-root", str(FIXTURE),
         "--out", str(edges), "--overrides", str(overrides)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit("oracle failed:\n" + result.stderr[-3000:])
    rows = [json.loads(line) for line in edges.read_text(encoding="utf-8").splitlines() if line.strip()]
    implements_file = out_dir / "implements.jsonl"
    implements = ([json.loads(line) for line in implements_file.read_text(encoding="utf-8").splitlines() if line.strip()]
                  if implements_file.is_file() else [])
    return rows, json.loads(overrides.read_text(encoding="utf-8")), implements


def find(rows: list[dict], *, caller_has: str, callee_has: str, op: str | None = None) -> list[dict]:
    return [r for r in rows
            if caller_has in r["Caller"] and callee_has in r["Callee"]
            and (op is None or r["Op"] == op)]


def check_edges(rows: list[dict]) -> list[str]:
    failures: list[str] = []

    def expect(label: str, caller_has: str, callee_has: str, *, op: str | None = None,
               marker: str | None = None, virtual: bool | None = None, cgen: bool | None = None):
        hits = find(rows, caller_has=caller_has, callee_has=callee_has, op=op)
        if not hits:
            failures.append(f"{label}: no edge {caller_has} -> {callee_has}"
                            + (f" with op={op}" if op else ""))
            return
        row = hits[0]
        if marker is not None:
            want = mark_line(marker)
            if row["CallerLine"] != want:
                failures.append(f"{label}: anchored to line {row['CallerLine']}, "
                                f"the call is on line {want} (// MARK: {marker})")
        if virtual is not None and row["VirtualDispatch"] is not virtual:
            failures.append(f"{label}: VirtualDispatch={row['VirtualDispatch']}, expected {virtual}")
        if cgen is not None and row["CallerCompilerGenerated"] is not cgen:
            failures.append(f"{label}: CallerCompilerGenerated={row['CallerCompilerGenerated']}, "
                            f"expected {cgen}")

    # An ordinary call, anchored to the line a reader would point at.
    expect("plain call", "FileSink::Emit", "FileSink::Store",
           op="call", marker="plain-call", virtual=False, cgen=False)

    # Object creation is an edge to the constructor, not to the type.
    expect("constructor", "Pipeline::.ctor", "FileSink::.ctor",
           op="newobj", marker="ctor-call")

    # THE case the override map exists for: IL names the DECLARED interface method, never FileSink's
    # override, even though FileSink is the only implementation ever added to the list.
    expect("interface dispatch names the declaration", "Pipeline::Broadcast", "ISink::Emit",
           op="callvirt", marker="interface-dispatch", virtual=True)
    if find(rows, caller_has="Pipeline::Broadcast", callee_has="FileSink::Emit"):
        failures.append("Broadcast was linked straight to FileSink::Emit — IL cannot know that, so "
                        "the oracle invented a resolution the compiler never performed")

    # Constructs that produce IL calls no source-level tool would ever report. They must be present
    # and flagged, because the grader's excluded cells are built from exactly these.
    expect("property read is a call in IL", "Pipeline::FirstName", "get_Name", op="callvirt")
    expect("foreach emits the enumerator protocol", "Pipeline::Broadcast", "MoveNext",
           marker="foreach-loop")

    # Calls a human wrote that IL attributes to generated members. Dropping these would lose real
    # user code; the grader remaps the caller instead, and depends on the mangled name surviving.
    expect("call inside a lambda", "<EachLambda>b__", "ISink::Emit", cgen=True)
    expect("call inside an async method", "<DrainAsync>d__", "Pipeline::Broadcast",
           marker="async-call", cgen=True)
    expect("call inside a local function", "<WithLocal>g__Helper", "Pipeline::Broadcast",
           marker="local-fn-body", cgen=True)

    # The static constructor is its own method. Merging it with .ctor would let an arm score a hit
    # on the wrong one.
    cctor = find(rows, caller_has="Pipeline::.cctor", callee_has="Pipeline::Build")
    if not cctor:
        failures.append("no edge from the static constructor — .cctor bodies are being skipped")
    elif cctor[0]["CallerLine"] != mark_line("static-field-init"):
        failures.append(f"static field initialiser anchored to line {cctor[0]['CallerLine']}, "
                        f"expected {mark_line('static-field-init')}")

    # IL names the closed instantiation. The grader strips it; the oracle must NOT, or the two sides
    # would be doing the same normalisation in two places and could drift apart.
    generic = find(rows, caller_has="Pipeline::UseGeneric", callee_has="Wrap")
    if not generic:
        failures.append("no edge for the generic call")
    elif "<" not in generic[0]["Callee"].split("::")[-1]:
        failures.append(f"the generic callee arrived already normalised as {generic[0]['Callee']!r} — "
                        "normalisation belongs to the grader alone")

    return failures


def check_override_map(overrides: dict) -> list[str]:
    """Every implementation must point at what it answers for, or virtual calls score as misses."""
    failures = []

    def declarations_of(fragment: str) -> set[str]:
        for key, values in overrides.items():
            if fragment in key:
                return set(values)
        return set()

    file_emit = declarations_of("FileSink::Emit")
    if not any("SinkBase::Emit" in d for d in file_emit):
        failures.append("FileSink::Emit does not point at the base class method it overrides")
    if not any("ISink::Emit" in d for d in file_emit):
        failures.append("FileSink::Emit does not point at the interface method it implements "
                        "(implicit implementation is not being detected)")

    explicit = declarations_of("NullSink::OracleFixture.ISink.Emit")
    if not any("ISink::Emit" in d for d in explicit):
        failures.append("an explicit interface implementation is missing from the override map")

    return failures


def check_implements(implements: list[dict]) -> list[str]:
    """The type-level ground truth: one row per DIRECTLY-declared base/interface, no transitive closure."""
    failures = []

    def rel(type_has: str, target_has: str, kind: str) -> dict | None:
        hits = [r for r in implements
                if type_has in r["Type"] and target_has in r["Target"] and r["Kind"] == kind]
        return hits[0] if hits else None

    # FileSink : SinkBase — a direct base class.
    row = rel("FileSink", "SinkBase", "inherits")
    if row is None:
        failures.append("no inherits edge FileSink -> SinkBase")
    else:
        if row["TargetExternal"]:
            failures.append("FileSink -> SinkBase marked TargetExternal, but SinkBase is in the corpus")
        if row["TypeFile"] is None or "Fixture.cs" not in row["TypeFile"]:
            failures.append(f"FileSink's TypeFile is {row['TypeFile']!r}, expected the fixture source")
        if row["TypeLine"] is None:
            failures.append("FileSink's TypeLine is null even though the fixture has debug info")

    # FileSink must NOT show an edge to ISink directly — that relationship is SinkBase's, and this
    # contract is one row per DIRECT relationship; closure is the grader's job, not the oracle's.
    if rel("FileSink", "ISink", "implements") is not None:
        failures.append("FileSink -> ISink is not a direct relationship (it comes via SinkBase) — "
                        "the oracle expanded the closure itself, which the grader must do instead")

    # SinkBase : ISink — a direct interface implementation.
    if rel("SinkBase", "ISink", "implements") is None:
        failures.append("no implements edge SinkBase -> ISink")

    # NullSink : ISink — another direct interface implementation, on an unrelated type.
    if rel("NullSink", "ISink", "implements") is None:
        failures.append("no implements edge NullSink -> ISink")

    # Numbers : IEnumerable — a BCL interface. Must resolve as external.
    row = rel("Numbers", "IEnumerable", "implements")
    if row is None:
        failures.append("no implements edge Numbers -> IEnumerable")
    elif not row["TargetExternal"]:
        failures.append("Numbers -> IEnumerable not marked TargetExternal, but IEnumerable is BCL")

    # Every non-interface type has an inherits edge to something (System.Object at the root, if
    # nothing else) — a class with zero inherits rows would mean the base-type walk was skipped.
    if rel("SinkBase", "Object", "inherits") is None:
        failures.append("no inherits edge SinkBase -> System.Object")

    return failures


def check_missing_symbols_never_shrink(tmp: Path) -> list[str]:
    """Without a PDB the answer key must lose ANCHORS, never edges.

    This is the one failure mode the oracle must not have. An assembly quietly dropped for a symbol
    problem would shrink the truth itself, and every arm would then be scored against a smaller
    world — looking better, not worse, with no sign anything was missing.
    """
    failures = []
    stripped = tmp / "no-pdb"
    stripped.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSEMBLY, stripped / ASSEMBLY.name)  # deliberately without the .pdb

    with_symbols, _, _ = run_oracle(ASSEMBLY, tmp / "with")
    without_symbols, _, _ = run_oracle(stripped / ASSEMBLY.name, tmp / "without")

    if len(without_symbols) != len(with_symbols):
        failures.append(f"dropping the PDB changed the edge count "
                        f"{len(with_symbols)} -> {len(without_symbols)}; symbols may only affect anchors")
    if not all(r["NoDebugInfo"] for r in without_symbols):
        failures.append("rows extracted without symbols are not all flagged NoDebugInfo, so a "
                        "reader cannot tell which numbers rest on a source anchor")
    if any(r["CallerFile"] for r in without_symbols):
        failures.append("a source file was reported for an assembly with no symbols — invented anchor")
    return failures


def main() -> int:
    if not shutil.which("dotnet"):
        print("SKIPPED: no dotnet SDK on PATH — the answer key cannot be tested without one")
        return 0

    build_fixture()
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        rows, overrides, implements = run_oracle(ASSEMBLY, tmp / "main")
        if not rows:
            print("FAILED: the oracle produced no rows at all for the fixture")
            return 1
        failures += check_edges(rows)
        failures += check_override_map(overrides)
        failures += check_implements(implements)
        failures += check_missing_symbols_never_shrink(tmp)

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"ok — the answer key holds on {len(rows)} fixture edges: anchors, flags, virtual dispatch, "
          "generated callers, the override map, and no shrinkage without symbols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
