# Pre-registration — full matrix on engine `d5619a63`

Written 2026-08-28 BEFORE the run. Engine published fresh from `d5619a63`; both code paths were
verified live in a binary on throwaway fixtures during the work that produced it.

## Why this run exists

Three engine commits landed since the last matrix (`bc1cde39`, `b7fb9a5c`, `d5619a63`) and only ONE
of the four measurable cells has been re-measured. Worse, both intermediate runs used
`--arms cheburnexus`, so the invariance check that guards every other run — repowise and grep must
come back byte-identical, because they never touch the engine — was absent by construction. Until
this run, "the engine improved" is a claim about FluentValidation and nothing else.

## Baseline — `results/2026-08-25-engine-fixes`, primary cell

| cell | oracle | arm | matched | precision | recall |
|---|---|---|---|---|---|
| Polly / without-tests | 703 | 397 | 381 | 0.9597 | 0.5420 |
| FluentValidation / without-tests | 624 | 399 | 373 | 0.9348 | 0.5978 |
| serilog / without-tests | 537 | 408 | 404 | 0.9902 | 0.7523 |
| serilog / with-tests | 2203 | 812 | 791 | 0.9742 | 0.3591 |

Polly/with-tests and FluentValidation/with-tests stay `not-measurable` (net10.0 test projects on a
pinned SDK 8.0.421) — four measured cells, not six, and that must be said with the numbers.

## No numeric ceiling is offered, and that is the point

The only cheap estimator available before a run is to regex-match source lines and count the shapes
the new walk reaches. That estimator has now been shown worthless twice: it produced forecasts of
+15…+26 and +14…+28 against measured +11 and +8, and when re-run for this pre-registration it
counted **1312** initializer-shaped lines in FluentValidation, a corpus where the measured recovery
was **8 edges**. It cannot separate a field initializer from a local variable declaration inside a
method body. Publishing another range built on it would be dressing a guess as a forecast.
See [[lesson-forecast-headroom-regex-miscounts-2026-08-27]].

So this pre-registration commits to DIRECTION and BOUNDS, anchored on the one cell measured
end-to-end (FluentValidation: matched 373 → 392, +5.1% relative, junk unchanged at 26):

- **Every cell's recall RISES**, none falls. The three commits only ever ADD call sites that were
  previously seen by nobody; none removes an edge.
- **Precision stays within ±0.02 of its baseline in every cell** and never drops below 0.92. The
  added edges are compiler facts; on FluentValidation the initializer work produced 8 new matches
  and **zero** new junk.
- **Relative recovery is of the same order as FluentValidation's**, i.e. matched rises by roughly
  2–10% relative in each cell. Stated as a band precisely because the estimator that would have
  narrowed it is not trustworthy.
- Polly is the cell most likely to move MOST in absolute terms (it has the most target-typed `new()`
  and `: base(...)`-with-a-call sites of the three by any counting), serilog/without-tests the
  least room (recall already 0.7523, precision 0.9902).

## Invariance check — the thing the last two runs could not do

repowise and grep must come back **byte-identical** in all four cells: 0.367/0.191 and 0.246/0.247
(Polly), 0.357/0.183 and 0.207/0.389 (FluentValidation), 0.544/0.348 and 0.260/0.542
(serilog/without-tests), 0.572/0.280 and 0.284/0.746 (serilog/with-tests). If any of them moves,
something other than our arm changed and **no number from this run may be read at all**.

## Kill condition

Precision below 0.92 in any cheburnexus cell, or recall falling anywhere. Either means the added
edges are not the facts they were argued to be.
