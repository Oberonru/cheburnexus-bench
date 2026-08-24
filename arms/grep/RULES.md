# The grep arm — pre-registered rules

**Status: written before this arm was ever graded against the oracle.** These rules were fixed by
reading `arms/ARCHITECTURE.md` and `grader/EDGE_FORMAT.md`, then implementing and testing this
arm on a synthetic fixture only — it never saw the oracle, the corpus's real source, or a graded
number before this file was finished. If a later run's precision or recall looks surprising, the
fix belongs in a dated "deviations" entry below, never in a silent rewrite of the rule that produced
it.

## Why this arm exists, and why it must not be softened

`grep` is not a punching bag. It is the actual fallback an AI coding agent uses when it has no
compiler-grade index: it can locate every place a name is *spelled*, and nothing tells it which of
several same-named declarations a given call site resolves to. If the compiler-grade arm cannot
beat this by a wide margin, the whole premise of this polygon — that structural indexing is worth
having — is false, and that needs to show up in the numbers rather than be argued away.

Concretely, that means: no per-repository tuning, no ranking or filtering of candidates by
plausibility, no shortcuts borrowed from knowing what the oracle looks like. Every rule below was
decided from the task brief and the two contract documents, not from a result.

## Why pure Python instead of shelling out to `grep`/`ripgrep`

`version()` reports `"pure-python-1"`. The two-pass algorithm needs cross-line state (brace depth
for namespace/type tracking, a per-file ordered declaration list for "nearest declaration above",
and a repo-wide candidate registry built before any call site is resolved) that a shell pipeline of
independent `grep` invocations cannot hold without an ad hoc coordination layer in Python anyway —
at which point using literal `grep`/`ripgrep` for the pattern matching would only add a version
dependency on whatever happens to be installed on the machine running the polygon, for no gain in
what the arm can see. The regexes below are exactly what an agent shelling out to ripgrep would
also have to write; "pure-python-1" names the implementation choice, not a difference in what the
tool is allowed to know.

## The algorithm, as implemented

**Pass 0 — strip comments and string/char literals.** A single character-level state machine over
each file's full text (not line-by-line, so multi-line `/* */` comments and verbatim `@"..."`
strings are handled correctly). Every stripped character is replaced with a space, newlines are
kept as newlines, so line numbers — and therefore `caller_line` — never shift. Escape sequences
(`\"`, `\\`, doubled `""` inside a verbatim string) are honored so an embedded quote doesn't end the
literal early.

*Known gap:* interpolated strings (`$"...{expr}..."`) are treated as ordinary strings. The `{expr}`
inside is blanked out along with the rest of the literal, so a call written only inside a string
interpolation is invisible to both passes. This is a real miss, not a rounding error, and is left
unfixed deliberately — handling it correctly requires balancing braces inside a string, which is
exactly the kind of one-off special case that would make the tool less honestly "grep-shaped."

**Pass 1 — declarations.** Each file is scanned top to bottom while tracking a brace-depth stack.
Two patterns push scope: `namespace X.Y { ... }` (or the C# 10 file-scoped `namespace X.Y;` form,
handled as a special case with no brace) and any `class`/`struct`/`interface`/`record`/`enum`
declaration. Namespaces join with `.`; nested types join with `/`, per the key format in
`grader/EDGE_FORMAT.md` (`Namespace.Outer/Inner`). This is brace counting, not parsing — chosen
because it is what a grep-based tool can actually do, and the task brief calls it out as an
acceptable simplification.

A line is classified as a method-shaped declaration when, after the comment/string strip, it
matches `<attributes>? <modifiers>* <return-type>? <name> <generic-args>? ( <params> )`, where
`<name>` may itself be a dotted chain (`IDisposable.Dispose`, for explicit interface
implementations — see below). Constructors are `<modifiers>* <name>(...)` where `<name>` equals the
enclosing type's own name and no return type is present; `static` in the modifiers makes it
`.cctor`, otherwise `.ctor`. A bare `Name(...)` with no modifier and no return type in front of it
is rejected as a declaration — that shape is a call statement, not a method signature.

*Known gap — multi-line signatures.* This is line-based: a parameter list that wraps onto a second
line is not recognized as a declaration at all (grep, scanning line by line, would not connect the
two either). Rare in the corpus's formatting conventions, not fixed.

*Known gap — explicit interface implementations.* `void IDisposable.Dispose()` is captured with its
qualifier, then reduced to the bare `Dispose` (the last dotted segment) before being recorded — the
same simplification `grader/EDGE_FORMAT.md` names as an accepted, pre-documented limitation ("an
arm that reports the member as plain `GetEnumerator` will not match" the oracle's qualified key).
Not fixed, by design, to match the documented limitation rather than build special handling that no
other arm gets credit for.

**Generic arity on declaring types.** The oracle keeps a type's generic arity
(`Foo.Cache\`1::Get`), so an arm that silently omitted it would not be demonstrating a real grep
limitation — it would be introducing an artificial mismatch with the key format that has nothing to
do with what grep can see. `class Cache<T>` is right there in the text `TYPE_RE` already matches, so
the type parameter list is captured (`(?P<generics>...)`, immediately after the type name) and
counted by a top-level comma split (`generic_arity()`), then folded into that type's path segment as
`` Name`N ``. Nested generic types get their own arity per segment (`Outer\`1/Inner\`2`), matching
the oracle's per-segment convention. **This is computed only from the declaration itself, never from
a use site** — `new Cache<int>()` says nothing about how many parameters `Cache` was *declared*
with, only how many were supplied at this particular call, and resolving from a use site is exactly
the kind of type inference this baseline does not attempt anywhere else. A constructor is always
spelled without the backtick in source (`public Cache(...)`, never `` public Cache`1(...) ``), so
constructor detection compares against the undecorated name — the two forms are tracked separately
per type-stack entry (`type_stack` holds `(plain_name, display_name, depth)`;
`current_simple_type()` returns the plain form for ctor matching, `current_type_key()` builds the
path from the display form).

Generic **method** arity (as distinct from the declaring *type's* arity above, e.g. a method
declared `T Get<T>(...)`) is explicitly out of scope — the task brief's own example
(`Foo.Cache\`1::Get`) is about type arity, and extending the same treatment to method type
parameters was judged a separate, unrequested feature rather than a fairness fix, so it was left
alone per the boundary drawn when this rule was added (see deviations log).

