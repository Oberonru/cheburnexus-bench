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

---

# RESULT — run of 2026-08-27, `results/2026-08-27-initfix` (gitignored, local only)

| | before | predicted | after |
|---|---|---|---|
| arm edges in cell | 410 | 425–445 | **418** |
| matched | 384 | 398–412 | **392** (+8) |
| junk | 26 | 26–34 | **26** ✅ |
| precision | 0.9370 | 0.930–0.950 | **0.9378** ✅ |
| recall | 0.6154 | 0.638–0.660 | **0.6282** ❌ |

**The prediction was CONTRADICTED on matched and recall, below the range — the second run in a row
to miss in the same direction.** That pattern is itself the finding: the classifier used to size
these forecasts reads source lines with a regular expression and keeps counting things that are not
what it thinks they are.

## The named risk did not materialise, and that is worth stating

Every one of the 8 new edges is a MATCH; new junk is **zero**. The synthesized constructor works as
a CALLER key — `ValidatorConfiguration::.ctor` and `ValidatorSelectorOptions::.cctor` are both
constructors nobody wrote, and both matched the oracle. The failure signature named before the run
(a junk jump concentrated on `.ctor` callers) did not appear.

## Why it missed low — four DIFFERENT defects, none of them this one

37 missed edges still have a constructor caller. Read one by one against the oracle's own
CallerFile/CallerLine, they are not one thing:

1. **Target-typed `new()`** — `internal TrackingCollection<…> Rules { get; } = new();`
   (`AbstractValidator.cs:37`). The walk matches `ObjectCreationExpressionSyntax` and target-typed
   `new()` is `ImplicitObjectCreationExpressionSyntax`. A silent loss of exactly the kind fixed
   today, on a spelling that is ordinary modern C#.
2. **Constructor-initializer ARGUMENTS** — `: base(BuildMessage(validatorType, wasInvokedByAspNet))`
   (`AsyncValidatorInvokedSynchronouslyException.cs:33`). The initializer node is handed to the
   resolver whole, so the `this`/`base` target resolves, but nothing walks INSIDE its argument list.
3. **Method-group conversions** — `private Func<…> _errorCodeResolver = DefaultErrorCodeResolver;`
   (`ValidatorOptions.cs:34,35,37`). IL emits `ldftn` plus a delegate constructor and the oracle
   records it; the engine does not model a method-group reference as a call at all.
4. **Implicit base-constructor calls** — a constructor with no written `: base()` still calls the
   base constructor in IL (`LengthValidator.cs:32`, `PrecisionScaleValidator.cs:44`, and others,
   whose oracle rows point at the constructor's DECLARATION line).

(1) and (2) are the same class as everything fixed today — code that runs and that nothing walks.
(3) and (4) are modelling decisions about what counts as a call, and each deserves its own
pre-registration rather than being folded in here.

## Not measured

FluentValidation / without-tests only. Polly and serilog not re-run; repowise and grep not re-run,
so the arm-invariance check is absent by construction (`--arms cheburnexus`).
