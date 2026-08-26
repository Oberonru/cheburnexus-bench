#!/usr/bin/env python3
"""Proves ceiling_grep_filter.py's comparative (grep vs cheburnexus) measurement actually runs.

The real results directory (results/full-matrix-2026-08-24) does not exist on this machine, so
this test builds a tiny SYNTHETIC fixture that mimics the on-disk shape
`RESULTS_ROOT/<repo>/without-tests/{grep,cheburnexus,_oracle}/...` and calls `analyze_arm` /
`comparative_summary` directly against it — no subprocess, no real data, no network.

The fixture is built so the comparison has real teeth:
  - one edge BOTH arms confirm (the honest common ground);
  - one contradicted edge ONLY grep emits (grep invents something cheburnexus does not);
  - one excluded-cell edge (a `get_`/`set_` accessor callee — the oracle has no opinion there);
  - one row grep DUPLICATES verbatim, so grep's rendered-byte total must exceed what summing its
    distinct edges alone would give — proving row-level (not edge-level) byte counting is in effect.

    python3 grader/test_junk_per_answer.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import tempfile

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ceiling_grep_filter as cgf  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def build_fixture(tmp: Path) -> Path:
    """Lay out RESULTS_ROOT/<repo>/without-tests/{grep,cheburnexus,_oracle}/... under tmp."""
    repo = "FixtureRepo"
    base = tmp / repo / "without-tests"

    # ── oracle: what the compiler actually recorded ──────────────────────────────
    oracle_rows = [
        # the one edge both arms will confirm
        dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.Repo::Load()",
             Op="call", CalleeAssembly="App", VirtualDispatch=False,
             CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
        # an accessor call, so callee_cell classifies get_Name as "accessor" (excluded)
        dict(Caller="System.Void App.Service::Run()", Callee="System.String App.Repo::get_Name()",
             Op="callvirt", CalleeAssembly="App", VirtualDispatch=False,
             CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    ]
    write_jsonl(base / "_oracle" / "oracle.jsonl", oracle_rows)
    (base / "_oracle" / "overrides.json").write_text("{}", encoding="utf-8")

    # ── grep arm: confirmed edge + a made-up contradicted edge + a duplicate raw row ──
    grep_rows = [
        dict(caller="App.Service::Run", callee="App.Repo::Load",
             caller_file="Service.cs", caller_line=10),
        # duplicate of the row above — same (caller,callee) pair, same location, repeated raw line
        dict(caller="App.Service::Run", callee="App.Repo::Load",
             caller_file="Service.cs", caller_line=10),
        # grep invents a call the oracle never recorded — contradicted, only grep emits this
        dict(caller="App.Service::Run", callee="App.Ghost::Never",
             caller_file="Service.cs", caller_line=11),
        # excluded-cell accessor call
        dict(caller="App.Service::Run", callee="App.Repo::get_Name",
             caller_file="Service.cs", caller_line=12),
    ]
    write_jsonl(base / "grep" / "edges.jsonl", grep_rows)
    (base / "grep" / "manifest.json").write_text(json.dumps({"first_party": ["App"]}), encoding="utf-8")

    # ── cheburnexus arm: only the confirmed edge, no duplicate, no invented edge ──
    cheb_rows = [
        dict(caller="App.Service::Run", callee="App.Repo::Load",
             caller_file="Service.cs", caller_line=10),
    ]
    write_jsonl(base / "cheburnexus" / "edges.jsonl", cheb_rows)
    (base / "cheburnexus" / "manifest.json").write_text(
        json.dumps({"first_party": ["App"]}), encoding="utf-8")

    return tmp


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = build_fixture(Path(tmp_str))
        repo = "FixtureRepo"

        grep_result = cgf.analyze_arm(repo, "grep", results_root=tmp)
        cheb_result = cgf.analyze_arm(repo, "cheburnexus", results_root=tmp)

        # sanity: classification landed where the fixture intended
        if grep_result["counts"]["confirmed"] != 2:  # the row + its duplicate
            failures.append(f"expected 2 confirmed grep rows (incl. duplicate), "
                             f"got {grep_result['counts']['confirmed']}")
        if grep_result["counts"]["contradicted"] != 1:
            failures.append(f"expected 1 contradicted grep row, got {grep_result['counts']['contradicted']}")
        if grep_result["counts"]["excluded"] != 1:
            failures.append(f"expected 1 excluded grep row, got {grep_result['counts']['excluded']}")
        if cheb_result["counts"]["confirmed"] != 1:
            failures.append(f"expected 1 confirmed cheburnexus row, got {cheb_result['counts']['confirmed']}")
        if cheb_result["counts"]["contradicted"] != 0 or cheb_result["counts"]["excluded"] != 0:
            failures.append("cheburnexus fixture should have zero junk rows")

        summary = cgf.comparative_summary(repo, {"grep": grep_result, "cheburnexus": cheb_result})
        grep_s = summary["arms"]["grep"]
        cheb_s = summary["arms"]["cheburnexus"]

        # (a) grep's proven-wrong (honest junk) fraction must exceed cheburnexus's
        if not (grep_s["proven_wrong_byte_fraction"] > cheb_s["proven_wrong_byte_fraction"]):
            failures.append(
                f"(a) grep proven_wrong_byte_fraction={grep_s['proven_wrong_byte_fraction']} did not "
                f"exceed cheburnexus's {cheb_s['proven_wrong_byte_fraction']}")

        # (a2) REGRESSION: proven-wrong counts ONLY contradicted bytes, never the excluded accessor.
        # grep's proven-wrong bytes must equal exactly its contradicted byte_sum — if the excluded
        # get_Name row leaked in, calling a legitimate out-of-cell edge "junk" would be an overclaim.
        grep_total = grep_result["total_rendered_bytes"]
        expected_pw = round(grep_result["byte_sums"]["contradicted"] / grep_total, 4)
        if grep_s["proven_wrong_byte_fraction"] != expected_pw:
            failures.append(
                f"(a2) proven_wrong_byte_fraction={grep_s['proven_wrong_byte_fraction']} != "
                f"contradicted-only {expected_pw} — excluded/unremappable bytes leaked into junk")

        # (b) grep's total rendered bytes must exceed cheburnexus's
        if not (grep_s["total_rendered_bytes"] > cheb_s["total_rendered_bytes"]):
            failures.append(
                f"(b) grep total_rendered_bytes={grep_s['total_rendered_bytes']} did not exceed "
                f"cheburnexus's {cheb_s['total_rendered_bytes']}")

        # (c) the duplicate row makes grep's rendered bytes exceed its distinct-edge count would
        # give: distinct edges seen by grep are {Load (confirmed), Ghost::Never (contradicted),
        # get_Name (excluded)} = 3 distinct edges, but 4 raw rows were rendered (the duplicate).
        distinct_edge_total = sum(grep_result["distinct_edge_counts"].values())
        if not (grep_result["total_edges_raw_rows"] > distinct_edge_total):
            failures.append(
                f"(c) duplicate row did not inflate raw rows ({grep_result['total_edges_raw_rows']}) "
                f"past distinct edges ({distinct_edge_total})")
        # and confirm the row-level byte sum actually double-counts the duplicated line, i.e. the
        # confirmed byte_sum is exactly 2x one rendered line's bytes, not 1x.
        one_line_bytes = len(cgf.render_line({"caller_file": "Service.cs",
                                               "caller_line": 10,
                                               "callee": "App.Repo::Load"}).encode("utf-8"))
        if grep_result["byte_sums"]["confirmed"] != 2 * one_line_bytes:
            failures.append(
                f"(c) confirmed byte_sum {grep_result['byte_sums']['confirmed']} is not 2x one "
                f"rendered line ({one_line_bytes}) — row-level duplication is not being counted")

    if failures:
        print("\nFAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\nOK — junk-per-answer comparison runs on a synthetic fixture: grep's proven-wrong "
          "(contradicted-only, no excluded leak) fraction and total bytes both exceed cheburnexus's, "
          "and the duplicate raw row inflates grep's byte count past what distinct-edge counting "
          "alone would give.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
