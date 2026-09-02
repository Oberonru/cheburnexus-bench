// Vitest config that instruments a TypeScript corpus (or one package/dir inside it) with the
// shadow-stack Vite plugin and runs its EXISTING test suite through it. Point:
//   ORACLE_TS_CORPUS_ROOT     -- corpus checkout root (labels are relative to this)
//   ORACLE_TS_PROJECT_DIR     -- the dir whose own vitest.config.(ts|js) to load and extend
//                                (defaults to ORACLE_TS_CORPUS_ROOT itself; use this to scope
//                                down to one package of a monorepo, e.g. packages/zod)
//   EDGE_OUT                  -- path to append edges to (a NEW file)
//
//   ORACLE_TS_CORPUS_ROOT=D:\DEV\TsTest\zod \
//   ORACLE_TS_PROJECT_DIR=D:\DEV\TsTest\zod\packages\zod \
//   EDGE_OUT=D:\DEV\TsTest\zod\edges.txt \
//     npx vitest run --root D:\DEV\TsTest\zod\packages\zod \
//       --config <this repo>/oracle/typescript/vitest.instrumented.config.ts
//
// We deliberately do NOT use vitest's own mergeConfig() to combine the corpus's config with ours:
// mergeConfig deep-merges the `test` block, and several real corpora (zod included) nest a
// `test.projects: [...]` workspace list in their ROOT config that a per-package config file only
// pulls in incidentally via its own mergeConfig with the root -- deep-merging that a second time
// here reintroduces the same relative "projects" paths, now resolved from the wrong directory,
// and vitest fails at startup before a single test runs (hit and reproduced while building this
// adapter: "Projects definition references a non-existing file: vitest.compile.config.ts"). So
// this file loads the corpus's own config as a plain object, explicitly drops any `projects`
// field (this run targets ONE project directory, not the corpus's whole workspace), and merges
// the rest by hand.
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { defineConfig } from 'vitest/config';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Loaded via a real, runtime `require()` (createRequire), NOT a static `import` -- vite's config
// bundler INLINES statically-imported local files into the config bundle, and on that path a
// plain CJS `require('fs')`/`require('path')` inside the inlined file got rewritten into an
// unsupported "dynamic require" shim ("Dynamic require of 'fs' is not supported"), reproduced
// while building this adapter. A require() reached only through a variable is invisible to that
// static-import bundling pass, so vite leaves it alone and Node's own module loader runs it
// unchanged -- transformer.js is untouched by this either way.
const require = createRequire(import.meta.url);
const { shadowStackPlugin } = require('./vite_shadow_stack_plugin.js');

const CORPUS_ROOT = process.env.ORACLE_TS_CORPUS_ROOT;
if (!CORPUS_ROOT) throw new Error('ORACLE_TS_CORPUS_ROOT env var required');
const PROJECT_DIR = process.env.ORACLE_TS_PROJECT_DIR || CORPUS_ROOT;

// Plain Node ESM `import()` cannot parse .ts syntax on its own, and the corpus's own config file
// (packages/zod/vitest.config.ts) itself imports a SECOND relative .ts file
// (../../vitest.config.ts) for shared settings -- so this needs to strip types through that whole
// local chain, not just the one entry file. Bundle it with the CORPUS's OWN esbuild
// (`packages: 'external'` leaves every bare-specifier import, e.g. "vitest/config", alone and
// resolved normally at runtime; only the corpus's own relative .ts files get inlined), write the
// bundled JS to a throwaway file next to the entry so its own relative imports still resolve, and
// import that.
async function loadCorpusConfig(dir, corpusRoot) {
  const fs = require('fs');
  const esbuild = require(require.resolve('esbuild', { paths: [corpusRoot] }));
  for (const name of ['vitest.config.ts', 'vitest.config.mts', 'vitest.config.js']) {
    const entry = path.join(dir, name);
    if (!fs.existsSync(entry)) continue;
    const result = await esbuild.build({
      entryPoints: [entry],
      bundle: true,
      packages: 'external',
      platform: 'node',
      format: 'esm',
      write: false,
      logLevel: 'silent',
    });
    const tmp = path.join(dir, `.oracle-corpus-config.${Date.now()}.mjs`);
    fs.writeFileSync(tmp, result.outputFiles[0].text);
    try {
      const mod = await import(pathToFileURL(tmp).href);
      return mod.default || mod;
    } finally {
      fs.unlinkSync(tmp);
    }
  }
  throw new Error(`No vitest.config.(ts|mts|js) found under ${dir}`);
}

const base = await loadCorpusConfig(PROJECT_DIR, CORPUS_ROOT);
const baseTest = { ...(base.test || {}) };
delete baseTest.projects; // see header comment: workspace fan-out is not what a scoped run wants

// KNOWN LIMITATION: when the corpus's own config chain spans multiple files (a project config
// importing a shared root config, zod's shape), esbuild bundling them together for the plain-Node
// `import()` above erases each file's own `import.meta.url` -- every `resolve(__dirname, ...)`
// call in the INLINED file now resolves against the OUTER bundle's location instead of its own,
// so a path like the root config's own setupFile can come out pointing at a directory that
// doesn't have it. Rather than silently run with a broken path (a setupFile vitest would fail to
// find is a startup error, not a silent no-op, so this is safe -- but drop it loudly instead of
// crashing the whole run over a corpus-side test helper unrelated to instrumentation):
if (Array.isArray(baseTest.setupFiles)) {
  const fs = require('fs');
  const kept = [];
  for (const f of baseTest.setupFiles) {
    if (typeof f === 'string' && !fs.existsSync(f)) {
      console.error(
        `[shadow-stack/vite] WARNING: dropping corpus setupFile that does not resolve on disk ` +
        `(likely the multi-file-config bundling limitation noted above): ${f}`
      );
      continue;
    }
    kept.push(f);
  }
  baseTest.setupFiles = kept;
}

export default defineConfig({
  ...base,
  root: PROJECT_DIR,
  plugins: [...(base.plugins || []), shadowStackPlugin({ corpusRoot: CORPUS_ROOT })],
  test: {
    ...baseTest,
    // Type-checking is a separate `tsc`/`vue-tsc` pass vitest can spawn per file; it exercises
    // neither our plugin nor the code under test's runtime call graph, and it roughly doubles
    // wall time for no benefit to this oracle. Off for the instrumented run.
    typecheck: { ...(baseTest.typecheck || {}), enabled: false },
    setupFiles: [...(baseTest.setupFiles || []), path.join(__dirname, 'vitest_setup_stack.js')],
  },
});
