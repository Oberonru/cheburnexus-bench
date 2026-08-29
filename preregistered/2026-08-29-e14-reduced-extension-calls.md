# Pre-registration — e14, reduced extension-method calls

Written and committed **before** the graded run. Engine commit `1c9c25af`, engine binary
`dist-all/_bench-engine-e14-reduced-extensions`. Baseline is the current standing numbers,
`results/2026-08-29-e13c-identity-hardening` (identical to e13 and e13b in all twelve arm×cell pairs).

Change under test: a PRODUCT fix, not an arm translation. `SemanticCallsResolver` now un-reduces the
symbol at a call site (`sym = sym.ReducedFrom ?? sym`) once, where the symbol is established, so that
both consumers — the index lookup and the edge's `Kind` — read the DECLARED method rather than the
reduced one Roslyn hands back at `receiver.Method(x)`.

## What was wrong, established by running the mechanism

A ten-line project through the bench's own engine binary:

| call site | edge before the fix |
|---|---|
| `Ext.Plain(this, "d")` — plain static | ✅ |
| `Ext.Tag(this, "c")` — extension, **unreduced** | ✅ |
| `this.Tag("a")` — extension, **reduced** | ❌ nothing |
| `this.Generic("e")` — generic extension, reduced | ❌ nothing |

Not the category "extension methods" — the reduced invocation form, which is how essentially all
real code is written. On Polly, **53 of 54** first-party extension methods never appeared as a call
target anywhere in the engine's raw output, against **48 of 191** plain statics that did.

⛔ And it was never a silent loss. The symbol IS resolved, so the lookup miss increments
`DroppedExternal` — a bucket that means "the callee lives in another assembly". It does not. We were
publishing a false statement about where a first-party method lives.

## Raw-edge sizing, measured with BOTH binaries

Arm run alone (no grading), old engine vs new, unique rows in `edges.jsonl`:

| cell | before | after | delta |
|---|---|---|---|
| Polly / without-tests | 684 | 713 | **+29, −0** |
| FluentValidation / without-tests | 687 | 774 | **+87, −0** |
| serilog / without-tests | 827 | 827 | **+0, −0** |
| serilog / with-tests | 1438 | 1476 | **+38, −0** |

Pure additions in every cell — nothing was removed. Polly's added edges name exactly the witnesses
the diagnosis predicted (`AddStrategy`, `WithCallerCancellationToken`, `DelayAsync`,
`AddResiliencePipeline*`, the `AddChaos*` family).

## The forecast

Computed by reconstruction before the run: `grader/grade.py`'s own `load_oracle` / `Cell.score` run
against both arm outputs. ⚠ Stated honestly — that reconstruction does **not** reproduce the official
baseline exactly (it reads Polly's baseline as 479 matched where the pipeline says 480, and
FluentValidation's arm-in-cell as 510 where the pipeline says 502, because the official path applies
scope filtering the reconstruction does not). Its **deltas** are trustworthy — the same function on
both sets — so the forecast is the official baseline plus the reconstructed delta, and the absolute
numbers carry ±1–3 rows of uncertainty from that mismatch. The delta claims do not.

| cell | matched | precision | recall |
|---|---|---|---|
| Polly / without-tests | 480 → **505** (+25) | 1.0000 → **1.0000** | 0.6828 → **0.7184** |
| FluentValidation / without-tests | 501 → **556** (+55) | 0.9980 → **0.9982** | 0.8029 → **0.8910** |
| serilog / without-tests | 494 → **494** (+0) | 1.0000 → **1.0000** | 0.9199 → **0.9199** |
| serilog / with-tests | 992 → **1017** (+25) | 1.0000 → **1.0000** | 0.4254 → **0.4361** |

**The sharpest claim, and the one that can most easily be wrong: junk does not increase in ANY cell.**
Every added edge that lands in the primary cell matches an oracle row — Polly 0→0, FluentValidation
1→1, serilog 0→0 and 0→0. A product fix that adds 154 raw edges across four cells and adds not one
junk edge is a strong statement; if the run shows junk anywhere, the mechanism is not what this
document says it is.

