# Pre-registration — engine commit b7fb9a5c (initializers attributed to the constructor)

Written 2026-08-27 BEFORE the run, after counting the answer key but before grading anything.
Baseline is the run of `bc1cde39` recorded in
`PREREGISTRATION-e2-constructor-resolution-2026-08-27.md`: FluentValidation / without-tests,
oracle 624, arm 410, matched 384, precision 0.9370, recall 0.6154, junk 26.

## What changed in the engine

Calls written in field and property initializers now reach the call graph, attributed to the
constructor C# compiles them into: every instance constructor that does not delegate through
`: this(...)`, or the synthesized one when none is declared; a static initializer to the static
constructor, declared or synthesized.

Before this, the call site never reached the resolver at all, so it was counted in NEITHER drop
bucket. Measured through the shipped binary on a seven-call fixture: `Resolved: 2`,
`DroppedExternal: 0`, `DroppedUnresolved: 0`, `UnresolvedShare: 0.0` — a fully-resolved claim over a
graph missing five of seven sites.

A second, older defect was fixed on the way and does NOT affect this run: `RoslynCallParser.Extract`
walked `DescendantNodes()` without the root, losing every expression-bodied member's call on the
`--project` path. The bench arm uses `--solution`, so no number here moves because of it.

## The measurement the prediction rests on

Of the 240 missed oracle edges in the baseline, classified by the oracle's own CallerFile/CallerLine
against the corpus source:

- 195 — caller is not a constructor at all; unrelated to this change.
- 18 — constructor caller, and the line sits inside the constructor's body; already captured.
- **14 — constructor caller, and the line is a field or property initializer** → this fix's scope.
- 13 — constructor caller with no source anchor in the oracle row; cannot be classified either way.

## Prediction — FluentValidation / without-tests, primary cell

- matched   384 → **398–412** (+14 from the classified group, up to +28 if most of the 13
  unclassifiable rows are also initializers)
- arm edges 410 → **425–445**
- junk       26 → **26–34**
- precision  0.9370 → **0.930–0.950**
- recall     0.6154 → **0.638–0.660**

## Named risk, stated before the run

The synthesized constructor has never been a CALLER key before. If the grader's caller
normalisation does not read `file::Ns.Type::.ctor()` as the oracle's `Ns.Type::.ctor`, every
initializer edge on a type with no declared constructor becomes junk instead of a match, and
precision falls while recall barely moves. That failure has a signature — a large junk jump
concentrated on `.ctor` callers — and it must be checked in the junk diff, not inferred from the
score.

## Kill condition

Precision below 0.92 in this cell. The new edges are argued to be compiler facts; if they are not
matching, the attribution rule is wrong and must be re-opened before any number here is published.
