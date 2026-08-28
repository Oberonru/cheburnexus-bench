# Decision — a member that explicitly implements an interface

Written **before** any code was changed and before anything was re-graded, following the discipline
of `DECISION-accessor-caller-spelling-2026-08-28.md`.

That earlier decision was a genuine conflict: two spellings, both correct, and the argument decided
which side translated. **This one is not a conflict. It is a defect in the product**, and the
benchmark only happens to be the instrument that found it.

## What C# does

    interface IFoo { void Bar(); }

    class Widget : IFoo {
        public void Bar()   { A(); }   // an ordinary method
        void IFoo.Bar()     { B(); }   // a DIFFERENT member, explicitly implementing IFoo
    }

These are two members. They have separate bodies, separate accessibility, and are invoked
differently (`this.Bar()` reaches the first; only an `IFoo`-typed reference reaches the second). IL
names them `Bar` and `Fixture.IFoo.Bar`.

## What our engine does today — measured, not read

`RoslynMethodParser.Parse` (`RoslynMethodParser.cs:18`) and `CallKeyBuilder.MethodKey`
(`CallKeyBuilder.cs:20-24`) both take the name from `node.Identifier.Text` alone.
`node.ExplicitInterfaceSpecifier` is never read — **zero occurrences of the string
`ExplicitInterface` anywhere in the repository.** So both members key as `Widget::Bar`.

Run on the fixture above, with the shipped engine
(`_bench-engine-methodgroup-20260828-043533`), semantic mode:

- **`methods.json` contains ONE `Fixture.Widget::Bar()`.** The other member is gone — not merged,
  not demoted, not counted: absent, with no warning and no counter.
- **`calls.json` reports `Widget::Bar → B()`.** The call to `A()` exists in the source and appears
  nowhere in the output. `A()` has zero incoming callers.
- **Reversing the declaration order flips the survivor**, exactly: `Widget::Bar → A()`, and `B()`
  becomes the orphan. `SemanticCallsResolver.cs:113-114` does `result[kv.Key] = kv.Value` —
  last-walked wins.
- The syntactic `--project` mode behaves identically on the caller side (same key-building code),
  and is additionally blind to the interface-dispatched call site.

And the worst of it is not the loss. On the **callee** side Roslyn resolves correctly:
`this.Bar()` targets `Widget::Bar`, `f.Bar()` targets `IFoo::Bar`. So in the original declaration
order the graph answers the question *"what does `Widget::Bar` call?"* with `B()` — confidently,
with no caveat, while the true answer for the member `this.Bar()` reaches is `A()`. **A silent loss
turned into a fabricated fact**, and which one you get depends on the order two methods happen to
appear in the file.

This is the same defect class already fixed twice in this key space and documented in
`CallKeyBuilder`'s own XML docs: instance vs static constructor (`CtorKey`), and overloaded indexers
(`IndexerKey`). Both were fixed by making the key carry the distinction. Nothing new is being
invented here; a third instance of a known bug is being closed.

## The decision

**Fix the ENGINE. The member's name becomes the source text of its explicit interface specifier,
verbatim, plus `.`, plus the identifier.** No translation in the arm.

    void IFoo.Bar()                            →  IFoo.Bar
    Task IValidationRuleInternal<T>.Validate() →  FluentValidation.IValidationRuleInternal<T>.Validate

Four reasons, in the order they carry weight.

### 1. Unlike the accessor case, the product's key is WRONG, not merely differently spelled

The accessor decision turned on the product's spelling being deliberate, documented and useful:
`who_calls` should answer with the member a human can go and look at. That argument does not
transfer. Here the product's key does not name a member a human wrote — it names *two* members with
one string, keeps one at random, and attributes its body to the other. Changing the engine to make a
benchmark number better would be the inversion this polygon exists to prevent; changing it because
the shipped answer is false is the polygon working as intended.

### 2. Only the engine can fix it — the arm demonstrably cannot

Two of FluentValidation's unmatched rows (`AbstractValidator\`1::IValidator.Validate` and
`::ValidateAsync`) sit on a type that has **both** a public `Validate` overload and an explicit
`IValidator.Validate`. The arm strips the parameter signature at
`arms/cheburnexus/run.py:198-209` (`method_sig.split("(",1)[0]`) before `contract_key` ever sees a
name, so the two are already one entry by the time any arm-side rewrite could run. No string
rewriting in the arm can separate members the engine already collapsed. The engine can, because it
stops collapsing them.

