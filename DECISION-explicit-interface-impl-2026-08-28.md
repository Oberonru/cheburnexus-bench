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

## ⚠ Correction, made before any code was written

This document first argued that the metadata name is the **source text** of the specifier, so that
"what the human wrote" and "what IL records" would be the same string. **That was wrong, and the
error was caught by checking the corpus instead of trusting the reasoning.** FluentValidation's
source writes `IValidator.Validate`; the answer key says `FluentValidation.IValidator.Validate`.
Serilog writes `IEnumerable.GetEnumerator`; the key says
`System.Collections.IEnumerable.GetEnumerator`.

Probed on metal — a net8.0 assembly compiled and its metadata names read back by reflection
(`scratchpad/ilprobe`), a class in namespace `Probe.Inner` implementing `Probe.IFoo`:

| written in source | metadata name |
|---|---|
| `void IFoo.Bar()` | `Probe.IFoo.Bar` |
| `void Probe.IFoo.Qux()` | `Probe.IFoo.Qux` |
| `void Alias.Quux()`, after `using Alias = Probe.IFoo;` | `Probe.IFoo.Quux` |
| `void global::Probe.IFoo.Baz()` | **`global::Probe.IFoo.Baz`** |
| `IEnumerator IEnumerable.GetEnumerator()` | `System.Collections.IEnumerable.GetEnumerator` |
| `void IGen<int>.G(int)` | `Probe.IGen<System.Int32>.G` |
| `int IFoo.this[int]` | `Probe.IFoo.get_Item` (property `Probe.IFoo.Item`) |

**The rule the data supports:** the name is the interface's FULLY-QUALIFIED name — aliases expanded,
generic arguments written out with fully-qualified type names — plus `.` plus the member. A `global::`
written by hand is the one thing carried over verbatim, which is why the odd row in the answer key has
it: that member comes from generated source that wrote `global::`, not from any qualification rule.

The consequence is structural: **the qualified name is NOT derivable from syntax.** `IFoo` is not
`Probe.IFoo` until a symbol says so. And the arm cannot recover it either — `ClassModel.Interfaces`
carries `FluentValidation.IValidator<T>` fully qualified but `IEnumerable<...>` unqualified in the
same list, i.e. exactly the serilog case is missing.

## The decision

**Fix the ENGINE, in two separate pieces, because two different questions are being answered.**

**(1) The key carries the SOURCE-TEXT qualifier**, in every mode and both sidecars:

    void IFoo.Bar()                            →  IFoo.Bar
    IEnumerator IEnumerable.GetEnumerator()    →  IEnumerable.GetEnumerator

This is what actually fixes the product defect. It un-collides the two members, so neither is dropped
and neither is credited with the other's calls. It needs no semantic model, so
`CallKeyBuilder.MethodKey` (syntax) and `MethodsSidecarBuilder.BuildKey` (parsed model) still produce
byte-identical strings, and the semantic `--input` path and the syntactic `--project` fallback still
agree — the invariant `CallKeyBuilder`'s own class doc states, kept rather than weakened. It is also
the right answer for a reader: `who_calls` names the member as it is written in the file they will
open.

**(2) The exact compiler name is RECORDED as a separate fact**, not folded into the key:
`CallSidecarEntry.MetadataName`, written only where a semantic model exists and only when it differs
from the key's own member segment. The arm reads that fact and spells the caller the oracle's way.

Reasons, in the order they carry weight.

### 1. The product's key is WRONG, not merely differently spelled

The accessor decision (e9) turned on the product's spelling being deliberate, documented and useful.
That argument does not transfer. Here the key does not name a member a human wrote — it names *two*
members with one string, keeps one at random, and attributes its body to the other. Changing the
engine to make a benchmark number better would be the inversion this polygon exists to prevent;
changing it because the shipped answer is false is the polygon working as intended.

### 2. Only the engine can fix it — the arm demonstrably cannot

Two of FluentValidation's unmatched rows (`AbstractValidator\`1::IValidator.Validate` and
`::ValidateAsync`) sit on a type carrying **both** a public `Validate` overload and an explicit
`IValidator.Validate`. The arm strips the parameter signature at
`arms/cheburnexus/run.py:198-209` (`method_sig.split("(",1)[0]`) before `contract_key` sees a name,
so the two are already one entry by the time any arm-side rewrite could run. No string rewriting in
the arm can separate members the engine already collapsed.

### 3. Splitting the two pieces is what keeps the invariant

Putting the fully-qualified name straight into the key would have been the shorter patch and would
have cost two invariants at once: the semantic and syntactic modes would spell the same member
differently, and the methods sidecar — built from a syntax-parsed `MethodModel`, with no symbol in
reach — could no longer produce the key the calls sidecar does, breaking the methods↔calls join
`MethodQuery.cs` documents as byte-identical. Keeping the key syntactic and recording the compiler's
name beside it costs one sparse field and keeps both.

### 4. The translation reads a recorded fact, exactly as e9 required

`CallEdge.Accessor` was added so the arm could re-spell an accessor caller without inferring
anything. `MetadataName` is the same move for the same reason: the arm copies a string the compiler
produced, it does not reconstruct a namespace it guessed. Null means no signal — the syntactic
fallback has no symbol and says nothing rather than inventing a qualification.

### 5. It generalises, and that is a bonus rather than the argument

A recorded metadata name is also the exact answer for `op_Implicit` (work item 3) and for the
accessor spelling e9 currently derives in the arm. ⛔ Neither is folded into this change; noted only
so the next reader sees that the arm's translation table has a principled end state.

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
