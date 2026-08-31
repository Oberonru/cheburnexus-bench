# e21 — our compilations had no identity, so a signed project's internals were invisible

Written BEFORE any run of the fixed engine. Engine build `_bench-engine-e21-signing` (working tree);
baseline = `_bench-engine-e20-tfm-ref` / results `2026-08-30-e20-sibling-tfm`.

## ⚠ Disclosure
Unlike e18/e19/e20 this forecast is written before ANY graded run of the change — no sizing run has
happened. The numbers below are a reconstruction from the baseline's own unresolved-site probe.

## The defect (PRODUCT)
`SemanticCompilationBuilder` created every compilation with
`new CSharpCompilationOptions(...).WithAllowUnsafe(true)` and **no crypto key**. A strong-named project
opens its internals with `[assembly: InternalsVisibleTo("Tests, PublicKey=0024…")]`, and Roslyn honours
that grant only for a requesting compilation whose identity carries that public key. With no key the
grant never matched, so every `internal` member of a signed project was invisible to its own test
project.

Probed on the live serilog/with-tests run (temporary dump inside `CollectEdges`, 54 unresolved sites):

| sites | callee | `CandidateReason` | candidates | what we did |
|---|---|---|---|---|
| 34 | `PropertyValueConverter.CreatePropertyValue` | **Inaccessible** | **2** | unresolved — we may not choose an overload |
| 17 | `Guard.AgainstNull` | None | **0** | unresolved — the TYPE is inaccessible, so there is no method group at all |
| 2 | `PropertyBinder.ConstructProperties` | **Inaccessible** | 1 | taken, via the single-candidate fallback |

🔑 That last row is why this cause hid for a day: `CollectEdges` takes `CandidateSymbols[0]` whenever
the binder names exactly ONE candidate, whatever the reason — so an inaccessible callee can still
produce an edge and look like a successful bind. It refuted nothing; it masked.

All of these are counted `DroppedUnresolved`. **No wrong fact is published** — this is an honest gap.

