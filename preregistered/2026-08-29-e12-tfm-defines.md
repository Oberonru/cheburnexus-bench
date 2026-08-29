# Pre-registered forecast — e12, implicit TFM symbols + condition-aware DefineConstants

Engine commit: 048d0cb5. Registered BEFORE any scored run.
Baseline (2026-08-28-bench-engine-bcl):
  recall    Polly 0.6700 / FV 0.8029 / serilog 0.8510 / serilog-with-tests 0.3962
  precision Polly 0.9792 / FV 0.9980 / serilog 1.0000 / serilog-with-tests 0.9978

Raw engine edge diff, measured (not read from source), without-tests cell:
  FV      687 -> 687   (+0 / -0, edges.jsonl byte-identical)
  Polly   688 -> 684   (+6 / -10)
  serilog 713 -> 827   (+127 / -13)

## Forecast

1. FluentValidation: ALL FOUR numbers EXACTLY unchanged. Edges are byte-identical, so any
   movement in this cell would mean the harness is nondeterministic, not that the fix acted.
2. Polly: movement is SMALL in both axes. |delta recall| bounded by 10 key rows; precision
   does not fall below 0.9792 — the 10 removed edges come from wrong-branch legacy code and
   should be junk, not matches.
3. serilog without-tests: recall UP, by at most the 127 added rows; precision stays >= 0.99.
   This is the cell the fix targets — Serilog's per-TFM <DefineConstants> was the larger defect.
4. serilog with-tests: recall UP in the same direction.
5. Falsifiable sub-claim: the 4 Polly junk edges filed under "conditional compilation" in e9
   DISAPPEAR. If they do, e9's junk category and this defect are the SAME defect, as suspected.
6. Counter-forecast worth naming: Polly was the LEADING hypothesis for the worst cell. A 16-edge
   move cannot explain a 0.67 vs 0.85 gap. If Polly barely moves, that hypothesis is REFUTED and
   the cause of Polly's cell is still unknown.

---

# RESULT — all six forecast items HIT

Run `results/2026-08-29-e12-tfm-defines`, engine `048d0cb5`, all three arms, all four cells.

| cell | precision | recall | matched |
|---|---|---|---|
| Polly / without-tests | 0.9792 → **0.9875** | 0.6700 → **0.6743** | 471 → **474** |
| FluentValidation / without-tests | 0.9980 → **0.9980** | 0.8029 → **0.8029** | 501 → **501** |
| serilog / without-tests | 1.0000 → **1.0000** | 0.8510 → **0.9199** | 457 → **494** |
| serilog / with-tests | 0.9978 → **1.0000** | 0.3962 → **0.4254** | 924 → **992** |

**Impartiality confirmed**: grep and repowise are byte-identical to
`results/2026-08-28-bench-engine-bcl` in every one of the twelve rows, all four cells. Movement is
only in `cheburnexus`, as it must be for an engine-only change.

**Raw edge diff, as sized before the run** (unique caller→callee pairs, `edges.jsonl` sorted):

| cell | before | after |
|---|---|---|
| FluentValidation / without-tests | 687 | 687 (byte-identical) |
| Polly / without-tests | 688 | 684 |
| serilog / without-tests | 713 | 827 |
| serilog / with-tests | 1291 | 1438 |

## Forecast items, read one by one

1. **HIT.** FluentValidation: all four numbers exactly unchanged, `edges.jsonl` byte-identical to
   the baseline file. The harness reproduced.
2. **HIT.** Polly: movement small in both axes — precision +0.0083, recall +0.0043 — and precision
   did not fall.
3. **HIT.** serilog without-tests: recall rose 0.8510 → 0.9199 (+37 matched rows), well inside the
   127-row ceiling; precision held at 1.0000.
4. **HIT.** serilog with-tests: recall rose 0.3962 → 0.4254 (+68 matched rows), same direction.
5. **HIT.** The four Polly junk edges filed under e9's "conditional compilation" category —
   `BrokenCircuitException::.ctor`, `BrokenCircuitException::GetObjectData`,
   `TimeoutRejectedException::GetObjectData`, `ExceptionUtilities::TrySetStackTrace` — are gone from
   the primary-cell junk set. e9's category and this defect are the SAME defect.
6. **HIT.** Polly moved only 3 matched rows (471→474) against a 0.67-vs-0.85 recall gap between
   cells. Sixteen raw edges cannot explain that gap; the counter-forecast is confirmed and the
   "implicit `#if` symbols explain Polly's worst-cell status" hypothesis from the prior session's
   plan is **REFUTED**. Polly's recall cause is now an open question.

## Polly's residual junk, read one by one

10 → 6 (grader's number, overrides map applied — a plain set difference reads 11 → 7, over-reporting
by one in both columns for the same reason e9 already documented: `SingleHealthMetrics::TryReset`
scores against `::Reset` through the oracle's overrides map, not as junk).

The 4 removed are exactly e9's "conditional compilation" edges, named in item 5 above — legacy
`#if`-gated exception-serialization and stack-trace plumbing that the net8.0 assembly never
compiles, and that this defect's fix now keeps out of the graded edge set entirely.

The remaining 6 are **all** `op_Implicit` — the same six `implicit operator Func` witnesses e9
already isolated (`PredicateBuilder\`1`, `FaultGenerator`, `OutcomeGenerator\`1`) — untouched by this
change and now the *only* junk left in Polly's primary cell. Work item 3 from the standing plan.

## What this closes and what it opens

Closed: implicit per-TFM `#if` symbol synthesis, the leading item on the prior session's plan. Its
predicted target (serilog) moved as forecast; its predicted secondary effect (Polly's junk) also
moved as forecast. What it does **not** close: Polly's worst-of-four recall (0.6743) has no remaining
named hypothesis. That question carries forward.
