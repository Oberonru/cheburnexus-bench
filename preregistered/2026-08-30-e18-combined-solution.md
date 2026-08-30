# e18 — the arm asked the engine about one project at a time (pre-registration)

## ⚠ Disclosure first: this forecast was NOT made blind

The sizing run was launched as "arm only", but `runner/run.py` grades every cell as it runs, so the
graded numbers for the cheburnexus arm **already existed on disk** when the reconstruction below was
computed. The reconstruction reproduced the e17 baseline exactly (24 of 24 cell scores) and then
matched the e18 grades exactly — but it had the answer available, so it is a CHECK, not a forecast.
Recorded here rather than quietly presented as a prediction.

What is still genuinely unknown, and IS pre-registered below: the other two arms, which were not run
at all in the sizing run.

## The defect (in the HARNESS, measured on both sides)

`arms/cheburnexus/run.py` invoked the engine on ONE `.csproj` at a time. A sibling in-scope
`<ProjectReference>` then entered that run only as a compiled DLL, and the engine's declaration index
is built from parsed SOURCE — so every call into a sibling first-party project was counted
`DroppedExternal`: **not a missing edge but a WRONG FACT**, publishing that a member in the corpus
lives in another assembly.

Proven on both sides with the same engine binary: per-project run of `Serilog.Tests.csproj` gives
`CreateLoggerThrowsIfCalledMoreThanOnce` → `{"Calls": [], "DroppedExternal": 4}`; the same binary on
`Serilog.sln` gives the three real edges into `Serilog.LoggerConfiguration`. The arm applies no
edge-level filter that could have removed them — it relayed an empty list faithfully.

Fix: one engine invocation per cell over a synthetic solution listing EXACTLY the in-scope candidates
(`armkit.in_scope` unchanged — not one project more or fewer), written into the arm's own scratch dir,
never into the checkout. Per-project coverage accounting is preserved by checking which candidates
contributed files, so a project that produces nothing still fails the cell loudly. The per-project
path is kept as a fallback, and the manifest states which mode produced the graph.

## Raw-edge sizing (before any grading was read)

| cell | base | new | added | removed |
|---|---|---|---|---|
| Polly / without-tests | 785 | 849 | +64 | 0 |
| FluentValidation / without-tests | 792 | 795 | +3 | 0 |
| serilog / without-tests | 847 | 847 | 0 | 0 |
| serilog / with-tests | 1541 | 2593 | +1052 | 0 |

serilog/without-tests has a single in-scope project, so 0 is the sanity check that the change adds
nothing where there is no sibling to reach.

## What the full matrix must show

**cheburnexus** (already graded in the arm-only run; the full run must reproduce it exactly):
* Polly/without-tests primary: 577→**615** arm rows, 576→**614** matched, precision 0.9983→**0.9984**,
  recall 0.8193→**0.8734**; +38 matched, **0 junk**.
* FluentValidation/without-tests primary: 571→**574**, 570→**573**, precision **0.9983**, recall
  0.9135→**0.9183**; **0 junk**.
* serilog/without-tests: **byte-identical**, every cell.
* serilog/with-tests primary: 1078→**1915** arm rows, 1077→**1905** matched, precision
  0.9991→**0.9948**, recall 0.4618→**0.8169**; +828 matched for **9 junk**, all of one shape
  (`LogEventPropertyValue::ToString`, `ILogEventSink::Emit` — a virtual/interface callee where the
  oracle names a different member). Also +3 in the external cell and +16 in the enumerator cell, all
  matched.

**grep and repowise: byte-identical to e17 in all four cells.** This is the impartiality check and
the only truly blind part of this run — the change touches one arm's invocation shape, and if a
rival's number moves, the harness is not neutral and the finding is that, not the score.

## Kill criterion
Any deviation from the numbers above refutes the reconstruction. A rival arm moving at all refutes
the change's neutrality.
