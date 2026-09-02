// Confirms/refutes: does the transformer's label collide across two DIFFERENT classes
// that each declare a method with the same name?
function bodyA() { return 'A'; }
function bodyB() { return 'B'; }

class Foo {
  value() { return bodyA(); }
}
class Bar {
  value() { return bodyB(); }
}

export async function main() {
  const foo = new Foo();
  const bar = new Bar();
  foo.value();
  bar.value();
  return 'done';
}
