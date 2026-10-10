# oracle/typescript — shadow-stack call-graph oracle

**Status: wired into `runner/run.py` (2026-09-03) — `build_oracle_typescript`, registered in
`ORACLE_BUILDERS["typescript"]`, produces a real `oracle.jsonl` end to end. Verified against
`typestack/class-validator` (ts-jest): 3270 unique edges (see "Module-scope frame" below — was
3255 before that fix), grader consumes the file cleanly (see the corpus.json entry and
`runner/test_runner.py`'s `check_typescript_key_and_guard`). The Vitest adapter is wired the same
way but not re-verified through `run.py` this session — see "Not done here" below. No TypeScript
arm exists yet, so no precision/recall NUMBER against a real arm has been published — see run.py's
`build_oracle_typescript` docstring for the key contract (both ends) and the recall-scope caveat
any future arm's grading must account for.**

## Module-scope frame (2026-09-03)

A coordinator review of the first `run.py` wiring caught a real gap the mechanism section below
had not flagged: `shadow_stack.js`'s `ROOT_FRAME` (`'<module>'`) is one hardcoded, location-less
sentinel, used as the caller for BOTH (a) a call the corpus's own module-evaluation code makes
directly at file top level (a decorator on a file-scope class, a top-level `describe(...)`
registration call, a bootstrap IIFE, DI container registration at import time) and (b) a callback
Jest's own scheduler invokes later with no ALS context at all (`it(...)`/`beforeEach(...)` bodies —
genuinely unattributable, no call expression in the corpus's source ever names them). Both
collapsed onto the literal string `"<module>"`, and `runner/run.py`'s `_ts_key` mapped THAT to a
key containing `<`, which `grade.py`'s existing compiler-mangled-caller rule then silently dropped
— erasing bucket (a) as a side effect of correctly excluding bucket (b). Measured, not assumed: on
class-validator this was 893 unique `<module> -> X` edges, of which 747 were `it`/`beforeEach`
bodies (bucket (b), correctly unattributable) and 144 + 2 were real top-level `describe(...)`
registrations and one decorator application (bucket (a), real facts silently destroyed by sharing
bucket (b)'s sentinel) — see `finding-ts-no-caller-calls-counted-2026-08-27.md` in project memory,
which had already flagged this exact class of TS module-level-call gap.

**Fix**: `transformer.js` now gives each file its own synthetic top-level frame
(`{file}:module-scope:0`, entered via the same `als.run()`-per-call strategy as every other
callable — never `enterWith`), covering only plain top-level `ExpressionStatement`s (side-effecting
calls/assignments — no binding they introduce, so nesting them changes nothing observable).
Deliberately left OUTSIDE any wrap: import/export declarations and anything carrying an `export`
modifier, class and function declarations (their own BODY is still instrumented normally — only
the file-scope binding/hoisting position is left alone), interface/type-alias/enum/module
declarations, variable statements (`const`/`let`/`var` — wrapping would move the declared BINDING
into the wrapper's closure, invisible to sibling top-level code referencing it by name), and a
leading directive prologue (`"use strict"` — syntactically a string-literal expression statement,
but nesting one silently stops it being a directive).

**Known, accepted residual**: a decorator on a class declared at file scope (class-validator's own
`test/functional/reject-validation.spec.ts:5-9`) still folds to the bare root sentinel, because the
class declaration itself must not be wrapped — its decorator list is evaluated as PART OF that
excluded node, never as a separate statement this pass sees. Only the subset that syntactically IS
a standalone top-level statement is fixed.

**Re-measured on class-validator, ts-jest, whole run.py, two full runs**: 806/806 tests pass both
runs; sorted-unique edge sets byte-identical (diff empty). 3255 → **3270** edges (+15), and the
delta was decomposed exactly, not eyeballed: **144 edges are relabeled** (caller changed from the
bare `<module>` sentinel to the file-qualified `{file}::module-scope:0` node, same callee — no new
fact, a previously-erased attribution now correct) and **15 edges are brand-new**
(`<module> -> {file}::module-scope:0`, one per file that has at least one wrappable top-level
statement — the entry into that file's new frame). 0 edges are unexplained. Do not read "+15" as
recall gained on arbitrary code; it is exactly these two effects, no more. Re-run through
`runner/run.py`'s own `main()`: oracle.jsonl now has 3270 rows (0 unparsed), and the
"caller not remappable" (bucket b, correctly dropped) count fell from 893 to **765** unique
`<module>`-caller rows in the raw stream / **764** unique rows in the deduplicated `oracle.jsonl`
(747 it/beforeEach bodies, unchanged + 17 "other": the 15 new module-scope entry edges themselves
plus the 2 documented class-decorator residual rows) — describe()-callback rows in this bucket:
**zero**, down from 144, exactly as intended.

**Also re-measured on zod, Vitest, `packages/zod` scope, two full runs**: bodies wrapped 8253 (was
7972 — the new module-scope wraps), nonzero both runs. Sorted-unique edge sets byte-identical
across both runs: 25766 unique edges both times, diff empty. Failing-test counts: 15 (run 1) / 16
(run 2) of 2744, both within the previously-recorded ceiling (4 baseline + 10 perf-threshold + 2
stack-text + 1 readonly = 17) — same categories as documented below (treeshake/polyfill-collision
baseline, `to-json-schema`/`compile-differential`/etc. perf-threshold flake, the stack-frame-text
pair, the intermittent `readonly.test.ts` ALS rejection), plus one perf-shaped test name
(`parses a factory-built recursive schema through every object builder`) that only failed on run 2
— consistent with the already-documented performance-threshold flakiness under instrumentation
overhead, not a new failure class. No regression: same edge-set determinism claim as before, scoped
to the SET of distinct call edges, not raw call volume or per-run test outcome.

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

**Fixed 2026-09-03 (second pass)** (each reproduced against `coverage.ts` before the fix landed,
matching prediction #38 for the default-param case; see the commit messages for the before/after
edges):

- ~~Default-parameter initializer calls are attributed one level too shallow~~ — PARTIALLY FIXED.
  For a SIMPLE identifier parameter with an initializer (`function f(x = g())`), the initializer is
  hoisted out of the signature into `if (x === undefined) { x = g(); }` as the first statement
  inside the `__stack.run()`-wrapped body, so `g()` is now correctly attributed to `f`, not `f`'s
  caller. `coverage.ts`'s `withDefault()` now records `withDefault -> defaultParamSource` (was
  `main -> defaultParamSource`). Deliberately left eager/unfixed (initializer stays in the
  signature, still misattributed one level shallow):
  - destructuring parameters with defaults (`{a = 1} = {}`, nested or not) — `param.name` is not a
    plain identifier, no safe single-expression rewrite.
  - `this` parameters and rest parameters — neither can carry an initializer in valid TypeScript,
    excluded naturally.
  - parameter properties (`constructor(private readonly x = f())`) — the initializer also drives an
    implicit `this.x = x` assignment TS synthesizes for the accessibility modifier; moving it would
    require reproducing that assignment by hand. Left as-is.
  - expression-bodied arrows (`(x = g()) => x`) — hoisting requires a statement position, i.e.
    converting the arrow from expression-bodied to block-bodied, an observable shape change the
    transformer deliberately avoids elsewhere. Not attempted; the shallow-attribution residual
    stands for these.
  - **Ordering caveat**: a signature that mixes a convertible identifier default with a
    left-in-place complex one no longer preserves strict source order — the left-in-place default
    still evaluates during binding at its original position, but every converted default now runs
    AFTER ALL parameters are bound (top of the wrapped body), not interleaved at its original spot.
    Only observable with side-effecting defaults mixed in one signature; not hit in the
    class-validator scale run.
- ~~Derived-class constructors containing `super()` are skipped entirely~~ — PARTIALLY FIXED. When
  `super(...)` is a DIRECT top-level statement of the constructor's own block (the common shape),
  the constructor now gets its own frame: `super(...)` and everything before it stay literal
  top-level statements (moving `super()` into a closure is illegal — V8 rejects it), and everything
  AFTER `super(...)` is wrapped with the same `wrapBody`/`als.run()` strategy as every other
  callable. Verified with an ad hoc probe (not checked in): `this` is preserved (an arrow closes
  over it lexically, doesn't rebind it) and an early `return;` inside the wrapped tail still
  behaves — a `bail`-branch constructor returned `[1, -1]` correctly and only the non-bail branch's
  trailing call reached its own callee frame. Still open, unchanged from before:
  - a `super()` call NESTED inside the constructor's own control flow (e.g.
    `if (cond) { super(a); } else { super(b); }`) has no single unambiguous "everything after"
    range across branches — those constructors are still skipped entirely, exactly as before
    (regression-checked against `bug_probe.ts`'s branching-super case: no crash, no new frame).
  - calls made in `super(...)`'s OWN ARGUMENT EXPRESSIONS (`super(f())`) still fold to the caller
    even in the now-fixed direct case — they execute before the wrapped tail begins.

**Still open:**

- **Generator and async-generator bodies** (`function*`, both declarations/methods/accessors and
  function expressions) cannot be wrapped in a plain arrow — `yield` is illegal there — so they are
  skipped rather than instrumented. Calls made from inside a generator body fold to whatever frame
  was active when `.next()` was invoked. Left open by decision, not attempted.
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

**Non-obvious invocation detail (cost a whole session to rediscover, so it's spelled out here):**
`npx jest` pulls whatever jest resolves on the CALLER'S path, which can be a different/wrong jest
than the one this repo's `node_modules` pins, and ts-jest resolves `tsconfig.spec.json` relative to
the shell's CWD — so running from the bench repo root silently picks up the wrong config or the
wrong jest binary. Run THIS REPO'S OWN jest binary with the shell's working directory set to the
CORPUS ROOT, not the bench root:

    cd <path to corpus, e.g. D:\DEV\TsTest\class-validator>
    ORACLE_TS_CORPUS_ROOT=<path to corpus> \
      EDGE_OUT=<path to append edges to, a NEW file> \
      node <path to this repo>/oracle/typescript/node_modules/jest/bin/jest.js \
      --config <path to this repo>/oracle/typescript/jest.instrumented.config.js \
      --runInBand --no-cache

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

## Vite/Vitest adapter (2026-09-03)

`transformer.js` and `shadow_stack.js` are compiler/harness-agnostic; ts-jest was the first
harness wired up, not the only one possible. A second adapter targets Vitest:

- `vite_shadow_stack_plugin.js` — a Vite plugin factory (`shadowStackPlugin({corpusRoot})`).
  **Vite compiles TS via esbuild, not the TypeScript compiler API** — esbuild only strips types,
  it runs no arbitrary AST transform, so a plugin that just "registers" would instrument NOTHING.
  This plugin's `transform()` hook does the work itself: `ts.createSourceFile` on the incoming
  source, `transformer.js`'s `transform` run over the AST, `ts.createPrinter().printFile()` back
  out as TS text, which Vite's own esbuild pass then strips as normal. It runs with
  `enforce: 'pre'` so it sees real un-stripped source. `SHADOW_STACK_DEBUG=1` prints the first
  file's raw incoming source so this can be checked by hand instead of assumed — done, see below.
  `transformer.js` is required unmodified; `ts` is injected, resolved from the corpus root, exactly
  as in the jest adapter.
- `vitest_setup_stack.js` — the Vitest `setupFiles` sink, functionally identical to
  `jest_setup_stack.js` (same disk-append-to-`EDGE_OUT` strategy; Vitest isolates test files by
  default too, so an in-memory Set wouldn't accumulate across files here either).
- `vitest.instrumented.config.mts` — loads the corpus's own `vitest.config.(ts|js)`, strips any
  `test.projects` workspace-fanout field (see the file's header comment for why: a per-package
  config that itself `mergeConfig()`s a monorepo root config drags the root's `projects` list back
  in, resolved from the wrong directory, and vitest refuses to start), and injects the plugin +
  setup file. **File extension must be `.mts`**, not `.ts`: `oracle/typescript/package.json` has
  no `"type": "module"`, so a `.ts` config gets bundled to CJS output and Vitest's own top-level
  `await` inside the loader dies with "Top-level await is currently not supported with the 'cjs'
  output format". Loading the corpus's own `.ts` config file is done by bundling it with the
  **corpus's own `esbuild`** (`packages: 'external'` so bare imports like `"vitest/config"` stay
  real runtime imports, only the corpus's own relative `.ts` files get inlined) rather than a
  plain `import()`, which cannot parse `.ts` syntax at all under plain Node.

### What was actually measured

Corpus: **zod** (`D:\DEV\TsTest\zod`), a pnpm monorepo. `pnpm install` at the repo root (pnpm was
not present on this machine; installed globally via `npm install -g pnpm@10.12.1` first — `corepack
enable` failed with `EPERM` writing into `Program Files`, that route was abandoned). Scope run:
**`packages/zod` only** (one workspace package, not the whole monorepo — `docs`, `bench`,
`integration`, `mini`, `resolution`, `treeshake`, `tsc` packages were not run), 192 `*.test.ts`
files, 2744 tests, via `--root packages/zod --config oracle/typescript/vitest.instrumented.config.mts`.

- **Tests: 2731/2744 passed instrumented** (run 1), **2728/2744** (run 2) — NOT stable between
  runs, see caveat below. Uninstrumented control (same scope, same config-loading path with the
  plugin line removed, to get an apples-to-apples comparison — the corpus's own bare
  `packages/zod/vitest.config.ts` cannot be run standalone at all, same `projects`-fanout bug
  noted above): **2740/2744 passed**.
- **7972 function bodies wrapped** this run (printed unconditionally now; the zero-wrap guard is
  gated on this being nonzero, confirmed both by a real run and by a `-t` filter that skips every
  test body but still triggers all module-load-time wrapping — same 7972 in both cases, i.e.
  wrapping happens at transform/import time, independent of which tests execute).
- **Determinism: sorted-UNIQUE edge sets are byte-identical across two full runs** — 25,560 unique
  edges both times, `diff` on the sorted-unique files is empty. Raw (non-deduplicated) edge line
  counts differ between runs (3,249,539 vs 3,249,874) and are NOT claimed identical — several of
  zod's own tests assert wall-clock performance budgets (e.g. "large registry converts in linear
  time" with a `< 5000` assertion) whose iteration counts/outcomes vary run to run under the
  instrumentation's own overhead, and one intermittent bug (next paragraph) affected a different
  number of tests each run. The determinism claim here is scoped to the same thing the class-
  validator/ts-jest measurement scoped it to: the SET of distinct call edges, not raw call volume.
- **⚠️ New, un-fixed bug found on this corpus, not seen on class-validator**: one intermittent
  unhandled rejection, `TypeError: Cannot assign to read only property 'Symbol(kResourceStore)' of
  object '#<Promise>'`, thrown from inside `AsyncLocalStorage.run()` itself (`shadow_stack.js:29`),
  reproduced in both runs, always from `src/v3/tests/readonly.test.ts` / `src/v3/types.ts`
  (`ZodOptional`). This looks like a Node ALS edge case triggered by some interaction between
  `als.run()`-per-call and zod's own Promise-subclassing/chaining internals — NOT diagnosed further
  (out of scope for this adapter task), reported here rather than hidden. This, plus the
  performance-threshold tests above, accounts for the gap between the 2740/2744 uninstrumented
  control and the ~2728-2731/2744 instrumented runs; none of it looks like a wrong-caller/
  fabricated-edge defect in the mechanism itself (the edge SET stayed identical across runs).
- **Wall time: ~72s instrumented (both runs, 71.8s / 72.6s) vs ~7.9s uninstrumented control** on
  the same scope — roughly **9x**, higher than the ~4.4x measured for class-validator/ts-jest; not
  decomposed further (candidates: zod's own test count is ~3.4x class-validator's, no per-file
  breakdown was taken here).
- Config-loading and plugin behavior were verified live, not assumed: `SHADOW_STACK_DEBUG=1`
  confirmed the `transform()` hook receives intact, un-stripped TypeScript (`import` statements,
  no type erasure) as its FIRST argument, before this plugin does anything to it.

**Not attempted**: date-fns, immer, bullmq were listed as fallback corpora but zod worked on the
first real attempt (after clearing the pnpm/esbuild/mergeConfig/top-level-await hurdles above), so
they were not tried. The other zod workspace packages (`mini`, `treeshake`, `resolution`, ...)
were not run either — `packages/zod` alone was judged sufficient scope for verifying the adapter
mechanism.

## Not done here

- **Wired for ts-jest, verified; wired for Vitest, unverified through run.py this session.**
  `build_oracle_typescript` in `runner/run.py` drives either harness from corpus.json
  (`typescript_harness: "ts-jest" | "vitest"`, plus `typescript_project_dir` to scope a Vitest
  monorepo package). The ts-jest path was run end to end against class-validator through
  `run.py`'s own `main()`; the Vitest path was not re-run through `run.py` this session — its
  standalone adapter was verified separately (zod, see above), but the run.py-level plumbing
  (env vars, cwd, npx resolution) is new code, untested on that path.
- No TypeScript arm exists yet to grade against this oracle. `runner/run.py`'s `--arms` still only
  names `grep`, `repowise`, `cheburnexus` — none of which understand TypeScript source, so a
  live run against this oracle currently produces 0 arm edges / no precision number. Building a
  TS arm (most obviously wrapping `TsAnalyzer`) is future work, out of this session's scope.
- The Vitest adapter's config-loading is a workaround (bundle the corpus's own `.ts` config with
  its own esbuild, drop `test.projects`), not a general "extend any vitest workspace" story — a
  corpus whose config chain is more than two files deep, or that needs its full multi-project
  fanout instrumented at once (not just one package), would need more work here.
- The intermittent ALS `Symbol(kResourceStore)` rejection on zod (`src/v3/types.ts`, see above) is
  reported, not fixed or root-caused.
- No precision/recall grading against a second oracle.
- The known holes above are not fixed, only documented and (where they previously crashed the
  whole module) made to fail safely instead.

## Jest corpus with its own `projects` and swc (react-hook-form, 2026-10-10)

`jest.instrumented.config.js` was generalised three ways (class-validator re-measured: still
3270 edges, 0.997 / 0.563):
- corpus config path: `typescript_jest_config` in corpus.json (default `jest.config.js`).
- `projects` fan-out: every project keeps its own settings, its transform is swapped for the
  instrumented one and the stack setup is added per project.
- no ts-jest in the corpus (it uses @swc/jest): this package's ts-jest is used, pinned to the
  corpus's own typescript by path, with `diagnostics: false` (the corpus never type-checked its tests).
  A corpus that has its own ts-jest keeps diagnostics as before.
