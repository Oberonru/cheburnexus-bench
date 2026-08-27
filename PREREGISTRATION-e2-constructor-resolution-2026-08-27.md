# Pre-registration — engine commit bc1cde39 (constructor resolution)

Written 2026-08-27 BEFORE the run, after measuring the answer key but before grading anything.
Engine: freshly published from `bc1cde39`, both code paths verified present in the binary on a
throwaway fixture (`--project` and `--solution`) before any corpus run.

## What changed in the engine

1. A call to an instance constructor could resolve to the type's STATIC constructor. Roslyn prints
   both as `Ns.T.T()`, and the syntactic builder filed both under the type's identifier with no
   static/instance test. A wrong FACT.
2. A class that declares no constructor still has a synthesized parameterless one. It was in no
   index, so `new T()` produced NO edge and was counted DroppedExternal — declared to live in
   another assembly, which is false.

Narrow by design: classes only (a struct's `new S()` is `initobj`, no call in IL) and parameterless
only. Nested types and positional records stay uncovered.

## Baseline (FluentValidation / without-tests, cheburnexus arm)

Reproduced byte-identically from `results/2026-08-25-engine-fixes` by importing `grader/grade.py`'s
own functions: oracle 624, arm 399, matched 373, precision 0.9348, recall 0.5978. Junk 26.

## The measurement the prediction rests on

Of the 251 missed oracle edges, 79 have a `.ctor` callee. Classified by hand against the corpus source:

- 41 — callee type declares NO instance constructor → inside this fix's scope
  - of which 15 have a caller that is ITSELF a synthesized `.ctor`: those calls are FIELD
    INITIALIZERS, which IL compiles into the constructor and which this engine walks nowhere.
    This fix cannot recover them; it is a separate defect and this run should leave it visible.
  - 26 have a caller whose body is already walked → recoverable here.
- 26 — callee declares an instance constructor; missed for some other reason.
- 12 — nested types (the member walk skips nested) and source-generated Regex types.

## Prediction — FluentValidation / without-tests, primary cell

- matched   373 → **388–399** (+15 to +26; 26 is an upper bound, since some callers counted as
  "declared .ctor" may hold the call in a field initializer rather than the constructor body)
- arm edges 399 → **415–425**
- junk       26 → **25–30**. Exactly one junk edge disappears by being CORRECTED rather than
  removed: `ComparableComparer\`1::.cctor -> .cctor` becomes `-> .ctor`.
- precision  0.9348 → **0.930–0.945** — essentially FLAT. This fix is not a precision play.
- recall     0.5978 → **0.618–0.639**

## Prediction — the other cells and repos

- Polly and serilog recall UP by a similar mechanism; no number pre-registered, the shape was not
  counted there. Precision roughly flat in both.
- Every `excluded — *` cell unchanged.
- repowise and grep byte-identical in every cell (they never touch the engine). If they move, the
  run is invalid and nothing here may be read.

## Kill condition for this prediction

If precision falls below 0.92 in any cheburnexus cell, the new edges are not the facts they were
argued to be, and the fix must be re-opened before any number here is published.

---

# RESULT — run of 2026-08-27, `results/2026-08-27-ctorfix` (gitignored, local only)

| | before | predicted | after |
|---|---|---|---|
| oracle edges | 624 | — | 624 |
| arm edges in cell | 399 | 415–425 | **410** |
| matched | 373 | 388–399 | **384** (+11) |
| junk | 26 | 25–30 | **26** ✅ |
| precision | 0.9348 | 0.930–0.945 | **0.9370** ✅ |
| recall | 0.5978 | 0.618–0.639 | **0.6154** ❌ |

**The prediction was CONTRADICTED on matched and recall** — not by a lot, but below the range, and
it is recorded as contradicted rather than "close". Precision and junk landed inside their ranges.
The kill condition (precision < 0.92 anywhere) did not trigger.

## The witness is fixed

`ComparableComparer\`1::.cctor -> .cctor` is gone from the junk list and
`ComparableComparer\`1::.cctor -> .ctor` is a match. The wrong fact this run existed to remove is
removed.

## Junk stayed at 26 because one edge left and a different one arrived

- **Left (corrected, not deleted):** the `ComparableComparer` `.cctor` edge above.
- **Arrived:** `MessageFormatter::.ctor <- ValidatorConfiguration::MessageFormatterFactory`. Read
  against the source, this is a TRUE call mis-spelled on the CALLER side, not a new wrong fact:
  `ValidatorOptions.cs` creates a `MessageFormatter` twice — once in a field initializer (line 36)
  and once in the property setter (line 84) — and IL names those callers `.ctor` and
  `set_MessageFormatterFactory`, while we emit one edge under the bare property name. That is the
  accessor-naming limitation `grader/EDGE_FORMAT.md` pre-declared against us before any run.

## Why the prediction missed low — measured, not guessed

The forecast assumed that when the caller is a DECLARED constructor its body is walked. It is not,
when the call is written in a **field or property initializer**: C# compiles those into the
constructor, and this engine walks no initializer anywhere. Of the 68 `.ctor` oracle edges still
missed after the fix, the initializer shape accounts for the largest identified group —
`ValidatorOptions.cs` alone supplies `_languageManager = new LanguageManager()`,
`ValidatorSelectors { get; } = new ValidatorSelectorOptions()`,
`Global { get; } = new ValidatorConfiguration()`, `DefaultSelector = new DefaultValidatorSelector()`.

⚠ This is a defect of the same class as the operator-body loss, and worse than the one just fixed:
the call site is never seen by `CollectEdges` at all, so it is counted as neither `DroppedExternal`
nor `DroppedUnresolved`. An invisible loss, not a declared gap. It is NOT fixed in this run, on
purpose, so that this number keeps showing it.

## Not measured here

Only FluentValidation / without-tests was run. Polly and serilog were not re-measured, so the
matrix is not refreshed and no cross-cell claim may be made from this file. repowise and grep were
not re-run either — the arm-invariance check that guards every other run is absent here by
construction (`--arms cheburnexus`).
