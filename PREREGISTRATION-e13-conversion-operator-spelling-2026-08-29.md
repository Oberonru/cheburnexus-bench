# Pre-registration — e13, conversion-operator caller spelling

Written and committed **before** the graded run, per this repository's rule. Same class of defect
as `_accessor_caller_method` (e9, `DECISION-accessor-caller-spelling-2026-08-28.md`): our engine's
own key spelling is right for the engine's own key space, IL spells the same member differently, and
this arm's job is to translate at the boundary — never to change the engine.

Change under test: the **arm** (`arms/cheburnexus/run.py`) now rewrites a caller whose method-name
segment is a user-defined conversion operator (`implicit operator T` / `explicit operator T`, from
`CallKeyBuilder.ConversionOperatorKey`) to the name the CLR actually gives it — `op_Implicit` /
`op_Explicit` — before the edge is graded. The engine, its keys, and every shipped fact are
untouched.

## Step 1 checks, run before any code changed

**(a) Is the engine's spelling deliberate?** Yes — read in full.
`ArchitectureAnalyzer.Core/Services/Sidecars/CallKeyBuilder.cs:161-172`,
`ConversionOperatorKey`'s own doc comment: *"The implicit/explicit keyword is PART of the identity
— `implicit operator int` and `explicit operator int` on the same type are two distinct conversions
and must not collapse onto one key."* This is the same reasoning `MethodSignature`'s comment gives
for generic arity and `OperatorKey`'s comment gives for the operator token — a considered choice for
the engine's own key space, not an oversight. The fix belongs in the arm, per the task's own
diagnosis, confirmed rather than assumed.

**(b) Is the CALLEE side also missing conversion operators?** Measured, not assumed — loaded
`grader/grade.py:load_oracle` as a module against each cell's real oracle.jsonl with the real
first-party assembly-stem sets, and searched the **primary** cell only (the only comparable one):

| cell | primary oracle rows | callee is `op_Implicit`/`op_Explicit` | caller is `op_Implicit`/`op_Explicit` |
|---|---|---|---|
| Polly / without-tests | 703 | **0** | **6** |
| FluentValidation / without-tests | 624 | **0** | **0** |
| serilog / without-tests | 537 | **0** | **0** |
| serilog / with-tests | 2332 | **0** | **0** |

The one conversion-operator callee that exists anywhere in the four raw answer keys
(`Span<T>::op_Implicit`, called from `Polly.Telemetry.TagsList::get_TagsSpan`) has
`CalleeAssembly: "System.Runtime"` — not first-party for Polly — so it lands in the **external**
cell, not primary, and is out of scope for this arm's translation regardless.

**Conclusion: this is caller-only, and Polly-only.** The callee side contributes **zero** to this
forecast. Nothing is added to the arm for the callee side; a note is left in the arm's comment for
whoever finds a corpus where it matters.

## How the arm-side effect was sized

Not by reading source and guessing — by running the mechanism. `grep -c operator` against each
cell's baseline `edges.jsonl` (`results/2026-08-29-e12-tfm-defines`, the current standing numbers,
engine `048d0cb5`):

| cell | raw arm rows containing "operator" |
|---|---|
| Polly / without-tests | 12 |
| FluentValidation / without-tests | 0 |
| serilog / without-tests | 0 |
| serilog / with-tests | 0 |

Only Polly's raw output contains any operator-shaped caller at all, so only Polly can move. The 12
raw rows are 6 distinct calls × 2 conversions each of the same shape appearing twice in source
(4 `PredicateBuilder<TResult>` conversions to different delegate types, `FaultGenerator`,
`OutcomeGenerator<TResult>` — each converts to `Guard::NotNull` and to one first-party helper); the
grader's `strip_generic_arguments` already collapses the 4 `PredicateBuilder` variants onto one
caller key (`Polly.PredicateBuilder\`1::implicit operator Func`) before comparison, which is exactly
why the graded baseline already shows 6 junk edges, not 12 — matching the task's diagnosis exactly.

Reconstructing the answer key's own 6 matching rows the same way (`grade.load_oracle`, primary
cell, filtered to a conversion-operator caller) gives the identical 6 `(caller, callee)` pairs the
translated arm output will produce, name for name:

