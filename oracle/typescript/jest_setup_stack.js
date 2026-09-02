// Jest setupFiles entry: installs global.__stack, backed by AsyncLocalStorage, appending every
// recorded edge to a shared file on disk (EDGE_OUT env var) -- necessary because jest resets the
// module registry PER TEST FILE even with --runInBand, so an in-memory Set would not accumulate
// across files.
const fs = require('fs');
const { AsyncLocalStorage } = require('async_hooks');

const als = new AsyncLocalStorage();
const OUT = process.env.EDGE_OUT;
if (!OUT) throw new Error('EDGE_OUT env var required');

global.__stack = {
  run(label, fn) {
    const store = als.getStore();
    const parentStack = store ? store.stack : ['<module>'];
    const caller = parentStack[parentStack.length - 1];
    fs.appendFileSync(OUT, `${caller} -> ${label}\n`);
    const newStack = parentStack.concat([label]);
    return als.run({ stack: newStack }, fn);
  },
};

// No enterWith here: als.getStore() returning undefined already defaults to ['<module>']
// in run() above, and the proven design (see finding-shadow-stack-oracle-go-als-per-call)
// is als.run() per call, never a shared/entered context.
