// COVERAGE SWEEP synthetic file. Every callable shape, each exercised by exactly one call.
// HAND-DERIVED EXPECTED EDGES are written in coverage_expected.js BEFORE this is run.
// Label convention the transformer uses: "<fileTag>:<name>" for declared things,
// "<fileTag>:arrowN" / "<fileTag>:anonFnExprN" for anonymous things (N = nth-visited counter).

// 1. expression-bodied arrow (no block body)
const exprArrow = (x: number) => x + 1;

// 2. nested/curried arrows, both expression-bodied
const curried = (a: number) => (b: number) => a + b;

// 3. generator + async generator, calls between yields
function* gen() {
  yield callFromGen1();
  yield callFromGen2();
}
function callFromGen1() { return 'g1'; }
function callFromGen2() { return 'g2'; }

async function* asyncGen() {
  yield callFromAsyncGen1();
  await delay(1);
  yield callFromAsyncGen2();
}
function callFromAsyncGen1() { return 'ag1'; }
function callFromAsyncGen2() { return 'ag2'; }
function delay(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

// 4. class with: static init block, computed method name, getter/setter, private method,
//    field initializer arrow, constructor calling super(), derived class.
const COMPUTED_KEY = 'computedMethod';

class Base {
  constructor() {
    baseCtorBody();
  }
}
function baseCtorBody() { return 'base-ctor'; }

class Widget extends Base {
  static staticFlag: boolean;
  static {
    staticBlockBody();
  }

  #secret = 1;

  fieldArrow = () => fieldArrowBody();

  constructor() {
    super();
    ctorBody();
  }

  [COMPUTED_KEY]() {
    return computedMethodBody();
  }

  get value() {
    return getterBody();
  }
  set value(v: number) {
    setterBody(v);
  }

  #privateMethod() {
    return privateMethodBody();
  }

  callPrivate() {
    return this.#privateMethod();
  }
}
function staticBlockBody() { return 'static-block'; }
function fieldArrowBody() { return 'field-arrow'; }
function ctorBody() { return 'ctor'; }
function computedMethodBody() { return 'computed'; }
function getterBody() { return 'getter'; }
function setterBody(v: number) { return 'setter' + v; }
function privateMethodBody() { return 'private-method'; }

// 5. object literal methods and shorthand methods
function shorthandBody() { return 'shorthand'; }
function longhandBody() { return 'longhand'; }
const objLit = {
  shorthand() { return shorthandBody(); },
  longhand: function () { return longhandBody(); },
};

// 6. IIFEs
function fnIifeBody() { return 'fn-iife'; }
function arrowIifeBody() { return 'arrow-iife'; }
(function () { return fnIifeBody(); })();
(() => { return arrowIifeBody(); })();

// 7. default parameter value that is itself a call
function defaultParamSource() { return 42; }
function withDefault(x = defaultParamSource()) { return x; }

// 8. callback passed to native higher-order function
function mapCallbackBody(n: number) { return n * 2; }
const mapped = [1, 2, 3].map(function mapCb(n) { return mapCallbackBody(n); });

// 9. new Function(...) and eval -- expected uninstrumentable, recorded as known hole
function uninstrumentableTarget() { return 'from-dynamic'; }
const dynFn = new Function('return 1;');

// 10. re-exported/re-bound function, function stored on object and invoked as method
function originalFn() { return 'original'; }
const rebound = originalFn;
const holder = { method: originalFn };

// --- driver ---
export async function main() {
  const edgeMarkers: string[] = [];

  exprArrow(1);
  curried(1)(2);

  const g = gen();
  g.next(); g.next(); g.next();

  const ag = asyncGen();
  await ag.next(); await ag.next(); await ag.next();

  const w = new Widget();
  w[COMPUTED_KEY]();
  w.fieldArrow();
  w.value; // getter
  w.value = 5; // setter
  w.callPrivate();

  objLit.shorthand();
  objLit.longhand();

  withDefault();

  mapped;

  try { dynFn(); } catch (e) { /* ignore */ }
  try {
    // eslint-disable-next-line no-eval
    eval('uninstrumentableTargetViaEval()');
  } catch (e) { /* expected: not in scope, or uninstrumented -- ignore */ }

  rebound();
  holder.method();

  return edgeMarkers;
}
