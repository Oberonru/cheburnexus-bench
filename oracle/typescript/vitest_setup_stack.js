// Vitest setupFiles sink: appends every edge to a shared file (EDGE_OUT). Mirrors
// jest_setup_stack.js exactly, same disk-append strategy -- Vitest also isolates test files by
// default (`isolate: true`, a fresh module graph per file), so an in-memory Set would not
// accumulate across files either. The mechanism lives in shadow_stack.js and is not duplicated
// here (see principle-single-source-copies-rot in project memory).
const fs = require('fs');
const { installShadowStack } = require('./shadow_stack');

const OUT = process.env.EDGE_OUT;
if (!OUT) throw new Error('EDGE_OUT env var required');

installShadowStack((caller, callee) => {
  fs.appendFileSync(OUT, `${caller} -> ${callee}\n`);
});