### 3. "What the human wrote" and "what IL records" are the SAME STRING here

The C# compiler builds the metadata name of an explicit implementation from the *source text* of the
specifier as written. The polygon's own answer key proves it: one FluentValidation row is spelled

    CollectionPropertyRule`2::global::FluentValidation.IValidationRuleInternal<T>.Validate

— `global::` and all, because the generated source that declared it wrote `global::`. So taking the
syntax verbatim is simultaneously the product-honest choice (we report the text the reader will find
in the file) and the oracle-exact one. There is no second key space and nothing for the arm to
translate. Verified, not assumed: feeding our prospective key through the grader's own `method_key`
yields the oracle's normalized string byte-for-byte — the grader's `strip_generic_arguments` absorbs
the `<T>` symmetrically on both sides, so the arm needs no generic handling either.

### 4. It is the cheapest correct place

`MethodsSidecarBuilder.BuildKey` derives its key from `MethodModel.Name`, so fixing the parser
carries the methods sidecar along for free. `CallKeyBuilder.MethodKey` must be changed in lockstep
with the identical rule, because `MethodQuery.cs` documents the two sidecars' keys as byte-identical
for the methods↔calls join — the two-ends contract that has already bitten this project once.

## What is deliberately NOT done

- **Overloads are still not separated on the caller side.** The arm drops the parameter signature
  from a caller key, so a public `Validate(T)` and a public `Validate(IValidationContext)` remain one
  entry there. This decision separates *explicit interface implementations* from ordinary members; it
  does not claim to separate ordinary overloads from each other. Named so the next reader does not
  mistake one for the other.
- **The callee side is untouched — measured, not assumed.** Zero oracle rows in any of the four cells
  carry an explicit-interface spelling in the callee position, matched or unmatched. The mechanism is
  structural: such a member can only be invoked through an interface-typed reference, so IL records
  the *interface's* method as the target. The qualified spelling exists only as a declaration.
- **`Visibility` is left as it is.** An explicit implementation carries no accessibility modifier and
  we report it as `private`. IL marks it `private final virtual`. Close enough to be out of scope
  here, and changing it would be a separate, unargued fact.

## The measured size — and what this fix cannot reach

Against `results/2026-08-28-accessor-caller`, primary cell only, oracle rows we do not match whose
caller is an explicit interface implementation:

| cell | such rows | reachable | our junk edges that become matches |
|---|---|---|---|
| Polly / without-tests | 0 | 0 | 0 of 10 |
| FluentValidation / without-tests | 43 | **24** | 21 of 22 |
| serilog / without-tests | 6 | 2 | 2 of 2 |
| serilog / with-tests | 6 | 2 | 2 of 4 |

**17 of FluentValidation's 43 are unreachable by any spelling fix**, and this is measured, not
estimated: they are declared in
`obj/Release/net8.0/Zomp.SyncMethodGenerator/…/CollectionPropertyRule{T,TElement}.ValidateAsync.g.cs`,
a source-generator output that exists inside the oracle's full `dotnet build` and **does not exist in
the corpus checkout at all** (`find -iname '*.g.cs'` → zero hits under both `corpus/FluentValidation`
and the arm's `.work` directory). Our engine sees the `[CreateSyncVersion]` attribute on the async
method and no generated sync member, which is the correct reading of the files present. Those rows
belong to a different, honest category — *the answer key was built from a compilation we do not
have* — and must not be counted toward this fix's payoff. A further 4 of serilog's 6 sit in the
separate, still-undiagnosed "caller never appears in our output at all" bucket.

⛔ **No score is forecast in this document.** The engine change un-collapses members across all three
corpora at once — including Polly, which has zero explicit-interface rows in the answer key but may
still have been losing edges to the same overwrite. Per
`lesson-size-a-forecast-by-running-the-mechanism`, the forecast will be computed by building both
binaries and diffing the raw edge sets, and pre-registered in
`PREREGISTRATION-e10-explicit-interface-impl-2026-08-28.md` before the grader is run.

## The impartiality test does not apply

As in e9, this change raises only our arm by construction. It is recorded as such **before** the run,
and the case rests on the argument above — a shipped answer that is false and order-dependent —
not on the table that follows it.
