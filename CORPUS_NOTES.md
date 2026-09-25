# Corpus verification notes

Environment: macOS, dotnet at /usr/local/share/dotnet/dotnet, SDKs installed 8.0.421 / 7.0.202 / 3.1.102.
No .NET 10 SDK is installed anywhere on this machine. Several of today's upstream `main` branches
now assume .NET 10 (global.json pins it, and/or the code uses C# 13 preview-only syntax). This was
the single biggest source of friction across all candidates tried.

Oracle tool used for verification: `<bench-root>/oracle/csharp` (Mono.Cecil-based,
reads PDBs, emits caller->callee edges as JSONL). It was NOT modified. It requires a *separate* .pdb
file next to the .dll — embedded PDBs are invisible to it (see FluentValidation below).

`timeout`/`gtimeout` are not installed on this shell, so build attempts were bounded by the Bash
tool's own `timeout` parameter (up to 5 min) rather than a wrapped shell command.

## 1. serilog/serilog — PASS (pinned to tag v2234, not tip-of-main)

- `git clone --depth 1` of `main` succeeded, HEAD `49b5339ce85385dc52d4d8e8f2b8308becf23506` (2026-07-31).
- Build of `src/Serilog/Serilog.csproj -c Release` FAILED on that HEAD:
  - `global.json` pins `sdk.version: 10.0.100` — not installed. Worked around by editing
    `global.json` locally to `8.0.100`/`rollForward: latestFeature`.
  - Even after that, and after stripping `net10.0;net9.0` from `<TargetFrameworks>` in
    `src/Serilog/Serilog.csproj` (restore otherwise evaluates the full multi-target list and
    still errors on net10.0), the build failed with:
    ```
    error CS8652: The feature 'params collections' is currently in Preview and *unsupported*.
    To use Preview features, use the 'preview' language version.
    [.../src/Serilog/Context/LogContext.cs(110,36)]
    ```
    `LogContext.cs` uses the C# 13 "params collections" feature, which is preview-only under our
    SDK 8 compiler and only ships non-preview with the .NET 10 SDK.
  - Decision: rather than keep hand-patching source on a moving `main`, re-cloned at the latest
    published release tag instead (`git ls-remote --tags`, sorted, took the newest: `v2234`).
    That tag's `Serilog.csproj` already targets `net8.0;net6.0;netstandard2.0` and its
    `global.json` already requires only `8.0.100`. No source or config edits were needed.
- Clean build: `dotnet build src/Serilog/Serilog.csproj -c Release` → 0 errors, 0 warnings, all
  three TFMs produced (net8.0/net6.0/netstandard2.0), each with a matching `.pdb`.
- Oracle probe on `src/Serilog/bin/Release/net8.0/Serilog.dll`: 3138 edges, 6.1% no-debug-info.
  Spot-checked 3 rows (`Guard.cs:20`, `ILogger.cs:53` ×2) — all matched the file on disk exactly.
- License: Apache-2.0 (`LICENSE`). Permissive — OK.
- Test/product split: `src/Serilog/` is the only product project. Everything under `test/` is
  test-only (`Serilog.Tests`, `Serilog.ApprovalTests`, `Serilog.PerformanceTests`, plus
  `TestDummies`/`AotTestApp` helper projects). Clean directory-based separation.

## 2. AutoMapper/AutoMapper — FAIL (license, disqualified before any build attempt)

- `git clone --depth 1` of `main` succeeded, HEAD `dfa6dd587c5854b4beee5934beb39ba6e9569b84`
  (2026-07-01).
- `LICENSE.md` is the **Reciprocal Public License 1.5 (RPL 1.5)** via the "Lucky Penny Software"
  dual-license terms — a copyleft/reciprocal license, not MIT/Apache/BSD. This alone disqualifies
  the repo under the task's permissive-license requirement.
- Did not attempt a build once the license was read (would also have needed SDK 10:
  `<TargetFrameworks>netstandard2.0;net8.0;net9.0;net10.0</TargetFrameworks>`,
  `<LangVersion>14.0</LangVersion>`).
- Clone was deleted after recording this (10M on disk, no reason to keep it).

## 3. App-vNext/Polly — PASS

- `git clone --depth 1` of `main` succeeded, HEAD `173d6d1d2e28a4697f1ac1d22da68f77ff19a0c9`
  (2026-08-21).
- `global.json` pins `sdk.version: 10.0.400` (no `rollForward` key, so default policy would refuse
  any non-matching SDK) — not installed. Edited locally to `8.0.100` with `rollForward:
  latestFeature` added. No source edits.
- `dotnet build src/Polly.Core/Polly.Core.csproj -c Release` succeeded cleanly (only
  CS9057 analyzer-version warnings, no errors), producing net8.0/net6.0/netstandard2.0/net472/net462,
  each with a `.pdb` (repo uses `UseArtifactsOutput=true`, so output lands under
  `artifacts/bin/Polly.Core/release_<tfm>/`, not the classic `bin/Release/<tfm>/`).
  `Polly.Extensions`, `Polly.RateLimiting`, and `Polly.Testing` were also built and all succeeded
  the same way.
- The **legacy** `src/Polly/Polly.csproj` (the pre-Polly.Core, "v7-style" API surface that Polly.Core
  is gradually superseding) FAILS to build under SDK 8:
  ```
  error CS0501: 'Context.WrappedDictionary.set' must declare a body because it is not marked
  abstract, extern, or partial [.../src/Polly/Context.Dictionary.cs(19,9)]
  ```
  `Context.Dictionary.cs` uses the C# 13 `field` keyword (auto-property backing-field access),
  again preview-only until the .NET 10 SDK. This project was excluded from the corpus's product
  assembly list; Polly.Core (the actively maintained modern surface) is unaffected and is what was
  probed.
- Oracle probe on `artifacts/bin/Polly.Core/release_net8.0/Polly.Core.dll`: 3611 edges, 21.1%
  no-debug-info (highest of the three passing repos, still well under the 50% bar). Spot-checked 3
  rows (`CircuitBreakerResiliencePipelineBuilderExtensions.cs:68`,
  `PredicateBuilder.Operators.cs:42`, `ResiliencePipeline.Async.cs:101`) — all matched exactly.
- License: BSD-3-Clause (`LICENSE`). Permissive — OK.
- Test/product split: `test/` directory holds everything test-related, and every test csproj sets
  `<ProjectType>Test</ProjectType>` explicitly (defined via `Directory.Build.props`/`eng/*.targets`)
  in addition to the `.Tests`/`.Specs` naming convention. Product code lives only under `src/`.

## 4. FluentValidation/FluentValidation — PASS

- `git clone --depth 1` of `main` succeeded, HEAD `daa00b795450881c233253488e3ddeb362f59f56`
  (2026-08-12).
- `global.json` pins `sdk.version: 10.0.0` — not installed. Edited locally to `8.0.100`
  (`rollForward: latestFeature` and `allowPrerelease: true` were already present). No source edits.
  Unlike serilog/Polly, `FluentValidation.csproj` single-targets `net8.0` only, so there was no
  multi-target restore problem to work around.
- First build attempt (`dotnet build src/FluentValidation/FluentValidation.csproj -c Release`)
  succeeded with 0 errors/warnings, but the oracle reported **100% no-debug-info** and
  `note: FluentValidation.dll: no .pdb, edges carry no source anchor`. Root cause:
  `Directory.Build.props` sets `<DebugType>embedded</DebugType>` — the PDB is embedded inside the
  DLL itself, and no sibling `.pdb` file is ever written. The oracle tool reads a separate `.pdb`
  file and does not (and per the task, should not) be modified to parse embedded PDBs. Fixed by
  rebuilding with `-p:DebugType=portable`, which produced a real sibling `.pdb`.
- Second problem, found only after fixing the PDB: with a real PDB, no-debug-info dropped to 8.0%,
  but every `CallerFile` value was prefixed `/_/` (e.g. `/_/src/FluentValidation/DefaultValidatorOptions.cs`)
  instead of being repo-relative. Root cause: the csproj sets
  `<ContinuousIntegrationBuild Condition="'$(Configuration)'=='Release'">true</ContinuousIntegrationBuild>`,
  which (combined with `Deterministic=true`/SourceLink-style settings) triggers MSBuild's
  deterministic source-path remapping to `/_/`. Fixed by also passing
  `-p:ContinuousIntegrationBuild=false`. Final build command:
  `dotnet build src/FluentValidation/FluentValidation.csproj -c Release -p:DebugType=portable -p:ContinuousIntegrationBuild=false`.
- Residual caveat: ~5.3% of edges with a non-null `CallerFile` (304 of 5762) point at
  `src/FluentValidation/obj/Release/net8.0/System.Text.RegularExpressions.Generator/.../RegexGenerator.g.cs`
  — the Regex source generator's virtual output. The PDB carries this path with `NoDebugInfo:false`,
  but the file is never written to disk (no `EmitCompilerGeneratedFiles`), so those specific rows
  will not resolve against the checkout. This is generated code, not a build defect; spot-checks
  below were deliberately done on genuine first-party rows.
- Oracle probe on `src/FluentValidation/bin/Release/net8.0/FluentValidation.dll`: 6262 edges, 8.0%
  no-debug-info. Spot-checked 3 rows (`AbstractValidator.cs:80`, `AbstractValidator.cs:226`,
  `AssemblyScanner.cs:72`) — all matched exactly.
- License: Apache-2.0 (`License.txt`). Permissive — OK.
- Test/product split: no separate `test/` directory — product and test projects sit side by side
  under `src/`, distinguished only by naming (`FluentValidation` vs `FluentValidation.Tests` /
  `FluentValidation.Tests.Benchmarks`) and by the test project referencing
  `Microsoft.NET.Test.Sdk` + `xunit`.

## Stopped after 3 passes

Per instructions ("keep the first 3 that fully pass"), candidates 5 (Newtonsoft.Json) and 6
(MediatR) were not attempted — 3 passing repos (serilog, Polly, FluentValidation) were already in
hand after AutoMapper's disqualification.

## Cross-repo pattern worth flagging for whoever builds this corpus next

All four repos actually attempted (serilog, AutoMapper, Polly, FluentValidation) currently pin or
default to the **.NET 10 SDK** on `main`, and three of the four use at least one C# 13-only syntax
feature (`params` collections in serilog, the `field` keyword in Polly's legacy project,
presumably similar in AutoMapper) that only compiles non-preview under SDK 10. This is a moving
target: pinning by commit today does not guarantee the pinned commit builds cleanly with a fixed
SDK 8/7/3.1 toolchain in the future, since these projects are actively chasing the newest SDK. The
three PASS entries here were all verified to build with `8.0.421` specifically, with the local
`global.json` edits noted above; if the benchmark harness later standardizes on a different SDK
version, these builds should be re-verified.

## Disk footprint

Total corpus directory after all three passing builds (checkouts + build outputs, failed
AutoMapper clone removed): ~44 MB. Free disk before/after: ~14 GB → ~13 GB (most of the drop was
NuGet package cache under `~/.nuget/packages`, not the corpus dir itself).
