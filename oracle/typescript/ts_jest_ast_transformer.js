// ts-jest custom AST transformer entry point. Wraps EVERY .ts file under class-validator/src
// with the shadow-stack transformer (whole-repo instrumentation, not a hand-picked allowlist).
const path = require('path');
const { makeTransformer } = require('./transformer');

// SRC_ROOT is the corpus checkout's src/ dir -- set per machine, there is no repo-relative
// default because the corpus (e.g. class-validator) is not checked into this repo.
const SRC_ROOT = process.env.ORACLE_TS_SRC_ROOT || '';

module.exports = {
  name: 'shadow-stack',
  version: 1,
  factory() {
    return (context) => (sourceFile) => {
      const fileName = sourceFile.fileName || '';
      let tag = fileName;
      if (fileName.startsWith(SRC_ROOT)) {
        tag = path.relative(SRC_ROOT, fileName).replace(/\\/g, '/');
      }
      return makeTransformer(tag)(context)(sourceFile);
    };
  },
};
