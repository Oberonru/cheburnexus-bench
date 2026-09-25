# The repowise arm — discovery notes and known limitations

**Status: written from reading the reference clone and the pinned package's own source, before
this arm was ever graded against the oracle.** If a later run's precision or recall looks
surprising, the fix belongs in a dated addendum below, never in a silent rewrite of a decision
recorded here.

## What repowise is

`repowise` (PyPI, AGPL-3.0-or-later) is a tree-sitter-based "codebase intelligence layer": it
parses a repository into a graph of files and symbols (`packages/core/src/repowise/core/ingestion`),
persists it to a SQLAlchemy/Alembic-managed store, and generates wiki documentation from it. It has
real C# support: a dedicated grammar spec (`languages/specs/csharp.py`), a heritage/inheritance
resolver, a member-reads extractor, and a three-tier call resolver
(`ingestion/call_resolver.py`) shared across every language it supports.

## Route chosen: read `graph_nodes` / `graph_edges` in the SQLite store directly

Three routes were considered:

1. **`repowise export`** (`packages/cli/src/repowise/cli/commands/export_cmd.py`) — exports wiki
   *pages* (markdown/HTML/JSON) or a Structurizr architecture-model DSL. Neither carries individual
   call edges: the JSON export's richest per-item fields are `page_id`/`title`/`content`/hierarchy
   columns for a wiki page, and Structurizr export operates at the C4 component level (one box per
   directory), not per-method. **Ruled out**: no call graph in the output at all.
2. **MCP server** (`repowise serve` / `repowise-cli` MCP tools) — exposes the same
   wiki/decision/dead-code surface interactively for an agent session; still no bulk call-edge
   export, and scripting an MCP client for a one-shot bulk dump is strictly more moving parts than
   reading the store the server itself reads from.
3. **The SQLite store directly** — `graph_nodes` and `graph_edges` (see below) hold exactly the
   call graph the whole product is built on: every wiki page, every dead-code finding, and the MCP
   tools all read out of these same two tables. This is not a side channel; it is the one place in
   the product where "which calls does repowise believe exist" is actually recorded in per-edge
   form. **Chosen.**

This also happens to be the *richest* available output — the fairness rule in the task brief. The
wiki markdown only ever names a handful of "key relationships" in prose per page; the graph tables
hold every edge the resolver produced, including ones no wiki page mentions.

### Exactly which table/columns are read

Schema definitions: `packages/core/src/repowise/core/persistence/models.py` (pinned reference
clone, SQLAlchemy declarative models — the arm reads the *SQLite file* these models describe, not
the ORM layer itself; `run.py` uses the stdlib `sqlite3` module with raw SQL so it never needs
`repowise` importable in its own process).

**`graph_nodes`** (`models.py:212`), filtered `node_type = 'symbol'`:
- `node_id` (text) — repowise's own symbol identifier, `"<rel_path>::<name>"` or
  `"<rel_path>::<parent_name>::<name>"` (built in `ingestion/parser.py`, ~L858-864 of the pinned
  clone). This is the join key `graph_edges` uses for its endpoints.
- `file_path` (text) — the file the symbol is declared in, repo-relative, forward-slash (repowise
  builds paths with `pathlib.Path.as_posix()`).
- `name` (text) — the symbol's own bare name (the method/function name, never namespace- or
  class-qualified).

**`graph_edges`** (`models.py:299`), filtered `edge_type = 'calls'`:
- `source_node_id` / `target_node_id` (text) — caller/callee, matching `graph_nodes.node_id`.
- `call_lines_json` (text, JSON array of ints) — every source line the resolver found a call site
  on for this caller→callee pair (multiple call sites between the same two symbols collapse onto
  one graph edge; `resolve_calls` in `ingestion/graph/_resolvers.py:~575` unions the line list
  rather than duplicating the edge). This arm takes `min(call_lines)` as `caller_line` — the
  earliest call site, picked deterministically since the contract wants one line per edge and
  repowise itself has already collapsed same-pair call sites onto one row.

Other `edge_type` values seen in the ingestion source (`imports`, `references`, `reads`,
`defines`, `has_method`, `extends`/`implements`, `dispatches_to`, `framework`, `type_use`,
`co_changes`, `method_implements`, `url_route`, ...) are **not** call edges and are excluded.
`dispatches_to` in particular looks call-shaped but is not: it maps a base/interface method to its
overrides (`ingestion/dispatch_edges.py`), i.e. a *declaration* relationship, not a call site —
including it would be inventing calls that were never observed.

