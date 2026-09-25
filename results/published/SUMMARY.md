# Published results — 2026-09-25

Assembled from three runs, latest valid row per arm×repo×cell (a `-fix` row overrides the
same cell in the base candidate run):

- `results/published-candidate-2026-09-25/` — base run, all arms except a working `repowise`
  (repowise wasn't installed on that pass; its rows there are `unavailable`). Polly's oracle
  there is the old, pre-fix one (3 product assemblies instead of 4) — not used for Polly.
- `results/published-candidate-2026-09-25-fix/` — Polly (`cheburnexus` + `grep` + `repowise`)
  regraded against the fixed 4-assembly oracle; zod (`cheburnexus-ts` + `grep`) rerun the same day.
- `results/published-candidate-2026-09-25-repowise/` — `repowise` for every repo, live. Its own
  Polly row still used the OLD Core-only oracle, so Polly's `repowise` row is taken from the
  `-fix` run instead (generated on top of bench commit 2baf14c specifically to close this gap).

## Provenance

- Engine: CheburNexus, commit `d6534f1c` (repo `D:\DEV\LLM-CheburNexus`, branch `main`),
  built as `dist-all/_bench-engine-publish-2026-09-25/arch-computer.exe`.
- Bench: this repo, commit `2baf14c` (`fix(grader): bucket.py больше не падает на cp1251-консоли
  Windows`) — the state the Polly `repowise` rerun and this assembly ran against.
- Date: 2026-09-25.
- OS: Windows-10-10.0.19045-SP0.
- .NET SDK: 10.0.400.
- Python: 3.12.8.

## What's excluded

- `cheburnexus-ts` for **zod** (`colinhacks/zod`) — under investigation in parallel (a read-only
  issue on the TS arm), not published here yet. `zod`'s `grep` and `repowise` rows are published.
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
| zod | with-tests | — / 0.000 | — / 0.000 | under investigation |

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

`results/published/` is ~14 MB total; the largest single file is
`grep/Humanizer/without-tests/edges.jsonl` at ~4.2 MB. Nothing approaches a 50 MB/file
concern — zod's only published arms here (`grep`, `repowise`) are both small (0 and 126
edges); the large zod `cheburnexus-ts` row is excluded pending the parallel investigation.
