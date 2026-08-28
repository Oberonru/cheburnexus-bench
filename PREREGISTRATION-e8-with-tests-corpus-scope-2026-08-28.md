# e8 — the with-tests corpus scope mismatch

This is a change to the POLYGON, not to any arm. It is written before the run, and Run A (today's
key and scope) is published beside Run B (the fixed one).

## 1. The defect

In `serilog / with-tests` the arms read five test projects — `corpus.json`'s
`test_assemblies_projects`, consumed by `arms/_lib/armkit.py:scope_dirs_from_entry` — while the
answer key is built from two test assemblies (`test_assemblies`). Three projects are therefore read
but not judgeable: `Serilog.PerformanceTests`, `TestDummies`, `AotTestApp`. Every edge whose CALLER
lives in one of them is junk by construction: the compiler recorded those calls, nobody put them in
the key, and the arm is charged for reporting them correctly.

Measured on the published e7 rows, replicating the grader exactly (928 / 882 / 0.9504 / 0.4004
reproduced to the digit), the cheburnexus arm's 46 junk pairs in that cell break down by CALLER:

| caller project | junk pairs | in the key? |
|---|---|---|
| Serilog.PerformanceTests | 28 | ❌ no |
| TestDummies | 8 | ❌ no |
| Serilog | 8 | ✅ yes — real junk |
| Serilog.Tests | 2 | ✅ yes — real junk |

**36 of 46 — 78% — are junk by construction.** ⚠ This corrects the earlier note that recorded "21
from Serilog.PerformanceTests/TestDummies": that count was taken by CALLEE, and an out-of-scope
assembly costs precision through the CALLER. An edge whose callee lives in an uncovered assembly is
excluded cleanly by `grader/grade.py`'s `build_callee_cell`; only the caller side lands in primary
with nothing to match. (Found while grading e7 — its one wrong forecast clause was this exact
confusion.)

## 2. The rule adopted

> **In the with-tests cell, the arms read exactly the projects whose assemblies the answer key is
> built from.**

A rule, not a per-repository hand-pick — it reads the same for all three corpus entries and gives no
arm anything the others do not get. The runner already refuses a with-tests cell whose key contains
no test assembly at all (`CellNotMeasurable`); this is the same principle applied one level finer.

Three consequences, all of them the change under test:

1. `Serilog.PerformanceTests` and `AotTestApp` build under the pinned SDK 8.0.421 and emit a net8.0
   assembly, so they JOIN the key: added to `test_assemblies`.
2. `TestDummies` targets `netstandard2.0;net462` and emits NO net8.0 assembly.
   `runner/run.py:assemblies_for` keeps only assemblies matching the cell's probed framework — and
   that filter exists to stop one method counting twice from two target frameworks, so it must not
   be weakened. The project therefore CANNOT be covered by a net8.0 key, and leaves
   `test_assemblies_projects` with that reason recorded in `corpus.json`.
3. `build_command` is corrected to build the projects the key now depends on. It previously built
   only `src/Serilog` while listing test assemblies — the corpus was already under-specified, and
   the assemblies happened to be on disk from an earlier hand build.

Plus a GUARD in the runner: when a repository's `test_assemblies_projects` and `test_assemblies` do
not describe the same set, say so loudly rather than measure it. This defect returned unnoticed for
five days; a rule with no enforcement is a comment.

## 3. The inputs, measured before this forecast was written

Neither number below is a score. Both were produced by running the ORACLE and the ARMS, never the
grader.

**The answer key** — the oracle invoked exactly as `runner/run.py:build_oracle` invokes it:

| key | rows | primary comparable set |
|---|---|---|
| A — Serilog + ApprovalTests + Tests | 10292 | **2203** |
| B — the same plus PerformanceTests + AotTestApp | 10733 | **2332** |
| delta | +441 | **+129** |

✅ Key A's primary set reproduces the published 2203 exactly, so the oracle is being driven the way
the runner drives it.

**Each arm's output**, with and without `TestDummies` in scope (serilog / with-tests, raw edges):

| arm | before | after | removed | added |
|---|---|---|---|---|
| grep | 13099 | 12881 | 218 | 0 |
| repowise | 1101 | 1095 | 6 | 0 |
| cheburnexus | 1286 | 1273 | 13 | 0 |

