# Published results — 2026-09-26 (unitystation added 2026-10-04)

Assembled from four runs, latest valid row per arm×repo×cell (a `-fix` row overrides the
same cell in the base candidate run):

- `results/published-candidate-2026-09-25/` — base run, all arms except a working `repowise`
  (repowise wasn't installed on that pass; its rows there are `unavailable`). Polly's oracle
  there is the old, pre-fix one (3 product assemblies instead of 4) — not used for Polly.
- `results/published-candidate-2026-09-25-fix/` — Polly (`cheburnexus` + `grep` + `repowise`)
  regraded against the fixed 4-assembly oracle; zod (`cheburnexus-ts` + `grep`) rerun the same day.
  **This zod `cheburnexus-ts` row was itself buggy** (precision 0.687 / recall 0.410) — see the
  next bullet — and was withheld rather than published on 2026-09-25.
- `results/published-candidate-2026-09-25-repowise/` — `repowise` for every repo, live. Its own
  Polly row still used the OLD Core-only oracle, so Polly's `repowise` row is taken from the
  `-fix` run instead (generated on top of bench commit 2baf14c specifically to close this gap).
- `results/published-candidate-2026-09-26-zod-fix/` — zod's `cheburnexus-ts` rerun after fixing
  a real engine regression (see "zod investigation, resolved" below): precision 0.934 / recall
  0.556, matching the pre-regression baseline within noise. This is the row published here now.

## Provenance

- Engine (C# arm, `cheburnexus`): commit `d6534f1c` (repo `D:\DEV\LLM-CheburNexus`, branch `main`),
  built as `dist-all/_bench-engine-publish-2026-09-25/arch-computer.exe`.
- Engine (TS arm, `cheburnexus-ts`, zod row only): commit `44d83dac` (2026-09-26,
  `fix(ts): вызов члена generic-интерфейса снова находит фабричное тело`), built from
  `D:\DEV\LLM-CheburNexus\TsAnalyzer` (`dist/core/*.js`, sha256 `0180fe8a9e09`). This is the
  fix shipped in engine release `v1.8.1`.
- Bench: this repo, commit `2baf14c` (`fix(grader): bucket.py больше не падает на cp1251-консоли
  Windows`) — the state the Polly `repowise` rerun and the C# assembly ran against. The zod rerun
  on 2026-09-26 used the same bench commit.
- Date: 2026-09-25 (all rows except zod's `cheburnexus-ts`, which is 2026-09-26).
- OS: Windows-10-10.0.19045-SP0.
- .NET SDK: 10.0.400.
- Python: 3.12.8.

## unitystation (added 2026-10-04)

Run on 2026-10-04, bench commit `80d0f24` (grader fix for a nested type inside a generic argument),
output `results/published/<arm>/unitystation/{without-tests,with-tests}/`. Needs an installed Unity
6000.2.10f1 (`UNITY_EDITOR_PATH`, `UNITY_LIBRARY_DIR`), see `CORPUS_NOTES.md` section 7.

- **Engine is NOT a public release.** `cheburnexus` rows here come from a local build of engine
  commit `52426f77` (`feat(engine): делегаты из исходников ...`, 2026-10-04), built with
  `build-bench-engine-win.sh` (win-x64, self-contained, multi-file); `ArchitectureAnalyzer.Core.dll`
  sha256 `98ec7ea68e68`. The public `v1.8.1` scores lower on this repo (recall 0.801 with the bench
  csproj files). The manifest `note` says the same.
- `repowise` 0.45.0 ran live (about 26 min per cell).
- `grep`: its `edges.jsonl` is NOT committed (173 MB and 176 MB, over GitHub's file limit);
  `result.json` and `manifest.json` are. Regenerate with `python runner/run.py --only unitystation
  --arms grep` (free).
- Licence AGPL-3.0: the committed `edges.jsonl` files hold only member names and file paths with
  line numbers, no source text.

## zod investigation, resolved (2026-09-26)

The 2026-09-25 `cheburnexus-ts` row for zod was withheld ("under investigation") because it looked
wrong: precision 0.687 / recall 0.410, well under class-validator's 0.997 / 0.563 on the same
engine. Root cause: a real regression in the TS analyzer's call-graph key building
(`TsAnalyzer/src/core/emitCore.ts`), introduced 2026-09-13 (`be3f2e62`) and never caught until this
bench re-check — a generic interface with a declaration-merged factory body (zod's own
`ZodObject<Shape, Config>` pattern) produced two different candidate-pool keys for the same logical
owner, one suffixed and one bare, so external `schema.partial()`-style calls resolved to nothing.
Fixed the same day (`44d83dac`), verified against a minimal repro test and rerun on the full zod
corpus here. **Released engine `v1.8.0` carries this bug** for TypeScript generic-interface call
resolution; `v1.8.1` has the fix. Full writeup (LLM-CheburNexus repo,
`.claude/memory/finding-ts-generic-owner-split-signature-impl-2026-09-26.md`).

## What's excluded

- `vuejs/core` — not part of this run batch; no fresh 2026-09-25 rows exist for it.

## Table (primary cell, precision / recall)

Same numbers as `README.md`'s Results table — see there for the full table with links and
licence notes. Quick reference:

| repo | cell | grep P/R | repowise P/R | cheburnexus(-ts) P/R |
|---|---|---|---|---|
| serilog | without-tests | 0.260 / 0.542 | 0.544 / 0.348 | 0.998 / 1.000 |
| serilog | with-tests | 0.321 / 0.744 | 0.592 / 0.273 | 0.995 / 0.999 |
| Polly | without-tests | 0.247 / 0.248 | 0.376 / 0.191 | 0.997 / 0.984 |
| FluentValidation | without-tests | 0.203 / 0.385 | 0.354 / 0.183 | 0.988 / 0.918 |
| MediatR | without-tests | 0.253 / 0.400 | 0.622 / 0.224 | 1.000 / 0.920 |
| AutoMapper | without-tests | 0.186 / 0.410 | 0.558 / 0.224 | 0.957 / 0.685 |
| Humanizer | without-tests | 0.123 / 0.238 | 0.925 / 0.239 | 0.998 / 0.400 |
| class-validator | with-tests | — / 0.000 | — / 0.000 | 0.997 / 0.563 |
| zod | with-tests | — / 0.000 | — / 0.000 | 0.934 / 0.556 |
| unitystation | without-tests | 0.068 / 0.739 | 0.831 / 0.481 | 0.997 / 0.999 (local engine build 52426f77) |
| unitystation | with-tests | 0.068 / 0.739 | 0.830 / 0.479 | 0.997 / 0.999 (local engine build 52426f77) |

`grep` and `repowise` show `— / 0.000` on the two TypeScript rows for different, genuine
reasons, not a shared bug:

- `grep`'s rules (`arms/grep/RULES.md`) only scan `.cs` files (`armkit.source_files`'s default
  suffix) — it never reads a `.ts` source line, so it emits 0 edges on both TS repos by
  construction. This is a real, reproducible "arm genuinely found nothing" result, not a crash.
- `repowise` does emit edges on both TS repos (18 on class-validator, 126 on zod) — but the
  TypeScript oracle is a runtime, executed-only shadow-stack key (see `README.md`'s
  Limitations), and every one of `repowise`'s callers falls in the "no oracle evidence of
  executing" bucket, so none land in the primary cell. `cheburnexus-ts`'s callers do match
  executed callers, which is why only that column scores non-zero recall here.

## Sizes

`results/published/` is ~53 MB total (unitystation adds ~36 MB: cheburnexus 12 MB per cell, repowise 6 MB); the largest single file is zod's
`cheburnexus-ts/zod/with-tests/edges.jsonl` at ~4.7 MB, just ahead of
`grep/Humanizer/without-tests/edges.jsonl` at ~4.2 MB. Nothing approaches a 50 MB/file concern.
