# oracle/typescript — shadow-stack call-graph prototype

**Status: prototype. Not wired into `runner/run.py` — there is no `build_oracle_typescript` entry
in `ORACLE_BUILDERS`, and this does not yet produce a graded answer key.**

## The mechanism

A TypeScript-compiler-API transformer (`transformer.js`) rewrites every function/method/
constructor/accessor body it can safely reach from

    { ...body... }

into

    return __stack.run("label", () => { ...body... });

`__stack` is a Node `AsyncLocalStorage`-backed runtime (see `als_stack.js`, and the
disk-appending variant in `jest_setup_stack.js` used under Jest). Each `run()` call looks up the
caller from the *current* ALS context, records `caller -> label`, and executes the wrapped body in
a new child context carrying the extended stack. This is `als.run()` per call, not a shared mutable
array — a shared array was tried first and proven to corrupt edges across `await` (interleaved
async continuations stomp on each other's stack position). See
`finding-shadow-stack-oracle-go-als-per-call-2026-09-02.md` in project memory.

Labels carry the source line, and the enclosing class name where there is one
(`file:Class.method:line`), because the file-plus-name scheme this replaced collided two
same-named methods in different classes onto one graph node and fabricated an edge that never
happened. See `finding-shadow-stack-deterministic-but-fabricates-edges-2026-09-02.md`.

## What has actually been measured

Run against class-validator's own test suite (806 tests, whole-`src/` instrumentation, not a
hand-picked allowlist), via `jest.instrumented.config.js`:

- 806/806 tests pass instrumented.
- 2772 unique call edges recorded.
- Two full runs, byte-identical after sorting: this is deterministic at real scale, not just on
  hand-written fixtures.
- Roughly 2x the plain test-suite wall time.

**Re-verified 2026-09-03 after the computed-name / `static {}` / expression-bodied-and-curried-arrow
fixes** (`9b7c329`, `119d64c`, `9b1a017`), same corpus, same command:

- 806/806 tests still pass instrumented (both runs).
- 3255 unique call edges (was 2772, +483), 1852 unique nodes (was 1376, +476). 1880 function
  bodies wrapped this run (measured with a temporary counter log, reverted — not committed).
- Two full runs, byte-identical after sorting (`edges_r1_v2.txt` vs `edges_r2_v2.txt`, `diff` on
  the sorted files is empty): determinism holds after the fixes, not just before them.
- Wall time ~16.7s instrumented vs ~3.8s for the same suite uninstrumented on this run (~4.4x, not
  the ~2x noted above — that older ratio came from a smaller/different measurement; take the ~4.4x
  as the current number, not a regression, since no like-for-like 2x figure from this exact
  environment exists to compare against).
- **The label scheme means a raw diff against the 2772/1376 baseline is not a clean recall
  measurement.** The anonymous-arrow label (`arrowN`) is assigned per file in AST visit order;
  adding a new wrappable-arrow branch shifts that counter for every later anonymous callable in
  the same file, so many "different" edges are the same call site under a renumbered label, not a
  new fact. To separate the two: normalizing every label by stripping the trailing `:line` and
  collapsing `arrowN` to `arrow*`, then comparing the coarse edge sets — 594 coarse edges appear
  only in the new run and 120 only in the old run. Spot-checking the new-only set shows the
  expected shape from the fix: expression-bodied validator arrows now appear as their own frame
  (e.g. `IsIn.ts:isIn -> IsIn.ts:arrow*:10` and the reverse call back out of that arrow), which
  were invisible/misattributed before. This is evidence of genuinely new call frames, not proof of
  their exact count — the coarse normalization can itself still merge or split edges in ways not
  fully audited. The raw per-line edge count going from 130,844 to 594,079 (4.5x) is far larger
  than a relabeling could produce on its own and is consistent with new arrow bodies now
  contributing their own call volume across the many repeated test invocations, but this was not
  independently decomposed further — reported as an observation, not a proven mechanism.
- The zero-wrap guard was confirmed to still gate correctly: the run exits non-zero only when 0
  bodies are wrapped, and this run wrapped 1880 (nonzero, exit 0).

Nothing beyond this corpus has been measured. No precision/recall number exists yet because there
is no second, independent oracle for TypeScript to grade this one against — that is the whole
reason the fixtures below (`coverage.ts` + `coverage_expected.md`) exist: hand-derived expected
edges, written *before* running the transformer, so a mechanism can be checked shape-by-shape
without waiting for a second implementation to compare against.

## Known holes

These are accepted, not accidental — each was found by running the mechanism against
`coverage.ts` and comparing to `coverage_expected.md`, not guessed from reading the code.

**Fixed 2026-09-03** (see `coverage_expected.md` § "Results after fixes 2026-09-03" for the
before/after edge diffs):

- ~~Expression-bodied, curried, and class-field arrow functions are invisible~~ — FIXED. Arrows
  with a non-`ts.isBlock` body now wrap the body EXPRESSION itself (`__stack.run(label, () =>
  (expr))`), not a converted block, so the expression-bodied shape is preserved. Covers curried
  arrows (both levels) and class-field arrow initializers.
- ~~`static {}` class initialization blocks are not a handled node kind~~ — FIXED.
  `ts.isClassStaticBlockDeclaration` is now wrapped with the same `wrapBody`+`als.run` strategy as
  every other callable, labeled `file:Class.<static>:line`.
