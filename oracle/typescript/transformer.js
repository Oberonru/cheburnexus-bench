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
  if (node.name && ts.isComputedPropertyName(node.name)) {
    const expr = node.name.expression;
    if (ts.isStringLiteral(expr) || ts.isNumericLiteral(expr)) return expr.text;
    return '<computed>';
  }
  return fallback;
}

// ARROW STATIC NAME (2026-09-03, oracle side of the two-ends contract): an arrow assigned to a
// variable, an object-literal property (possibly nested), or a bare `Target.member = () => {}`
// HAS a real static name -- discarding it in favor of a positional `anonL<line>` is our own
// ruler's defect, sized independently at 265 edges on class-validator / 1625 on zod (see
// finding-zod-recall-bucketed-no-remainder-2026-09-03.md, bucket 4). Mirrors, on purpose, the
// SAME three shapes and the SAME precedence TsAnalyzer's `describeCallable` (emitCore.ts) already
// uses on the engine side -- read that function first if touching this one, do not reinvent a
// different scheme, or the two ends of the label contract drift apart again (the exact failure
// mode of 161842f5's regression). Returns null (never a guess) when no static root is reachable
// (destructuring targets, computed member expressions, an object literal with no qualifying
// root) -- the caller falls back to anonLabel(), same as before this fix existed.
// IMPORTANT: called with the ORIGINAL (pre-visitEachChild) node -- .parent is only reliable on
// nodes still attached to the parsed tree, and an arrow's static-name-bearing ancestor
// (VariableDeclaration/PropertyAssignment/BinaryExpression) never changes shape under recursion
// into the arrow's own body, so using the original node's parent chain is exact, not an
// approximation.
function arrowStaticName(ts, node) {
  const par = node.parent;
  if (!par) return null;

  if (ts.isVariableDeclaration(par) && par.initializer === node && ts.isIdentifier(par.name)) {
    return par.name.text;
  }

  const staticKeyName = (nameNode) => {
    if (ts.isIdentifier(nameNode) || ts.isPrivateIdentifier(nameNode)) return nameNode.text;
    if (ts.isStringLiteral(nameNode) || ts.isNumericLiteral(nameNode)) return nameNode.text;
    return null;
  };

  // CLASS-FIELD-INITIALIZER FIX (2026-09-03): `class C { static create = (p) => {...} }` is a
  // PropertyDeclaration, not a VariableDeclaration/PropertyAssignment/BinaryExpression -- none of
  // the branches below matched it, so an arrow initializing a class field (static or instance)
  // fell through to anonLabel() despite JS's own inferred-name rule giving it the field's name
  // (verified in node: `A.create.name === "create"`). The engine's describeCallable already
  // treats this shape as a named member (`ZodString.create`); classStack (see labelFor) already
  // supplies the class-name half, so this branch only needs to return the bare property key, not
  // a qualified name -- prefixing the class here would double it.
  if (ts.isPropertyDeclaration(par) && par.initializer === node) {
    const key = staticKeyName(par.name);
    if (key) return key;
  }

  if (ts.isPropertyAssignment(par) && par.initializer === node) {
    const key = staticKeyName(par.name);
    if (key) {
      const segs = [key];
      let cur = par.parent; // the ObjectLiteralExpression that owns `par`
      for (;;) {
        const holder = cur && cur.parent;
        if (holder && ts.isPropertyAssignment(holder) && holder.initializer === cur) {
          const hName = staticKeyName(holder.name);
          if (!hName) break;
          segs.unshift(hName);
          cur = holder.parent;
          continue;
        }
        if (holder && ts.isVariableDeclaration(holder) && holder.initializer === cur && ts.isIdentifier(holder.name)) {
          segs.unshift(holder.name.text);
        }
        break;
      }
      return segs.join('.');
    }
  }

  if (
    ts.isBinaryExpression(par) &&
    par.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
    par.right === node
  ) {
    const target = par.left;
    // ONLY a plain dotted chain rooted in an identifier/`this` (`Mocker.pick`, `this.x.y`) is a
    // safe static name. Anything else reachable through PropertyAccessExpression.getText() -- a
    // parenthesized cast (`(payload as $RefinementCtx).addIssue`), a call result, an element
    // access in the chain -- prints its FULL SOURCE TEXT including `(`/`as`/etc, which breaks the
    // grader's `_CECIL_FULLNAME` regex (`[^(]+` for the method part) and silently drops the row
    // as unparsed. MEASURED, not assumed: an early version of this fix produced exactly 46 such
    // unparsed rows on zod (all from this one shape) before this guard was added. Falls through to
    // anonLabel(), same as any other non-static-root shape.
    if (isSimpleChain(ts, target)) return target.getText();
  }

  return null;
}

