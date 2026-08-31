#!/usr/bin/env python3
"""Take a graded cell apart: what is still missed, grouped BY CAUSE, and what a change moved.

WHY THIS EXISTS. Every session that ranked the remaining gaps by their SHAPE — "this looks like the
widest one left" — lost. Every session that BUCKETED them first paid for itself: 96% of one cell's
misses turned out to be a single cause, and the next bucketing of the same cell found another. This
file is that procedure, so it stops being retyped from memory into a scratch directory each time and
being subtly different on each retyping.

Two rules are wired in rather than left to the operator:

  1. **The grader's own functions are imported, never re-implemented.** A bucketing that keys edges
     even slightly differently from `grade.py` groups rows the published number never contained.
  2. **The published `result.json` is reproduced EXACTLY before anything is grouped**, and a mismatch
     is fatal. A grouping is only ever as trustworthy as the total it decomposes.

Usage:

    # what is still missed in one cell, and why
    python3 grader/bucket.py bucket results/2026-08-31-e21-strong-name
    python3 grader/bucket.py bucket <run> --repo Polly --cell without-tests

    # what a change moved, including the kill criterion
    python3 grader/bucket.py diff <baseline-run> <new-run> --repo serilog --cell with-tests

`bucket` answers "where should the next experiment go". `diff` answers "may this change be kept" —
its KILL CRITERION section is the one that matters: any oracle row matched by the baseline and
unmatched now kills the change, whatever the topline recall does. Totals cannot show that; a silent
loss can move the honest counters the wrong way (measured — see the e19 revert).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_grader():
    """Import `grade.py` as a module. The bucketing MUST agree with the published number key for
    key, cell and override, and the only way to guarantee that is to run the same code."""
    path = ROOT / "grader" / "grade.py"
    spec = importlib.util.spec_from_file_location("grade", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["grade"] = module
    spec.loader.exec_module(module)
    return module


grade = _load_grader()


class Graded:
    """One arm's graded cell, plus the material the published number is made of."""

    def __init__(self, run: str, repo: str, cell: str, arm: str):
        # Accept either the bare run name or the `results/<run>` path shell completion produces.
        run = run.rstrip("/")
        run = run[len("results/"):] if run.startswith("results/") else run
        base = ROOT / "results" / run / repo / cell
        if not base.is_dir():
            raise SystemExit(f"no such cell on disk: {base}")
        self.label = f"{run}/{repo}/{cell}/{arm}"
        self.base = base

        manifest = json.loads((base / arm / "manifest.json").read_text(encoding="utf-8"))
        first_party = set(manifest.get("first_party") or []) or None

        self.cells, _unused_overrides, self.stats = grade.load_oracle(
            str(base / "_oracle" / "oracle.jsonl"), first_party)
        self.overrides = self._load_overrides(base / "_oracle" / "overrides.json")
        self.arm_edges, self.arm_dropped = grade.load_arm(str(base / arm / "edges.jsonl"))

        callee_cell = grade.build_callee_cell(self.cells)
        self.by_cell: dict[str, set] = defaultdict(set)
        for edge in self.arm_edges:
            self.by_cell[grade.cell_of(edge[1], callee_cell, self.overrides)].add(edge)

        self.published = json.loads((base / arm / "result.json").read_text(encoding="utf-8"))
        self._reproduce_or_die()

        self.oracle = self.cells["primary"].oracle
        self.in_cell = self.by_cell["primary"]
        self.matched, self.junk = self._match(self.oracle, self.in_cell, self.overrides)
        self.missed = self.oracle - self.matched

    @staticmethod
    def _load_overrides(path: Path) -> dict:
        """Same shape `grade.main` builds: an override key mapping to the declarations it may stand
        for, with self-references dropped."""
        overrides: dict[str, set[str]] = defaultdict(set)
        if not path.is_file():
            return overrides
        for override, declarations in json.loads(path.read_text(encoding="utf-8")).items():
            key = grade.method_key(override)
            if key is None:
                continue
            for declaration in declarations:
                declared = grade.method_key(declaration)
                if declared is not None and declared != key:
                    overrides[key].add(declared)
        return overrides

    @staticmethod
    def _match(oracle: set, arm: set, overrides: dict) -> tuple[set, set]:
        """Which oracle rows the arm hit, and which arm edges hit nothing. Mirrors `Cell.score`'s
        acceptance rule — including that overrides are walked in sorted order, so which declaration
        is recorded never depends on Python's per-process hash seed."""
        matched, junk = set(), set()
        for edge in arm:
            if edge in oracle:
                matched.add(edge)
                continue
            caller, callee = edge
            for declared in sorted(overrides.get(callee, ())):
                if (caller, declared) in oracle:
                    matched.add((caller, declared))
                    break
            else:
                junk.add(edge)
        return matched, junk

    def _reproduce_or_die(self) -> None:
        """Recompute every published cell and refuse to continue on any disagreement. Not a
        formality: a grouping is only as trustworthy as the total it decomposes, and the cheapest
        way to be wrong here is to key an edge differently from the run being explained."""
        recomputed = [cell.score(self.by_cell[name], self.overrides) for name, cell in self.cells.items()]
        published = self.published["cells"]
        problems = [f"  {r} != {p}" for r, p in zip(recomputed, published) if r != p]
        if len(recomputed) != len(published):
            problems.append(f"  cell count {len(recomputed)} != {len(published)}")
        if problems:
            raise SystemExit(
                f"REPRODUCTION FAILED for {self.label} — refusing to group numbers that are not the "
                f"published ones:\n" + "\n".join(problems))

    def caller_files(self) -> dict[str, str]:
        """Caller key -> the source file the ANSWER KEY anchors it to. Read from oracle.jsonl with
        the grader's own key functions, because a MISSED row has no arm edge to ask. This is what
        makes "every miss in this cell has its caller under test/" a fact rather than an impression."""
        files: dict[str, str] = {}
        with open(self.base / "_oracle" / "oracle.jsonl", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                caller = grade.method_key(row.get("Caller") or "")
                if caller is None:
                    continue
                caller, attributable = grade.normalize_caller(caller)
                if attributable and row.get("CallerFile") and caller not in files:
                    files[caller] = row["CallerFile"]
        return files


def _type_of(key: str) -> str:
    return key.rpartition("::")[0] or "<none>"


def _member_of(key: str) -> str:
    return key.rpartition("::")[2] or "<none>"


def _histogram(title: str, counts: Counter, total: int, limit: int) -> None:
    print(f"\n### {title}")
    if not counts:
        print("  (nothing)")
        return
    for name, count in counts.most_common(limit):
        share = f"{100.0 * count / total:.0f}%" if total else "—"
        print(f"  {count:4d}  {share:>4}  {name}")
    hidden = len(counts) - min(limit, len(counts))
    if hidden > 0:
        print(f"  … {hidden} more group(s); raise --limit to see them")


def cmd_bucket(args) -> int:
    graded = Graded(args.run, args.repo, args.cell, args.arm)
    missed, junk = graded.missed, graded.junk
    print(f"REPRODUCTION: PASS — {graded.label}")
    print(f"oracle rows in primary cell: {len(graded.oracle)}   matched: {len(graded.matched)}   "
          f"missed: {len(missed)}   junk: {len(junk)}")
    print(f"oracle load stats: {graded.stats}   arm rows dropped by caller discipline: {graded.arm_dropped}")

    if missed:
        _histogram("MISSED, by callee", Counter(callee for _, callee in missed), len(missed), args.limit)
        _histogram("MISSED, by callee TYPE", Counter(_type_of(c) for _, c in missed), len(missed), args.limit)
        _histogram("MISSED, by caller TYPE", Counter(_type_of(c) for c, _ in missed), len(missed), args.limit)
        _histogram("MISSED, by member SHAPE",
                   Counter("constructor" if _member_of(c) == ".ctor"
                           else "static constructor" if _member_of(c) == ".cctor"
                           else "operator" if _member_of(c).startswith("op_")
                           else "event accessor" if _member_of(c).startswith(("add_", "remove_"))
                           else "property accessor" if _member_of(c).startswith(("get_", "set_"))
                           else "ordinary method"
                           for _, c in missed), len(missed), args.limit)

        files = graded.caller_files()
        _histogram("MISSED, by caller FILE (from the answer key)",
                   Counter(files.get(caller, "<no source anchor>") for caller, _ in missed),
                   len(missed), args.limit)

        # Does the arm know this callee at all? Separates "never seen this member" from "saw it from
        # some callers and not others" — two different defects that read the same in a callee list.
        arm_callees = {callee for _, callee in graded.arm_edges}
        known = Counter("arm emits this callee from SOME caller" if c in arm_callees
                        else "arm never emits this callee at all" for _, c in missed)
        _histogram("MISSED, is the callee known to the arm elsewhere?", known, len(missed), args.limit)

    if junk:
        _histogram("JUNK, by callee", Counter(callee for _, callee in junk), len(junk), args.limit)
        _histogram("JUNK, by caller TYPE", Counter(_type_of(c) for c, _ in junk), len(junk), args.limit)

    if args.list:
        print("\n### every missed row")
        for caller, callee in sorted(missed):
            print(f"  MISS  {caller}  ->  {callee}")
        print("\n### every junk edge")
        for caller, callee in sorted(junk):
            print(f"  JUNK  {caller}  ->  {callee}")
    return 0


def cmd_diff(args) -> int:
    base = Graded(args.baseline, args.repo, args.cell, args.arm)
    new = Graded(args.run, args.repo, args.cell, args.arm)
    print(f"REPRODUCTION: PASS for both\n  baseline: {base.label}\n  new:      {new.label}")

    if base.oracle != new.oracle:
        print("\n⚠ THE ANSWER KEY ITSELF CHANGED between these runs — the two cells are not comparable "
              "row by row. Fix that before reading anything below as a result of the change.")
        print(f"  rows only in baseline: {len(base.oracle - new.oracle)}   "
              f"only in new: {len(new.oracle - base.oracle)}")

    lost = base.matched - new.matched
    gained = new.matched - base.matched
    print(f"\nmatched: {len(base.matched)} -> {len(new.matched)}  ({len(gained):+d} gained, {len(lost)} lost)")

    print(f"\n### KILL CRITERION — rows matched by the baseline and unmatched now: {len(lost)}")
    if lost:
        print("  ⛔ THE CHANGE MUST NOT BE KEPT on this evidence alone. Explain every row below.")
        for caller, callee in sorted(lost):
            print(f"  LOST  {caller}  ->  {callee}")
    else:
        print("  ✅ none — no previously correct row was traded away")

    _histogram("NEWLY MATCHED, by callee", Counter(c for _, c in gained), len(gained), args.limit)

    new_junk = new.junk - base.junk
    gone_junk = base.junk - new.junk
    print(f"\n### junk: {len(base.junk)} -> {len(new.junk)}  ({len(new_junk)} new, {len(gone_junk)} gone)")
    for caller, callee in sorted(new_junk)[:args.limit]:
        print(f"  NEW JUNK  {caller}  ->  {callee}")

    added = new.arm_edges - base.arm_edges
    removed = base.arm_edges - new.arm_edges
    print(f"\n### raw arm edges (all cells): {len(base.arm_edges)} -> {len(new.arm_edges)}  "
          f"({len(added)} added, {len(removed)} removed)")
    # Removals are printed in full whatever the limit: a removed edge is the shape a silent loss
    # takes, and it is exactly what the topline cannot show.
    for caller, callee in sorted(removed):
        print(f"  RAW REMOVED  {caller}  ->  {callee}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--repo", default="serilog", help="corpus repository name (default: serilog)")
        p.add_argument("--cell", default="with-tests", choices=("with-tests", "without-tests"),
                       help="corpus scope (default: with-tests)")
        p.add_argument("--arm", default="cheburnexus", help="arm to explain (default: cheburnexus)")
        p.add_argument("--limit", type=int, default=25, help="rows per histogram (default: 25)")

    b = sub.add_parser("bucket", help="group one cell's misses and junk by cause")
    b.add_argument("run", help="directory name under results/")
    b.add_argument("--list", action="store_true", help="also print every missed row and junk edge")
    common(b)
    b.set_defaults(func=cmd_bucket)

    d = sub.add_parser("diff", help="what a change moved, kill criterion first")
    d.add_argument("baseline", help="directory name under results/ to compare against")
    d.add_argument("run", help="directory name under results/ holding the change")
    common(d)
    d.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