⚠ Of grep's 218, only **45** have a caller inside `test/TestDummies/`; the other **173** have a
caller in `Serilog.Tests` or `Serilog` and a `TestDummies.*` CALLEE — grep resolves type names from
the files in scope, so removing the directory removes its ability to name those callees at all.
Those edges have a callee outside the corpus in BOTH keys, so they sit in the excluded cell and
cannot touch primary precision either way. repowise and cheburnexus show no such coupling: every
edge they lose has a caller physically inside `test/TestDummies/`.

## 4. Predictions — serilog / with-tests, primary cell, all three arms

⚠ **Recall is NOT comparable between A and B.** The answer key grew 2203 → 2332 by construction, so
a recall fall is expected and is not a defect. Precision within the cell, and the impartiality
check, are the comparisons that mean anything.

| arm | arm edges | matched | precision | recall |
|---|---|---|---|---|
| grep | 5787 → **5740–5790** | 1644 → **1690–1750** | 0.2841 → **0.290–0.310** | 0.7463 → **0.720–0.755** |
| repowise | 1077 → **1065–1077** | 616 → **625–650** | 0.5720 → **0.575–0.620** | 0.2796 → **0.265–0.285** |
| cheburnexus | 928 → **912–920** | 882 → **902–910** | 0.9504 → **0.985–0.992** | 0.4004 → **0.383–0.392** |

Point estimates: grep 5765/1724/0.299/0.739 · repowise 1072/636/0.593/0.273 · cheburnexus
916/906/0.989/0.389.

The cheburnexus band is narrow for a reason worth stating: whether one of the 28 PerformanceTests
pairs ends up MATCHED (its callee is primary in Key B) or merely leaves the primary cell (its callee
is an accessor there — the known `get_`/`set_` spelling mismatch, plan item 3), it stops being junk
either way. Precision is `(882+k)/(892+k)` for k ∈ [20,28], which is 0.989 at both ends. Recall is
the number that moves with k.

## 5. Kill conditions

1. **Impartiality — the one that matters.** Precision must rise for ALL THREE arms. If it rises for
   cheburnexus and not for grep and repowise, this change flatters its author and is reverted, not
   explained. (The same test the answer-key fix of 2026-08-25 had to pass.)
2. **No leakage.** The other three cells — Polly/without-tests, FluentValidation/without-tests,
   serilog/without-tests — must be BYTE-IDENTICAL to e7 for every arm. This change touches one cell.
3. **No junk by construction may survive.** After the run, cheburnexus's remaining junk in this cell
   must be ~10 pairs and EVERY one of them must have a caller in `Serilog` or `Serilog.Tests`. A
   surviving `PerformanceTests`/`TestDummies`/`AotTestApp` caller means the fix is incomplete.
4. **The guard must fire on the broken configuration.** Proved by running it against today's
   `corpus.json` before the fix, not by reading it.
5. Both runs are published: Run A is the existing `results/2026-08-28-implicitbase`, Run B is a new
   directory, and the RESULT section shows them side by side with the different-key caveat attached.

⛔ The corpus, the grader and this rule are not touched again after the numbers are seen. If the
result contradicts the forecast, the forecast is what was wrong.

Run command: `PYTHONHASHSEED=0 python3 runner/run.py --checkouts corpus --out results/2026-08-28-corpus-scope`

---

# RESULT — run of 2026-08-28, `results/2026-08-28-corpus-scope` (Run B) beside `results/2026-08-28-implicitbase` (Run A)

⚠ The two runs use DIFFERENT answer keys — 2203 primary edges in A, 2332 in B. Precision within the
cell is comparable; **recall is not**, and its fall is by construction.

serilog / with-tests, primary cell:

| arm | arm edges | matched | precision | recall |
|---|---|---|---|---|
| grep | 5787 → **5410** (pred 5740–5790 ❌) | 1644 → **1734** (pred 1690–1750 ✅) | 0.2841 → **0.3205** (pred 0.290–0.310 ❌) | 0.7463 → **0.7436** (pred 0.720–0.755 ✅) |
| repowise | 1077 → **1071** (pred 1065–1077 ✅) | 616 → **637** (pred 625–650 ✅) | 0.5720 → **0.5948** (pred 0.575–0.620, pt 0.593 ✅) | 0.2796 → **0.2732** (pred 0.265–0.285, pt 0.273 ✅ exact) |
| cheburnexus | 928 → **920** (pred 912–920 ✅) | 882 → **910** (pred 902–910 ✅) | 0.9504 → **0.9891** (pred 0.985–0.992, pt 0.989 ✅ exact) | 0.4004 → **0.3902** (pred 0.383–0.392, pt 0.389 ✅) |

