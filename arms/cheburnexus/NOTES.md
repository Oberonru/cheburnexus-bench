# cheburnexus arm — notes

## What this arm does

Runs our own product's C# analyzer (`ArchitectureAnalyzer.CLI`, the same engine the Unity package
and the MCP server both wrap) over a checkout and converts its call-graph sidecar into the edge
contract. Everything below was learned by reading the product's own source at
`/Users/alexey/dev/Cheburnexus/LLM-CheburNexus` (read-only — nothing there was modified) and by
running the built `dist-all/cheburnexus-all-1.7.0-osx-x64` binary against the bench corpus.

**Headline finding: on this machine (no licence passport, the default state) the engine never
emits a call graph at all, in ANY analysis mode.** Free tier gets an aggregate
(`{callee: incoming-call-count}`, no caller identity) instead. Zero edges is therefore the correct
live result here — see "The entitlement wall" below for the full mechanism, verified by reading
the gating code and by direct measurement.

## How the engine is invoked

The binary is `arch-computer.exe` inside the product repo's `dist-all/cheburnexus-all-1.7.0-osx-x64/`
folder — despite the name, this **is** `ArchitectureAnalyzer.CLI` (renamed at packaging time; see
the product repo's `package-all.sh`/`package-all.ps1`). Do not confuse it with `cheburnexus.exe` in
the same folder, which is the unrelated bun-compiled MCP server.

`ArchitectureAnalyzer.CLI/Program.cs` exposes four modes; `--solution <sln|slnx|csproj>` is the one
that matters here — it is the standalone semantic (Roslyn) front end, reading `obj/project.assets.json`
for NuGet references instead of invoking MSBuild (see `DotNetProjectInputProvider`'s doc comment for
why: `MSBuildWorkspace` crashes on Unity-style legacy csproj, `.slnx`, and SDK-pinning `global.json`).
`--project <dir>` is the syntactic fallback (no Roslyn semantic binding at all).

The arm's actual invocation, per product `.csproj` discovered (not per repo — see "Multi-project
repos" below):

```
arch-computer.exe --solution <csproj> --output <scratch>/<projname>/architecture.json
```

with a narrow fallback to `--project <csproj's own directory>` — see "Engine bug #2" below — used
only when `--solution` reports zero in-scope sources, never when it refuses over a missing restore
(that refusal is the product's own correctness guardrail; working around it would silently trade a
complete answer for an incomplete one, which is exactly what the guardrail exists to prevent).

Project discovery globs the checkout for `*.csproj` (skipping `bin/`, `obj/`, `.git/`), classifies
each by `armkit.is_test_path` (the same directory-marker rule every arm shares), and for
`without-tests` runs only the non-test set; for `with-tests` it adds the test set. Edges are unioned
across however many product projects a repo ships (FluentValidation has 2, Polly has 4).

2026-08-25 (defect #10): the candidate list is then additionally filtered through
`armkit.in_scope`, which reads corpus.json's `product_projects`/`test_assemblies_projects` — the
one documented exception to "never reads corpus.json" (see `arms/ARCHITECTURE.md`). Before this
fix, Polly's legacy `src/Polly/` (excluded from the corpus, a different assembly, same `Polly`
namespace as `Polly.Core`) glob-matched as a product candidate — it is not a test directory by
name — and the engine happily analyzed it, feeding 369 false-positive edges into the graded cell.
`_discover_csproj` itself is unchanged; `collect()` narrows its output before running the engine.

## Which output field carries the edges

`ArchitectureAnalyzer.CLI` writes a main `architecture.json` plus sidecars next to it. On a
Pro-entitled run, the call graph lives in **`architecture.calls.json`**:

```
SidecarEnvelope<Dictionary<string, CallSidecarEntry>> {
  Data: {
    "<callerFile>::<Namespace.Type>::<Method>(<paramTypes>)": {
      Calls: [ { Target: "<calleeFile>::<Namespace.Type>::<Method>(<paramTypes>)",
                 Line: <int>, Kind: "this"|"base"|"static"|"new"|"instance", InLambda?: true } ]
    }
  }
}
```

produced by `ArchitectureAnalyzer.Compilation.SemanticCallsResolver` (semantic/Roslyn — resolves
`_field.X()`, `localVar.X()`, property/expr receivers, which the syntactic builder cannot) or, when
semantics were never computed (`--project` mode), by the syntactic `CallsSidecarBuilder` (this/base/
static/new receivers only). Both share one key format (`Services/Sidecars/CallKeyBuilder.cs`), so
the sidecar is structurally the same either way — the difference is coverage, not shape.

Key facts read directly from `CallKeyBuilder`/`SemanticCallsResolver`, load-bearing for the
conversion below:
- The caller/callee key's middle segment is `Namespace.TypeName` — **only for top-level types**.
  `SemanticCallsResolver.EnumerateMembers` (and the syntactic builder identically) explicitly skips
  any type whose `Parent is TypeDeclarationSyntax`, i.e. nested types are entirely absent from the
  calls graph by the engine's own deliberate, documented design. So this arm never needs to solve
  the general "where does the namespace end in a dotted chain" problem, and the contract's
  `Outer/Inner` nested-type notation is never exercised here.
- A constructor's method-name segment is the **class's own simple name** (Roslyn's
  `ConstructorDeclarationSyntax.Identifier.Text`, which is literally the class name), not IL's
  `.ctor`/`.cctor`.
- Generic type declarations carry the **source-level open form** (`Cache<T>`), not IL arity
  (`` Cache`1 ``).

## The conversion (`run.py`)

1. **Namespace/type split** — trivial given the "top-level only" fact above: for each analyzed
   project we parse its own `architecture.json` `Files[]` (never gated by tier — `ClassModel`/
   `MethodModel` are core facts, untouched by `ModelProjectionService`) and build
   `{"Namespace.Name": (namespace, name)}` directly; no dotted-string guessing needed.
2. **Constructor rename** — a class's own `Methods[]` list carries `IsConstructor: true` per entry.
   Any call whose method-name segment matches a name that list marks as a constructor gets renamed
   to `.ctor`, matching the oracle's IL convention (`grader/test_identity.py` explicitly asserts
   `.ctor`/`.cctor` must not collapse into the same key). **Known gap:** we cannot distinguish an
   explicit static constructor from a same-signature instance one this way — both key as `.ctor`.
   Static constructors cannot be called explicitly from C# source, so this only matters for a static
   ctor's own body as a *caller*, which is rare, and is left as a documented limitation rather than
   solved with signature-position heuristics.
3. **Generic arity** — `Cache<T>` → `` Cache`1 ``, `Pair<K,V>` → `` Pair`2 `` (counting top-level
   commas inside the bracket), done ourselves rather than left as-is, because
   `grader/grade.py`'s `strip_generic_arguments` deletes *anything* inside `<...>` on the assumption
   it is a closed generic argument the oracle wrote. Left as `Cache<T>`, the grader would erase the
   arity entirely and silently merge `Cache<T>` with a non-generic `Cache` sitting in the same
   namespace, on both the arm and (harmlessly, since it never appears) the oracle side.
4. **Explicit interface implementations** — a **known, unfixed mismatch**, called out here because
   `EDGE_FORMAT.md` already documents the shape it expects (`App.Box::System.Collections.Generic.
   IEnumerable.GetEnumerator`) and our engine cannot produce it: `Identifier.Text` for
   `void IFoo.Bar() {}` is just `"Bar"`, with no qualification captured anywhere in the model. Any
   call into an explicit interface implementation will not match on this axis.
5. **`caller_file`** — the calls-sidecar key's own file segment, made repo-relative with forward
   slashes. **`caller_line`** — the per-call `Line` field (the call SITE line, one per edge, not the
   method's declaration line — matches the oracle's IL sequence-point convention).
6. **Cross-project calls are dropped** for a repo whose product code spans more than one `.csproj`
   (FluentValidation ships two). Each project is analyzed as an independent `--solution` invocation
   (the CLI's `--solution` only ever takes one entry point), so a call from one product assembly
   into a sibling one resolves through the sibling's *built DLL* as metadata, which has no matching
   entry in that run's own source-key index — `SemanticCallsResolver` correctly-but-unhelpfully
   treats it as "external" and drops it, exactly as it would a call into the BCL.

None of 1–6 could be exercised against a live edge list on this machine — see next section — so
the conversion logic is written and can be sanity-read, but is only proven correct against the
recorded sample shape (an old self-analysis run of an unrelated project found on disk at
`dist-all/cheburnexus-all-1.7.0-osx-x64`'s sibling checkout,
`ArchitectureAnalyzer.CLI/architecture.calls.json`, used only to confirm field names — never as
data). It has not been graded against real edges. If a Pro passport is ever wired into the bench
runner, re-verify this arm's precision/recall the moment `architecture.calls.json` actually appears
in a run's output, since that is the first time this arm's core logic gets exercised at all.

## The entitlement wall (verified, not inferred)

`ArchitectureAnalyzer.CLI/Program.cs` sets `Ent.SetClaimsSource(new FileClaimsSource())` — the
production entitlement source, which fails closed to the Free tier on any missing/unreadable/
unsigned/expired/machine-mismatched passport (`ArchitectureAnalyzer.Entitlements/FileClaimsSource.cs`).
No passport exists on this machine, and per the task's instructions this arm never went looking for
one, so every run here resolves to Free.

`Services/ModelProjectionService.cs`:

```csharp
var hasSemantic = entitlements.IsEntitled(FeatureKeys.AnalyzerSemantic);   // Pro+
if (!hasSemantic) {
    CaptureCallCounts(model);          // aggregates BEFORE nulling the source
    ProjectSidecarSources(model);      // model.SemanticCalls = null;  (+ SemanticDependencies, etc.)
}
```

`Services/SidecarExportService.cs`:

```csharp
if (model.CallCounts != null)                 // TRUE exactly when tier lacks analyzer.semantic
    Export("calls-counts", model.CallCounts, ...);   // {calleeKey: incoming count} — NO caller
else
    Export("calls", model.SemanticCalls ?? new CallsSidecarBuilder().Build(model), ...);
```

This is the whole mechanism, and it is gated on **tier**, not on **analysis mode**: `--project`
(syntactic — needs no licence to compute in principle) hits the exact same `!hasSemantic` branch as
`--solution` (semantic), because the gate is `ModelProjectionService.Project()`, called once, after
either mode finishes. So there is no "reduced but usable" edge list to fall back to on Free — the
call graph is withheld **at every resolution**, not just the precise one. `architecture.calls.json`
is simply never written; only the count-only `architecture.calls-counts.json` is.

**Verified directly** (not just read from source): ran
`arch-computer.exe --solution corpus/serilog/src/Serilog/Serilog.csproj --output /tmp/probe/architecture.json`.
Console printed `Mode: semantic` (so `SemanticCallsResolver` genuinely ran — the computation is not
skipped, only its export). Output folder contains `architecture.calls-counts.json`
(240 distinct callees, counts only, confirmed by inspection) and **no** `architecture.calls.json`.
`architecture.json`'s own `ExportedTier` field reads `"free"`. This is the same result an
independent self-analysis of the product repo itself (found already on disk, unrelated to this
task) shows for the same reason.

Per the task's instructions, this wall was not bypassed, patched, or worked around in any way, and
no licence key or passport was searched for. **0 edges from a live run on an unlicensed machine is
the correct, expected measurement** — it is the fact this task exists to surface, not a defect in
the arm.

## Two engine bugs hit while building this arm (reported, not patched)

LLM-CheburNexus was read-only for this task, so both are reported here rather than fixed.

**Bug #1 — Polly's product build is entirely unrestorable via `--solution`.** Polly uses MSBuild's
`UseArtifactsOutput` convention: `dotnet restore` writes to a centralized
`artifacts/obj/<ProjectName>/project.assets.json`, not the conventional `<projectdir>/obj/`.
`DotNetProjectInputProvider.PackageReferences()` only ever looks at
`Path.Combine(csprojDir, "obj", "project.assets.json")`, so every Polly project that declares
package references is reported as unrestored regardless of whether it actually is. Confirmed by
`find corpus/Polly -iname project.assets.json`: all five entries live under `artifacts/obj/`, none
under a project's own `obj/`.

**Bug #2 — a project with an explicit `<Compile Include>` pointing outside its own directory
reports ZERO in-scope sources, not just the extra file.** Hit on `FluentValidation.csproj`,
`FluentValidation.DependencyInjectionExtensions.csproj` (both via
`<Compile Include="..\CommonAssemblyInfo.cs">`) and three of Polly's four `src/` projects (via
`<Compile Include="$(MSBuildThisFileDirectory)..\Shared\*.cs">`) — always the same trait: an
explicit `Compile Include` reaching outside the project's own folder into a shared-sources file,
combined with the implicit SDK-style glob. `--project` (syntactic mode, a completely different
file-discovery path in `ProjectStructureService`) finds the project's own files without trouble —
e.g. 2 files for `FluentValidation.DependencyInjectionExtensions` — which is what isolates the bug
to `DotNetProjectInputProvider.SourceFiles()` specifically, without us tracing the exact defect
inside it. This arm works around it with a narrow, documented fallback (`--project` on the same
directory, only when `--solution` reports zero sources) rather than leaving those projects
unmeasured — see `_run_engine` in `run.py`.

## Cells: with-tests vs without-tests

Discovery uses `armkit.is_test_path` (directory-marker check) on every `.csproj` found under the
checkout — the same shared rule every arm uses, never `corpus.json`.

- **Restore.** On this corpus snapshot, every test project's `obj/project.assets.json` is missing —
  the checkouts were evidently prepared by building only the product configuration
  (`dotnet build src/X/X.csproj`), never restoring the test projects. So on `with-tests`, test
  `.csproj` candidates are attempted and (mostly) skipped with "no restore output", exactly like a
  missing-restore product project would be — this is not special-cased, it is the same code path.
  A few succeed anyway when they declare no packages at all (`Serilog`'s `AotTestApp`, `Polly`'s
  `Polly.AotTest`) or restore under the conventional path where a product sibling didn't
  (`Polly.Core.Tests` etc., once the `--project` fallback kicks in). None of this changes the arm's
  own edge output today, since the licence wall zeroes every project's contribution regardless —
  but it does mean **`with-tests` and `without-tests` currently score identically for cheburnexus on
  this corpus** (both empty), which is a fact about this corpus snapshot's restore state, not a
  cell-handling bug in this arm.
- **FluentValidation's test/product split is not separable by directory at all** — its test project
  lives beside product code as `src/FluentValidation.Tests/`, distinguished only by the `.Tests`
  name suffix, not a `test/` directory. `armkit.is_test_path` (a directory-marker rule, shared by
  every arm) does not catch this, so this arm's `without-tests` candidate set for FluentValidation
  currently includes `FluentValidation.Tests.csproj` as if it were product code. This does not
  corrupt today's numbers (0 edges regardless), but on a licensed run it would leak test-file edges
  into the `without-tests` cell. `ARCHITECTURE.md` anticipates exactly this shape of problem
  ("a repo whose test sources are not separable by path does not get a with-tests cell at all, and
  that is recorded") — flagging it here rather than silently absorbing it.

## `--describe` / version

`version()` parses the distributable package version (`1.7.0`) out of the engine binary's own
folder name (`cheburnexus-all-1.7.0-osx-x64`) — fast and stable, since `--describe` is called on
every runner invocation and a full analysis just to read a version field would be far too slow for
that. For the record: the assembly-level `EngineVersion` field actually written into
`architecture.json` by this exact build was observed as `"1.1.0"` (the `ArchitectureAnalyzer.Core`
assembly version, distinct from the 1.7.0 distributable) — grepped the product source for a git-sha
field to surface alongside it (the task asks for one if the output exposes it) and found none:
no commit sha appears anywhere in `ArchitectureAnalyzer.CLI`'s own JSON output.

## Engine discovery

`$CHEBURNEXUS_ENGINE` env var, else a hardcoded default pointing at this machine's own product-repo
checkout (`~/dev/Cheburnexus/LLM-CheburNexus/dist-all/cheburnexus-all-1.7.0-osx-x64/arch-computer.exe`).
This only resolves here — the "live for us" half of `ARCHITECTURE.md`'s live/replay split. Anyone
else needs `$CHEBURNEXUS_ENGINE` pointed at their own build, or the published replay rows once those
exist (none are published yet — this arm has never produced a nonzero edge list to publish).

## Verification run (2026-08-23, this machine)

```
python3 arms/cheburnexus/run.py --repo corpus/serilog --cell without-tests --out /tmp/ch-serilog.jsonl
python3 runner/run.py --only serilog --arms cheburnexus --cells without-tests
```

```
repo      cell           arm          mode   prec   recall   note
serilog   without-tests  cheburnexus  live    n/a    0.000
```

- edges produced: **0** (entitlement wall — `architecture.calls-counts.json` only, no
  `architecture.calls.json`; `Mode: semantic` printed, so the resolver ran, only its export was
  withheld)
- wall-clock: ~2.7s for serilog's single product project (see manifest `seconds` field); ~19s for
  Polly's full `with-tests` set (5 product + 5 test projects that analyze, several via the
  `--project` fallback)
- precision: n/a (arm produced no edges — nothing to divide by, per `grader/grade.py`'s own
  convention for an empty arm output)
- recall: 0.000 across every cell (534 primary oracle edges on serilog `without-tests`, matched: 0)

Re-ran the same for `FluentValidation` (both cells) and `Polly` (`without-tests`, `with-tests`)
directly against `run.py` (not through `runner/run.py`, since Polly's *oracle* side separately
fails to build — "no built assemblies for this cell" — an oracle/corpus issue, unrelated to this
arm, not investigated further here): same result, exit 0, 0 edges, wall correctly identified on
every product project that analyzed. No repo, and no cell, currently reports `unavailable` for this
arm — the `--project` fallback (bug #2 above) was the difference between that and a working,
honestly-empty measurement for 2 of the 3 corpus repos.

Per the task's explicit instruction, no tuning was done against these numbers — recall 0.000 is
reported as measured, not treated as a bug to chase.
