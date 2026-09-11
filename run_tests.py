#!/usr/bin/env python3
"""Run every suite in the polygon, and say plainly which parts are covered.

The suites live next to the code they check next to the code they check. Without one entry point they get
run one at a time and drift apart, which is how a harness ends up green in the places someone
happened to look.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SUITES = [
    ("oracle", "oracle/csharp/test_oracle.py", "the answer key: anchors, virtual dispatch, generated callers, no shrinkage"),
    ("armkit", "arms/_lib/test_armkit.py", "shared with-tests/without-tests cell split, dotted test-suffix directories"),
    ("grader", "grader/test_identity.py", "neutrality, key agreement between forms, the override map"),
    ("grader:bucket", "grader/test_bucket.py", "the decomposition tool: it reproduces a published total, or refuses"),
    ("grader:implements", "grader/test_implements.py", "type-level axis: primary/external/generic cell separation, neutrality"),
    ("harness", "runner/test_runner.py", "assembly selection, cell split, determinism, blocked, refused cells"),
    ("arm:grep", "arms/grep/test_grep.py", "caller attribution, cell exclusion, constructors, candidate fan-out"),
    ("arm:repowise", "arms/repowise/test_repowise.py", "key reconstruction from their path-anchored ids, drop accounting"),
    ("arm:cheburnexus", "arms/cheburnexus/test_cheburnexus.py", "key shape, anchors, refusal is not emptiness"),
]


def main() -> int:
    failed: list[str] = []
    for name, relative, what in SUITES:
        script = ROOT / relative
        if not script.is_file():
            print(f"MISSING  {name:18} {relative}")
            failed.append(name)
            continue
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        status = "ok     " if result.returncode == 0 else "FAILED "
        print(f"{status}  {name:18} {what}")
        if result.returncode != 0:
            failed.append(name)
            for line in result.stdout.splitlines():
                if line.strip().startswith("- "):
                    print(f"           {line.strip()}")

    print()
    if failed:
        print(f"{len(failed)} suite(s) failed: {', '.join(failed)}")
        return 1
    print("all suites green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
