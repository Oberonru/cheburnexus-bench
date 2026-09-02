// Shadow-stack instrumentation transformer using the TypeScript compiler API
// purely as a syntax-tree rewriter (no type-checker calls) -- inserts:
//   __stack.push("<file>:<name>"); try { ...body... } finally { __stack.pop(); }
// into every function declaration, function expression, arrow function (with a block body),
// method, and constructor.

// IMPORTANT: the TypeScript instance is INJECTED, never required here.
// ts.isFunctionDeclaration() and friends compare node.kind against SyntaxKind, which is a
// NUMERIC enum whose values SHIFT BETWEEN TYPESCRIPT VERSIONS (5.4.5: FunctionDeclaration=262,
// 5.9.3: 263). Requiring our own copy while inspecting nodes built by the corpus's copy makes
// every type guard silently return false: zero functions get wrapped, the suite still passes,
// and the run produces an EMPTY answer key while reporting success. Always hand this module the
// same ts that produced the nodes.

function nameOf(ts, node, fallback) {
  if (node.name && ts.isIdentifier(node.name)) return node.name.text;
  if (node.name && ts.isPrivateIdentifier(node.name)) return node.name.text;
  if (node.name && ts.isComputedPropertyName(node.name)) return '<computed>';
  return fallback;
}

// COVERAGE-SWEEP FIX: wrapping a derived class's constructor body in a nested arrow moves the
// super() call out of the constructor's own top-level statement position, which V8 rejects
// ("Must call super constructor in derived class before accessing 'this'"). Detect a direct
// super(...) call among the constructor's own statements (not inside nested functions) and skip
// instrumenting that constructor entirely -- KNOWN HOLE: derived-class constructor calls lose
// their own frame (callee still reachable via calls made from further down, e.g. super() itself
// records nothing, but the constructor's other calls also silently fold to the caller's frame).
// BUG FIX (2026-09-03): the original scan only looked at `block.statements` directly, so a
// super() call nested inside an `if`/`else`/`try`/etc. (still the constructor's OWN top-level
// control flow, not a nested function) slipped past the guard and got wrapped inside the
// __stack.run() arrow -- an illegal position ("super() outside of a derived class constructor" /
// "Must call super constructor..."). Walk the whole constructor body looking for any super(...)
// call, but do NOT descend into nested functions, arrow functions, or nested class declarations/
// expressions -- a super() there belongs to THAT function/class, not this constructor.
function hasDirectSuperCall(ts, block) {
  let found = false;
  function walk(node) {
    if (found) return;
    if (
      ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) ||
      ts.isArrowFunction(node) || ts.isMethodDeclaration(node) ||
      ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node) ||
      ts.isConstructorDeclaration(node) ||
      ts.isClassDeclaration(node) || ts.isClassExpression(node)
    ) {
      return;
    }
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.SuperKeyword
    ) {
      found = true;
      return;
    }
    ts.forEachChild(node, walk);
  }
  block.statements.forEach(walk);
  return found;
}

function isAsyncNode(ts, node) {
  const mods = node.modifiers;
  if (!mods) return false;
  return mods.some((m) => m.kind === ts.SyntaxKind.AsyncKeyword);
}

