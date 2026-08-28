# e7 — implicit base-constructor calls

Engine change under test: a constructor written with **no** `: base()` / `: this()` clause still calls
the base type's parameterless constructor in IL. The engine emitted nothing for that shape. It now
emits it, on both the semantic (`--solution`) and the syntactic (`--project`) path.

Engine commit: `f417ea38` (working tree published as
`dist-all/_bench-engine-feat-e7/arch-computer.exe`). Baseline binary: `dd1076aa` published as
`dist-all/_bench-engine-baseline-e7/arch-computer.exe`; that baseline was proved edge-identical to
the binary that produced e6's numbers — `arms/cheburnexus/run.py` on serilog/without-tests gives 685
edges from each, and `sort`-diff is empty.

Written BEFORE any grader run of this change. The forecast below is arithmetic on a measured edge
diff (section 3), not an estimate from source text.

Baseline — e6, `results/2026-08-28-methodgroups`, read out of each cell's `cheburnexus/result.json`:

| cell | oracle | arm edges | matched | precision | recall |
|---|---|---|---|---|---|
| Polly / without-tests | 703 | 446 | 430 | 0.9641 | 0.6117 |
| FluentValidation / without-tests | 624 | 429 | 400 | 0.9324 | 0.6410 |
| serilog / without-tests | 537 | 443 | 435 | 0.9819 | 0.8101 |
| serilog / with-tests | 2203 | 917 | 871 | 0.9498 | 0.3954 |

---

## 1. What changed in the engine

C# compiles `public Derived() { }` over a base class into a constructor whose first instruction is
`call instance void Base::.ctor()`. Nobody writes that call; the compiler prepends it. The oracle
reads instructions, so it has always had this row — `op = call`, callee a first-party `.ctor`, which
puts it squarely in the primary comparable set. The arm had nothing to match it with.

Two shapes are now emitted, both with `Kind: "base"` — the same kind an explicitly written
`: base(...)` already gets, because it is the same fact whether or not a human typed it:

- **(A)** a declared instance constructor with `Initializer == null`, anchored to the constructor's
  own declaration line (the call has no source text of its own to anchor to);
- **(B)** the synthesized parameterless constructor of a class that declares no instance
  constructor, anchored to the type's declaration line.

Silent, deliberately: `: this(...)` (the constructor it chains to makes the base call, and IL emits
none here), an explicit `: base(...)` (already emitted — it must not be doubled), structs (no base
constructor call in IL at all), static constructors, and the three shapes already disclosed in
`mcp/FACT_CONTRACT.md` whose caller has no key spelling — a positional record, a C# 12 primary
constructor, and a `partial` type whose constructors live in another file.

Also silent, and newly disclosed in `mcp/FACT_CONTRACT.md`: when the base type is not part of the
analysed project, no edge is emitted and — in a compilation carrying no metadata references for that
type — the site is counted in NEITHER drop bucket. Measured through the published binary on
`corpus/Polly/src/Polly.Core`: `Resolved` 607 → 637 while `DroppedExternal` stayed at 48 and
`DroppedUnresolved` at 572. Accepted because a base outside the project could never be an
intra-project edge, so counting it would inflate the very number a reader uses to judge coverage
while telling them nothing; and because a first-party base always resolves and always yields an
edge, so no intra-project edge can hide inside that silence. This has no effect on any number in
this run — the grader reads edges, not counters.

## 2. Why this is a modelling decision and not a bug fix

"Does a call the programmer never wrote count as a call?" has two defensible answers, and the answer
had to be argued before it was measured. It counts, for the same reason `.ctor`/`.cctor` are spelled
the IL way and a method group is reported as `ldftn`: the engine reports what the compiler emits,
and the reader who asks "who constructs a `PipelineComponent`?" is asking about the program, not
about the source text. The line anchor is the honest compromise — there is no source token to point
at, so it points at the constructor that owns the call.

## 3. The measurement the forecast rests on

Both binaries were run through `arms/cheburnexus/run.py` on all four measurable cells and their raw
edge files `sort`-compared line by line:

| cell | base edges | feat edges | added | **removed** |
|---|---|---|---|---|
| Polly / without-tests | 648 | 678 | 30 | **0** |
| FluentValidation / without-tests | 572 | 605 | 33 | **0** |
| serilog / without-tests | 685 | 695 | 10 | **0** |
| serilog / with-tests | 1271 | 1286 | 15 | **0** |

