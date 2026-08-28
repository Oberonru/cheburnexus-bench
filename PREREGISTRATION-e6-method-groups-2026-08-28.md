# Pre-registration — e6, method-group conversions emitted as `ldftn` edges

Written 2026-08-28 BEFORE the run. The engine change is finished, its unit tests are green, and the
new edges have been COUNTED through the published binary on every corpus — but nothing has been
graded. Baseline is run **e5** (`PREREGISTRATION-e5-audit-fixes-2026-08-28.md`), read out of
`results/2026-08-28-auditfix/*/cheburnexus/result.json`, primary cell:

| cell | oracle | arm edges | matched | precision | recall |
|---|---|---|---|---|---|
| Polly / without-tests | 703 | 436 | 420 | 0.9633 | 0.5974 |
| FluentValidation / without-tests | 624 | 421 | 395 | 0.9382 | 0.6330 |
| serilog / without-tests | 537 | 442 | 434 | 0.9819 | 0.8082 |
| serilog / with-tests | 2203 | 914 | 868 | 0.9497 | 0.3940 |

## What changed in the engine

A **method group** — a method named at a site without being entered, `_resolver = DefaultResolver;`,
`Apply(Helper)`, `Changed += OnChanged;` — is now a call edge with `Kind: "ldftn"`.

Before this, neither C# walk matched anything but invocations and object creations, so a method group
was not an unresolved call site — it was no call site at all. It hit neither drop counter, and
`who_calls` answered "nobody" about a method the program reaches at run time through the delegate.

The decision, written before the code: **a method group IS a call edge.** Two reasons, and neither is
the score.
1. IL emits `ldftn <method>` there, and this benchmark's own oracle counts that op in its primary
   metric alongside `call` / `callvirt` / `newobj` (`grader/EDGE_FORMAT.md`). By the ruler we have
   already committed to, the edge exists.
2. A missing edge here is a false "no callers", the same class of silent loss as the walk-seam work.

The distinct `Kind` is the other half of the decision: the method is REFERENCED, not entered, and a
consumer must be able to tell those apart. It is spelled `ldftn` for the same reason `.ctor`/`.cctor`
are spelled the IL way — the instruction's real name, and the same vocabulary the oracle's `op` field
uses, so nothing has to be translated between them.

The shape rule lives once, in `MethodGroupSyntax`, shared by both C# builders. The syntactic
(`--project`) path is deliberately narrower — no symbols means it emits only in positions where the
language expects a delegate value, only when nothing in the member shadows the name, and never when
two overloads share it. The bench uses `--solution`, so no number here moves because of that half.

## The measurement the prediction rests on — counted by the MECHANISM, not by regex

Both binaries (with and without the feature) were run through `--solution` on every first-party
project of all three corpora, and the emitted edges compared directly. The two previous forecasts
missed low because the group was sized by regex-matching source lines; this one counts the edges the
recovering mechanism actually produces.

**Every non-`ldftn` Kind count is byte-identical between the two binaries on all 12 projects run** —
`this`, `base`, `static`, `new`, `instance` all unchanged. `DroppedUnresolved` moved by +1 in exactly
one project and by 0 everywhere else. So every prediction below is about ADDED edges only.

| corpus / project | new `ldftn` edges | caller shape |
|---|---|---|
| FluentValidation (product) | **8** | 5 method or constructor, **3 property setter** |
| Polly.Core | **11** | all method or constructor |
| Polly.Extensions / RateLimiting / Testing | 0 | — |
| serilog (product) | **1** | constructor |
| Serilog.Tests | **2** | methods |
| Serilog.ApprovalTests | 0 | — |

The 3 FluentValidation edges with a property-setter caller are expected to be **junk, by our own
pre-declared spelling**: `CallKeyBuilder.MemberKey` names the member the user wrote
(`ValidatorConfiguration::PropertyNameResolver`) while the oracle names the accessor
(`set_PropertyNameResolver`). That mismatch is older than this change, already recorded against us in
`EDGE_FORMAT.md` and in the e4 junk read-through. It is NOT to be "fixed" in response to this run.

## Predictions — primary cell, per corpus

Bands run from "every match we expect fails" to "every one lands".

