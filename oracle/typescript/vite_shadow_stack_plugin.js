// Vite/Vitest adapter for the shared shadow-stack transformer (transformer.js), REUSED UNCHANGED.
//
// Vite compiles TypeScript via ESBUILD, not the TypeScript compiler API. esbuild only strips
// types -- it does not run arbitrary AST transforms -- so a plugin that merely "registers" and
// hopes esbuild will apply transformer.js instruments NOTHING by itself. This plugin's
// transform() hook therefore does the work directly: it parses the INCOMING source with
// ts.createSourceFile (the corpus's own `typescript`), runs transformer.js's transform over the
// resulting AST, and prints the result back out as TypeScript text. Vite's own esbuild transform
// (which always runs afterward in the pipeline) then strips the types as usual.
//
// This hook MUST run with enforce: 'pre' so it sees real, un-stripped .ts/.tsx source -- if any
// other plugin (or esbuild itself) ran first, transformer.js's syntax-only guards would be
// looking at already-simplified JS and most of its node-kind branches would never match. Set
// SHADOW_STACK_DEBUG=1 to print the first file's raw incoming source verbatim, so this can be
// eyeballed by hand rather than assumed (constraint: this project has twice shipped a transform
// that silently ran on already-stripped/mismatched input and reported success).
//
// The `typescript` instance is INJECTED, resolved from the corpus root, never required from this
// package -- see transformer.js's header for why (SyntaxKind is a numeric enum that shifts
// between TS versions; using our own copy makes every ts.isXxx guard silently return false).
const path = require('path');
const { makeTransformer } = require('./transformer');

const SCRIPT_EXT_RE = /\.(ts|tsx|mts|cts|vue)$/;

function normSlash(p) {
  return p.replace(/\\/g, '/');
}

function shadowStackPlugin(options = {}) {
  const corpusRoot = options.corpusRoot || process.env.ORACLE_TS_CORPUS_ROOT;
  if (!corpusRoot) {
    throw new Error('shadowStackPlugin: corpusRoot (or ORACLE_TS_CORPUS_ROOT env var) is required');
  }
  const corpusRootAbs = path.resolve(corpusRoot);
  const corpusRootNorm = normSlash(corpusRootAbs).toLowerCase();

  // Resolve TypeScript FROM THE CORPUS -- see transformer.js header for the full "two TypeScript
  // instances" incident this guards against.
  const ts = require(require.resolve('typescript', { paths: [corpusRootAbs] }));

  let vueSfcMod = null;
  function getVueSfc() {
    if (!vueSfcMod) vueSfcMod = require(require.resolve('vue/compiler-sfc', { paths: [corpusRootAbs] }));
    return vueSfcMod;
  }

  let wrappedTotal = 0;
  let printedDebugSample = false;

  // ZERO-WRAP GUARD (constraint #4): a run that instruments nothing must never look like a
  // successful run. Mirrors ts_jest_ast_transformer.js's exit hook exactly.
  process.on('exit', () => {
    console.error(`[shadow-stack/vite] total function bodies wrapped this run: ${wrappedTotal}`);
    if (wrappedTotal === 0) {
      console.error(
        '\n[shadow-stack/vite] FATAL: instrumented 0 functions across the whole run.\n' +
        '  The edge output would be empty and the suite would still be green.\n' +
        '  Likely causes: a TypeScript instance mismatch (see transformer.js header), this\n' +
        '  plugin never matched any file (check corpusRoot / include-exclude filters below),\n' +
        '  or enforce: "pre" is missing so esbuild ran first and stripped the TS this plugin\n' +
        '  needs to see.\n'
      );
      process.exitCode = 1;
    }
  });

  function isInCorpus(absPath) {
    const n = normSlash(absPath).toLowerCase();
    return n.startsWith(corpusRootNorm + '/') || n === corpusRootNorm;
  }

  return {
    name: 'shadow-stack',
    enforce: 'pre',

    transform(code, id) {
      // Vite/Vitest ids can carry a `?query` suffix (HMR params, virtual-module markers, etc.);
      // strip it before treating the rest as a filesystem path.
      const filePath = id.split('?')[0];
      if (!SCRIPT_EXT_RE.test(filePath)) return null;
      if (normSlash(filePath).includes('/node_modules/')) return null;
      if (!isInCorpus(filePath)) return null;

      if (process.env.SHADOW_STACK_DEBUG && !printedDebugSample) {
        printedDebugSample = true;
        console.error(
          `[shadow-stack/vite] DEBUG sample input (${filePath}), first 400 chars -- ` +
          'expect intact TS syntax (types, interfaces, "as" casts), NOT stripped JS:\n' +
          code.slice(0, 400) + '\n[shadow-stack/vite] --- end sample ---'
        );
      }

      // Anchor every tag relative to the corpus ROOT, forward-slashed, never an absolute machine
      // path (constraint #5) -- same scheme ts_jest_ast_transformer.js uses.
      const tag = normSlash(path.relative(corpusRootAbs, filePath));

      // Vue SFC: this pre-plugin sees the raw .vue text (plugin-vue runs later). Instrument the
      // <script>/<script setup> blocks IN PLACE: each block is parsed as a blank buffer (every
      // char outside the block replaced by a space, newlines kept) so node line numbers equal
      // the real .vue lines, then only the block's text is replaced by the printed result. The
      // template/style stay byte-identical, so plugin-vue compiles as usual and the label file
      // is the .vue path itself (same convention as the engine's SFC script extraction).
      if (filePath.endsWith('.vue')) {
        const vueSfc = getVueSfc();
        const { descriptor } = vueSfc.parse(code, { filename: filePath });
        const blocks = [descriptor.script, descriptor.scriptSetup].filter(Boolean)
          .sort((a, b) => b.loc.start.offset - a.loc.start.offset);
        let outCode = code;
        for (const b of blocks) {
          const start = b.loc.start.offset, end = b.loc.end.offset;
          const lang = b.lang || 'js';
          const kind = lang === 'tsx' ? ts.ScriptKind.TSX : lang === 'jsx' ? ts.ScriptKind.JSX
            : lang === 'ts' ? ts.ScriptKind.TS : ts.ScriptKind.JS;
          const virt = code.slice(0, start).replace(/[^\n]/g, ' ') + code.slice(start, end);
          const sf = ts.createSourceFile(filePath + '.' + lang, virt, ts.ScriptTarget.Latest, true, kind);
          const { transform, wrapped } = makeTransformer(ts, tag);
          const res = ts.transform(sf, [transform]);
          const printed = ts.createPrinter({ newLine: ts.NewLineKind.LineFeed }).printFile(res.transformed[0]);
          res.dispose();
          wrappedTotal += wrapped();
          outCode = outCode.slice(0, start) + '\n' + printed + '\n' + outCode.slice(end);
        }
        return { code: outCode, map: null };
      }
      const scriptKind = /\.tsx$/.test(filePath) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
      const sourceFile = ts.createSourceFile(filePath, code, ts.ScriptTarget.Latest, true, scriptKind);

      const { transform, wrapped } = makeTransformer(ts, tag);
      const result = ts.transform(sourceFile, [transform]);
      const outSourceFile = result.transformed[0];
      const printer = ts.createPrinter({ newLine: ts.NewLineKind.LineFeed });
      const out = printer.printFile(outSourceFile);
      result.dispose();

      wrappedTotal += wrapped();

      // No source map: this is an oracle instrumentation pass, not a shipped build. Returning
      // map: null avoids Vite trying (and failing) to chain a map through a rewrite this size.
      return { code: out, map: null };
    },
  };
}

module.exports = { shadowStackPlugin };
