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

const base = require(path.join(CORPUS_ROOT, 'jest.config.js'));

// Two things from the corpus config are deliberately dropped:
//   `globals` -- ts-jest 29 still READS globals['ts-jest'] (printing only a deprecation warning),
//     but astTransformers never reach the transformer from there. The suite then runs GREEN with
//     zero instrumentation and no edge file at all.
//   `preset`/`transform` -- the preset contributes its own .ts transform entry under a different
//     regex key, so a plain, uninstrumented ts-jest can win the match.
// Both failure modes are silent successes, which is the worst thing a ruler can do, so the
// transform is declared explicitly here instead.
const { globals: _g, preset: _p, transform: _t, ...baseRest } = base;

module.exports = {
  ...baseRest,
  rootDir: CORPUS_ROOT,
  setupFiles: [path.join(__dirname, 'jest_setup_stack.js')],
  transform: {
    '^.+\.tsx?$': [
      'ts-jest',
      {
        tsconfig: 'tsconfig.spec.json',
        astTransformers: {
          before: [path.join(__dirname, 'ts_jest_ast_transformer.js')],
        },
      },
    ],
  },
};