## The fix
Read `SignAssembly` / `PublicSign` / `AssemblyOriginatorKeyFile`, the project's own value first and
then the nearest `Directory.Build.props` that defines it (the e19 plumbing, MSBuild's own order), and
apply it as `.WithCryptoKeyFile(k).WithStrongNameProvider(new DesktopStrongNameProvider())` — or
`.WithPublicSign(true)` when the project public-signs. We never emit, so the key is read only to
compute the identity.

Nothing is guessed: only `$(MSBuildThisFileDirectory)` and `$(MSBuildProjectDirectory)` are expanded
(each has exactly one meaning given the file the value was read from); any other `$(...)`, or a key
file not on disk, leaves the compilation exactly as it is today.

Proven to FAIL first, on BEHAVIOUR: with only the two behaviour sites reverted (the `AssemblyInput`
fields kept, so nothing fails to compile), **5 of the 8 new tests fail**, including
`InternalMemberOfASignedSiblingBindsWhenTheConsumerCarriesTheSameKey` — same sources, same references,
only the requesting assembly's identity differing. The other 3 are guards that do NOT discriminate
(unsigned stays unsigned; no identity is invented from a missing key), and are named as guards rather
than counted as proof. The tests generate a real key pair at run time (`ExportCspBlob`) so no key
material is committed.

## FORECAST
**serilog / with-tests, primary cell**
* matched **2265 → 2308** (+43): `PropertyValueConverter::CreatePropertyValue` ×33 and
  `Guard::AgainstNull` ×10 — the oracle rows behind those 51 sites.
* recall **0.9713 → 0.9897**; oracle_edges unchanged at 2332.
* junk: I expect it unchanged at 11, but this is the weakest clause — 51 call sites that produced
  nothing now produce edges, and a newly bound callee can land in a cell the oracle spells differently.
* the 2 `ConstructProperties` sites already came through the fallback; their outcome must not change.

**serilog / without-tests** — byte-identical: one in-scope project, no consumer of its internals.

**Polly / without-tests and FluentValidation / without-tests** — both DO sign
(`Polly/eng/Common.props`, `FluentValidation/src/Directory.Build.props`), so unlike e20 I will NOT
claim byte-identical. I expect no movement, because neither cell has a first-party consumer of another
in-scope project's internals — but I was wrong about accessibility once already this week, so this is
the clause I trust least. Any movement must be explained row by row before the change is kept.

## Kill criterion
**Any oracle row matched in `2026-08-30-e20-sibling-tfm` that is unmatched here kills the change**,
whatever the topline does. Checked pair by pair against the baseline's own matched set.

## Impartiality
grep and repowise must be byte-identical in all 8 measured cells.

## Outcome — SHIPPED
Full matrix `results/2026-08-31-e21-strong-name`.

| cell | recall e20 → e21 | precision | matched |
|---|---|---|---|
| serilog / with-tests | **0.9713 → 0.9961** | 0.9952 → **0.9953** | 2265 → **2323** (+58) |
| serilog / without-tests | 1.0000 (edges byte-identical) | 0.9981 | 537 |
| Polly / without-tests | 0.8734 (edges byte-identical) | 0.9984 | 614 |
| FluentValidation / without-tests | 0.9183 (edges byte-identical) | 0.9983 | 573 |

**Kill criterion PASSED — 0 previously matched rows lost**, checked pair by pair. **0 new junk**
(10 → 10 at row level; `arm_edges_in_cell − matched` stays 11). Raw arm edges 2303 → 2361:
**+58 added, 0 removed**. **Impartiality PASSED** — grep and repowise byte-identical in all 8 cells.

### The forecast, scored
✅ Both named groups landed EXACTLY: `PropertyValueConverter::CreatePropertyValue` **33/33**,
`Guard::AgainstNull` **10/10**.
✅ The clause I trusted least held: Polly and FluentValidation both sign, and both are byte-identical —
neither cell has a first-party consumer of another in-scope project's internals, as predicted.
✅ Junk unchanged, the clause I called weakest on the other side.
❌ **+58 against +43 forecast.** 15 more rows returned than the probe's site list accounted for: mostly
constructors of internal types (`PropertyBinder::.ctor`, `MessageTemplateCache::.ctor`,
`PropertyValueConverter::.ctor`, `LevelOverrideMap::.ctor`, `FailureAwareBatchScheduler::.ctor`) plus
`LoggerSinkConfiguration::Logger`, `IMessageTemplateParser::Parse`, `MessageTemplate::Render`,
`LoggerFilterConfiguration::With`.

🔑 **Why the forecast was low, and it is the same reason twice this week**: I sized the group from the
sites the probe PRINTED, and the probe only printed sites whose text mentioned one of the three names I
was chasing. Everything else inaccessible for the same reason was invisible to my own instrument.
Sizing by running the mechanism is right; sizing by running a FILTERED view of it is still reading.

### Where serilog / with-tests now stands — 9 misses left
| rows | callee | note |
|---|---|---|
| 7 | `InMemoryBatchedSink::.ctor` ×4, `CallbackBatchedSink::.ctor` ×2, `SyncState::.ctor` ×1 | C#12 primary constructors — already queued |
| 1 | `JsonValueFormatter::.ctor` | base ctor of a nested test type |
| 1 | `LoggingLevelSwitch::add_MinimumLevelChanged` | event accessors have no key of their own — named in `CollectEdges` |

🕳️ Named, not fixed: `CollectEdges` takes `CandidateSymbols[0]` on a single candidate whatever the
`CandidateReason`, so an INACCESSIBLE callee can still produce an edge. That fallback is what hid this
whole cause for a day. It is defensible on its own terms and is NOT changed here — but it deserves a
decision of its own rather than continuing by default.