*Known gap — pointer returns, indexers, destructors, local functions.* Unsafe pointer return types
(`void* Foo()`), indexers (`this[int i]`), and destructors (`~Foo()`) are not recognized as
declarations (the regex has no token for `*`-suffixed types, `this` is excluded as a control word,
and `~` isn't in the name-start character class). Local functions declared inside a method body
*are* picked up as their own declaration (same detection rule applies at any brace depth), attached
to the enclosing type rather than nested inside their host method — a real simplification, but one
that keeps the caller-attribution rule below simple and matches how a human skimming with grep would
plausibly log it too.

**Pass 2 — call sites.** Re-scans the same stripped files for `Name(` occurrences with
`\b([A-Za-z_]\w*)\s*\(`. For each call site, the caller is **the nearest declaration at or above
that line, within the same file** — a line-number lookup (`bisect_right`), not brace matching. This
is deliberately the same "scan upward" shape the task brief specifies, distinct from the brace
tracking used in Pass 1: an agent doing this by hand would scroll up from a call site to the closest
`public ... Name(...)` line, not re-derive scope from braces.

A call site is excluded from candidate-matching, per the task brief, when its name is one of
`if while for foreach switch catch lock using return nameof typeof sizeof new` — plus, as a
documented deviation, `checked unchecked fixed` (three more C# keywords with the identical `word(`
shape the brief's list didn't happen to enumerate) and `this base` (constructor-chaining calls,
which never have a real declaration to match anyway, so excluding them is a no-op in practice but
keeps the exclusion list honest about what it is doing). The declaration's own `Name(` on its own
signature line is separately excluded by character offset, so a method is never recorded as calling
itself by virtue of its own declaration.

**Candidate resolution — the whole point of this arm.** Grep cannot resolve `Name(` to one
declaration; it can only tell you every declaration in the repository sharing that name. So this
arm emits **one edge per candidate**: for a plain call, one edge to `Type::Name` for every declared
method named `Name`, across every type in the repo (not just the current file — the candidate
registries are built in a first full pass over every file before any call site is resolved, so
results never depend on file processing order). For `new Foo(...)`, one edge to `Type::.ctor` for
every type whose simple name is `Foo`, regardless of whether that type had an explicit constructor
in source — C# gives every type an implicit parameterless constructor, and the oracle's IL sees
that too. Matching is by *simple* name only; a qualified `new My.Ns.Foo(...)` is resolved exactly
like a bare `new Foo(...)` — this arm does not attempt namespace-qualified resolution even where it
would technically be possible, because doing so only for `new` and not for plain method calls would
be an inconsistent, hand-picked improvement to precision that a real grep-shaped agent has no
principled reason to apply selectively.

*Known gap — target-typed `new()`.* `Foo x = new();` (C# 9+) has no identifier for this arm to
resolve at all; `new(` is in the keyword-exclusion list (matching the task brief's own `new(`
entry) and produces nothing. A grep-based agent genuinely cannot resolve this without tracking the
left-hand-side type, which is exactly the kind of inference a compiler frontend does and a grep
baseline should not quietly reach for.

## Determinism

No randomness, no filesystem-order dependency (`armkit.source_files` yields a sorted walk; the
candidate registries are fully built before Pass 2 starts, so results do not depend on which file
is visited first); `armkit.write_edges` sorts and dedups the output. Running twice on the same
checkout produces byte-identical output.

## Deviations log

- **2026-08-23 — generic arity added to declaring-type keys.** The first graded run against
  `corpus/serilog` `without-tests` (precision 0.209, recall 0.515) used a version of this arm that
  did not compute arity at all, described at the time as a "known gap." That framing was wrong: the
  oracle's key format documents arity as part of the key, and grep can plainly see `class Cache<T>`
  and count one type parameter — nothing about the technique prevented it. Omitting it was an
  unforced mismatch with the documented key format, not a grep limitation, so it was fixed rather
  than left as a footnote. Scope was deliberately kept narrow: only declaring-type arity, computed
  only from the declaration; no generic-method arity, no receiver resolution, no narrowing of the
  candidate fan-out (the fan-out is the mechanism this whole arm exists to demonstrate and was not
  touched). Re-run once after the change, not iterated on. See the report accompanying this change
  for the before/after numbers.
- **2026-08-23 — a separate, pre-existing bug was found and deliberately NOT fixed in the same
  pass.** While extending the test fixture to cover arity, `return Get(key);` (an ordinary early
  return of a call's result) was discovered to be misclassified by `classify_decl()` as *declaring*
  a method named `Get` — the code checks whether the captured `name` is a control-flow keyword but
  never checks whether the captured `ret` token is one (`return` reads as a plausible one-word
  return type). This predates the arity change, already contaminated the 0.209/0.515 numbers above,
  and is a plausibly significant source of spurious declarations/candidates on real code where
  `return someCall();` is common. It was left unfixed here specifically so the arity change could be
  measured in isolation, per instruction; the test fixture was written to avoid triggering it
  (`testdata/repo/src/Cache.cs` uses `var value = Get(key); return value;`) rather than fixing the
  underlying bug. This is flagged for a follow-up pass, not fixed as a drive-by.
