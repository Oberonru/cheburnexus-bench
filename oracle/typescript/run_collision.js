// Fixture runner. The mechanism and the in-memory sink live in als_stack.js /
// shadow_stack.js -- this file must never carry its own copy of them.
require('./als_stack');
const edgeList = global.__edgeList;

(async () => {
  await require('./collision.compiled.js').main();
  console.log('--- EDGES ---');
  for (const e of edgeList) console.log(e);
})();
