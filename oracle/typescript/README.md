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

Nothing beyond this corpus has been measured. No precision/recall number exists yet because there
is no second, independent oracle for TypeScript to grade this one against — that is the whole
reason the fixtures below (`coverage.ts` + `coverage_expected.md`) exist: hand-derived expected
edges, written *before* running the transformer, so a mechanism can be checked shape-by-shape
without waiting for a second implementation to compare against.

## Known holes

These are accepted, not accidental — each was found by running the mechanism against
`coverage.ts` and comparing to `coverage_expected.md`, not guessed from reading the code:

- **Expression-bodied, curried, and class-field arrow functions** (`const f = () => expr`) are
  invisible: the transformer only rewrites arrows whose body is `ts.isBlock(...)`. Calls made
  inside them silently fold to whichever frame was active at the call site, one level too shallow.
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
- **`static {}` class initialization blocks** are not a handled node kind; calls inside them fold
  directly to `<module>`.
- **Computed member names** (`[SOME_KEY]() {}`) get a real edge but a degraded label — the
  transformer's `nameOf()` falls back to the literal string `<computed>` rather than resolving the
  computed expression.
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

    ORACLE_TS_CORPUS_ROOT=<path to corpus> ORACLE_TS_SRC_ROOT=<path to corpus>/src \
      EDGE_OUT=<path to append edges to> \
      npx jest --config oracle/typescript/jest.instrumented.config.js --runInBand

## Not done here

- Not wired into `runner/run.py` / `ORACLE_BUILDERS`. There is no `.sln`/`csproj`-equivalent
  "single command produces the answer key for any TS repo" story yet — the corpus root and src
  root are hand-supplied env vars, and the edge format has not been converted to whatever
  `oracle/csharp` emits.
- No precision/recall grading against a second oracle.
- The known holes above are not fixed, only documented and (where they previously crashed the
  whole module) made to fail safely instead.
