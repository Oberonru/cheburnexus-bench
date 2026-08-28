# Pre-registration — e11, the ruler stops blinding the engine

Written and committed **before** the graded run.

This is a **repair of the RULER, not a change to the product.** Not one line of engine source
differs from e10 (`1c3a5cd0`). The only difference is how the benchmark's binary is published.

## What was wrong

`build-bench-engine.sh` published `--self-contained -p:PublishSingleFile=true`. In that mode the CLI
loses the BCL entirely, by two independent routes that fail together:

- `RuntimeEnvironment.GetRuntimeDirectory()` returns the bundle's own extraction directory, so
  `DotnetRoot()` derives a `packs/` path that does not exist and `FrameworkPackReferences` finds
  nothing;
- the fallback `AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")` returns an **empty string, not
  null**, so `AddFrameworkBaselineIfMissing`'s null guard never fires and it adds zero references.

Measured on FluentValidation, same source, only the flag differing: unresolved call sites
**749 → 5**, external **43 → 810**.

⛔ **The shipped product was never affected**, and this was checked on the real assets rather than on
the scripts: the MCP package publishes multi-file (probed: 0 unresolved), and the Unity plugin, which
IS single-file, only ever calls `--input` — a branch that never touches the broken provider (probed:
the same single-file binary gives 2 unresolved via `--solution` and 0 via `--input`). Only the ruler
was blind.

## Forecast

Sized by running both binaries and diffing raw edges. Baseline is the e10 result
(`results/2026-08-28-explicit-interface`).

| cell | precision | recall |
|---|---|---|
| Polly / without-tests | 0.9789 → **0.9792** | 0.6615 → **0.6700** |
| FluentValidation / without-tests | 0.9978 → **0.9980** | 0.7276 → **0.8029** |
| serilog / without-tests | 1.0000 → **1.0000** | 0.8399 → **0.8510** |
| serilog / with-tests | 0.9978 → **0.9978** | 0.3937 → **0.3962** |

Supporting counts: matched 465→**471**, 454→**501**, 451→**457**, 918→**924**. Junk unchanged in
every cell (10 / 1 / 0 / 2). Raw unique edges +6 / +50 / +6 / +6, removed 0 / 3 / 2 / 2.

## Predictions that would falsify this

1. **grep and repowise must be byte-identical** to `results/2026-08-28-explicit-interface`. Nothing
   about them changed.
2. **Junk must not rise in any cell**, and serilog/without-tests must stay at exactly 1.0000. A
   binary that suddenly resolves the BCL could plausibly start emitting edges the answer key does not
   have; the forecast says it does not.
3. The impartiality test **does not apply** and is pre-registered as not applying: this raises only
   our arm, by construction.

## Honest scope of the correction

The recall gain is real but smaller than the 749→5 headline suggests, and the reason is worth stating
before the run rather than after: **most of those unresolved calls went INTO the BCL**, and a call
whose callee is not first-party produces no edge in the graded cell either way. What moves the number
is the smaller set of FIRST-PARTY calls whose resolution needed framework knowledge — an overload
picked through a `System.*` type, a receiver whose type came from a generic BCL container.

⛔ Every polygon number published before this run was measured against a blinded engine. The
direction was always favourable to a sceptic — our coverage was understated, never inflated — but the
figures did not describe the shipped engine, and the ones after this run do.

---

# RESULT — every number landed exactly, again

Run `results/2026-08-28-bench-engine-bcl`, all three arms, all four cells.

| cell | precision | recall | junk | matched |
|---|---|---|---|---|
| Polly / without-tests | 0.9789 → **0.9792** | 0.6615 → **0.6700** | 10 → 10 | 465 → **471** |
| FluentValidation / without-tests | 0.9978 → **0.9980** | 0.7276 → **0.8029** | 1 → 1 | 454 → **501** |
| serilog / without-tests | 1.0000 → **1.0000** | 0.8399 → **0.8510** | 0 → 0 | 451 → **457** |
| serilog / with-tests | 0.9978 → **0.9978** | 0.3937 → **0.3962** | 2 → 2 | 918 → **924** |

Falsifiers all held: grep and repowise byte-identical in all twelve rows; junk rose in no cell;
serilog/without-tests stayed at exactly 1.0000.

**These are the first polygon numbers ever measured against an engine that could see the framework.**
Everything published before was scored on a binary with zero BCL references.

Side effect, worth recording: with the framework visible, `CallSidecarEntry.MetadataName` is now
recorded for explicit implementations of framework interfaces too — entries carrying it went from 10
to 21 across the corpora. Two remain without it (`EnricherStack::IEnumerable<ILogEventEnricher>.GetEnumerator`),
a residual disclosed rather than hidden.
