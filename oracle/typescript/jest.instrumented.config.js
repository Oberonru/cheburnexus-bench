// Jest config to instrument a TypeScript corpus with the shadow-stack transformer and run its
// existing test suite through it. Point ORACLE_TS_CORPUS_ROOT at the corpus checkout (the dir
// holding its own jest.config.js) and ORACLE_TS_SRC_ROOT at its src/ dir before running, e.g.:
//
//   ORACLE_TS_CORPUS_ROOT=D:\DEV\TsTest\class-validator
//   ORACLE_TS_SRC_ROOT=D:\DEV\TsTest\class-validator\src
//   EDGE_OUT=<path to append edges to>
//   npx jest --config oracle/typescript/jest.instrumented.config.js --runInBand
const path = require('path');

const CORPUS_ROOT = process.env.ORACLE_TS_CORPUS_ROOT;
if (!CORPUS_ROOT) throw new Error('ORACLE_TS_CORPUS_ROOT env var required');

const base = require(path.join(CORPUS_ROOT, 'jest.config.js'));

module.exports = {
  ...base,
  rootDir: CORPUS_ROOT,
  setupFiles: [path.join(__dirname, 'jest_setup_stack.js')],
  globals: {
    'ts-jest': {
      tsconfig: 'tsconfig.spec.json',
      astTransformers: {
        before: [path.join(__dirname, 'ts_jest_ast_transformer.js')],
      },
    },
  },
};
