# Hand-derived expected edges for coverage.ts (written BEFORE running the transformer)

Module tag: coverage.ts. Top-of-module frame is `<module>`.

Module-eval-time (IIFEs + the module-level array .map call) happen BEFORE main() is invoked:
1. `<module> -> coverage.ts:fnIife(anon)`         (function IIFE)
2. `coverage.ts:fnIife(anon) -> coverage.ts:fnIifeBody`
3. `<module> -> coverage.ts:arrowIife(anon)`       (arrow IIFE)
4. `coverage.ts:arrowIife(anon) -> coverage.ts:arrowIifeBody`
5. `<module> -> coverage.ts:mapCb`                 (named function expr passed to native .map)
6. `coverage.ts:mapCb -> coverage.ts:mapCallbackBody`  (x3, same edge, dedup)
7. `<module> -> Widget:staticBlock`  (class static init block — NOT SPEC of transformer; expect this
   frame to be MISSING because static blocks aren't a handled node kind. If missing, the call inside
   should instead show as `<module> -> coverage.ts:staticBlockBody` directly.)
8. `Widget:staticBlock -> coverage.ts:staticBlockBody` (expected only if #7 exists)

main() body, in call order:
9.  `coverage.ts:main -> coverage.ts:exprArrow`            (expression-bodied arrow — PRIME SUSPECT,
    expect MISSING because transformer only rewrites arrows with ts.isBlock(body))
10. `coverage.ts:main -> coverage.ts:curried`               (outer curried call)
11. `coverage.ts:curried -> coverage.ts:curried:innerArrow` (returned inner arrow, also expr-bodied —
    expect BOTH curried and its inner arrow missing since neither has a block body)
12. `coverage.ts:main -> coverage.ts:gen` -- generator function CALL itself just constructs the
    iterator, does not run the body. No call edge for the construction call.
13. On first `g.next()`: generator body starts running up to first yield. Expected caller frame is
    `coverage.ts:gen` (if generators are instrumentable) calling `coverage.ts:callFromGen1`.
    KNOWN RISK: transformer wraps a `function*` body into a plain (non-generator) arrow function
    passed to `__stack.run` — arrows cannot contain `yield`. Expect this to be a HARD FAILURE
    (TypeScript compile error or runtime SyntaxError) unless proven otherwise by running it.
14. On second `g.next()`: `coverage.ts:gen -> coverage.ts:callFromGen2` (same caveat as #13).
15. `coverage.ts:main -> coverage.ts:asyncGen` -- construits async iterator, no call edge yet.
16. `await ag.next()` (#1): `coverage.ts:asyncGen -> coverage.ts:callFromAsyncGen1`, same generator-
    wrapping risk as #13, PLUS the async+generator combination doubles the surface area for failure.
17. `await ag.next()` (#2, after the internal `await delay(1)`): `coverage.ts:asyncGen -> coverage.ts:delay`,
    then continuation calls `coverage.ts:asyncGen -> coverage.ts:callFromAsyncGen2`.
18. `new Widget()` -> `coverage.ts:main -> Widget:constructor`
19. Inside Widget constructor, `super()` -> `Widget:constructor -> Base:constructor`
20. `Base:constructor -> coverage.ts:baseCtorBody`
21. After super() returns, field initializers run (fieldArrow assigned, no call — assigning an arrow
    is not invoking it, so NO edge here for fieldArrowBody itself)
22. Constructor body continues: `Widget:constructor -> coverage.ts:ctorBody`
23. `w[COMPUTED_KEY]()` -> `coverage.ts:main -> Widget:computedMethod` (computed name; the transformer's
    nameOf() falls back to the literal string '<computed>', so expect the label text to be
    "Widget:<computed>" rather than "Widget:computedMethod" — a naming defect, not a missing edge)
24. `Widget:<computed> -> coverage.ts:computedMethodBody`
25. `w.fieldArrow()` -> `coverage.ts:main -> coverage.ts:fieldArrow(anon-arrow-in-field-initializer)`.
    KNOWN RISK: a class-field arrow initializer is an ArrowFunction node, but is it visited as a
    top-level arrow by the transformer, or is the surrounding PropertyDeclaration itself unhandled
    (transformer never inspects PropertyDeclaration explicitly, but ts.visitEachChild still descends
    into it, so the inner ArrowFunction node should still be found and, if it has a block body,
    wrapped normally). Since `fieldArrowBody()` is called via an expression-bodied arrow
    `() => fieldArrowBody()`, expect this arrow to ALSO be missing per the expr-bodied-arrow hole (#9).
26. `Widget:fieldArrow -> coverage.ts:fieldArrowBody` (only if #25 survives; expect MISSING, folding
    into `coverage.ts:main -> coverage.ts:fieldArrowBody` directly)
27. `w.value` (getter read) -> `coverage.ts:main -> Widget:get value`
28. `Widget:get value -> coverage.ts:getterBody`
29. `w.value = 5` (setter) -> `coverage.ts:main -> Widget:set value`
30. `Widget:set value -> coverage.ts:setterBody`
31. `w.callPrivate()` -> `coverage.ts:main -> Widget:callPrivate`
32. `Widget:callPrivate -> Widget:#privateMethod` (private method call via `this.#privateMethod()`)
33. `Widget:#privateMethod -> coverage.ts:privateMethodBody`
34. `objLit.shorthand()` -> `coverage.ts:main -> coverage.ts:shorthand` (object-literal shorthand method)
35. `coverage.ts:shorthand -> coverage.ts:shorthandBody`
36. `objLit.longhand()` -> `coverage.ts:main -> coverage.ts:longhand(anon-fn-expr, object-literal
    property value)`
37. `...longhand -> coverage.ts:longhandBody`
38. `withDefault()` -> `coverage.ts:main -> coverage.ts:withDefault`.
    Default-parameter call `defaultParamSource()` -- SEMANTICALLY this should be attributed to
    withDefault (default values are evaluated during withDefault's own activation, per spec), giving
    `coverage.ts:withDefault -> coverage.ts:defaultParamSource`. KNOWN RISK: because the transformer
    only wraps the *body block*, not the parameter list, `defaultParamSource()` executes BEFORE
    `__stack.run` for withDefault has been entered — expect the WRONG caller to be recorded: it will
    look like it was called directly by whoever called withDefault (`coverage.ts:main ->
    coverage.ts:defaultParamSource`), attributing the call one level too shallow.
39. `dynFn()` (`new Function('return 1;')`) -- expected FUNDAMENTAL, uninstrumentable: it is compiled
    from a runtime string by the V8 Function constructor, never passes through our TS-source
    transformer. No edge should appear for it at all (known hole, not attempted to fix).
40. `eval('uninstrumentableTargetViaEval()')` -- expected FUNDAMENTAL for the same reason (eval'd
    string bypasses the transformer entirely); additionally the referenced function name is not even
    in eval's scope the way it's called here, so this call is expected to throw ReferenceError and be
    swallowed by the try/catch — no edge, and this is fine, it demonstrates the eval hole either way.
41. `rebound()` -> `coverage.ts:main -> coverage.ts:originalFn` (rebound is just an alias reference to
    the same instrumented function; expect this to work correctly since instrumentation lives on the
    function object itself, not on the call-site spelling)
42. `holder.method()` -> `coverage.ts:main -> coverage.ts:originalFn` (same reasoning as #41; method
    stored on a plain object and invoked as `obj.method()` should still resolve to the same
    instrumented function)

## Summary of pre-registered predictions (before running anything)
- Expression-bodied arrows (#9, #11, #25/#26 folded case): predicted MISSING.
- Curried arrows: predicted MISSING (both levels, same reason).
- Generators / async generators (#13/#14/#16/#17): predicted HARD FAILURE — arrow cannot host `yield`.
- Static init block (#7/#8): predicted the block-level frame is MISSING, calls fold to `<module>`.
- Computed method name (#23/#24): predicted edge is PRESENT but the label is wrong/degraded to
  `<computed>` instead of the real property name.
- Getter/setter/private-method/constructor/super/object-literal-methods/IIFEs/rebound-alias/
  object-stored-method: predicted CORRECT (all have real block bodies and are handled node kinds).
- Default-parameter call (#38): predicted PRESENT but WRONG CALLER (attributed one level too shallow).
- `new Function` / `eval` (#39/#40): predicted FUNDAMENTAL holes, by design, not fixable.
