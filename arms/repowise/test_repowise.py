#!/usr/bin/env python3
"""Tests for the conversion that turns repowise's storage into our edge contract.

The competitor's arm is where an unfair result is easiest to produce by accident. Their graph stores
a node id built from a FILE PATH, not from a namespace, so every key we hand the grader is something
we reconstructed on their behalf. Reconstruct it slightly wrong and their column drops — and the
mistake looks exactly like their tool being weak.

So the reconstruction is pinned here, on synthetic rows shaped like theirs. No repowise install and
no indexing run is needed: these are the pure decisions, which is precisely the part that can be
unfair.

    python3 arms/repowise/test_repowise.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "arms" / "_lib"))
sys.path.insert(0, str(HERE))

import armkit  # noqa: E402
from run import _node_class_bare  # noqa: E402


def check_node_id_shapes() -> list[str]:
    """Their node id is `path::name` or `path::parent::name`, and nothing else is safe to assume."""
    failures = []
    cases = [
        # (node_id, file_path, name, expected enclosing type, why)
        ("src/App/Guard.cs::Guard::NotNull", "src/App/Guard.cs", "NotNull", "Guard",
         "the ordinary three-part shape: the middle segment is the enclosing type"),
        ("src/App/Program.cs::Main", "src/App/Program.cs", "Main", None,
         "two-part shape: repowise found no enclosing type, so we must not invent one"),
        ("src/A/B.cs::Outer.Inner::Do", "src/A/B.cs", "Do", "Outer.Inner",
         "a nested type arrives as one middle segment and is passed through unchanged"),
        ("something/else/entirely", "src/App/Guard.cs", "NotNull", None,
         "a shape we do not recognise yields nothing — guessing here would fabricate an edge"),
        ("src/App/Guard.cs::Guard::Other", "src/App/Guard.cs", "NotNull", None,
         "the row's own name column must match the tail, or the id belongs to a different symbol"),
    ]
    for node_id, file_path, name, expected, why in cases:
        got = _node_class_bare(node_id, file_path, name)
        if got != expected:
            failures.append(f"_node_class_bare({node_id!r}) = {got!r}, expected {expected!r} — {why}")

    # A file path containing the separator must not be mistaken for the type segment.
    tricky = _node_class_bare("src/od::d/File.cs::Type::M", "src/od::d/File.cs", "M")
    if tricky != "Type":
        failures.append(f"a path containing '::' broke the split: got {tricky!r}, expected 'Type'")
    return failures


def check_cell_filter_matches_every_other_arm() -> list[str]:
    """The competitor's cell split must be the same split the other two arms use.

    Their indexer cannot be told to skip tests, so the arm filters afterwards on the caller's file.
    If that filter drifted from `armkit.is_test_path`, the competitor would be measured on a
    different set of files than grep and our engine, and the columns would stop being comparable.
    """
    failures = []
    repo = Path("/repo")
    expectations = {
        "src/App/Service.cs": False,
        "test/App.Tests/ServiceTests.cs": True,
        "tests/Thing.cs": True,
        "src/App/ServiceTests.cs": False,   # test-shaped name, production directory
        "benchmarks/Bench.cs": True,
    }
    for relative, is_test in expectations.items():
        got = armkit.is_test_path(repo / relative, repo)
        if got is not is_test:
            failures.append(f"is_test_path({relative}) = {got}, expected {is_test} — the competitor "
                            "would then see a different file set than the other arms")
    return failures


def check_drops_are_counted_not_hidden() -> list[str]:
    """Edges we cannot key must be counted, because that count is a finding about their design.

    Their node identity is path-anchored; the share we cannot turn into a namespace-qualified key is
    the price of that choice. Buried inside a lower recall it reads as their tool missing calls,
    which is a different — and unfair — claim.
    """
    failures = []
    source = (HERE / "run.py").read_text(encoding="utf-8")
    for counter in ("dropped_missing_node", "dropped_no_class_segment"):
        if counter not in source:
            failures.append(f"the arm no longer counts {counter} — drops would become invisible")
    if "extract_stats.json" not in source:
        failures.append("the arm no longer writes extract_stats.json, so drop counts are not "
                        "recoverable after a run")
    return failures


def main() -> int:
    failures = check_node_id_shapes()
    failures += check_cell_filter_matches_every_other_arm()
    failures += check_drops_are_counted_not_hidden()

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok — key reconstruction, the shared cell split, and drop accounting all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