- ~~Computed member names get a degraded `<computed>` label~~ — PARTIALLY FIXED, syntax-only (no
  type checker, per this file's header). `nameOf()` now resolves the computed expression when it is
  a `StringLiteral` or `NumericLiteral` literal (e.g. `['name']() {}` → label `Class.name`).
  Genuinely dynamic keys (`Symbol.iterator`, call expressions, **identifier references to a
  `const`**, computed via a variable) are still `<computed>` — a syntax-only rewriter cannot resolve
  those without the type checker/constant folding, and this file deliberately stays checker-free.
  `coverage.ts`'s own `COMPUTED_KEY` case is an identifier reference, so it is unaffected by this
  fix and correctly stays `<computed>`.

**Still open:**

- **Generator and async-generator bodies** (`function*`, both declarations/methods/accessors and
  function expressions) cannot be wrapped in a plain arrow — `yield` is illegal there — so they are
  skipped rather than instrumented. Calls made from inside a generator body fold to whatever frame
  was active when `.next()` was invoked.
- **Derived-class constructors whose `super()` call is not reachable at all** are not a hole (a
  `super()` anywhere in the constructor's own control flow, not inside a nested function/class, is
  detected and the constructor is skipped rather than emit illegal JS) — but the skip itself means
  a derived constructor's own frame, and everything it calls before/after `super()`, folds to the
  caller.
- **Default-parameter initializer calls** (`function f(x = source())`) are attributed one level too
  shallow: the transformer only wraps the body block, not the parameter list, so `source()` runs
  before `__stack.run` for `f` has been entered.
- **`new Function(...)` and `eval(...)`** are a permanent, accepted blind spot: code compiled from a
  runtime string never passes through the TS-source transformer at all. Not attempted, not
  attemptable without a completely different mechanism (e.g. runtime instrumentation).

## Files

- `transformer.js` — the AST rewriter.
- `als_stack.js` — standalone ALS-backed `__stack` runtime (for ad hoc runs against a single
  compiled file, e.g. `run_collision.js`/`run_coverage.js`).
- `ts_jest_ast_transformer.js`, `jest_setup_stack.js`, `jest.instrumented.config.js` — the ts-jest
  harness that instruments a whole corpus and runs its existing test suite through it. The setup
  file appends edges to a file on disk (`EDGE_OUT` env var) rather than keeping them in memory,
  because Jest resets the module registry per test file even under `--runInBand`.
- `build.js` — compiles one `.ts` file through the transformer, for the small fixtures below.
- `coverage.ts` / `coverage_expected.md` — the shape-coverage fixture: one file touching every node
  kind the transformer might mishandle, with edges predicted *before* running it, so a change can
  be checked against a fixed prediction rather than eyeballed. Run with `run_coverage.js`.
- `collision.ts` — regression fixture for the same-named-method-in-different-classes fabricated
  edge (see "labels carry class name" above). Run with `run_collision.js`.
- `bug_probe.ts` — regression fixture for two crash bugs found and fixed in this transformer:
  a generator *function expression* missing the same `asteriskToken` guard the generator
  declaration/method branch has, and a `super()` call nested inside constructor control flow
  (e.g. inside an `if`) slipping past the direct-statement-only detector.

## Running it

    npm install
    node build.js coverage.ts coverage coverage.compiled.js
    node run_coverage.js

Against an external corpus (e.g. class-validator checked out separately — it is not part of this
repo):

    ORACLE_TS_CORPUS_ROOT=<path to corpus> \
      EDGE_OUT=<path to append edges to> \
      npx jest --config oracle/typescript/jest.instrumented.config.js --runInBand --no-cache

## Two silent no-ops this harness now guards against

Both of these made the whole suite run GREEN while instrumenting NOTHING, producing no edge file
at all. A ruler that reports success without measuring is worse than one that fails, so each is
now either impossible to hit or fails loudly.

- **Two TypeScript instances.** `ts.isFunctionDeclaration()` and friends compare `node.kind`
  against `SyntaxKind`, a NUMERIC enum whose values shift between versions (5.4.5:
  FunctionDeclaration=262, 5.9.3: 263). The transformer used to `require("typescript")` itself
  while inspecting nodes built by the corpus own copy, so every type guard returned false and zero
  bodies were wrapped. The `ts` instance is now INJECTED and resolved from the corpus.
- **ts-jest config in the wrong place.** ts-jest 29 still reads `globals["ts-jest"]` (warning only),
  but `astTransformers` never reach the transformer from there; and the `ts-jest` preset adds a
  second .ts transform entry that can win the match uninstrumented. The transform is now declared
  explicitly and the preset dropped.

On top of that, the adapter counts the bodies it wraps and **exits non-zero if a whole run wrapped
zero of them**.

## Not done here

- Not wired into `runner/run.py` / `ORACLE_BUILDERS`. There is no `.sln`/`csproj`-equivalent
  "single command produces the answer key for any TS repo" story yet — the corpus root is a
  hand-supplied env var, and the edge format has not been converted to whatever
  `oracle/csharp` emits.
- No precision/recall grading against a second oracle.
- The known holes above are not fixed, only documented and (where they previously crashed the
  whole module) made to fail safely instead.
