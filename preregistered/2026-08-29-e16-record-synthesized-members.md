# Pre-registration — e16, a record's compiler-written members

Written and committed **before** the graded run. Baseline `results/2026-08-29-e15-nested-types`,
engine binary `dist-all/_bench-engine-e16-record-members`.

## What was wrong

The compiler writes `Equals(T)`, `Equals(object)`, `GetHashCode`, `ToString`, `PrintMembers`,
`operator==` and `operator!=` for every record, with bodies that exist in IL and in no source file.
`EnumerateMembers` walks declarations, so these members existed on **neither** end of our graph: not
as a callee (a call to one was counted as leaving the assembly — a false statement about where a
first-party member lives) and not as a caller (the calls they make to each other were absent).

Four of those calls are fixed by the language and emitted for every record that does not write the
member itself: `operator!= → operator==`, `operator== → Equals(T)`, `Equals(object) → Equals(T)`,
`ToString → PrintMembers`. Polly has 11 record types and the answer key records exactly these four
rows for each — **44 rows** our graph had nowhere.

## The change

**Product**: `SynthesizedRecordMembers` registers the implicitly-declared members in the index (so a
call site can land on them, exactly as `SynthesizedConstructors` already does for a default
constructor), and `SynthesizedRecordEdges` emits the four edges, `Implicit = true` so no consumer is
told the call is greppable — the same treatment `ImplicitBaseEdges` gives an implicit `base()`.
Emitted only when the compiler actually synthesized BOTH ends, asked of the symbol: a record that
declares its own `ToString` gets nothing invented for it, and there is a test for that.

**Arm**: the engine keys an operator by its C# token (`operator==`) — deliberate, per
`CallKeyBuilder.OperatorKey` — while IL names it `op_Equality`. Translated at the boundary, on
**both ends**, unlike e13's conversion-operator translation which measured zero such callees: a
record's `operator!=` calls its `operator==`, so the shape is caller and callee of the same edge.
Reading one end only would have turned a miss into a junk edge instead of a match.

## Sizing, measured with both binaries

Raw arm rows: Polly 741 → **785 (+44, −0)** · FluentValidation 788 → **792 (+4)** ·
serilog 847 → **847 (+0)** · serilog with-tests 1541 → **1541 (+0)**. Polly's +44 is 11 records × 4.

## The forecast

Reconstructed with `grade.py`'s own `load_oracle`/`Cell.score` against both arm outputs. The
reconstruction reproduces the official baseline exactly in three cells and is one row low on Polly,
so deltas are the claim and absolutes carry ±2.

| cell | matched | junk | precision | recall |
|---|---|---|---|---|
| Polly / without-tests | 532 → **576** (+44) | 1 → **1** | 0.9981 → **0.9983** | 0.7568 → **0.8194** |
| FluentValidation / without-tests | 566 → **570** (+4) | 1 → **1** | 0.9982 → **0.9982** | 0.9071 → **0.9135** |
| serilog / without-tests | 509 → **509** | 1 → **1** | 0.9980 | 0.9479 |
| serilog / with-tests | 1077 → **1077** | 1 → **1** | 0.9991 | 0.4618 |

**Junk does not move in any cell**, and both serilog cells must be BYTE-IDENTICAL — neither corpus
declares a record, so the change is a no-op there and that is a falsifiable claim, not a hope.

## ⚠ Named in advance, and NOT fixed here

An operator USE is not collected as a call site at all: `CallNodes` yields invocations, object
creations and method groups, never a `BinaryExpressionSyntax`. So `a == b` on a first-party type
reaches nothing to resolve, and this change does not alter that. It is a wider gap than records — it
costs every user-defined operator — and it gets its own experiment. The unit test for this change
deliberately uses `a.Equals(b)` rather than `a == b` so that a record-shaped name cannot hide it.

## Kill criteria

1. grep or repowise move by one edge in any cell.
2. Junk rises anywhere.
3. Either serilog cell's cheburnexus `edges.jsonl` is not byte-identical to baseline.
4. Matched rows land outside the forecast ±2 in any cell.

⛔ Nothing about the engine, the arm, the grader or the corpus is touched after these numbers are seen.

---

# RESULT — run and grading, 2026-08-29

`results/2026-08-29-e16-record-members`.

| cell | matched | junk | precision | recall |
|---|---|---|---|---|
| Polly / without-tests | **576** (+44) | 1 | **0.9983** | 0.7568 → **0.8193** |
| FluentValidation / without-tests | **570** (+4) | 1 | 0.9982 | 0.9071 → **0.9135** |
| serilog / without-tests | 509 | 1 | 0.9980 | 0.9479 — byte-identical |
| serilog / with-tests | 1077 | 1 | 0.9991 | 0.4618 — byte-identical |

Every forecast number exact. Junk moved in no cell. grep and repowise byte-identical everywhere, and
so are both serilog cells' own cheburnexus rows — the no-op claim held to the byte.

Polly's primary cell: **0.6743 → 0.8193 recall across four experiments today**, precision never below
0.998.
