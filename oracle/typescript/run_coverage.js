// Fixture runner. The mechanism and the in-memory sink live in als_stack.js /
// shadow_stack.js -- this file must never carry its own copy of them.
require('./als_stack');
const edgeList = global.__edgeList;

(async () => {
  try {
    const mod = require('./coverage.compiled.js');
    const result = await mod.main();
    console.log('--- RECORDED EDGES (first-seen order) ---');
    for (const e of edgeList) console.log(e);
    console.log('--- main() returned ---', result);
  } catch (e) {
    console.log('--- RECORDED EDGES BEFORE FAILURE ---');
    for (const e of edgeList) console.log(e);
    console.error('--- RUN FAILED ---', e && e.stack || e);
    process.exitCode = 1;
  }
})();
