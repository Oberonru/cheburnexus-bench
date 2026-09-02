// ts-jest custom AST transformer entry point. Instruments EVERY .ts file ts-jest compiles for
// the corpus (whole-repo, not a hand-picked allowlist).
const path = require('path');
const { makeTransformer } = require('./transformer');

const CORPUS_ROOT = process.env.ORACLE_TS_CORPUS_ROOT;
if (!CORPUS_ROOT) throw new Error('ORACLE_TS_CORPUS_ROOT env var required');

// Resolve TypeScript FROM THE CORPUS, not from this package. ts-jest builds the nodes we inspect
// with the corpus's own typescript, and SyntaxKind is a numeric enum whose values shift between
// versions (5.4.5: FunctionDeclaration=262, 5.9.3: 263). Using our own copy makes every ts.isXxx
// guard return false, wrapping ZERO functions while the suite still reports success -- a silent
// no-op that yields an empty answer key. See the header of transformer.js.
const ts = require(require.resolve('typescript', { paths: [CORPUS_ROOT] }));

// A run that instruments nothing must never look like a successful run.
let wrappedTotal = 0;
process.on('exit', () => {
  if (wrappedTotal === 0) {
    console.error(
      '\n[shadow-stack] FATAL: instrumented 0 functions across the whole run.\n' +
      '  The edge output would be empty and the suite would still be green.\n' +
      '  Most likely cause: a TypeScript instance mismatch (see transformer.js header).\n'
    );
    process.exitCode = 1;
  }
});

module.exports = {
  name: 'shadow-stack',
  version: 1,
  factory() {
    return (context) => (sourceFile) => {
      const fileName = sourceFile.fileName || '';
      // Anchor every tag relative to the corpus ROOT (so 'src/...' and 'test/...'), never to an
      // absolute machine path: an answer key that embeds D:/DEV/... cannot be compared with one
      // rebuilt on another machine, which is the whole point of a public polygon.
      const tag = path.relative(CORPUS_ROOT, fileName).replace(/\\/g, '/');
      const { transform, wrapped } = makeTransformer(ts, tag);
      const out = transform(context)(sourceFile);
      wrappedTotal += wrapped();
      return out;
    };
  },
};