| cell | arm edges | matched | precision | recall |
|---|---|---|---|---|
| Polly / without-tests | 436 → **447** | 420 → **429–431** | 0.9633 → **0.960–0.965** | 0.5974 → **0.610–0.613** |
| FluentValidation / without-tests | 421 → **429** | 395 → **399–400** | 0.9382 → **0.928–0.933** ⬇ | 0.6330 → **0.639–0.641** |
| serilog / without-tests | 442 → **443** | 434 → **434–435** | 0.9819 → **0.980–0.982** | 0.8082 → **0.808–0.811** |
| serilog / with-tests | 914 → **917–922** | 868 → **870–871** | 0.9497 → **0.943–0.950** | 0.3940 → **0.395** |

**FluentValidation precision is predicted to FALL, by about 0.006–0.011, and that is written here
before the run.** Three of its eight new edges are true facts our key spells differently from the
oracle. Recall rises in every cell; nothing is predicted to fall except that one precision figure.

The serilog / with-tests arm-edge band is wider on purpose: the arm reads the sources of
`Serilog.PerformanceTests` and `TestDummies`, which the answer key does not cover — the corpus scope
mismatch that is item 3 of the standing plan. Any method group found there is junk BY CONSTRUCTION,
not a defect of this change, and it is not to be touched in response to this run.

## Named risks, stated before the run

- **The self-referential edge.** `Serilog.Tests` contains a method whose body names ITSELF as a
  method group (`PropertyValueConverterTests::Delegates…Destructuring` → itself, line 163). It is a
  real `ldftn` and a real self-edge. If the grader rejects self-edges, it is one junk edge.
- **Generic callers.** Seven of the Polly edges have a generic type as caller
  (`CircuitStateController<T>`, `ObjectPool<T>`, `HedgingController<T>`). Their keys must arity-mangle
  the TYPE the way the oracle does (`Cache\`1`). If they do not, those become junk with a signature —
  junk concentrated on generic types — and it must be read in the diff, not inferred from the score.
- **Externally-targeted method groups.** FluentValidation gained 7 and serilog 1 `DroppedExternal`:
  method groups whose target lives outside the project. These are correctly NOT edges. They are new
  DISCLOSURE, not new loss, and they move `UnresolvedShare` slightly in the honest direction.

## Kill conditions

1. Any non-`ldftn` edge changes anywhere. The arm's raw edge set minus the new pairs must be
   identical to e5's, `sort`-compared line for line. This is checked directly, not inferred from a
   score that could hide two cancelling movements.
2. Precision below the predicted floor in any cell (Polly 0.960, FV 0.928, serilog 0.980 / 0.943).
   Then the modelling decision or the key spelling is wrong and must be re-opened before publishing.
3. Recall falls in any cell.
4. repowise or grep move at all — they are untouched by construction, so any movement is a harness
   defect and invalidates the run.

The full matrix is run with ALL THREE arms. `--arms cheburnexus` cannot perform check 4.

---

## Addendum — the with-tests band, narrowed BEFORE any grading

Appended minutes after the file above was first written, from a measurement that was still running
when it was. **No graded output had been produced or read at that point** — the matrix run had been
launched but nothing had been scored, and this addendum changes only a band, never a threshold.

The three projects behind the corpus scope mismatch were measured with both binaries:
`Serilog.PerformanceTests` (33 edges), `TestDummies` (11), `AotTestApp` (0) — **zero `ldftn` edges in
all three**, and every non-`ldftn` kind count identical between the binaries.

So the wider band was unnecessary. serilog / with-tests gains exactly the 3 edges already counted
(1 product + 2 from `Serilog.Tests`):

| cell | arm edges | matched | precision | recall |
|---|---|---|---|---|
| serilog / with-tests | 914 → **917** | 868 → **870–871** | 0.9497 → **0.9488–0.9498** | 0.3940 → **0.3949–0.3954** |

Kill condition 2's floor for this cell tightens with it: **0.9488**.

---

# RESULT — run of 2026-08-28, `results/2026-08-28-methodgroups` (gitignored, local only)

Full matrix, all three arms, `PYTHONHASHSEED=0`.

