// The shadow stack itself -- ONE copy of the mechanism.
//
// Every instrumented function body is rewritten by transformer.js into
//   return __stack.run("<label>", () => { ...body... });
// so when a function starts, the top of the current stack IS its caller.
//
// The stack is carried by AsyncLocalStorage with als.run() PER CALL, creating a fresh child
// context for each invocation. This is not a style choice and must not be "simplified":
// a shared mutable array with push/finally-pop was PROVEN to record a wrong caller when a
// function suspends at an await and unrelated code runs in the gap, and enterWith() leaks the
// same way across sibling branches. See finding-shadow-stack-oracle-go-als-per-call-2026-09-02.
//
// Callers differ only in where edges GO (memory, a file, ...), so the sink is injected and
// this file is never copied. A hand-copied mechanism already cost this project two days of a
// published number that flapped -- see principle-single-source-copies-rot.
const { AsyncLocalStorage } = require('async_hooks');

const ROOT_FRAME = '<module>';

// record(caller, callee) is called once per call, before the callee runs.
function installShadowStack(record) {
  const als = new AsyncLocalStorage();

  global.__stack = {
    run(label, fn) {
      const store = als.getStore();
      const parentStack = store ? store.stack : [ROOT_FRAME];
      record(parentStack[parentStack.length - 1], label);
      return als.run({ stack: parentStack.concat([label]) }, fn);
    },
  };
}

module.exports = { installShadowStack, ROOT_FRAME };
