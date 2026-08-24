#!/usr/bin/env python3
"""Tests for our own arm's conversion, and for the one behaviour we must never get wrong.

Our engine's output has to be reshaped into the edge contract, and this arm is the one place where a
generous mistake would flatter us. It is also the arm that currently cannot run without a licence,
which makes the second half of this file the important half: being refused must stay distinguishable
from finding nothing.

No engine run is needed — these are the pure decisions.

    python3 arms/cheburnexus/test_cheburnexus.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "arms" / "_lib"))
sys.path.insert(0, str(HERE))

import armkit  # noqa: E402
from run import _parse_raw_key, _to_repo_relative, backtick_arity  # noqa: E402


def check_generic_arity() -> list[str]:
    """Arity is kept, closed arguments go. The oracle spells `Cache`1`, so we must too."""
    failures = []
    cases = [
        ("Cache<T>", "Cache`1", "one parameter"),
        ("Pair<K,V>", "Pair`2", "two parameters"),
        ("Map<K,List<V>>", "Map`2", "a nested argument is one parameter, not two"),
        ("Plain", "Plain", "a non-generic name is untouched"),
        ("Cache`1", "Cache`1", "already-suffixed input must not be suffixed twice"),
    ]
    for raw, expected, why in cases:
        got = backtick_arity(raw)
        if got != expected:
            failures.append(f"backtick_arity({raw!r}) = {got!r}, expected {expected!r} — {why}")
    return failures


def check_key_parsing() -> list[str]:
    """The engine's own key shape, split only on the separator it actually inserts."""
    failures = []

    parsed = _parse_raw_key("src/App/Guard.cs::App.Guard::NotNull(System.String)")
    if parsed != ("src/App/Guard.cs", "App.Guard", "NotNull"):
        failures.append(f"ordinary key parsed as {parsed!r}")

    # A Windows-style path carrying a drive letter still splits on '::' alone.
    parsed = _parse_raw_key(r"C:/repo/src/A.cs::App.A::Run()")
    if parsed is None or parsed[2] != "Run":
        failures.append(f"a drive-lettered path broke parsing: {parsed!r}")

    if _parse_raw_key("not-a-key") is not None:
        failures.append("a malformed key was parsed instead of rejected — a bad row must be "
                        "dropped, never turned into an edge that scores")
    return failures


def check_paths_are_repo_relative() -> list[str]:
    """Anchors must be comparable across machines, and never invented when they are not."""
    failures = []
    repo = Path("/home/u/repo")

    got = _to_repo_relative("/home/u/repo/src/App/Service.cs", repo)
    if got != "src/App/Service.cs":
        failures.append(f"a path under the checkout was not made relative: {got!r}")

    got = _to_repo_relative(r"\\home\\u\\repo\\src\\App\\Service.cs".replace("\\\\", "\\"), repo)
    if "\\" in got:
        failures.append(f"backslashes survived into the anchor: {got!r} — rows must be portable")

    outside = _to_repo_relative("/elsewhere/Other.cs", repo)
    if outside != "/elsewhere/Other.cs":
        failures.append(f"a path outside the checkout was rewritten to {outside!r} — better an "
                        "honest absolute path than an invented relative one")
    return failures


def check_refused_is_not_empty() -> list[str]:
    """The licence wall must surface as `blocked`, never as an arm that looked and found nothing.

    This is the assertion that matters most in this file. Our engine analyses a repository cleanly
    and then withholds the call graph, because the graph is the paid feature. Reporting that as zero
    edges would publish `recall 0.000` beside our own product — a claim that it searched and failed,
    when it was never allowed to search.
    """
    failures = []
    source = (HERE / "run.py").read_text(encoding="utf-8")
    if "armkit.Blocked" not in source:
        failures.append("the arm no longer raises armkit.Blocked — a licence refusal would be "
                        "graded as a genuine zero")
    if "calls-counts.json" not in source:
        failures.append("the arm no longer recognises the counts-only output that marks the wall")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        probe = tmp / "arm.py"
        probe.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'arms' / '_lib')!r})\n"
            "import armkit\n"
            "def collect(repo, cell):\n"
            "    raise armkit.Blocked('analyzer.semantic is a Pro feature')\n"
            "sys.exit(armkit.main('cheburnexus', lambda: 'test', collect))\n",
            encoding="utf-8")
        (tmp / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(probe), "--repo", str(tmp / "repo"),
             "--cell", "without-tests", "--out", str(tmp / "out.jsonl")],
            capture_output=True, text=True)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(f"a refused arm exited {result.returncode}, expected {armkit.EXIT_BLOCKED}")
        if (tmp / "out.jsonl").exists():
            failures.append("a refused arm produced an output file, which the grader would score")
    return failures


def check_describe_carries_identity() -> list[str]:
    """A published row set is worthless if it cannot be tied to the version that produced it."""
    failures = []
    result = subprocess.run([sys.executable, str(HERE / "run.py"), "--describe"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        failures.append(f"--describe failed: {result.stderr.strip()[:200]}")
        return failures
    try:
        described = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        failures.append(f"--describe did not print one JSON object: {result.stdout[:200]!r}")
        return failures
    if described.get("name") != "cheburnexus":
        failures.append(f"--describe reports name {described.get('name')!r}")
    if not described.get("version") or described["version"] in ("unknown", ""):
        failures.append("--describe reports no engine version — results could not be traced to a build")
    return failures


def main() -> int:
    failures = check_generic_arity()
    failures += check_key_parsing()
    failures += check_paths_are_repo_relative()
    failures += check_refused_is_not_empty()
    failures += check_describe_carries_identity()

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok — key shape, arity, anchors, identity, and refusal-is-not-emptiness all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
