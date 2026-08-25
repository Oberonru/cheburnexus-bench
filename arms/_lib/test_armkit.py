#!/usr/bin/env python3
"""Tests for `is_test_path` and the corpus-scope functions, the shared with-tests/without-tests
cell split and the shared first-party-scope filter.

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

Defect #10 (found 2026-08-24): `is_test_path` (and `source_files`, before this fix) only ever
excluded TEST code from the without-tests cell — nothing excluded a sibling, non-first-party
project that is not a test directory at all. Polly's pinned checkout carries `src/Polly/`, the
legacy pre-Polly.Core library (excluded from the corpus, a different assembly), sharing the
`Polly` namespace with `Polly.Core`. Every arm scanned it, and on the 2026-08-24 measurement 369
of the cheburnexus arm's 1005 emitted edges originated there, all keyed indistinguishably from
genuine `Polly.Core` callees — forced false positives that collapsed precision for all three arms
on Polly. `scope_dirs_from_entry`/`in_declared_scope`/`corpus_scope_dirs` fix this by scoping every
arm's search to corpus.json's own declared `product_projects`/`test_assemblies_projects`.

    python3 arms/_lib/test_armkit.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
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


# ── defect #10: first-party scope ────────────────────────────────────────────────────────────────

def check_scope(relative: str, scope_dirs: list[str] | None, expect_in_scope: bool) -> str | None:
    path = REPO_ROOT / relative
    got = armkit.in_declared_scope(path, REPO_ROOT, scope_dirs)
    if got != expect_in_scope:
        return f"{relative!r} against {scope_dirs!r}: expected in_declared_scope={expect_in_scope}, got {got}"
    return None


POLLY_PRODUCT_SCOPE = [
    "src/Polly.Core", "src/Polly.Extensions", "src/Polly.RateLimiting", "src/Polly.Testing",
]

SCOPE_CASES: list[tuple[str, list[str] | None, bool, str]] = [
    # (relative path, scope_dirs, expected in_declared_scope, why)
    ("src/Polly.Core/Utils/Guard.cs", POLLY_PRODUCT_SCOPE, True,
     "file under a declared first-party project directory — must be IN scope"),
    ("src/Polly.Core/CircuitBreaker/Controller/CircuitStateController.cs", POLLY_PRODUCT_SCOPE, True,
     "nested arbitrarily deep under a declared project directory — still IN scope"),
    ("src/Polly/Context.Dictionary.cs", POLLY_PRODUCT_SCOPE, False,
     "defect #10: the legacy sibling project, same string prefix as every 'src/Polly.*' declared "
     "dir, different assembly, not declared — must be OUT of scope"),
    ("src/Polly/Utils/SomeHelper.cs", POLLY_PRODUCT_SCOPE, False,
     "defect #10, nested: still the legacy project two levels down — still OUT of scope"),
    ("src/Polly.Core.Tests/FooTests.cs", POLLY_PRODUCT_SCOPE, False,
     "guard against a naive string-prefix fix: 'src/Polly.Core.Tests' starts with the string "
     "'src/Polly.Core' but is a different, undeclared directory — must be OUT of scope"),
    ("src/Snippets/Program.cs", POLLY_PRODUCT_SCOPE, False,
     "a real sibling project (code samples) that is simply not declared as product — OUT of scope"),
    ("src/Polly.Extensions/Telemetry/TagsList.cs", POLLY_PRODUCT_SCOPE, True,
     "a repo whose corpus record lists SEVERAL first-party projects — every one of them must be "
     "reachable, not just the first"),
    ("src/Polly.RateLimiting/RateLimiterRejectedException.cs", POLLY_PRODUCT_SCOPE, True,
     "same check, a third of the several declared projects"),
    ("src/Polly.Testing/ResiliencePipelineDescriptor.cs", POLLY_PRODUCT_SCOPE, True,
     "same check, the fourth declared project"),
    ("anything/anywhere.cs", None, True,
     "scope_dirs=None means corpus.json had nothing to say — unscoped, everything passes"),
]


ENTRY_WITH_TESTS = {
    "product_projects": [
        "src/Polly.Core/Polly.Core.csproj",
        "src/Polly.Extensions/Polly.Extensions.csproj",
        "src/Polly.RateLimiting/Polly.RateLimiting.csproj",
        "src/Polly.Testing/Polly.Testing.csproj",
    ],
    "test_assemblies_projects": [
        "test/Polly.Core.Tests/Polly.Core.Tests.csproj",
        "test/Polly.TestUtils/Polly.TestUtils.csproj (test helper, not itself a suite)",
    ],
}

ENTRY_NO_PRODUCT_PROJECTS = {"product_projects": []}
ENTRY_MISSING_FIELD = {"name": "example/example"}


def check_entry() -> list[str]:
    failures: list[str] = []

    without = armkit.scope_dirs_from_entry(ENTRY_WITH_TESTS, "without-tests")
    expected_without = sorted([
        "src/Polly.Core", "src/Polly.Extensions", "src/Polly.RateLimiting", "src/Polly.Testing",
    ])
    if without != expected_without:
        failures.append(
            f"scope_dirs_from_entry(without-tests): expected {expected_without}, got {without} "
            f"(a repo record listing several first-party projects must include ALL of them)"
        )

    with_tests = armkit.scope_dirs_from_entry(ENTRY_WITH_TESTS, "with-tests")
    expected_with = sorted(expected_without + ["test/Polly.Core.Tests", "test/Polly.TestUtils"])
    if with_tests != expected_with:
        failures.append(
            f"scope_dirs_from_entry(with-tests): expected {expected_with}, got {with_tests} "
            f"(with-tests must ALSO include the declared test projects, and strip the trailing "
            f"parenthetical note from 'Polly.TestUtils.csproj (test helper, ...)')"
        )

    if armkit.scope_dirs_from_entry(ENTRY_NO_PRODUCT_PROJECTS, "without-tests") is not None:
        failures.append(
            "scope_dirs_from_entry with an empty product_projects list must return None "
            "(insufficient to derive a scope), not an empty-but-valid []"
        )

    if armkit.scope_dirs_from_entry(ENTRY_MISSING_FIELD, "without-tests") is not None:
        failures.append(
            "scope_dirs_from_entry with no product_projects key at all must return None, not "
            "invent a rule"
        )

    return failures


# ── defect #10: source_files() end to end — the existing without-tests/with-tests split must
# still hold once scope is layered on top of it ──────────────────────────────────────────────────

def check_source_files_integration() -> list[str]:
    """Builds a tiny fake checkout on disk, monkeypatches `armkit.corpus_scope_dirs` (so this test
    does not depend on the real corpus.json / real corpus checkouts), and asserts `source_files`
    combines the scope filter with the pre-existing test/product cell split correctly in both
    directions — a scoped-out product file, and a scoped-out test file, must never leak into either
    cell, and a scoped-in file must still respect the without-tests/with-tests split exactly as it
    did before this fix."""
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="armkit_scope_test_"))
    try:
        files = {
            "src/Polly.Core/Foo.cs": True,           # declared product -> always in scope
            "src/Polly/Bar.cs": False,                # legacy sibling, not declared -> always out
            "test/Polly.Core.Tests/FooTests.cs": True,  # declared test project
        }
        for relative in files:
            full = tmp / relative
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("// fixture\n", encoding="utf-8")

        real_corpus_scope_dirs = armkit.corpus_scope_dirs

        def fake_scope_dirs(repo_root: Path, cell: str):
            if cell == "without-tests":
                return ("src/Polly.Core",)
            return ("src/Polly.Core", "test/Polly.Core.Tests")

        armkit.corpus_scope_dirs = fake_scope_dirs
        try:
            without = {p.relative_to(tmp).as_posix() for p in armkit.source_files(tmp, "without-tests")}
            with_tests = {p.relative_to(tmp).as_posix() for p in armkit.source_files(tmp, "with-tests")}
        finally:
            armkit.corpus_scope_dirs = real_corpus_scope_dirs

        if without != {"src/Polly.Core/Foo.cs"}:
            failures.append(
                f"without-tests: expected only the in-scope product file, got {without} "
                f"(scope must exclude the legacy sibling, is_test_path must still exclude the "
                f"declared test project)"
            )
        if with_tests != {"src/Polly.Core/Foo.cs", "test/Polly.Core.Tests/FooTests.cs"}:
            failures.append(
                f"with-tests: expected the product file AND the declared test file, got "
                f"{with_tests} (with-tests must still include tests once scope is applied, and "
                f"must still exclude the undeclared legacy sibling)"
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return failures


def main() -> int:
    failures: list[str] = []
    for relative, expect_test, why in CASES:
        failure = check(relative, expect_test)
        if failure:
            failures.append(f"{failure} ({why})")

    scope_case_count = 0
    for relative, scope_dirs, expect_in_scope, why in SCOPE_CASES:
        scope_case_count += 1
        failure = check_scope(relative, scope_dirs, expect_in_scope)
        if failure:
            failures.append(f"{failure} ({why})")

    failures.extend(check_entry())
    failures.extend(check_source_files_integration())

    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"ok — {len(CASES)} path-classification cases, {scope_case_count} scope cases, "
          f"scope_dirs_from_entry cases, source_files scope+cell integration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
