# The edge contract

Every arm — the oracle included — writes JSON Lines, one call edge per line. The grader reads
nothing else. An arm that cannot express a field leaves it out; it is never invented.

```json
{"caller": "Serilog.Core.Logger::Write", "caller_file": "src/Serilog/Core/Logger.cs",
 "caller_line": 412, "callee": "Serilog.Core.Sinks.SafeAggregateSink::Emit", "op": "callvirt"}
```

| field | required | meaning |
|---|---|---|
| `caller` | yes | the enclosing method of the call site, as `Namespace.Type::Method` |
| `callee` | yes | the called method, same shape |
| `caller_file` | no | repo-relative, forward slashes |
| `caller_line` | no | 1-based |
| `op` | oracle only | `call` / `callvirt` / `newobj` / `ldftn` / `calli` |

The oracle emits extra flag fields (`VirtualDispatch`, `CallerCompilerGenerated`, `NoDebugInfo`, …).
Arms never do. The grader reads the flags only from the oracle side.

## Identity: how two edges are decided to be the same

This is the decision that most affects the result, so it is fixed here rather than discovered later.

**A method key is `Namespace.Type::Method`.** Return type and parameter types are *not* part of the
key, and overloads therefore collapse into one node.

Why: the arms disagree about overload resolution far more than they disagree about *whether a call
exists*, and this benchmark is about the second question. A strict key that carried full parameter
types would mostly measure how well each tool reconstructs C# overload rules — a real question, but
a different one, and one where the oracle's IL-level types (`System.Int32`) do not line up with what
a source-level tool sees (`int`, `var`, an inferred generic).

**What we give up, stated plainly:** if a type has `Log(string)` and `Log(Exception)`, an arm that
emits the wrong one still scores a hit. This inflates every arm equally, ours included, and it must
be reported as a limitation next to the numbers, never omitted. A strict-key cell is computed as a
secondary result so the size of the effect is visible instead of assumed.

**Generic arity** is kept when the source makes it unambiguous (`Foo.Cache\`1::Get`), because the
open and closed forms are different types and merging them would lose real structure.

## Virtual dispatch

IL names the *declared* method at a `callvirt` site; the runtime target may be an override. A
source-level tool often names an implementation instead. Neither is wrong.

The grader therefore accepts an arm's edge when its callee is the declared method **or any override
of it**, using the override map the oracle emits alongside the edges (`--overrides`). It covers
base-class virtuals, explicit interface implementations, and implicit ones — matched by name and
parameter count, since the key drops parameter types anyway. The reduction runs on the arm side
only; the oracle's row is never rewritten.

An arm's edge is also *classified* by the declaration it answers for, not by the implementation it
names. Otherwise a first-party class implementing `IDisposable` would drag a standard-library call
into the primary cell and lose precision there — punishing the arm for resolving correctly.

**Size of the effect, measured on the corpus** with a synthetic arm that names implementations
everywhere: precision 0.759 → 0.943 and recall 0.884 → 0.998 on Serilog, 0.720 → 0.875 and
0.858 → 0.995 on FluentValidation. Published without the map, a perfectly correct arm would have
scored around 0.72–0.76 — indistinguishable from the tree-sitter tools this benchmark exists to
tell apart.

### Explicit interface implementations

IL names them `Namespace.IContract<T>.Method`, and the key keeps that qualification (minus the
generic arguments): `App.Box::System.Collections.Generic.IEnumerable.GetEnumerator`. An arm that
reports the member as plain `GetEnumerator` will not match. This is a known limitation rather than
a decision we are confident in; it is rare, and it is recorded here so a reader can see it instead
of discovering it in a number.

## Calls written inside lambdas, iterators and async methods

The compiler moves that code into generated types, so IL reports a generated caller like
`<Write>b__4_0` or `<Emit>d__7`. The call is real and every arm should find it, so the edge is
**not dropped**: the caller is remapped to the enclosing user method recoverable from the mangled
name. An edge whose caller cannot be remapped confidently goes to a separate cell and counts for
nobody.

### A local function nested inside another local function or lambda remaps to the OUTERMOST one

Roslyn nests the mangled names when scopes nest: a local function written inside another local
function, or an async local function's own state machine, produces something like
`<<TryConvertEnumerable>g__MapToDictionaryElements|15_0>d`, where `TryConvertEnumerable` is the
user-written outer method and `MapToDictionaryElements` is the local function actually containing
the call. The remap (`enclosing_user_method` / `_extract_enclosing` in `grade.py`) resolves this
to `TryConvertEnumerable` — the OUTERMOST enclosing method — not `MapToDictionaryElements`, the
nearest one.

This is a consequence of how the marker scan works (leftmost `>[bdgf]__` in the string, which for
nested mangling is always the outer scope's own marker, scanned first), not a separate rule
written for this case. It was true under the flat regex this replaced too, so it is not a G2
regression — it is simply undecided territory that `EDGE_FORMAT.md` never stated a rule for
before now.

**This is a stated decision, not an accident, and behaviour is unchanged**: when several nested
enclosing methods are candidates, the OUTERMOST one wins. The alternative (mapping to the nearest
enclosing method) is not obviously more correct — a source-level tool reading the call site is at
least as likely to attribute it to the outer method it can see written in the file as to a nested
local function — so this is recorded rather than "fixed" without evidence either choice serves the
comparison better.

## The comparable set

Fixed in the pre-registration before any run. The primary metric counts an oracle edge only when:

- the callee is declared in a first-party assembly of the pinned corpus;
- the op is `call`, `callvirt`, `newobj` or `ldftn`;
- the callee is not a property accessor (`get_` / `set_`);
- the callee is not the enumerator protocol (`GetEnumerator` / `MoveNext` / `get_Current` /
  `Dispose` emitted by `foreach`);
- the callee is not compiler-generated.

Everything excluded is still counted and published as its own cell. On a calibration run these
categories were ~90% of all IL edges, which is exactly why the boundary is written down in advance:
moving it moves the denominator by roughly ten times.

## What an arm could not resolve

Precision and recall cannot separate two very different behaviours. A tool that **saw a call site
and could not resolve the target**, and a tool that **never noticed the site**, lose the same recall
point — but only one of them told the truth about its own limits.

So an arm may write a sidecar next to its edges, `<edges>.jsonl.coverage.json`:

```json
{"is_exact": false, "reason": "unresolved_call_sites", "unresolved_call_sites": 412}
```

The runner prints it as its own **`declared`** column and never folds it into recall. A blank cell
means the arm made no statement — which is not the same as having nothing unresolved.

This exists because our engine already draws the distinction internally
(`ArchitectureAnalyzer.Core/Model/CallGraphCoverage.cs`: `is_exact`, `reason`,
`unresolved_call_sites`, `budget_exhausted`) and hands it to the model it serves, so that the model
can decide what to do with a gap. The edge contract was discarding exactly that, which would have
rendered a deliberately honest lower bound as an ordinary hole.

⚠ **It is not a scoring adjustment.** A declared unresolved site earns no credit: an unmatched edge
stays unmatched, and recall is computed exactly as before. All the column does is let a reader see
whether a gap was admitted or concealed. It was added while our own column was still blank, so it
cannot be a category invented to rescue a number — and any arm may fill it, including theirs.