```
(Polly.PredicateBuilder`1::op_Implicit,            Polly.PredicateBuilder`1::Build)
(Polly.PredicateBuilder`1::op_Implicit,            Polly.Utils.Guard::NotNull)
(Polly.Simmy.Fault.FaultGenerator::op_Implicit,    Polly.Simmy.Utils.GeneratorHelper`1::CreateGenerator)
(Polly.Simmy.Fault.FaultGenerator::op_Implicit,    Polly.Utils.Guard::NotNull)
(Polly.Simmy.Outcomes.OutcomeGenerator`1::op_Implicit, Polly.Simmy.Utils.GeneratorHelper`1::CreateGenerator)
(Polly.Simmy.Outcomes.OutcomeGenerator`1::op_Implicit, Polly.Utils.Guard::NotNull)
```

All six are `op_Implicit` (Polly's corpus has no `explicit operator` at all); no explicit-interface
conversion occurs anywhere in this corpus either, so the qualifier-preservation branch of the new
function is argued (and unit-tested against a synthetic case) but not exercised by a real corpus row
— named here as a risk, the same way e9 named `init`/`add`/`remove`/indexers as unexercised.

## The forecast

Baseline is the current standing numbers, engine `048d0cb5`, `results/2026-08-29-e12-tfm-defines`:

| cell | oracle | arm edges | matched | junk | precision | recall |
|---|---|---|---|---|---|---|
| Polly / without-tests | 703 | 480 | 474 | 6 | 0.9875 | 0.6743 |
| FluentValidation / without-tests | 624 | 502 | 501 | 1 | 0.9980 | 0.8029 |
| serilog / without-tests | 537 | 494 | 494 | 0 | 1.0000 | 0.9199 |
| serilog / with-tests | 2332 | 992 | 992 | 0 | 1.0000 | 0.4254 |

Predicted:

| cell | arm edges | matched | junk | precision | recall |
|---|---|---|---|---|---|
| Polly / without-tests | 480 → **480** | 474 → **480** | 6 → **0** | 0.9875 → **1.0000** | 0.6743 → **0.6828** |
| FluentValidation / without-tests | 502 → **502** | 501 → **501** | 1 → **1** | 0.9980 → **0.9980** | 0.8029 → **0.8029** |
| serilog / without-tests | 494 → **494** | 494 → **494** | 0 → **0** | 1.0000 → **1.0000** | 0.9199 → **0.9199** |
| serilog / with-tests | 992 → **992** | 992 → **992** | 0 → **0** | 1.0000 → **1.0000** | 0.4254 → **0.4254** |

Point estimate for Polly's recall: 480/703 = 0.68280…, rounded **0.6828**.

## BYTE-IDENTICAL requirements

1. **grep and repowise, all four cells** — untouched by construction; this change is inside the
   cheburnexus arm's own translation function.
2. **cheburnexus's own `edges.jsonl` for FluentValidation/without-tests, serilog/without-tests, and
   serilog/with-tests** — no raw row in any of these three cells contains the word "operator"
   (measured above), so the new function is a no-op there and the raw output must not differ by one
   byte from `results/2026-08-29-e12-tfm-defines`.
3. **cheburnexus's `edges.jsonl` for Polly/without-tests is NOT byte-identical** — this is the one
   cell expected to move, and only in the 6 renamed rows (12 raw lines, once generics are stripped by
   the grader). Every other line in that file must be unchanged.

## Impartiality check

This is an arm-only translation, not a ruler change, so the ordinary impartiality test (every arm
must move together or none should) **does not apply** — same as e9 and e10, for the same reason: a
change to one arm's own key-spelling translation cannot move another arm's numbers unless the
harness itself is broken. grep and repowise moving at all, by even one edge, in any cell is treated
as a harness defect, not as evidence about this change.

## Kill criteria

1. grep and repowise are BYTE-IDENTICAL to `results/2026-08-29-e12-tfm-defines` in all four cells,
   all eight result files (`result.json` + `edges.jsonl` × 2 arms × ... — the full set).
2. cheburnexus's `edges.jsonl` is BYTE-IDENTICAL to baseline in FluentValidation/without-tests,
   serilog/without-tests, and serilog/with-tests.
3. No cell may lose precision or recall anywhere, including the three cells predicted unchanged.
4. Polly/without-tests must land on the exact point estimates above: arm edges 480, matched 480,
   junk 0, precision 1.0000, recall 0.6828. Any other outcome is a miss, not a close call, given
   the answer key's 6 rows were reconstructed by name in advance.
5. The unit test `check_conversion_operator_caller_spelling` in
   `arms/cheburnexus/test_cheburnexus.py` must be proven to FAIL against the pre-fix code (it does:
   the function it imports did not exist, `ImportError`) and PASS after the fix — both checked
   before this run, not after.

⛔ Nothing about the translation, the grader, or the corpus is touched after these numbers are seen.

Run command:
`PYTHONHASHSEED=0 python3 runner/run.py --checkouts corpus --out results/2026-08-29-e13-conversion-operator-spelling`
