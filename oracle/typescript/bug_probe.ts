// Bug A: GENERATOR FUNCTION EXPRESSION — the asteriskToken guard exists for declarations
// and methods, but NOT for function expressions.
const genExpr = function* () { yield 1; yield 2; };

// Bug B: super() not as a direct top-level statement of the constructor.
class Base { constructor(public n: number) {} }
class Derived extends Base {
  constructor(n: number) {
    if (n > 0) { super(n); } else { super(0); }
  }
}

export function main() {
  const g = genExpr();
  g.next();
  return new Derived(1).n;
}
