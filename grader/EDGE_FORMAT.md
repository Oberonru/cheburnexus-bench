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
of it**, using the override map the oracle emits alongside the edges. The reduction runs on the arm
side only; the oracle's row is never rewritten.

## Calls written inside lambdas, iterators and async methods

The compiler moves that code into generated types, so IL reports a generated caller like
`<Write>b__4_0` or `<Emit>d__7`. The call is real and every arm should find it, so the edge is
**not dropped**: the caller is remapped to the enclosing user method recoverable from the mangled
name. An edge whose caller cannot be remapped confidently goes to a separate cell and counts for
nobody.

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
