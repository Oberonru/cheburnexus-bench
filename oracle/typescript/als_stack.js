// In-memory sink: collects deduplicated edges for the small fixture runners.
// The mechanism lives in shadow_stack.js and is not duplicated here.
const { installShadowStack } = require('./shadow_stack');

const edgeList = [];
const edges = new Set();

installShadowStack((caller, callee) => {
  const key = `${caller} -> ${callee}`;
  if (!edges.has(key)) {
    edges.add(key);
    edgeList.push(key);
  }
});

global.__edgeList = edgeList;
