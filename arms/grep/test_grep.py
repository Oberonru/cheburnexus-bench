#!/usr/bin/env python3
"""Checks for the grep arm against the synthetic fixture under testdata/repo.

No pytest dependency — a plain script in the same style as grader/test_identity.py, run directly:

    python3 arms/grep/test_grep.py

Three things are asserted, matching the task brief exactly:

  1. caller attribution — a call inside Widget.Run() is attributed to Widget::Run, not to some
     other declaration;
  2. the without-tests cell excludes a test file entirely — nothing from tests/WidgetTests.cs
     leaks into the without-tests output, neither as an edge nor as a candidate declaration;
  3. `new Widget(1)` produces a `.ctor` edge.

A fourth check covers the mechanism the whole arm exists to demonstrate: grep cannot resolve which
declaration a bare `Name(` belongs to, so a call to an ambiguous method name must fan out into one
edge per candidate (Widget.Helper AND Gadget.Helper both share the name `Helper`).

Every assertion here was proven capable of failing before this file was considered done: each was
broken in turn (attribution silently misrouted, cell exclusion removed, `.ctor` detection reverted
to the pre-fix bug, and the multi-candidate loop capped at one edge), observed to fail, then
restored. See the report for the exact way each was broken.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE / "testdata" / "repo"
RUN = HERE / "run.py"


def run_arm(cell: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "edges.jsonl"
        result = subprocess.run(
            [sys.executable, str(RUN), "--repo", str(REPO), "--cell", cell, "--out", str(out)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"arm exited {result.returncode}: {result.stderr}")
        import json
        rows = []
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows


def main() -> int:
    failures: list[str] = []

    without = run_arm("without-tests")
    with_tests = run_arm("with-tests")

    # 1. Caller attribution: the call to Helper() sitting inside Widget.Run() must be attributed
    # to Widget::Run, never to Widget::.ctor (the previous declaration above it) or Widget::Helper
    # (the declaration below it).
    run_edges = [e for e in without if e["caller"] == "Acme.Widgets.Widget::Run"]
    if not run_edges:
        failures.append("no edges attributed to Acme.Widgets.Widget::Run at all")
    bad_callers = {e["caller"] for e in without if "Helper" in e["callee"] or ".ctor" in e["callee"]}
    if bad_callers - {"Acme.Widgets.Widget::Run"}:
        failures.append(f"call sites attributed to the wrong caller: {bad_callers}")

    # 2. without-tests must exclude the test file completely: no edge from it, and its Helper()
    # declaration must not even appear as a candidate callee (which would prove it was read as a
    # source of declarations despite being excluded from source_files()).
    test_file_edges = [e for e in without if e.get("caller_file", "").startswith("tests/")]
    if test_file_edges:
        failures.append(f"without-tests leaked edges from the test file: {test_file_edges}")
    leaked_candidate = [e for e in without if e["callee"] == "Acme.Widgets.Tests.WidgetTests::Helper"]
    if leaked_candidate:
        failures.append("without-tests treated the test file's Helper() as a call candidate")
    # ...but with-tests must see it, or the split isn't actually doing anything.
    if not any(e.get("caller_file", "").startswith("tests/") for e in with_tests):
        failures.append("with-tests produced no edges from the test file — cell split looks broken")

    # 3. `new Widget(1)` must produce a `.ctor` edge.
    ctor_edges = [e for e in without if e["callee"] == "Acme.Widgets.Widget::.ctor"]
    if not ctor_edges:
        failures.append("new Widget(1) produced no .ctor edge")
    elif ctor_edges[0]["caller"] != "Acme.Widgets.Widget::Run":
        failures.append(f".ctor edge attributed to the wrong caller: {ctor_edges[0]['caller']}")

    # 4. Candidate explosion: Widget.Helper and Gadget.Helper share a method name, so the call to
    # Helper() inside Widget.Run() must fan out to BOTH — this is the mechanism the arm exists to
    # measure, not an incidental detail.
    helper_callees = {e["callee"] for e in without if e["caller"] == "Acme.Widgets.Widget::Run"
                       and e["callee"].endswith("::Helper")}
    expected = {"Acme.Widgets.Widget::Helper", "Acme.Widgets.Gadget::Helper"}
    if helper_callees != expected:
        failures.append(f"candidate fan-out wrong: got {helper_callees}, expected {expected}")

    # 5. Generic arity on the DECLARING TYPE: `class Cache<T>` must key as `Cache`1`, computed from
    # the declaration itself (testdata/repo/src/Cache.cs), never from a use site's argument count.
    arity_edges = [e for e in without if e["caller"] == "Acme.Widgets.Cache`1::GetTwice"]
    if not arity_edges:
        failures.append("no edge found with caller 'Acme.Widgets.Cache`1::GetTwice' — "
                         "generic declaring-type arity is not being keyed into Namespace.Type")
    elif arity_edges[0]["callee"] != "Acme.Widgets.Cache`1::Get":
        failures.append(f"arity-keyed callee wrong: got {arity_edges[0]['callee']!r}, "
                         f"expected 'Acme.Widgets.Cache`1::Get'")

    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"ok — {len(without)} edges without-tests, {len(with_tests)} edges with-tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
