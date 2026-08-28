# Pre-registration — re-run after the self-audit, engine `f2b4a8cd`

Written 2026-08-28 BEFORE the run.

## Why this run exists, and what it says about the last one

An audit of the day's own work found three FABRICATED facts introduced by that work: an initializer's
call attributed to a synthesized `.ctor()` on a positional record, on a C# 12 primary-constructor
class, and on a `partial` type whose constructor lives in another file — in all three the compiler
synthesizes nothing, so the caller named no member. A fourth was older and adjacent: `: base()` /
`: this()` resolved through a name+arity lookup that could return the STATIC constructor.

⛔ **The matrix recorded in `PREREGISTRATION-e4-full-matrix-2026-08-28.md` was produced by an engine
that emitted those edges.** Its numbers are not withdrawn — they were honestly measured and honestly
reported — but they are SUPERSEDED by this run, and any of them quoted after today must be quoted
from here instead.

## What the fixes can do to a number

Every one of them either removes an edge (a fabricated caller now yields silence) or corrects a
target. Two add edges, both on the `--project` syntactic path only, which the bench does not use
(`--solution`): constructor-initializer arguments, and `Dep? d = new();`.

So, mechanically:

- **matched can only FALL or stay.** A fabricated caller key never matched the oracle anyway — unless
  the oracle happens to hold a real `.ctor()` for a type whose synthesized ctor we were right about
  by accident, which the audit found no instance of.
- **junk can only FALL or stay**, for the same reason, plus the `: base()` → `.cctor()` correction.
- **precision can only RISE or stay**, since junk falls at least as fast as matched.
- **recall can only FALL or stay.**

## Prediction

- Precision: **rises or is unchanged in all four cells; never falls.**
- Recall: **falls or is unchanged in all four cells; never rises.**
- Movement is expected to be SMALL — the audit's fabricated shapes (positional records, primary
  constructors, cross-file partial initializers) may not occur at all in these three corpora. Zero
  movement in every cell is an entirely acceptable outcome and would mean the corpora never exercised
  the defect; it would NOT mean the defect was imaginary, which the unit tests settle independently.
- repowise and grep: byte-identical again, or the run is void.

## Kill condition

Precision falling anywhere, or recall rising anywhere. Either would mean the change did something
other than what it was argued to do, and the argument would have to be re-opened.

---

# RESULT — run of 2026-08-28, `results/2026-08-28-auditfix` (gitignored, local only)

**Every cell is unchanged, and not merely in its score: the arm's raw edge sets are byte-identical
to the superseded run** — 638, 564, 684 and 1268 edges, `sort`-compared line for line, zero
differences. repowise and grep byte-identical as well.

| cell | precision | recall |
|---|---|---|
| Polly / without-tests | 0.963 | 0.597 |
| FluentValidation / without-tests | 0.938 | 0.633 |
| serilog / without-tests | 0.982 | 0.808 |
| serilog / with-tests | 0.950 | 0.394 |

Prediction held on every clause: precision never fell, recall never rose, and the pre-registered
"zero movement is an acceptable outcome" is what happened. The kill condition did not trigger.

## What this actually proves, and what it does not

It proves the three corpora contain none of the shapes the audit found — no positional record or
C# 12 primary-constructor class with a field initializer, and no `partial` type whose initializer
and constructor sit in different files — and that the `: base()` → `.cctor()` defect is confined to
the `--project` syntactic path the bench never uses.

🔴 It does NOT prove the defects were minor. It proves **this benchmark could not see them.** Three
fabricated facts shipped, and the matrix moved by exactly zero — every cell would have reported the
engine as unchanged and healthy. A wrong fact that the corpus never exercises is invisible to the
ruler, and no number in this repository can be read as evidence that such facts are absent. The unit
tests settle those three independently, which is why they exist and why each was proven failing
first.

## Standing numbers

The four cells above supersede `PREREGISTRATION-e4-full-matrix-2026-08-28.md`. They are numerically
identical to it, so nothing published from e4 becomes wrong — but e5 is the run made by an engine
with no known fabricated facts, and it is the one to quote.
