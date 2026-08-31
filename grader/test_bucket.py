#!/usr/bin/env python3
"""What `bucket.py` must not get wrong.

Only two properties really matter, and both are about REFUSING rather than about producing output:

  1. it reproduces the published number before it groups anything, and
  2. when it cannot, it stops instead of printing a decomposition of a total nobody published.

The second is the one worth a test. Every grouping this tool prints is an argument about where the
next experiment should go, and a grouping of the wrong total is an argument for the wrong experiment
— the failure mode is a confident wrong answer, not an obvious crash.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKET = ROOT / "grader" / "bucket.py"


def _newest_run_with(repo: str, cell: str) -> Path | None:
    """The most recently WRITTEN results directory holding this graded cell. The tool is exercised
    against real published material rather than a fixture: its whole job is to agree with a real run,
    and a fixture would only let it agree with a hand-made file.

    By mtime, not by name: the directory names are not all date-prefixed, so sorting them as strings
    picks an arbitrary old run — and an old run legitimately fails to reproduce, because the grader's
    rules have changed since it was graded. That refusal is the tool working, not a bug, but it makes
    for a test that fails for the wrong reason."""
    runs = (ROOT / "results").iterdir() if (ROOT / "results").is_dir() else []
    candidates = [r for r in runs if (r / repo / cell / "cheburnexus" / "result.json").is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


class BucketTests(unittest.TestCase):
    def setUp(self):
        self.run = _newest_run_with("serilog", "with-tests")
        if self.run is None:
            self.skipTest("no graded serilog/with-tests cell on disk to check against")

    def _run(self, *args):
        return subprocess.run([sys.executable, str(BUCKET), *args],
                              capture_output=True, text=True, cwd=str(ROOT))

    def test_bucket_reproduces_the_published_numbers_and_decomposes_them(self):
        result = self._run("bucket", self.run.name)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("REPRODUCTION: PASS", result.stdout)

        published = json.loads(
            (self.run / "serilog" / "with-tests" / "cheburnexus" / "result.json").read_text(encoding="utf-8"))
        primary = next(c for c in published["cells"] if c["cell"].startswith("primary"))
        # The decomposition must add up to the published number, or it is describing something else.
        self.assertIn(f"oracle rows in primary cell: {primary['oracle_edges']}", result.stdout)
        self.assertIn(f"matched: {primary['matched']}", result.stdout)

    def test_it_refuses_to_group_a_total_it_cannot_reproduce(self):
        """A tampered result.json stands in for every real way the two can drift apart — a changed
        grader rule, a stale run, the wrong arm. The tool must stop, not explain."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "tampered"
            shutil.copytree(self.run, fake, symlinks=True)
            target = fake / "serilog" / "with-tests" / "cheburnexus" / "result.json"
            published = json.loads(target.read_text(encoding="utf-8"))
            for cell in published["cells"]:
                if cell["cell"].startswith("primary"):
                    cell["matched"] += 1
            target.write_text(json.dumps(published), encoding="utf-8")

            staged = ROOT / "results" / "_test_tampered"
            if staged.exists():
                shutil.rmtree(staged)
            shutil.copytree(fake, staged, symlinks=True)
            try:
                result = self._run("bucket", "_test_tampered")
            finally:
                shutil.rmtree(staged, ignore_errors=True)

            self.assertNotEqual(0, result.returncode, "a total that does not reproduce must be fatal")
            self.assertIn("REPRODUCTION FAILED", result.stdout + result.stderr)

    def test_diff_reports_the_kill_criterion_against_itself(self):
        """A run diffed against itself moved nothing: no row lost, none gained. If this ever prints a
        loss, the tool's own matching is unstable and no verdict it gave was worth anything."""
        result = self._run("diff", self.run.name, self.run.name)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("rows matched by the baseline and unmatched now: 0", result.stdout)
        self.assertIn("(+0 gained, 0 lost)", result.stdout)


if __name__ == "__main__":
    unittest.main()
