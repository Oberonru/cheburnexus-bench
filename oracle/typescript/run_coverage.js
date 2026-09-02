const { AsyncLocalStorage } = require('async_hooks');
const als = new AsyncLocalStorage();

const edges = new Set();
const edgeList = [];

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
  try {
    const result = await als.run({ stack: ['<module>'] }, () => {
      const mod = require('./coverage.compiled.js');
      return mod.main();
    });
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