function isSimpleChain(ts, expr) {
  if (ts.isIdentifier(expr)) return true;
  if (expr.kind === ts.SyntaxKind.ThisKeyword) return true;
  if (ts.isPropertyAccessExpression(expr)) return isSimpleChain(ts, expr.expression);
  return false;
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

// DEFAULT-PARAM FIX (2026-09-03): `function f(x = g())` records `g()`'s call edge one frame too
// shallow -- `g()` runs during parameter binding, BEFORE `__stack.run("f", ...)` has been entered,
// so the edge lands as caller->g instead of f->g. Fix: for each parameter that is a SIMPLE
// identifier binding (no destructuring, no rest, no `this`-parameter, no accessibility/readonly
// modifier -- i.e. not a TypeScript parameter property) with an initializer, strip the initializer
// from the signature and emit `if (x === undefined) { x = <initializer>; }` as a statement at the
// front of the wrapped body, so the call now happens inside the __stack.run() frame.
// DELIBERATELY LEFT UNTOUCHED (initializer stays in the signature, eager, still misattributed):
//   - destructuring parameters with defaults (`{a = 1} = {}`), nested or not -- param.name is not
//     an Identifier there, no safe single-expression rewrite exists without a temp/rest-of-pattern
//     dance.
//   - `this` parameters -- never carry an initializer in valid TS, excluded naturally.
//   - rest parameters -- cannot have an initializer in TS, excluded naturally (dotDotDotToken guard
//     is defensive).
//   - parameter properties (`constructor(private readonly x = f())`) -- rewriting would separate
//     the `private`/`readonly` modifier from an initializer TypeScript uses to both bind the
//     parameter AND assign `this.x`; moving the initializer into the body would require also
//     synthesizing the `this.x = x;` assignment TS normally does implicitly. Left eager.
// ORDERING CAVEAT (see README "Known holes"): a signature that MIXES a convertible identifier
// default with a left-in-place complex default no longer preserves strict left-to-right source
// order -- every left-in-place default still evaluates during binding, in its original position,
// but every converted default now runs AFTER ALL parameters are bound (at the top of the wrapped
// body), not interleaved at its original position. Only observable if two defaults with side
// effects are mixed in one signature; not exercised by class-validator in the scale run.
function hoistDefaultParams(ts, factory, parameters) {
  const hoistStatements = [];
  const newParams = parameters.map((param) => {
    if (
      param.initializer &&
      ts.isIdentifier(param.name) &&
      (!param.modifiers || param.modifiers.length === 0) &&
      !param.dotDotDotToken
    ) {
      const paramName = param.name.text;
      hoistStatements.push(
        factory.createIfStatement(
          factory.createBinaryExpression(
            factory.createIdentifier(paramName),
            factory.createToken(ts.SyntaxKind.EqualsEqualsEqualsToken),
            factory.createIdentifier('undefined')
          ),
          factory.createBlock(
            [
              factory.createExpressionStatement(
                factory.createBinaryExpression(
                  factory.createIdentifier(paramName),
                  factory.createToken(ts.SyntaxKind.EqualsToken),
                  param.initializer
                )
              ),
            ],
            true
          )
        )
      );
      return factory.updateParameterDeclaration(
        param, param.modifiers, param.dotDotDotToken, param.name,
        param.questionToken, param.type, undefined
      );
    }
    return param;
  });
  return { newParams, hoistStatements };
}

// SUPER-CALL FIX (2026-09-03): a derived-class constructor was skipped ENTIRELY whenever it
// contained a super() call anywhere in its own control flow -- its own frame, and everything it
// calls (including before AND after super()), folded to the caller. `super(...)` cannot be moved
// into a nested closure (illegal: "Must call super constructor in derived class before accessing
// 'this'"), so the whole body can't be wrapBody()'d the way every other callable is. Fix: handle
// only the common, safe shape -- super(...) as a DIRECT top-level statement in the constructor's
// own block (not nested inside if/try/etc, where "the statement range after it" is ambiguous
// across branches) -- and wrap only the STATEMENTS AFTER IT via the existing wrapBody/als.run
// strategy, leaving super(...) (and anything before it) as literal top-level statements. An arrow
// function closes over `this` lexically (does not rebind it), so `this` inside the wrapped
// tail is identical to `this` in the original constructor body; an early `return;` inside the
// tail still works exactly as before since it now returns from the wrapping arrow, and
// wrapBody()'s own `return __stack.run(...)` forwards whatever the arrow returns as the
// constructor's return value (undefined unless a value was explicitly returned) -- constructors
// with no return use in JS/TS anyway obey the same "ignore the return value unless it's an
// object" spec rule regardless of how deep the return sits.
// RESIDUAL (documented in README, not attempted here): a super() call NESTED inside the
// constructor's own control flow (e.g. inside `if (cond) { super(a); } else { super(b); }`) has
// no single unambiguous "everything after" range across both branches, so those constructors
// still fall back to being skipped entirely (previous behavior, unchanged) -- their own frame and
// every call they make, before and after super(), still folds to the caller. Calls made in
// super()'s OWN ARGUMENT EXPRESSIONS (`super(f())`) also still fold to the caller in the fixed
// case too -- they run before the wrapped tail begins, same residual as before.
function findDirectSuperStatementIndex(ts, statements) {
  for (let i = 0; i < statements.length; i++) {
    const stmt = statements[i];
    if (
      ts.isExpressionStatement(stmt) &&
      ts.isCallExpression(stmt.expression) &&
      stmt.expression.expression.kind === ts.SyntaxKind.SuperKeyword
    ) {
      return i;
    }
  }
  return -1;
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

    // POSITIONALLY-STABLE ANONYMOUS LABEL (2026-09-03 follow-up): replaces the old visit-order
    // `anon${counter}`/`anonFnExpr${counter}`/`arrow${counter}` fallback. That scheme was the one
    // label component in this whole file that was NOT positionally stable — memory records it
    // renumbering every later anonymous callable in a file after an unrelated earlier edit, which
    // made a 2772->3255 shadow-stack comparison undecomposable and forced a "this delta is NOT
    // recall" caveat (finding-shadow-stack-deterministic-but-fabricates-edges-2026-09-02.md). The
    // engine side (TsAnalyzer's `describeCallable` in emitCore.ts) already made the deliberate
    // choice to label an anonymous callable by its own start line instead — this brings the oracle
    // to the SAME scheme so the two ends can join by an exact string, not a fuzzy one.
    //
    // Line alone is not always unique: two distinct anonymous callables CAN start on the same
    // source line (`arr.map(() => f()).filter(() => g())`). Collapsing them onto one label would
    // silently fabricate a merged identity for two different callables — the exact defect the
    // 2026-09-02 class-qualified-label fix killed for named members, so it must not be reintroduced
    // here for anonymous ones. `anonLineUsed` counts same-line occurrences (reset per file, same
    // scope as `counter`/`classStack`) and the label for a SECOND-OR-LATER callable on an
    // already-seen line gets a `:C<column>` suffix — the column is itself a real, deterministic,
    // positionally-stable fact (two distinct nodes cannot start at the same line AND column), not a
    // counter. A line with only one anonymous callable (the overwhelming majority) gets the plain
    // `anonL<line>` form.
    //
    // NO ANGLE BRACKETS (measured, not a style choice): grade.py's `normalize_caller`/
    // `enclosing_user_method` treats ANY caller key containing '<' as a compiler-generated,
    // unattributable name (built for C#'s `<>c__DisplayClass` mangling) and drops the WHOLE oracle
    // row out of the primary cell before it can ever be matched. Tried `<anon:L<line>>` first
    // (matching TsAnalyzer's own raw spelling byte-for-byte) and measured the result: recall rose
    // from 0.095 to a hollow 0.397, but `matched` stayed at exactly 240 either run — the "gain" was
    // ~1900 oracle primary rows silently vanishing into "caller not remappable" (2665 vs 765
    // before), not anything newly joined. TsAnalyzer's own raw label keeping its angle brackets is
    // fine (`<anon:L<line>>`/`<iife:L<line>>` never reach the grader directly) — only THIS label,
    // and this arm's translated key built to match it (see arms/cheburnexus-ts/run.py's
    // `_ANON_LABEL_RE`/`_anon_join_name`), need to stay bracket-free.
    const anonLineUsed = new Map();
    function anonLabel(node) {
      const line = lineOf(node);
      const col = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).character + 1;
      const seen = anonLineUsed.get(line) || 0;
      anonLineUsed.set(line, seen + 1);
      return seen === 0 ? `anonL${line}` : `anonL${line}C${col}`;
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

    // EXPR-BODIED-ARROW FIX (2026-09-03): wraps a non-block arrow BODY EXPRESSION (`() => expr`)
    // into `__stack.run("label", (async) () => (expr))` as an EXPRESSION, not a Block+Return --
    // turning `() => expr` into `() => { return __stack.run(...) }` would silently change the
    // arrow from expression-bodied to block-bodied, which is observable (e.g. as the return value
    // of Function.prototype.toString()) and unnecessary since als.run() already returns whatever
    // the inner callback returns. Same als.run()-per-call strategy as wrapBody, just expression-
    // shaped output.
    function wrapExpr(bodyExpr, label, async) {
      const innerArrow = factory.createArrowFunction(
        async ? [factory.createModifier(ts.SyntaxKind.AsyncKeyword)] : undefined,
        undefined,
        [],
        undefined,
        factory.createToken(ts.SyntaxKind.EqualsGreaterThanToken),
        bodyExpr
      );
      return factory.createCallExpression(
        factory.createPropertyAccessExpression(factory.createIdentifier('__stack'), 'run'),
        undefined,
        [factory.createStringLiteral(label), innerArrow]
      );
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

      // FIX (2026-09-03): `static {}` class initialization blocks were not a handled node kind,
      // so calls inside them folded straight to `<module>` (or whatever frame was active when the
      // class was evaluated) instead of carrying their own frame. A static block is a BlockLike
      // body with no name/params, so it wraps with the exact same als.run() strategy as every
      // other callable here -- label uses the fixed tag '<static>' since there is nothing else to
      // name it by (matches the label scheme's kindSuffix idea, just always-on rather than
      // get/set-conditional).
      if (ts.isClassStaticBlockDeclaration(visited)) {
        counter++;
        const label = labelFor(visited, '<static>');
        const newBody = wrapBody(visited.body, label, false);
        return factory.updateClassStaticBlockDeclaration(visited, newBody);
      }

      // COVERAGE-SWEEP FIX: a generator body cannot be wrapped in a plain arrow (yield is
      // illegal inside a non-generator function) -- wrapping it produced a SyntaxError that
      // crashed the whole compiled module. Cheapest correct fix: skip instrumenting generator
      // bodies entirely (asteriskToken present) rather than emit invalid JS. This is a KNOWN
      // HOLE (calls made from inside a generator/async-generator body lose their caller frame,
      // folding to whatever frame was active when .next() was invoked) but it no longer corrupts
      // the whole file. A full fix (push/pop around each yield instead of wrap-the-whole-body)
      // is a bigger change, not attempted here.
      if (
        ts.isConstructorDeclaration(visited) &&
        visited.body &&
        hasDirectSuperCall(ts, visited.body) &&
        findDirectSuperStatementIndex(ts, visited.body.statements) !== -1
      ) {
        counter++;
        const label = labelFor(visited, 'constructor');
        const { newParams, hoistStatements } = hoistDefaultParams(ts, factory, visited.parameters);
        const superIdx = findDirectSuperStatementIndex(ts, visited.body.statements);
        const beforeAndSuper = visited.body.statements.slice(0, superIdx + 1);
        const afterBlock = factory.createBlock(visited.body.statements.slice(superIdx + 1), true);
        const wrappedAfter = wrapBody(afterBlock, label, isAsyncNode(ts, visited));
        const newBody = factory.createBlock(
          [...hoistStatements, ...beforeAndSuper, ...wrappedAfter.statements],
          true
        );
        return factory.updateConstructorDeclaration(visited, visited.modifiers, newParams, newBody);
      }

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
          // nameOf(..., null) so a real bound name is never discarded after anonLabel() has
          // already consumed a same-line collision slot for it -- see anonLabel's comment.
          : (nameOf(ts, visited, null) ?? anonLabel(visited));
        const kindSuffix = ts.isGetAccessorDeclaration(visited)
          ? '(get)'
          : ts.isSetAccessorDeclaration(visited)
          ? '(set)'
          : '';
        const label = labelFor(visited, name, kindSuffix);
        const { newParams, hoistStatements } = hoistDefaultParams(ts, factory, visited.parameters);
        const bodyWithDefaults = hoistStatements.length
          ? factory.createBlock([...hoistStatements, ...visited.body.statements], true)
          : visited.body;
        const newBody = wrapBody(bodyWithDefaults, label, isAsyncNode(ts, visited));
        if (ts.isFunctionDeclaration(visited)) {
          return factory.updateFunctionDeclaration(
            visited, visited.modifiers, visited.asteriskToken, visited.name,
            visited.typeParameters, newParams, visited.type, newBody
          );
        } else if (ts.isMethodDeclaration(visited)) {
          return factory.updateMethodDeclaration(
            visited, visited.modifiers, visited.asteriskToken, visited.name,
            visited.questionToken, visited.typeParameters, newParams,
            visited.type, newBody
          );
        } else if (ts.isConstructorDeclaration(visited)) {
          return factory.updateConstructorDeclaration(
            visited, visited.modifiers, newParams, newBody
          );
        } else if (ts.isGetAccessorDeclaration(visited)) {
          return factory.updateGetAccessorDeclaration(
            visited, visited.modifiers, visited.name, newParams, visited.type, newBody
          );
        } else {
          return factory.updateSetAccessorDeclaration(
            visited, visited.modifiers, visited.name, newParams, newBody
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
        const name = nameOf(ts, visited, null) ?? anonLabel(visited);
        const label = labelFor(visited, name);
        const { newParams, hoistStatements } = hoistDefaultParams(ts, factory, visited.parameters);
        const bodyWithDefaults = hoistStatements.length
          ? factory.createBlock([...hoistStatements, ...visited.body.statements], true)
          : visited.body;
        const newBody = wrapBody(bodyWithDefaults, label, isAsyncNode(ts, visited));
        return factory.updateFunctionExpression(
          visited, visited.modifiers, visited.asteriskToken, visited.name,
          visited.typeParameters, newParams, visited.type, newBody
        );
      }

      if (ts.isArrowFunction(visited) && visited.body && ts.isBlock(visited.body)) {
        counter++;
        // ORACLE-NAME FIX (2026-09-03): use the arrow's static name (var/property/assignment
        // target) when one exists, matching the engine's describeCallable -- see arrowStaticName
        // above. Falls back to the old positional anonLabel() only when no static root exists.
        const label = labelFor(visited, arrowStaticName(ts, node) ?? anonLabel(visited));
        const { newParams, hoistStatements } = hoistDefaultParams(ts, factory, visited.parameters);
        const bodyWithDefaults = hoistStatements.length
          ? factory.createBlock([...hoistStatements, ...visited.body.statements], true)
          : visited.body;
        const newBody = wrapBody(bodyWithDefaults, label, isAsyncNode(ts, visited));
        return factory.updateArrowFunction(
          visited, visited.modifiers, visited.typeParameters, newParams,
          visited.type, visited.equalsGreaterThanToken, newBody
        );
      }

      // FIX (2026-09-03): expression-bodied arrows (`() => expr`), including curried arrows
      // (`(a) => (b) => a + b`, both levels expr-bodied) and class-field arrow initializers
      // (`fieldArrow = () => fieldArrowBody()`), were previously invisible -- the only arrow
      // branch above requires ts.isBlock(visited.body). Wrap the BODY EXPRESSION itself via
      // wrapExpr rather than converting to a block, preserving the arrow's expression-bodied
      // shape and its async-ness.
      if (ts.isArrowFunction(visited) && visited.body && !ts.isBlock(visited.body)) {
        counter++;
        // Same ORACLE-NAME FIX as the block-bodied arrow branch above.
        const label = labelFor(visited, arrowStaticName(ts, node) ?? anonLabel(visited));
        const newBody = wrapExpr(visited.body, label, isAsyncNode(ts, visited));
        return factory.updateArrowFunction(
          visited, visited.modifiers, visited.typeParameters, visited.parameters,
          visited.type, visited.equalsGreaterThanToken, newBody
        );
      }

      return visited;
    }

    let outSf = ts.visitNode(sourceFile, visit);

    // MODULE-FRAME FIX (2026-09-03): before this, EVERY call made directly at a file's true top
    // level -- not inside any function -- was invisible to the shadow stack, because
    // shadow_stack.js's ROOT_FRAME ('<module>') is one hardcoded, location-less sentinel used for
    // TWO structurally different situations that collapsed onto the same string: (a) a real,
    // deterministic, first-party call the corpus's own module-evaluation code makes (a decorator
    // applied to a file-scope class, a top-level `describe(...)` registration call, a bootstrap
    // IIFE, DI container registration at import time -- see finding-ts-no-caller-calls-counted-
    // 2026-08-27.md), and (b) a callback Jest's own scheduler invokes later with no ALS context
    // at all (an `it(...)`/`beforeEach(...)` body -- genuinely unattributable, no call expression
    // in source ever names it). On class-validator this was measured, not assumed: 893 unique
    // "<module> -> X" edges, of which 747 were it()/beforeEach() bodies (bucket (b), correctly
    // unattributable) and 144 + 2 were real top-level `describe(...)` registrations and one
    // decorator application (bucket (a), a real fact silently destroyed by the shared sentinel).
    //
    // Fix: give each file its OWN synthetic top-level frame, entered via the SAME als.run()-per-
    // call strategy as every other callable (never enterWith -- same ban as everywhere else in
    // this file), covering only statements SAFE to move into a nested closure without changing
    // module semantics: plain top-level ExpressionStatements (side-effecting calls, assignments --
    // no binding they introduce, so nesting them changes nothing observable). Left OUTSIDE any
    // wrap, deliberately, because moving them would change TDZ/hoisting/live-binding/export
    // semantics in ways that look like flaky tests, not like a call-graph fix:
    //   - import/export declarations and anything carrying an `export` modifier
    //   - class and function declarations (their OWN body is still instrumented normally by the
    //     `visit` pass above -- only the file-scope BINDING/hoisting position is left alone)
    //   - interface/type-alias/enum/module declarations (type-only or need their own hoisting)
    //   - variable statements (`const`/`let`/`var`) -- wrapping would move the DECLARED BINDING
    //     into the wrapper's own closure scope, invisible to any sibling top-level code that
    //     references it by name; only bindings are risky, not use, so this is deliberately more
    //     conservative than strictly necessary
    //   - a leading directive prologue ("use strict", ...) -- an ExpressionStatement whose
    //     expression is a plain string literal, syntactically identical to a real string-literal
    //     expression statement, but nesting it inside a closure silently stops it being a
    //     directive at all; excluded unconditionally rather than only when actually leading, since
    //     telling the two apart needs full directive-prologue scanning for no real-world benefit
    //     here (no corpus measured so far has a bare string-literal statement mid-file)
    //
    // KNOWN, ACCEPTED RESIDUAL: a decorator on a class declared at file scope (like this
    // corpus's own reject-validation.spec.ts:5-9) still folds to the bare ROOT_FRAME, because the
    // class declaration itself must not be wrapped -- its decorator list is evaluated as PART OF
    // that (excluded) node, not as a separate statement this pass ever sees. Only the subset that
    // syntactically IS a standalone top-level statement is fixed here.
    function isWrappableTopLevelStatement(node) {
      if (!ts.isExpressionStatement(node)) return false;
      if (ts.isStringLiteralLike(node.expression)) return false; // possible directive prologue
      return true;
    }

    const hasWrappableTopLevel = outSf.statements.some(isWrappableTopLevelStatement);
    if (hasWrappableTopLevel) {
      const fileLabel = `${fileTag}:module-scope:0`;
      const newStatements = [];
      let i = 0;
      const stmts = outSf.statements;
      while (i < stmts.length) {
        if (!isWrappableTopLevelStatement(stmts[i])) {
          newStatements.push(stmts[i]);
          i++;
          continue;
        }
        const run = [];
        while (i < stmts.length && isWrappableTopLevelStatement(stmts[i])) {
          run.push(stmts[i]);
          i++;
        }
        counter++; // one wrap event per contiguous run, folded into the same zero-wrap guard
        const runCall = factory.createCallExpression(
          factory.createPropertyAccessExpression(factory.createIdentifier('__stack'), 'run'),
          undefined,
          [
            factory.createStringLiteral(fileLabel),
            factory.createArrowFunction(
              undefined, undefined, [], undefined,
              factory.createToken(ts.SyntaxKind.EqualsGreaterThanToken),
              factory.createBlock(run, true)
            ),
          ]
        );
        newStatements.push(factory.createExpressionStatement(runCall));
      }
      outSf = factory.updateSourceFile(outSf, newStatements);
    }

    wrappedInFile += counter;
    return outSf;
  };

  // wrapped() reports how many function bodies this transformer actually rewrote; a caller that
  // sees zero across a whole run must fail loudly rather than publish an empty answer key.
  return { transform, wrapped: () => wrappedInFile };
}

module.exports = { makeTransformer };
