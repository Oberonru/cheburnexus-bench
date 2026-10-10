// Jest config to instrument a TypeScript corpus with the shadow-stack transformer and run its
// existing test suite through it. Point ORACLE_TS_CORPUS_ROOT at the corpus checkout (the dir
// holding its own jest.config.js)  before running, e.g.:
//
//   ORACLE_TS_CORPUS_ROOT=D:\DEV\TsTest\class-validator
//   EDGE_OUT=<path to append edges to>
//   npx jest --config oracle/typescript/jest.instrumented.config.js --runInBand --no-cache
const path = require('path');

const CORPUS_ROOT = process.env.ORACLE_TS_CORPUS_ROOT;
if (!CORPUS_ROOT) throw new Error('ORACLE_TS_CORPUS_ROOT env var required');

// The corpus's own jest config is jest.config.js at its root unless corpus.json's
// `typescript_jest_config` names another path (react-hook-form keeps it in scripts/jest/); the
// runner passes it through ORACLE_TS_JEST_CONFIG, relative to the checkout.
const base = require(path.join(CORPUS_ROOT, process.env.ORACLE_TS_JEST_CONFIG || 'jest.config.js'));

// Two things from the corpus config are deliberately dropped:
//   `globals` -- ts-jest 29 still READS globals['ts-jest'] (printing only a deprecation warning),
//     but astTransformers never reach the transformer from there. The suite then runs GREEN with
//     zero instrumentation and no edge file at all.
//   `preset`/`transform` -- the preset contributes its own .ts transform entry under a different
//     regex key, so a plain, uninstrumented ts-jest can win the match.
// Both failure modes are silent successes, which is the worst thing a ruler can do, so the
// transform is declared explicitly here instead.
const { globals: _g, preset: _p, transform: _t, projects: _projects, ...baseRest } = base;

const fs = require('fs');

// Which tsconfig ts-jest compiles with: tsconfig.spec.json when the corpus has one (class-validator),
// else its plain tsconfig.json (react-hook-form). Chosen by existence, not by corpus name.
const tsconfigName = fs.existsSync(path.join(CORPUS_ROOT, 'tsconfig.spec.json'))
  ? 'tsconfig.spec.json'
  : 'tsconfig.json';

// ts-jest: the corpus's own when it has one (class-validator), otherwise this package's copy
// (react-hook-form compiles with @swc/jest and ships no ts-jest). Either way ts-jest must compile
// with the CORPUS's typescript, the same instance ts_jest_ast_transformer.js inspects nodes with
// (SyntaxKind numbers differ between versions, see that file), so the compiler is pinned by path.
const corpusTs = require.resolve('typescript', { paths: [CORPUS_ROOT] });
let tsJestPath;
// A corpus that does not use ts-jest itself (swc/babel) never had its tests type-checked, so type
// diagnostics must not fail suites that are green under its own runner; a corpus that picked
// ts-jest keeps its diagnostics exactly as before.
let ownTsJest = true;
try {
  tsJestPath = require.resolve('ts-jest', { paths: [CORPUS_ROOT] });
} catch (_) {
  tsJestPath = require.resolve('ts-jest');
  ownTsJest = false;
}

const instrumentedTransform = {
  '^.+\.tsx?$': [
    tsJestPath,
    {
      compiler: corpusTs,
      ...(ownTsJest ? {} : { diagnostics: false }),
      tsconfig: path.join(CORPUS_ROOT, tsconfigName),
      astTransformers: {
        before: [path.join(__dirname, 'ts_jest_ast_transformer.js')],
      },
    },
  ],
};

// A corpus whose config fans out into `projects` (react-hook-form: a jsdom "web" project and a node
// "server" project, each with its OWN transform and setup) keeps every project, but each project's
// transform is replaced by the instrumented one and our stack setup file is added to each. Options
// declared at the top level of such a config are NOT inherited by projects, so they must be set
// per project; for a flat config (no projects) the same fields are set at the top level as before.
const instrumentedFields = {
  rootDir: CORPUS_ROOT,
  transform: instrumentedTransform,
};
const stackSetup = path.join(__dirname, 'jest_setup_stack.js');

if (Array.isArray(_projects) && _projects.length) {
  module.exports = {
    ...baseRest,
    projects: _projects.map((proj) => ({
      ...proj,
      ...instrumentedFields,
      setupFiles: [stackSetup, ...(proj.setupFiles || [])],
    })),
  };
} else {
  module.exports = {
    ...baseRest,
    ...instrumentedFields,
    setupFiles: [stackSetup],
  };
}