No confidence threshold is applied: every `calls` edge repowise's resolver emitted is included,
regardless of its `confidence` column, again in service of "richest available output, best honest
shot."

## The mapping step: rebuilding `Namespace.Type::Method`

This is the part that needs real documentation, because it is not a straight read.

**repowise's persisted `qualified_name` column is not usable for this contract.** It is built by
`_build_qualified_name()` (`ingestion/parser_helpers.py:156`) as
`<file-path-with-dots-instead-of-slashes>.<parent_name>.<name>` — a *path*-derived qualifier, not
the C# `namespace X.Y { }` the type actually sits in. For a class at
`src/Serilog/Core/Logger.cs`, its `qualified_name` is `src.Serilog.Core.Logger.Logger.Write`, which
happens to look plausible for this one repo's directory layout but is not the same string the
oracle's IL-derived `Serilog.Core.Logger::Write` needs, and would diverge completely on any repo
whose folder structure doesn't mirror its namespaces (a `src/`-nested layout, a namespace that
doesn't match the directory it lives in, multi-namespace files, etc.). Using it as-is would either
silently fail to match the oracle for reasons that have nothing to do with whether repowise found
the right call, or coincidentally half-match in a way that overstates repowise's actual namespace
resolution — neither is honest.

**`node_id`'s embedded "parent" segment is one level deep only.** repowise's own `_find_parent()`
(`ingestion/parser.py:898`) walks up the AST and returns the *first* ancestor whose type is a class/
struct/interface/enum/record/namespace — for a method two levels down (`Outer` class containing
`Inner` class containing the method), the method's own `parent_name` is `"Inner"`, never
`"Outer.Inner"`. This is a genuine repowise limitation, not a mapping artifact this arm introduces:
repowise's own cross-reference resolution (`call_resolver.py:1737`, `_extract_class_id`) carries the
same one-level assumption and comments on it directly ("path::Outer::Inner -> path::Outer... miss
silently"). **What this arm does about it:** rebuilt keys go through repowise's own
`scan_type_declarations()` (see next paragraph), whose brace-depth tracking *does* capture one level
of nesting (`Outer.Inner`) correctly even though `node_id` alone does not — so this arm's keys are
in fact slightly better qualified than a literal read of `node_id` would give, for exactly the
common one-level-nesting case, while a class nested two or more levels deep still collapses onto its
immediate parent only, inherited from repowise's own `_find_parent` and unrecoverable from what
repowise persists.

**The actual reconstruction**, in `run.py`'s `_KeyBuilder`:

1. Recover the bare class/type name repowise folded into `node_id` (`_node_class_bare`): since
   `node_id == f"{file_path}::{parent_name}::{name}"` or `f"{file_path}::{name}"`, and `file_path`/
   `name` are both trusted columns already read off the row, whatever sits between them (if
   anything) is `parent_name`. If nothing does (`node_id == f"{file_path}::{name}"`), there is no
   enclosing type at all as far as repowise recorded — see "what gets dropped" below.
2. Re-scan that file's source text with repowise's own
   `resolvers/dotnet/namespace_map.scan_type_declarations()` and `declared_namespaces()` — loaded by
   `importlib` straight out of the *pinned venv's* installed copy (not a transcription, not the
   reference clone's copy — the exact bytes `pip install repowise==0.45.0` put on disk). This module
   has zero third-party imports (only `re`/`pathlib`), which is also why it is loadable in `run.py`'s
   own process even though `run.py` runs under whatever Python the benchmark runner uses, not the
   venv's interpreter — the only step that genuinely needs the venv's interpreter is the `repowise
   init` subprocess itself.
3. Match the bare class name against that file's `TypeDecl` list. On a match, use `TypeDecl.fqn` —
   repowise's own `f"{namespace}.{qualified}"`, where `qualified` already carries one level of
   nesting. This is Tier 1 and is what almost every edge resolves through.
4. If no declaration in the file matches by name (partial-class fragment contributing only a member
   in this particular file, or `parent_name` actually being a namespace segment because `_find_parent`
   hit a `namespace_declaration` before any enclosing class — happens for namespace-level constructs
   like a `delegate` declared directly under a `namespace {}` block with no class around it), fall
   back to the file's own `declared_namespaces()`: if the file declares exactly one namespace, prefix
   it onto the bare class name. This is Tier 2.
5. If the file declares no namespace at all, emit the bare class name with no prefix (Tier 3) — a
   real C# global-namespace type, which is a legitimate (if unqualified) `Type::Method` key, not a
   guess.

### A real bug this inherited from repowise, found while testing this on Serilog

`namespace_map.py`'s `scan_type_declarations()` assigns a type's namespace as "the nearest
`namespace X {}` declaration that textually precedes it in the file" — it does **not** track when a
block-form namespace's closing brace has already been passed. `corpus/serilog/src/Serilog/Guard.cs`
is a real, present-in-the-corpus example:

```csharp
using JetBrains.Annotations;

namespace JetBrains.Annotations
{
    [AttributeUsage(AttributeTargets.Parameter)]
    sealed class NoEnumerationAttribute : Attribute { }
}

static class Guard   // <- global namespace: the block above is CLOSED
{
    public static T AgainstNull<T>(...) { ... }
}
```

repowise's own scanner reports `Guard`'s namespace as `JetBrains.Annotations` (verified directly
against the pinned clone's `scan_type_declarations()`, not assumed) — wrong; `Guard` is at file
scope. This arm reuses that scanner verbatim rather than patching it, on the principle that this arm
measures what repowise's own logic actually produces, not a corrected version of it. It means at
least this one caller/callee key in the Serilog cell (`Guard::AgainstNull` and any callers who
reach it) is misqualified in exactly the way repowise's own product would misqualify it, and will
likely show up as a false negative on the oracle's `Guard::AgainstNull` (global namespace) rather
than the (wrong) `JetBrains.Annotations.Guard::AgainstNull` this arm emits.

## What gets dropped, and why

Per the task brief: emit what can honestly be produced, never invent a namespace, and count what
cannot be resolved rather than silently omitting it. Every `collect()` run writes
`.index/<repo>/extract_stats.json` with:

- `edges_total` — raw `calls`-typed rows in `graph_edges` for the repository.
- `edges_emitted` — rows that produced a full caller+callee key pair.
- `dropped_missing_node` — an edge endpoint's `node_id` was not found in `graph_nodes` (should be
  rare/zero; recorded as a canary in case a future repowise version changes the join contract).
- `dropped_no_class_segment` — `_node_class_bare` found no parent segment at all (a call whose
  caller or callee repowise itself recorded with no enclosing type — chiefly top-level-statement
  local functions in an entry-point file). These are dropped rather than emitted as a bare
  `Method` with no `Type::` prefix, since that shape cannot honestly be called
  `Namespace.Type::Method` and would not mean anything to the grader.
- `dropped_unreadable_file` — the source file named in `file_path` could not be read back from the
  scratch checkout (should be zero; the checkout is a straight copy of what was just indexed).

## Cells

`without-tests` filters on `caller_file` using `armkit.is_test_path()` against the *original*
checkout root passed to this script — the same rule every other arm uses, applied after the fact.
repowise does have a `--skip-tests` init flag, but this arm does not use it: its own test-path
detection is a separate, undocumented heuristic, and using it would make the two cells mean
something different for this arm than they mean for `grep` and `cheburnexus`. Both cells share one
underlying index (`.index/<repo>/raw_edges.json`, cached across cells) — repowise's own graph does
not distinguish them, so there is nothing to gain from indexing twice, and the task brief explicitly
allows "index everything and filter on the way out."

## Filesystem isolation

`repowise init` writes `.repowise/` (SQLite store, `state.json`, and — unless suppressed —
`CLAUDE.md` / `.vscode/extensions.json` / other editor integration files) straight into the
directory it indexes. The corpus checkouts under `corpus/` must stay read-only, so this arm never
points repowise at them directly: `_ensure_scratch_copy()` copies each checkout once into
`.index/<repo>/checkout/` (excluding `bin/`, `obj/`, `.vs/`, `node_modules/` — build output only,
already outside repowise's own C# parse set) and indexes that copy. `--no-editor-setup
--no-claude-md` are passed too, belt-and-suspenders, but the copy is what actually guarantees zero
writes to the real checkout regardless of whatever else a given repowise version decides to write.

## Version

Pinned to `repowise==0.45.0` via `pip install` into `arms/repowise/.venv` — this is the exact
version the reference clone at `<repowise-checkout>` is checked out at
(`pyproject.toml: version = "0.45.0"`), and it was available on PyPI, so no fallback to a nearby
version was needed.

## Disk footprint

Reported in the run summary; venv + one scratch checkout + SQLite index per corpus repo, well
under the 2 GB budget for these small repositories.

---

## Settled 2026-08-24: the namespace bug is THEIRS, and it counts

The open question was whether `namespace_map.py` sits in repowise's live C# indexing path. If it did
not, its wrong answers would be an artifact *we* created by calling a module their pipeline never
calls, and charging that to them would be manufacturing a competitor error.

**Traced in their source, and it is live.** The chain runs unconditionally inside the graph build:

    graph/builder.py:516            self._resolve_csharp_same_namespace(ctx, progress=progress)
      -> graph/_resolvers.py:189    resolve_csharp_same_namespace_refs(...)
        -> languages/csharp_same_namespace.py:134,157
             from ..resolvers.dotnet.namespace_map import scan_type_declarations, declared_namespaces

Those are the exact two functions this arm calls. Not behind a flag, not a C#-only opt-in: it sits
in the ordinary build sequence beside the JVM and Swift passes.

**Reproduced independently**, calling their function on a corpus file rather than trusting a report:

    corpus/serilog/src/Serilog/Guard.cs
      1  using JetBrains.Annotations;
      3  namespace JetBrains.Annotations {   ... }      <- block namespace, CLOSES on line 9
     11  static class Guard                              <- global namespace

    scan_type_declarations(text) -> [('NoEnumerationAttribute', 'JetBrains.Annotations'),
                                     ('Guard',                  'JetBrains.Annotations')]

`Guard` is in the global namespace. Their scanner does not track that the block namespace ended, so
every type after a closed block inherits it. Their own docstring calls the pass a "conservative
text-level scan", which is exactly what this is: a technique limit, not a slip.

**Therefore: category (a). The error is theirs, it reaches their users, and it flows into the
numbers uncorrected.** We do not fix it on their behalf, and we do not hide it either — it is
recorded here so a reader can see which part of their column is their tool.

What we *do* still correct for them, and must keep disclosing: the key format. Their stored
`qualified_name` is derived from the file path, so a namespace-qualified key had to be rebuilt at
all. Scoring them on a spelling they never claimed to produce would be a strawman.

## What we could not key at all, per repository

Edges dropped for want of a usable key — reported here as its own number, never folded into recall,
because "we could not address 16% of their graph" is a finding about path-anchored node identity,
while the same loss buried inside recall reads as "their tool misses calls". Different claims.

| repository | their edges | keyed | dropped | share |
|---|---:|---:|---:|---:|
| FluentValidation | 1293 | 1087 | 206 | **15.9%** |
| serilog | 1231 | 1101 | 130 | **10.6%** |
| Polly | 5072 | 4759 | 313 | **6.2%** |

Every drop is `dropped_no_class_segment`: their `node_id` carried no enclosing-type segment at all,
so there is nothing to qualify. None were lost to a missing node or an unreadable file.

### ⚠ Correction: this number is NOT all "their loss"

Inspecting the nodes behind it (serilog, 634 `.cs` nodes with no type segment) shows three different
things wired together, and only one of them is a gap in their graph:

| what it is | count | whose problem |
|---|---:|---|
| `__module__` — the file itself as a node | 204 | nobody's. Not a method, so an edge touching it is not a method-to-method call and *should* be dropped |
| a namespace as a node (`Serilog.Core`, `JetBrains.Annotations`) | many of the rest | nobody's, same reason |
| a real method with no enclosing type recorded (`Write`, `ForContext` in `ILogger.cs`) | the remainder | **theirs** — the method exists, the type was not attached |

So "we could not address 16% of their graph" overstates it: part of that share is edges that were
never method calls to begin with. The honest claim is narrower — *some* of their symbol nodes lose
the enclosing type, and until the counter is split by node kind we cannot say how many. Splitting
`dropped_no_class_segment` into "endpoint was not a method" and "method without a type" is the fix,
and it must land before any of these numbers is published.
