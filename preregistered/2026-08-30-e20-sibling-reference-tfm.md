# e20 — the sibling reference DLL was picked by file time, so we bound against another TFM's API

Written BEFORE the arm-only sizing run finished. Engine build `_bench-engine-e20-tfm-ref`
(working tree), baseline = `_bench-engine-e19b-final` / results `2026-08-30-e19b-implicit-usings`.

## ⚠ Disclosure, same as e18/e19
`runner/run.py` grades every cell it runs, so the arm-only sizing run produces graded cheburnexus
numbers on disk. The forecast below is written before those numbers are read, but it is a forecast
made by RECONSTRUCTION from the baseline's own rows, not a blind one. Genuinely blind: the two rival
arms in the full matrix.

## The defect (PRODUCT)
`DotNetProjectInputProvider.FindNewestInBin` chose a sibling project's built assembly by
LAST WRITE TIME across the whole `bin` tree, ignoring the target framework the very same method
`ResolveTargetFramework` had already decided. A multi-targeted project ships one assembly per TFM and
they are DIFFERENT API SURFACES, not copies.

Measured on serilog: `src/Serilog/bin/Release/` holds net8.0, net6.0 and netstandard2.0, written
within one second of each other — net8.0 is the OLDEST by mtime, so it could never win. The
netstandard2.0 assembly has no `DisposeAsync` at all (`strings … | grep -c '^DisposeAsync$'` → 1, 1, 0
for net6.0, net8.0, netstandard2.0), because the member sits behind `#if FEATURE_ASYNCDISPOSABLE`,
which that TFM does not define. So `Serilog.Tests` — analysed as net8.0 — bound against the
netstandard2.0 surface, `await log.DisposeAsync()` did not bind, and the edge was lost with nothing
said: no `DroppedExternal`, no unresolved counter, no diagnostic.

Fix: prefer a candidate whose path has a directory segment EXACTLY equal to the resolved TFM; keep
newest-wins when the sibling has no output for our framework (no compatibility ladder is walked — an
exact match or the old evidence, never a guess), and layouts with no TFM folder (Unity,
legacy `bin/Debug`) are untouched. `ResolveTargetFramework` moved above `ProjectReferences` in
`BuildAssembly` so the decision is made once and reused.

Proven to FAIL first: with the engine change stashed, `PrefersSiblingOutputBuiltForOurTargetFramework`
FAILS and `FallsBackToNewestWhenSiblingHasNoOutputForOurTargetFramework` PASSES (1 failed, 1 passed) —
the second is a no-regression guard and is said plainly not to discriminate. With the fix, all 47
tests in `DotNetProjectInputProviderTests` pass.

## Where it came from — the second bucketing of serilog/with-tests
90 missed primary rows at recall 0.9614 grouped into three causes, not a list:

| bucket | rows | share | cause |
|---|---|---|---|
| A | 59 | 66% | calls from tests into **internal** members of Serilog — our compilations are unsigned, and `InternalsVisibleTo("Serilog.Tests, PublicKey=…")` denies access. NOT touched by e20. |
| B | 20 | 22% | members absent from the netstandard2.0 surface — **this experiment** |
| C | 7 | 8% | C#12 primary constructors on test-support types |
| D | 4 | 4% | tail: event `add_`, base ctor of a nested type, two overload spellings |

## FORECAST (reconstruction; the arm-only run must reproduce it)
**serilog / with-tests, primary cell**
* matched **2242 → 2259** (+17): `Serilog.Core.Logger::DisposeAsync` ×14 and `Serilog.Log::CloseAndFlushAsync` ×3.
* recall **0.9614 → 0.9687**, oracle_edges unchanged at 2332.
* junk unchanged at 11 ⇒ arm_edges_in_cell 2253 → 2270, precision 0.9951 → **0.9952**.
* ⛔ `Serilog.Core.Sinks.Batching.BatchingSink::DisposeAsync` ×2 must STAY missed: `BatchingSink` is
  internal, so bucket A blocks it whichever TFM we bind. If those two come back, my model of bucket A
  is wrong and it must be re-examined before anything else is claimed.