| cell | arm edges | predicted | matched | predicted | junk | precision | predicted | recall | predicted |
|---|---|---|---|---|---|---|---|---|---|
| Polly / without | 436→**446** | 447 | 420→**430** | 429–431 ✅ | 16→**16** | 0.9633→**0.9641** | 0.960–0.965 ✅ | 0.5974→**0.6117** | 0.610–0.613 ✅ |
| FluentValidation / without | 421→**429** ✅ | 429 | 395→**400** | 399–400 ✅ | 26→**29** | 0.9382→**0.9324** | 0.928–0.933 ✅ | 0.6330→**0.6410** | 0.639–0.641 ✅ |
| serilog / without | 442→**443** ✅ | 443 | 434→**435** | 434–435 ✅ | 8→**8** | 0.9819→**0.9819** | 0.980–0.982 ✅ | 0.8082→**0.8101** | 0.808–0.811 ✅ |
| serilog / with | 914→**917** ✅ | 917 | 868→**871** | 870–871 ✅ | 46→**46** | 0.9497→**0.9498** | 0.9488–0.9498 ✅ | 0.3940→**0.3954** | 0.3949–0.3954 ✅ |

**Every clause of the prediction held. Recall rose in all four cells; precision moved only where it
was predicted to fall, and by the predicted amount.** After two forecasts in a row that missed low,
this one landed — the difference is that the group was sized by running the recovering mechanism,
not by matching source lines with a regex.

## The checks, run rather than inferred

- **Kill 1 — nothing else moved.** The cheburnexus raw edge set was `sort`-compared with e5's, line
  by line, in all four cells: **0 edges removed anywhere**. e5's set is a strict subset of e6's, and
  the additions are exactly 10 / 8 / 1 / 3. Not a score that could hide two cancelling movements — a
  byte diff of the raw output.
- **Kill 4 — invariance.** repowise and grep are byte-identical to e5 in all four cells
  (1131/377, 5602/322, 3647/348, 13099/1101 edges). Run with all three arms so that this check could
  exist at all.
- **Kill 2 and 3 — not triggered.** No precision below its floor, no recall fell.

## Every new edge, read one by one — 22 added, 19 matched, 3 junk

The 3 junk edges are the three predicted ones, and no others: FluentValidation's
`ValidatorConfiguration::PropertyNameResolver` / `DisplayNameResolver` / `ErrorCodeResolver` naming
their `Default…` counterpart from a property SETTER. Every one is a true `ldftn`; the oracle spells
the caller `set_PropertyNameResolver` and we spell it as the member the user wrote. **The pre-declared
mismatch, costing exactly what was pre-declared. Nothing was changed in response.**

## The one number that missed, and why — 11 engine edges became 10 arm edges in Polly

`ResiliencePipelineBuilderBase` has two constructors, `.ctor()` and `.ctor(ResiliencePipelineBuilderBase)`,
and the field initializer at line 100 is attributed to both — which is what IL contains. The grader's
key drops parameter types, so both collapse onto `Polly.ResiliencePipelineBuilderBase::.ctor` and the
pair deduplicates. Predicted as possible ("assume no collapse; upper bound"), and it is the only
place it happened. The prediction of 447 was one high for this reason; matched, precision and recall
all still landed in band.

## Risks that did NOT materialise

- **The self-referential edge.** `PropertyValueConverterTests::Delegates…Destructuring` naming itself
  at line 163 MATCHED — the grader accepts a self-edge, and the oracle has that `ldftn`.
- **Generic callers.** All seven generic-type callers matched: `CircuitStateController\`1`,
  `ObjectPool\`1`, `HedgingController\`1`, `RetryStrategyOptions\`1`, `OutcomeGenerator\`1`,
  `RuleBase\`3`. The type-arity spelling already agreed with the oracle.

## Standing numbers

These four cells supersede e5. ⛔ P1 (≥0.97 everywhere) is still MISSED: Polly 0.964,
FluentValidation 0.932, serilog / with-tests 0.9498 — three of four below, unchanged in status by
this work. FluentValidation moved further from it, knowingly and for a disclosed reason.

---

## ⚠ Correction to this file, 2026-08-28, after the run

This pre-registration twice called the accessor-caller mismatch **"pre-declared"** (§"The measurement
the prediction rests on" and §"Every new edge, read one by one"). **That attribution is wrong.**
`grader/EDGE_FORMAT.md` pre-declared the *explicit interface implementation* spelling; it said nothing
about a caller that is a property accessor — its only accessor rule excludes an accessor as **callee**.

What is true and unchanged: the mismatch was known to us before this run (9 edges, read one by one on
2026-08-25) and the 3 junk edges were predicted here by name and by count before grading. What is
false is that the BENCH had disclosed it. It is disclosed now, in `EDGE_FORMAT.md`, explicitly marked
as written after its cost was known. No number in this file changes.

