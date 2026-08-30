# e19 — two engine defects behind the remaining misses; ONE shipped, one REVERTED

## Outcome (written after the run, kept in the same file as the forecast)
**Shipped: B alone** (`<ImplicitUsings>` inherited from `Directory.Build.props`).
**Reverted: A** (source winning over a stale same-name .dll) — it traded 12 previously-correct edges
for 20, and did it through a mechanism that would silently blind any repo whose sibling is signed:
our compilation is never strong-named, so displacing a signed `Serilog.dll` leaves a third prebuilt
reference (`TestDummies.dll`) unable to see the type at all (`CS0012`). ⛔ Rejected on that trade,
and rejected AFTER measuring both, not by argument. The kill criterion below is what caught it.

Final numbers, B alone, full matrix, all three arms:
serilog/without-tests **0.9479 → 1.0000** (precision 0.9981) · serilog/with-tests
**0.8169 → 0.9614** (precision 0.9948 → 0.9951) · Polly and FluentValidation byte-identical ·
**grep and repowise byte-identical in all four cells**.
A+B would have given 0.9657 on with-tests — 10 matched rows more, at the cost above.

🔑 The 12 lost edges were lost SILENTLY: `DroppedExternal` went DOWN (2→1), because a site that fails
to BIND is not an external call, and there is no per-caller unresolved counter at all. The run-wide
unresolved number IMPROVED at the same time (1547→102), so no number a reader checks would have shown
it. It was found only by checking every removed row against the oracle.



## ⚠ Same disclosure as e18: the cheburnexus numbers were not forecast blind
`runner/run.py` grades every cell it runs, so the arm-only sizing run already produced the graded
cheburnexus numbers before this file was written. They are reported below as MEASURED, not predicted.
Genuinely blind, and the reason the full matrix is run at all: the two rival arms.

## The defects (both in the PRODUCT, found by bucketing the worst cell)
**A. A stale .dll beat the sources under analysis.** `SemanticCompilationBuilder` linked a sibling's
compilation only when no .dll of that name was already in the reference list — so a project whose
sibling had ever been built bound against that BUILD. Both are "assembly A", so nothing looked wrong;
but metadata symbols carry fully-qualified signatures, while pass 1 indexes the declaration as
written in source. Fixed by inverting the winner: the sibling analyzed in this run is linked, and its
stale .dll is dropped from the references. The "never link the same assembly twice" invariant is kept
— ⚠ the test that pinned it asserted the WRONG winner and certified the defect; it is rewritten.

**B. `<ImplicitUsings>` was read only from the project's own .csproj**, never from
`Directory.Build.props` — the ordinary modern layout. Under such a root NO implicit usings were
synthesised, so a type written bare (`TextWriter`, `IFormatProvider`, `IEnumerable<T>`) became an
ERROR TYPE in our own compilation, and Roslyn prints an unresolved type by the bare name it was
written with. Declaration key `Format(LogEvent, TextWriter)`, call-site key
`Format(Serilog.Events.LogEvent, System.IO.TextWriter)`: one method, two dictionary keys, and the
edge published as DroppedExternal. Now read from the nearest `Directory.Build.props` that defines it,
with the project's own value still winning (MSBuild's own order).

Both proven on behaviour against the pre-fix binary: 2 discriminating tests fail there, 0 compile
errors, 51 other tests in the same two classes unaffected. A third test is a guard that does not
discriminate — said plainly rather than counted as proof.

## Raw edges (measured, arm-only run)
| cell | e18 | e19 | added | removed |
|---|---|---|---|---|
| Polly / without-tests | 849 | 849 | 0 | 0 |
| FluentValidation / without-tests | 795 | 795 | 0 | 0 |
| serilog / without-tests | 847 | 895 | 49 | 1 |
| serilog / with-tests | 2593 | 2980 | 400 | 13 |

## What the full matrix must show
**cheburnexus** (measured in the arm-only run; the full run must reproduce it exactly):
* serilog/without-tests primary: arm 510→**538**, matched 509→**537**, precision **0.9981**,
  recall 0.9479→**1.0000** — the first cell in this polygon's history at full recall.
* serilog/with-tests primary: arm 1915→**2263**, matched 1905→**2252**, precision 0.9948→**0.9951**,
  recall 0.8169→**0.9657**.
* Polly/without-tests and FluentValidation/without-tests: **byte-identical** to e18 (neither corpus
  sets `ImplicitUsings` in a props file, and neither has a stale sibling .dll in the analyzed set).

**grep and repowise: byte-identical to e18 in all four cells** — the blind half, and the impartiality
check: this is an ENGINE change, so a rival moving at all would mean the harness is not neutral.

## Kill criterion
Any deviation refutes the reconstruction. Separately: the 14 removed edges are being checked row by
row — if ANY previously matched oracle row is now unmatched, that is a regression and the finding is
that, not the recall.