## BYTE-IDENTICAL requirements

1. **grep and repowise, all four cells** — this is an engine change inside the cheburnexus arm's
   binary and cannot touch another arm. Any movement is a harness defect, not evidence about this
   change.
2. **cheburnexus's `edges.jsonl` for serilog / without-tests** — the raw diff measured above is
   exactly zero, so this file must not differ by one byte. Serilog's core has no reduced call to a
   first-party extension method.

## Impartiality

An engine change moves ONE arm by construction; the "all arms move together" test does not apply,
same as e9/e10/e13. What DOES apply and is checked: grep and repowise byte-identical, and no cell
losing precision or recall anywhere.

## Kill criteria

1. Junk increases in any cell ⇒ the stated mechanism is wrong; publish the number, stop, find out why.
2. Any cell loses precision or recall.
3. grep or repowise move by one edge in any cell.
4. serilog / without-tests `edges.jsonl` is not byte-identical.
5. Matched rows land outside the forecast ±3 in any cell (the uncertainty the reconstruction's own
   baseline mismatch justifies, and no more).

⛔ Nothing about the engine, the arm, the grader or the corpus is touched after these numbers are seen.

Run command:
`CHEBURNEXUS_ENGINE=…/_bench-engine-e14-reduced-extensions/arch-computer.exe PYTHONHASHSEED=0 python3 runner/run.py --checkouts corpus --out results/2026-08-29-e14-reduced-extension-calls`

---

# RESULT — run and grading, 2026-08-29

`results/2026-08-29-e14-reduced-extension-calls`, engine `1c9c25af`, all three arms, all four cells.

| cell | arm edges | matched | junk | precision | recall | forecast |
|---|---|---|---|---|---|---|
| Polly / without-tests | 505 | **505** | **0** | 1.0000 | 0.6828 → **0.7183** | ✅ exact |
| FluentValidation / without-tests | 557 | **556** | 1 | 0.9980 → **0.9982** | 0.8029 → **0.8910** | ✅ exact |
| serilog / without-tests | 494 | 494 | 0 | 1.0000 | 0.9199 | ✅ unchanged |
| serilog / with-tests | 1017 | **1017** | **0** | 1.0000 | 0.4254 → **0.4361** | ✅ exact |

Every matched count landed on the point estimate — 505 / 556 / 494 / 1017 — not merely inside the
±3 the reconstruction's own baseline mismatch would have excused.

All five kill criteria cleared:

1. ✅ **Junk did not increase in any cell** — the sharpest claim in this document, and the one most
   able to fail. 154 raw edges added across four cells, not one of them junk. Polly 0→0,
   FluentValidation 1→1, serilog 0→0 and 0→0.
2. ✅ No cell lost precision or recall; three cells hold precision 1.0000 and FluentValidation rose.
3. ✅ grep and repowise byte-identical to baseline in all four cells — eight files, `result.json`s
   identical objects, not merely equal numbers.
4. ✅ serilog / without-tests cheburnexus `edges.jsonl` byte-identical, as the raw sizing required.
5. ✅ Matched rows exact, well inside ±3.

**Polly's primary cell keeps precision 1.0000 while recall rises 0.6828 → 0.7183**, and
FluentValidation gains **+0.0881 recall** — the largest single-cell recall move of the campaign so far
on that repository. serilog/without-tests did not move by one byte, exactly as measured in advance:
its core has no reduced call to a first-party extension method.

## What this closes

The largest open bucket in [[finding-polly-recall-gap-bucketed-2026-08-29]] — "callees that vanish
entirely, 75 rows, mechanism UNKNOWN" — is closed with a named mechanism and a product fix. The
"silent loss" reading was itself wrong: these edges were counted as `DroppedExternal`, i.e. published
as a false claim about which assembly a first-party method lives in.

Still open on Polly: record-synthesized members (44) and C#12 primary constructors (≥15).
