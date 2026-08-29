# Pre-registration — e15, nested types

Written and committed **before** the graded run. Baseline: `results/2026-08-29-e14-reduced-extension-calls`.
Engine binary `dist-all/_bench-engine-e15-nested-types`.

## What was wrong

Nested types did not exist anywhere in the product. Six walks carried the same filter
`.Where(t => t.Parent is not TypeDeclarationSyntax)` — `RoslynFileParser` (the root, which builds
`FileModel.Types`), four in `SemanticCallsResolver`, one in `CallsSidecarBuilder` — and the resolver's
own comment called this "scope matches the syntactic builder". They were aligned on the same blind
spot. A ten-line probe: a class with a nested class and a nested record produced **two** type nodes
(the nested ones absent), the enclosing method reported **zero** calls while calling its nested type
twice, and the nested type's own call out was never collected. Not a call-graph gap — `find_class`,
`who_calls`, dependencies and metrics could none of them see a nested type.

## The change

**Product**, not an arm translation:
1. All six filters removed; nested types are types.
2. One spelling for a type's nesting chain, `CallKeyBuilder.TypePath` → `Outer<T>/Inner`.
   **Slash, not dot**, for the reason the methods sidecar already gives for nested local functions:
   `MyNs.Outer.Inner` cannot be told apart from a type `Inner` in a namespace `MyNs.Outer`, so a
   consumer splitting it has to guess. It is also the notation the answer key reads out of IL.
3. Every ancestor is spelled with its arity. The ancestor walk took `Identifier.Text`, silently
   dropping an outer type's type parameters — dead code until now, wrong the moment it ran.
4. `RoslynClassParser` had a second, independently-maintained copy of this naming; it now defers to
   the one spelling.

**Arm** (`arms/cheburnexus/run.py`): `ClassIndex` keyed a type's path by its own simple `Name`, which
for a nested type drops the enclosing type. Measured, not assumed: with the engine fixed and the arm
untouched, Polly gained **21 junk edges** naming types that do not exist (`Polly.SharedPool` for
`Polly.ResilienceContextPool/SharedPool`). The arm now derives the chain from the type's `fqn` and
arity-converts **per segment**. 🔑 the second end of the same key contract the engine fixed at the first.

## The forecast

Reconstructed with `grade.py`'s own `load_oracle`/`Cell.score` against both arm outputs, before the
run. The reconstruction reproduces the official baseline exactly in three cells and is one row low on
Polly (504 vs 505), so **deltas** are the claim and absolutes carry ±2.

| cell | matched | junk | precision | recall |
|---|---|---|---|---|
| Polly / without-tests | 505 → **532** (+27) | 0 → **1** | 1.0000 → **0.9981** | 0.7183 → **0.7568** |
| FluentValidation / without-tests | 556 → **566** (+10) | 1 → **1** | 0.9982 → **0.9982** | 0.8910 → **0.9071** |
| serilog / without-tests | 494 → **509** (+15) | 0 → **1** | 1.0000 → **0.9980** | 0.9199 → **0.9479** |
| serilog / with-tests | 1017 → **1077** (+60) | 0 → **1** | 1.0000 → **0.9991** | 0.4361 → **0.4618** |

⚠ **Precision is forecast to FALL in three cells**, and that is stated here rather than discovered
afterwards. +112 matched rows against **3 junk edges**, and both causes are identified in advance:

- `Polly.ResilienceContextPool/SharedPool::.ctor → Polly.ResilienceContext::.ctor` (1 edge). The
  oracle records the other two constructor calls from this same body and not this one; suspected
  compiler hoisting into a display class, so IL attributes it elsewhere. Not investigated further —
  named, not explained.
- `Serilog.Context.EnricherStack::IEnumerable.GetEnumerator → …/Enumerator::.ctor` (1 edge, appearing
  in both serilog cells). The oracle spells this caller
  `System.Collections.IEnumerable.GetEnumerator`, fully qualified; we spell it `IEnumerable.GetEnumerator`,
  as written in source. **A caller-spelling defect, and true to the rule it shows up TWICE** — as our
  junk AND as two unmatched oracle rows. This is the next item, not this one.

## BYTE-IDENTICAL requirements

grep and repowise in all four cells. An engine change moves one arm by construction; any movement in
another arm is a harness defect, not evidence about this change.

## Kill criteria

1. grep or repowise move by one edge in any cell.
2. Junk rises by more than the 3 edges forecast above, or a junk edge appears whose cause is neither
   of the two named.
3. Any cell loses RECALL.
4. Matched rows land outside the forecast ±2 in any cell.
5. Precision falls below 0.9975 in any cell — the dip is accepted only at the size argued above.

⛔ Nothing about the engine, the arm, the grader or the corpus is touched after these numbers are seen.

---

# RESULT — run and grading, 2026-08-29

`results/2026-08-29-e15-nested-types`.

| cell | matched | junk | precision | recall | forecast |
|---|---|---|---|---|---|
| Polly / without-tests | **532** | 1 | **0.9981** | 0.7183 → **0.7568** | ✅ exact |
| FluentValidation / without-tests | **566** | 1 | **0.9982** | 0.8910 → **0.9071** | ✅ exact |
| serilog / without-tests | **509** | 1 | **0.9980** | 0.9199 → **0.9479** | ✅ exact |
| serilog / with-tests | **1077** | 1 | **0.9991** | 0.4361 → **0.4618** | ✅ exact |

All sixteen forecast numbers landed exactly, the pre-registered precision dip included. grep and
repowise byte-identical in all four cells. **+112 matched rows for 3 junk edges**, both of whose
causes were named before the run.

Every cell of the matrix now sits at recall 0.45–0.95 with precision ≥ 0.998. Two named items remain
on Polly: record-synthesized members (44 rows, 36 of them only reachable now that nested types are)
and C#12 primary constructors. A third was found by this run's own junk: an explicit-interface
caller must be spelled with its FULL interface qualifier
(`System.Collections.IEnumerable.GetEnumerator`, not `IEnumerable.GetEnumerator`) — visible twice, as
one junk edge and two unmatched oracle rows.