Ten of twelve numbers landed in band; both misses are grep's, and they are one miss with two faces.

## The miss, and the channel it exposed

I predicted grep's arm count would barely move, reasoning that the 173 `TestDummies`-callee edges
sit in the excluded cell either way. It fell by 377. Counted, not guessed — every arm pair labelled
by `cell_of` under both keys:

- **162** primary edges left the ARM entirely when `TestDummies` left the read scope;
- **215** primary edges MIGRATED to `excluded — callee outside the corpus`, unchanged themselves.

The 215 are the channel I did not model. **Enlarging the answer key does not only add rows an arm
can match — it also teaches the grader that more CALLEES are external.** `Serilog.PerformanceTests`
drags BenchmarkDotNet, xunit and the test platform in with it, so the oracle's external cell grew
3459 → 3595 and `build_callee_cell` began recognising callees that previously fell through to
primary as junk. grep, which names far more callees than it can resolve, had the most to gain.

That is the same class of error as e7's one wrong clause: **I keep modelling the caller side of the
key and forgetting the callee side.** e7 predicted junk from a callee that turned out to be
excluded; e8 predicted no movement from callees that turned out to migrate. Written down as a
standing correction, not as a one-off.

## The checks, run rather than inferred

**Kill 1 — impartiality, the one that matters.** Precision rose for ALL THREE arms:
grep **+0.0364**, repowise **+0.0228**, cheburnexus **+0.0387**. The change does not flatter its
author; it removes junk every arm was being charged for.

**Kill 2 — no leakage.** All nine other-cell edge files (three cells × three arms) are
BYTE-IDENTICAL to Run A. The change touches exactly one cell.

**Kill 3 — no junk by construction may survive.** cheburnexus's junk in this cell is **exactly 10
pairs — the number predicted — and every one has a caller in `Serilog` or `Serilog.Tests`**, both
covered by the key. Zero from `Serilog.PerformanceTests`, `TestDummies` or `AotTestApp`. Read one by
one, **six of the ten are the accessor-as-caller spelling** — `LoggerConfiguration::AuditTo`,
`::Filter`, `::MinimumLevel`, `::ReadFrom`, `Log::Logger`, `LoggingLevelSwitch::MinimumLevel`, where
IL names `get_`/`set_` and we name the member a human wrote. 🔑 With the construction junk gone,
**plan item 3 is now the dominant residual junk source in this cell**, which is exactly the evidence
that item needed before its argument can be written.

**Kill 4 — the guard fires.** Run against the pre-fix configuration it produces:
`test_assemblies_projects and test_assemblies disagree for net8.0: the arms are scoped to read
test/AotTestApp/AotTestApp.csproj, test/Serilog.PerformanceTests/Serilog.PerformanceTests.csproj,
test/TestDummies/TestDummies.csproj, but test_assemblies lists no matching net8.0 assembly for them
— build the project and add its assembly to test_assemblies, or drop it from
test_assemblies_projects.` Against the fixed configuration it is silent. Polly and FluentValidation,
whose `test_assemblies` are empty, still get the older and more specific `CellNotMeasurable`
message — checked by invoking `build_oracle` on the real entries, not by reading the code.

**Kill 5 — both runs published**, side by side, with the different-key caveat attached above.

## Standing numbers after e8

| cell | grep | repowise | cheburnexus |
|---|---|---|---|
| Polly / without-tests | 0.246 / 0.247 | 0.367 / 0.191 | **0.9663 / 0.6529** |
| FluentValidation / without-tests | 0.207 / 0.389 | 0.357 / 0.183 | **0.9357 / 0.6763** |
| serilog / without-tests | 0.260 / 0.542 | 0.544 / 0.348 | **0.9823 / 0.8250** |
| serilog / with-tests | 0.3205 / 0.7436 | 0.5948 / 0.2732 | **0.9891 / 0.3902** |

⚠ serilog / with-tests is measured against a DIFFERENT answer key from every earlier run and is not
comparable to them on recall. The other three cells are unchanged from e7, byte for byte.