**serilog / without-tests** — byte-identical (one in-scope project, no ProjectReference at all).
**Polly / without-tests** — byte-identical. Polly uses the `artifacts/bin/<Project>/release_net8.0`
layout, which `FindNewestInBin` never searches (`<projectDir>/bin` does not exist), so its
ProjectReferences resolve through the source cross-link today and still will. 🕳️ Named, not fixed:
the ArtifactsPath layout is invisible to this resolver, and `release_net8.0` would not match the
exact-segment rule even if it were searched.
**FluentValidation / without-tests** — byte-identical (no built output in the checkout at all).

**Raw edge counts**: with-tests may rise by MORE than 17 — the net8.0 surface also exposes members
whose callees land in excluded cells (BCL `IAsyncDisposable::DisposeAsync` and friends). Only the
primary-cell number above is a claim.

## Kill criterion (unchanged from e19, and it is what caught e19's other half)
**Any oracle row matched in `2026-08-30-e19b-implicit-usings` that is unmatched here kills the
change**, whatever the topline does. Checked row by row, not by comparing totals — a silent loss can
move the honest counters the wrong way.

## Impartiality
grep and repowise must be byte-identical in all four cells of the full matrix. This change is inside
the engine, so any movement there means the harness moved with it.

## Outcome — SHIPPED
Arm-only sizing `results/2026-08-30-e20-size`, full matrix `results/2026-08-30-e20-sibling-tfm`
(cheburnexus edges byte-identical between the two, all four cells).

| cell | recall e19b → e20 | precision | matched |
|---|---|---|---|
| serilog / with-tests | **0.9614 → 0.9713** | 0.9951 → **0.9952** | 2242 → **2265** (+23) |
| serilog / without-tests | 1.0000 (byte-identical) | 0.9981 | 537 |
| Polly / without-tests | 0.8734 (byte-identical) | 0.9984 | — |
| FluentValidation / without-tests | 0.9183 (byte-identical) | 0.9978 | — |

**Kill criterion: PASSED — 0 previously matched rows lost**, checked pair by pair against the
baseline's own matched set, not by comparing totals. **0 new junk** (10 → 10 by the row-level count;
`arm_edges_in_cell − matched` stays 11, the extra one being two legal override edges collapsing onto
one oracle target). Raw arm edges 2280 → 2303: **+23 added, 0 removed**.

**Impartiality PASSED**: grep and repowise byte-identical in all 8 measured cells.

### The forecast, scored honestly
✅ The two named groups landed EXACTLY: `Logger::DisposeAsync` **14/14**, `Log::CloseAndFlushAsync`
**3/3**.
❌ **The explicit ⛔ prediction was WRONG, and it matters more than the hit.** I predicted
`BatchingSink::DisposeAsync` ×2 would STAY missed because `BatchingSink` is internal and bucket A
would block it whichever TFM we bind. It came back — and so did four more internal-callee rows I had
not forecast at all: `PropertyBinder::ConstructProperties` ×2 and `LevelOverrideMap::GetEffectiveLevel`
×2, every one of them called from a test project. +23 measured against +17 forecast.

🔑 **So bucket A's stated cause is REFUTED by this run.** Internal members of Serilog DO bind from the
test compilations; `InternalsVisibleTo` + our unsigned compilation is NOT what blocks them, and no
work should be spent on strong-naming on the strength of that story. `ConstructProperties` shows what
was really happening in those six rows: its SIGNATURE differs by TFM
(`ReadOnlySpan<object?>` under `FEATURE_SPAN`, `object?[]` otherwise), so binding against the wrong
surface produced a different grader key — a MISMATCH, not an absence. A wrong-TFM reference therefore
causes both silent losses (member absent) and wrong facts (member present with another signature).

### Where serilog / with-tests now stands — 67 misses, re-bucketed
| rows | callee | note |
|---|---|---|
| 33 | `PropertyValueConverter::CreatePropertyValue` | cause UNKNOWN — the old "internal is inaccessible" story is dead |
| 10 | `Guard::AgainstNull` | ditto; `Guard` is a type in the GLOBAL namespace |
| 17 | assorted `::.ctor` | includes the 7 C#12 primary constructors already queued |
| 7 | tail | event `add_`, nested-type base ctor, two overload spellings |

▶️ Next: bucket the 43 `CreatePropertyValue` + `Guard` rows for their REAL cause before proposing
anything. 🕳️ Named, not fixed: `FindNewestInBin` never looks at the .NET 8 `artifacts/bin/<Project>/
release_net8.0` layout (Polly's) — its ProjectReferences resolve through the source cross-link today,
and `release_net8.0` would not match the exact-segment rule even if that tree were searched.