The grader's key is `Type::Method` with parameter types dropped, so several of those raw edges
collapse: `RegularExpressionValidator`1` contributes six raw edges (six constructors, all calling
the same base constructor) and exactly one graded pair. Deduplicated to `(caller, callee)` pairs and
differenced against the baseline's pairs:

| cell | new distinct pairs | lost pairs |
|---|---|---|
| Polly / without-tests | 29 | 0 |
| FluentValidation / without-tests | 22 | 0 |
| serilog / without-tests | 8 | 0 |
| serilog / with-tests | 13 | 0 |

Every one of them is a `Derived::.ctor -> Base::.ctor` pair with a first-party callee. This is the
number e6's own miss was about — sizing by the mechanism measures the ENGINE, and the grader's key
normalisation sits between that and the score. Here that normalisation has been applied by hand
first, which is why the bands below are narrow.

## 4. Predictions — primary cell, per corpus

| cell | arm edges | matched | precision | recall |
|---|---|---|---|---|
| Polly / without | 446 → **473–476** | 430 → **456–459** | 0.9641 → **0.960–0.967** | 0.6117 → **0.649–0.653** |
| FluentValidation / without | 429 → **449–452** | 400 → **419–422** | 0.9324 → **0.930–0.937** | 0.6410 → **0.672–0.677** |
| serilog / without | 443 → **449–452** | 435 → **441–443** | 0.9819 → **0.980–0.983** | 0.8101 → **0.821–0.825** |
| serilog / with | 917 → **928–931** | 871 → **879–882** | 0.9498 → **0.944–0.951** | 0.3954 → **0.398–0.401** |

Point estimates, stated so a miss is unambiguous: Polly 475/459/0.9663/0.6529 · FluentValidation
451/422/0.9357/0.6763 · serilog-without 451/443/0.9823/0.8249 · serilog-with 930/882/0.9484/0.4003.

**A precision DROP is predicted in serilog / with-tests and nowhere else**, and its cause is named in
advance: two of that cell's thirteen new pairs have a `TestDummies` caller and callee, and
`TestDummies` is not among the assemblies the answer key covers. They are junk BY CONSTRUCTION — the
standing corpus scope mismatch that is plan item 2. ⛔ Nothing about the corpus, the grader or the
arm's scope will be changed in response to this run.

Precision rises slightly in the other three cells because every new pair there is expected to match.

## 5. Named risks, stated before the run

1. **`TestDummies` (2 pairs, serilog/with-tests).** Junk by construction, above. Predicted, ⛔ untouched.
2. **Arity-crossing pairs.** `Polly.PredicateBuilder::.ctor -> Polly.PredicateBuilder`1::.ctor`,
   `CircuitBreakerStrategyOptions::.ctor -> CircuitBreakerStrategyOptions`1::.ctor`,
   `RetryStrategyOptions::.ctor -> RetryStrategyOptions`1::.ctor` — a non-generic class deriving from
   a closed instantiation of its own generic twin. `EDGE_FORMAT.md` keeps generic ARITY and strips
   generic ARGUMENTS on both sides, so these should match; if the two sides spell arity differently
   these three become junk in Polly and precision lands at the bottom of its band.
3. **`Serilog.Tests.Support` (3 pairs, serilog/with-tests).** `Serilog.Tests` IS in that cell's
   first-party set, so these should match. If the assembly did not build into the oracle they become
   three more junk edges and serilog/with-tests falls below its band.
4. **Shape (B) under a partial type.** The semantic path keys a synthesized constructor by the file
   it was seen in, so a `partial` class with no declared constructor and parts in two files would be
   spelled under two keys for one IL method. The arm collapses both onto `Type::.ctor`, so this
   cannot surface as junk here; it did not fire on these corpora (no added caller appears with two
   files). Named because it is a real key-space duplication in the product, not because it threatens
   this run.
5. **Recall gains are small in absolute terms** — 29/22/8/13 pairs against oracles of 703/624/537/2203.
   The recall movement is 3–4 points and no cell approaches its ceiling.

## 6. Kill conditions

1. **Nothing but the target group moves.** Already checked before the run: raw edge sets `sort`-compared
   per cell, **0 edges removed** anywhere and every added edge a `.ctor -> .ctor` pair. Re-checked
   after the graded run against the published `edges.jsonl`.
2. **Recall must not fall in any cell.** A recall drop means the change removed a true edge.
3. **Precision floor.** No cell below 0.93; serilog/without-tests must stay at or above **0.97** —
   that is the pre-registered P1 criterion, and it is currently passing.
4. **The untouched arms must be byte-identical.** repowise and grep are untouched by construction, so
   their edge files must match e6's exactly. This requires the full matrix with ALL THREE arms —
   `--arms cheburnexus` cannot perform this check.

Run command: `PYTHONHASHSEED=0 python3 runner/run.py --checkouts corpus --out results/2026-08-28-implicitbase`
