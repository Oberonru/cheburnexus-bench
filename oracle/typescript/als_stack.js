// ALS-based shadow stack runtime, shared by the real class-validator probe.
const { AsyncLocalStorage } = require('async_hooks');
const als = new AsyncLocalStorage();
const edgeList = [];
const edges = new Set();

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
global.__edgeList = edgeList;
