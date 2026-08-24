#!/usr/bin/env python3
"""Tests for `is_test_path`, the shared with-tests/without-tests cell split.

Every arm (`grep`, `repowise`, `cheburnexus`) calls `armkit.is_test_path` — either directly, or
through `armkit.source_files` — to decide whether a file belongs to the `without-tests` cell. If
this function is wrong, the cell split is silently a no-op for whichever repository's test layout
it misses, and both cells come out identical without anything printing an error.

Defect #7 (found 2026-08-24): the original rule matched a path SEGMENT against
`TEST_DIR_MARKERS` by exact string equality only. That is correct for a top-level `test/` or
`tests/` directory (Polly: `test/Polly.Core.Tests/...`), but silent for a repository that names its
test project directory with a dotted suffix instead of a separate top-level directory
(FluentValidation: `src/FluentValidation.Tests/...`, `src/FluentValidation.Tests.Benchmarks/...`).
`corpus.json`'s own `test_detection_rule` for FluentValidation already documented this layout
("distinguished by name only") — the filter just did not implement it.

    python3 arms/_lib/test_armkit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import armkit  # noqa: E402

REPO_ROOT = Path("/repo")  # never touches the filesystem — is_test_path only inspects path parts


def check(relative: str, expect_test: bool) -> str | None:
    """Returns a failure string, or None if `relative` was classified as expected."""
    path = REPO_ROOT / relative
    got = armkit.is_test_path(path, REPO_ROOT)
    if got != expect_test:
        return (f"{relative!r}: expected is_test_path={expect_test}, got {got}")
    return None


CASES: list[tuple[str, bool, str]] = [
    # (relative path, expected is_test_path, why)
    ("test/Foo.Tests/X.cs", True,
     "top-level test/ directory — the original rule, must still work"),
    ("src/FluentValidation.Tests/X.cs", True,
     "defect #7: a project directory named with a .Tests dotted suffix, no separate test/ dir"),
    ("src/FluentValidation.Tests.Benchmarks/X.cs", True,
     "defect #7: two dotted suffixes stacked (...Tests.Benchmarks) — the LAST component decides"),
    ("src/Contest/X.cs", False,
     "guard against a naive substring fix: 'contest' contains 'test' but is not a test directory"),
    ("src/Latest/X.cs", False,
     "guard against a naive substring fix: 'latest' contains 'test' but is not a test directory"),
    ("src/X.cs", False,
     "a file directly in src/ — no directory segment at all should look like a test marker"),
]


def main() -> int:
    failures: list[str] = []
    for relative, expect_test, why in CASES:
        failure = check(relative, expect_test)
        if failure:
            failures.append(f"{failure} ({why})")

    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"ok — {len(CASES)} path-classification cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
