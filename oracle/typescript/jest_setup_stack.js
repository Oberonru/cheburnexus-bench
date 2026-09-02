// Jest setupFiles sink: appends every edge to a shared file (EDGE_OUT), because jest resets the
// module registry PER TEST FILE even under --runInBand, so an in-memory set would not accumulate
// across files. The mechanism lives in shadow_stack.js and is not duplicated here.
const fs = require('fs');
const { installShadowStack } = require('./shadow_stack');

const OUT = process.env.EDGE_OUT;
if (!OUT) throw new Error('EDGE_OUT env var required');

installShadowStack((caller, callee) => {
  fs.appendFileSync(OUT, `${caller} -> ${callee}\n`);
});
