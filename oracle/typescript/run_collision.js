const { AsyncLocalStorage } = require('async_hooks');
const als = new AsyncLocalStorage();
const edges = new Set(); const edgeList = [];
global.__stack = {
  run(label, fn) {
    const store = als.getStore();
    const parentStack = store ? store.stack : ['<module>'];
    const caller = parentStack[parentStack.length - 1];
    const key = `${caller} -> ${label}`;
    if (!edges.has(key)) { edges.add(key); edgeList.push(key); }
    const newStack = parentStack.concat([label]);
    return als.run({ stack: newStack }, fn);
  },
};
(async () => {
  await als.run({ stack: ['<module>'] }, () => require('./collision.compiled.js').main());
  console.log('--- EDGES ---');
  for (const e of edgeList) console.log(e);
})();
