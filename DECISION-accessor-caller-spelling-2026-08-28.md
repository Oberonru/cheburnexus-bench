# Decision — a caller that is a property accessor

Written **before** any code was changed and before anything was re-graded, because this is a
conflict between two goals that are both right, and the resolution has to be argued rather than
scored.

## The conflict

A call written inside a property, indexer or event accessor is keyed by the engine as the member a
**human wrote**:

    FluentValidation.ValidatorConfiguration::PropertyNameResolver

IL — and therefore this polygon's answer key — names the **accessor**:

    FluentValidation.ValidatorConfiguration::set_PropertyNameResolver

Both denote the same member. The grader compares `(caller, callee)` strings, so every such edge is
counted as junk. Nineteen edges across the three corpora carry an accessor tag today, and after the
corpus-scope fix (e8) six of the ten residual junk pairs in serilog/with-tests are exactly this.

Neither side is a bug:

- The product's spelling is **deliberate and documented**. `CallKeyBuilder.MemberKey`: *"Accessors
  are NOT split into separate get/set nodes: the call graph answers 'which member calls this', and a
  property is one member — splitting would invent a distinction the reader never asked about."*
  `mcp/FACT_CONTRACT.md` says the same. A user asking `who_calls` gets the member they can go and
  look at, never a name the compiler synthesised.
- The oracle's spelling is **the compiled truth**. IL has no property, only two methods.

## The decision

**Keep the product's key exactly as it is. Translate in the ARM, at the boundary where the arm
already expresses the engine's key space in the oracle's vocabulary.**

Three reasons, in the order they carry weight:

1. **The arm already does exactly this, for constructors.** `arms/cheburnexus/run.py`'s
   `ClassIndex.contract_key` renames a call whose method-name segment matches a class's own
   constructor to `.ctor`, because the engine keys a constructor by the class's simple name and IL
   does not. Retargeting `PropertyNameResolver` to `set_PropertyNameResolver` is the same class of
   translation at the same boundary function — a second instance of an existing pattern, not a new
   seam and not a special case invented to move a number.
2. **The translation reads a recorded fact; it does not infer one.** The engine already tags each
   call with the accessor it was written in — `CallEdge.Accessor` ∈ {get, set, init, add, remove} —
   precisely so the distinction exists without a second key space. The arm therefore has the datum
   in hand; it is not guessing which accessor a call sat in.
3. **It changes no shipped fact and no product behaviour.** `who_calls`, `get_method` and the
   sidecars are untouched. The alternative — changing the engine's key — would make the product
   worse to make a benchmark number better, which is the exact inversion this polygon exists to
   prevent.

## The objection, and the answer

> *If the arm translates and the product does not, the polygon measures something you do not ship.*

It measures whether we found the right CALL, which is what it is for. A spelling difference that is
mechanically derivable from data the engine already publishes is not a knowledge gap — the fact
"this call is made by this member, in its setter" is in the shipped output either way. And the
translation is not hidden: `arms/cheburnexus/run.py` is published in this repository, so a sceptic
reads exactly what it rewrites and can re-grade our published rows without it.

Leaving it unfixed is not the neutral option. It reports edges that are **correct** as wrong, which
makes the published number less true, not more cautious.

## What is translated, and what is deliberately NOT

Translated — `accessor + "_" + memberName`, with one special case:

| accessor tag | IL name | note |
|---|---|---|
| `get` | `get_X` | includes an expression-bodied property, which the resolver already tags `get` |
| `set` | `set_X` | |
| `init` | **`set_X`** | ⚠ NOT `init_X`. Verified empirically against a compiled `{ get; init; }` property (`PropertyInfo.SetMethod.Name` → `set_X`). ⛔ `RoslynMethodParser.ParseAccessor` synthesizes `init_X` for its own in-memory, never-serialised list; that convention must NOT be reused here — it would ship a new bug on init-only properties |
| `add` / `remove` | `add_X` / `remove_X` | |

**NOT translated — an INDEXER.** Our key is `this[paramTypes]` and carries no item name at all; IL
uses `get_Item`/`set_Item` by default, but `[IndexerName("…")]` changes it and the engine records
nothing about that attribute. Translating would mean assuming a name we did not read, and this
engine does not guess. Indexer-accessor callers therefore stay junk against the ruler, and that is
now a disclosed limitation rather than an unexamined one. None occur in the current corpora.

**Nothing to translate on the `--project` path.** The accessor tag exists only on the semantic
(`--solution`) path; the syntactic builder does not walk property, indexer or event bodies at all,
so it has no such edge to spell either way. That is a larger, separate gap in the syntactic path and
is recorded as such — it is not created or hidden by this decision.

## Why a collision cannot happen

Retargeting to `set_X` could in principle collide with a real method literally named `set_X` on the
same type. It cannot: C# reserves those names (CS0082, *"Type already reserves a member called
'set_X'"*), so no compilable source declares both.

## Where this is disclosed

`grader/EDGE_FORMAT.md` currently lists this mismatch as a limitation counted against cheburnexus
(disclosed 2026-08-28, after its cost was known — that lateness is recorded there and stands). That
entry is rewritten to say what is translated, what is not, and why.