// FABRICATION FIX (2026-09-02): the old label was `fileTag:memberName` -- no class qualifier,
// no line. Two classes in the same file declaring a method with the same name (e.g. Foo.value
// and Bar.value) collapsed onto ONE graph node, so the graph showed calls that never happened
// (an INVENTED edge, not an omission -- see finding-shadow-stack-deterministic-but-fabricates-
// edges-2026-09-02.md). Fix stolen from njstrace's `name@file::line` scheme: every frame now
// carries its 1-based source line (the required, minimal part -- unique per node by construction,
// since two declarations cannot start on the same line) plus, cheaply available at the same
// point, the enclosing class name and a getter/setter tag for accessors.
function makeTransformer(ts, fileTag) {
  let wrappedInFile = 0;
  const transform = (context) => (sourceFile) => {
    const { factory } = context;
    let counter = 0;
    const classStack = [];

    function lineOf(node) {
      return sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
    }

    function labelFor(node, name, kindSuffix) {
      const cls = classStack.length ? classStack[classStack.length - 1] : null;
      const qualifiedName = cls ? `${cls}.${name}` : name;
      return `${fileTag}:${qualifiedName}:${lineOf(node)}${kindSuffix || ''}`;
    }

    // Rewrites `{ ...body... }` into:
    //   return __stack.run("label", (async) () => { ...body... });
    // __stack.run() looks up the CURRENT AsyncLocalStorage-tracked stack (correctly
    // scoped across awaits since ALS snapshots context per async continuation, unlike
    // a shared global array), records caller->label, and runs the callback in a NEW
    // child context carrying the extended stack.
    function wrapBody(block, label, async) {
      const innerArrow = factory.createArrowFunction(
        async ? [factory.createModifier(ts.SyntaxKind.AsyncKeyword)] : undefined,
        undefined,
        [],
        undefined,
        factory.createToken(ts.SyntaxKind.EqualsGreaterThanToken),
        block
      );
      const runCall = factory.createCallExpression(
        factory.createPropertyAccessExpression(factory.createIdentifier('__stack'), 'run'),
        undefined,
        [factory.createStringLiteral(label), innerArrow]
      );
      return factory.createBlock([factory.createReturnStatement(runCall)], true);
    }

    function visit(node) {
      if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
        const cname = node.name ? node.name.text : `<anonClass:${lineOf(node)}>`;
        classStack.push(cname);
        const visitedClass = ts.visitEachChild(node, visit, context);
        classStack.pop();
        return visitedClass;
      }

      const visited = ts.visitEachChild(node, visit, context);

      // COVERAGE-SWEEP FIX: a generator body cannot be wrapped in a plain arrow (yield is
      // illegal inside a non-generator function) -- wrapping it produced a SyntaxError that
      // crashed the whole compiled module. Cheapest correct fix: skip instrumenting generator
      // bodies entirely (asteriskToken present) rather than emit invalid JS. This is a KNOWN
      // HOLE (calls made from inside a generator/async-generator body lose their caller frame,
      // folding to whatever frame was active when .next() was invoked) but it no longer corrupts
      // the whole file. A full fix (push/pop around each yield instead of wrap-the-whole-body)
      // is a bigger change, not attempted here.
      if (
        (ts.isFunctionDeclaration(visited) || ts.isMethodDeclaration(visited) ||
         ts.isConstructorDeclaration(visited) || ts.isGetAccessorDeclaration(visited) ||
         ts.isSetAccessorDeclaration(visited)) &&
        visited.body && !visited.asteriskToken &&
        !(ts.isConstructorDeclaration(visited) && hasDirectSuperCall(ts, visited.body))
      ) {
        counter++;
        const name = ts.isConstructorDeclaration(visited)
          ? 'constructor'
          : nameOf(ts, visited, `anon${counter}`);
        const kindSuffix = ts.isGetAccessorDeclaration(visited)
          ? '(get)'
          : ts.isSetAccessorDeclaration(visited)
          ? '(set)'
          : '';
        const label = labelFor(visited, name, kindSuffix);
        const newBody = wrapBody(visited.body, label, isAsyncNode(ts, visited));
        if (ts.isFunctionDeclaration(visited)) {
          return factory.updateFunctionDeclaration(
            visited, visited.modifiers, visited.asteriskToken, visited.name,
            visited.typeParameters, visited.parameters, visited.type, newBody
          );
        } else if (ts.isMethodDeclaration(visited)) {
          return factory.updateMethodDeclaration(
            visited, visited.modifiers, visited.asteriskToken, visited.name,
            visited.questionToken, visited.typeParameters, visited.parameters,
            visited.type, newBody
          );
        } else if (ts.isConstructorDeclaration(visited)) {
          return factory.updateConstructorDeclaration(
            visited, visited.modifiers, visited.parameters, newBody
          );
        } else if (ts.isGetAccessorDeclaration(visited)) {
          return factory.updateGetAccessorDeclaration(
            visited, visited.modifiers, visited.name, visited.parameters, visited.type, newBody
          );
        } else {
          return factory.updateSetAccessorDeclaration(
            visited, visited.modifiers, visited.name, visited.parameters, newBody
          );
        }
      }

      // BUG FIX (2026-09-03): same KNOWN HOLE as the generator guard above (comment near line
      // ~99), just missing on this branch -- a generator FUNCTION EXPRESSION (`const g =
      // function* () {...}`) has no asteriskToken check here, so it got wrapped and crashed the
      // whole module with a SyntaxError (yield illegal inside the non-generator wrapper arrow).
      // Skip instrumenting it, same as generator declarations/methods: the frame is lost, but the
      // module no longer crashes.
      if (ts.isFunctionExpression(visited) && visited.body && !visited.asteriskToken) {
        counter++;
        const name = nameOf(ts, visited, `anonFnExpr${counter}`);
        const label = labelFor(visited, name);
        const newBody = wrapBody(visited.body, label, isAsyncNode(ts, visited));
        return factory.updateFunctionExpression(
          visited, visited.modifiers, visited.asteriskToken, visited.name,
          visited.typeParameters, visited.parameters, visited.type, newBody
        );
      }

      if (ts.isArrowFunction(visited) && visited.body && ts.isBlock(visited.body)) {
        counter++;
        const label = labelFor(visited, `arrow${counter}`);
        const newBody = wrapBody(visited.body, label, isAsyncNode(ts, visited));
        return factory.updateArrowFunction(
          visited, visited.modifiers, visited.typeParameters, visited.parameters,
          visited.type, visited.equalsGreaterThanToken, newBody
        );
      }

      return visited;
    }

    const outSf = ts.visitNode(sourceFile, visit);
    wrappedInFile += counter;
    return outSf;
  };

  // wrapped() reports how many function bodies this transformer actually rewrote; a caller that
  // sees zero across a whole run must fail loudly rather than publish an empty answer key.
  return { transform, wrapped: () => wrappedInFile };
}

module.exports = { makeTransformer };
