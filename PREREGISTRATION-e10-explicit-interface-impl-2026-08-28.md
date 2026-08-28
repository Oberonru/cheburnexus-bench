# Pre-registration — e10, explicit interface implementations

Written and committed **before** the graded run, per this repository's rule. The decision it
implements was argued and committed first, in
`DECISION-explicit-interface-impl-2026-08-28.md` (and corrected there, also before any code).

Engine: `1c3a5cd0`, the tip of a three-commit chain — `b211ca88` (the key carries the source-written
qualifier, plus the recorded `MetadataName`), `5fc5c62c` and `1c3a5cd0` (the target index, where the
same collision turned out to live a second time). Published as `_bench-engine-e10-fix`; baseline
`_bench-engine-e10-base` is `b211ca88^`.

## How this forecast was sized

By **running the mechanism on both binaries and diffing the raw edges**, per
`lesson-size-a-forecast-by-running-the-mechanism`. Not by reading source, and not by counting
candidate declarations.

The check that makes the delta trustworthy: **the baseline build reproduced the published edge set
byte-identically in all four cells** (`results/2026-08-28-accessor-caller`) — 483 / 458 / 453 / 922
unique edges, zero added, zero removed. So every difference below is caused by the change under
test and by nothing else.

⚠ **Disclosure.** `runner/run.py` grades as part of a run, so the two sizing directories
(`results/_e10-diff-base`, `results/_e10-diff-fix`) contain `result.json` files. They were **not
read** before this document was committed. The numbers below were computed from `edges.jsonl` plus
the answer key, through `grade.py`'s own `method_key` / `normalize_caller` / `cell_of` / `Cell.score`
imported as a module — and that reconstruction reproduces every published baseline number exactly
(precision 0.9789 / 0.9512 / 0.9956 / 0.9957, recall 0.6615 / 0.6875 / 0.8361 / 0.3928, junk
10 / 22 / 2 / 4), which is what licenses using it to state the forecast.

## The forecast

| cell | precision | recall | junk edges |
|---|---|---|---|
| Polly / without-tests | 0.9789 → **0.9789** (unchanged) | 0.6615 → **0.6615** (unchanged) | 10 → **10** |
| FluentValidation / without-tests | 0.9512 → **0.9978** | 0.6875 → **0.7276** | 22 → **1** |
| serilog / without-tests | 0.9956 → **1.0000** | 0.8361 → **0.8399** | 2 → **0** |
| serilog / with-tests | 0.9957 → **0.9978** | 0.3928 → **0.3937** | 4 → **2** |

Supporting counts, all falsifiable:

- FluentValidation: matched 429 → **454**; arm edges in the primary cell 451 → **455**.
- serilog/without-tests: matched 449 → **451**; arm edges 451 → 451 (unchanged).
- serilog/with-tests: matched 916 → **918**; arm edges 920 → 920 (unchanged).
- Polly: every number identical, arm edges 475 → 475. Polly's answer key contains **zero**
  explicit-interface callers, so this cell is the control: if anything moves there, the change did
  something it was not supposed to do.

**Raw edge deltas** (unique caller→callee pairs, before cell routing): Polly +0/−0,
FluentValidation +30/−25, serilog/without +2/−2, serilog/with-tests +2/−2.

## Where the gain comes from — split by cause, in advance

The 25 newly matched FluentValidation rows are **not** one effect:

- **24** come from the caller being re-spelled — the key now carries the interface qualifier and
  `CallSidecarEntry.MetadataName` hands the arm the compiler's own fully-qualified spelling.
- **1** comes from a *target* correction: `AssemblyScanner::GetEnumerator → AssemblyScanner::Execute`.
  Before, the explicit `IEnumerable.GetEnumerator` overwrote the ordinary one in the resolver's
  target index and the edge pointed at a method the call site cannot reach.

serilog's +2 are re-spellings only. **Zero rows are lost** in any cell.

Three further edges change from a self-loop to the correct target
(`AbstractValidator`, `AssemblyScanner`, `TrackingCollection` — each explicit `IEnumerable.GetEnumerator`
now pointing at the type's ordinary `GetEnumerator` instead of at itself). They sit outside the
graded primary cell, so they move no number; they are named because they are the product improvement
this change is actually for.

## Predictions that would falsify this, stated plainly

1. **grep and repowise must be byte-identical** to `results/2026-08-28-accessor-caller`. This change
   touches only our engine and our arm. Any movement in another arm means the harness moved too and
   the comparison is void.
2. **Polly must not move at all** — not precision, not recall, not the edge count.
3. serilog/without-tests reaching **exactly 1.0000** precision (zero junk in the primary cell) is the
   sharpest claim here and the easiest to miss.
4. The impartiality test that governed e8 **does not apply** and is pre-registered as not applying:
   this change raises only our arm, by construction. The case rests on the decision document — a
   shipped answer that was false and order-dependent — not on the table above.

## The earlier estimate was wrong, and by how much

`plan-coverage-campaign-2026-08-28` estimated FluentValidation would land at "≈0.985 precision and
≈0.756 recall". Recorded here before the run so the miss is graded on the same page:

- precision **higher** than estimated (0.9978 vs ≈0.985),
- recall **lower** (0.7276 vs ≈0.756).

The recall shortfall has a measured cause, not a mysterious one: **17 of FluentValidation's 43
explicit-interface oracle rows are unreachable by any spelling**, because they are declared in a
`Zomp.SyncMethodGenerator` `.g.cs` that exists only inside the oracle's full `dotnet build` and is
absent from the corpus checkout. The estimate had counted them.

## What this change deliberately does not fix

- **Explicit implementations of FRAMEWORK interfaces get no recorded `MetadataName`**, because in the
  compilations this arm feeds the engine those interfaces do not bind at all —
  `IMethodSymbol.ExplicitInterfaceImplementations` is empty for them. The key still carries the
  source-written qualifier, so the collision is closed either way, but the arm cannot re-spell such a
  caller for the oracle. serilog's `EnricherStack::System.Collections.IEnumerable.GetEnumerator`
  rows stay unmatched for exactly this reason.
- That non-binding is itself a much larger finding, uncovered by this work and **not** addressed
  here: FluentValidation reports **749 unresolved call sites against 43 external**, Polly.Core 572
  against 48. It is being diagnosed separately and is the most promising lead yet for work item 2 of
  the coverage campaign, "caller never appears in our output at all".
