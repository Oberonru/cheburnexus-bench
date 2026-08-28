# e9 — a caller that is a property accessor

Change under test: the **arm** translates a caller that is a property or event accessor into IL's
spelling when emitting rows for the oracle. The engine, its keys and every shipped fact are
untouched.

The argument for doing it there rather than in the product was written first and separately —
`DECISION-accessor-caller-spelling-2026-08-28.md`, committed before a line of code changed. This
file only measures it.

⚠ **The impartiality test does not apply to this run, and that is why the decision needed an
argument.** e8 changed the RULER, so it had to raise every arm or be reverted. e9 changes OUR arm's
own key translation; grep and repowise have their own spellings and are untouched by construction.
A number that rises only for us is therefore the expected outcome here and proves nothing on its
own — the case rests on the decision document, not on this table.

Baseline — e8 (`results/2026-08-28-corpus-scope`), cheburnexus, primary cell:

| cell | oracle | arm edges | matched | junk | precision | recall |
|---|---|---|---|---|---|---|
| Polly / without-tests | 703 | 475 | 459 | 16 | 0.9663 | 0.6529 |
| FluentValidation / without-tests | 624 | 451 | 422 | 29 | 0.9357 | 0.6763 |
| serilog / without-tests | 537 | 451 | 443 | 8 | 0.9823 | 0.8250 |
| serilog / with-tests | 2332 | 920 | 910 | 10 | 0.9891 | 0.3902 |

## 1. What changed

`arms/cheburnexus/run.py` rewrites the caller's method-name segment when the engine's per-call
`Accessor` tag is present: `get`→`get_X`, `set`→`set_X`, `add`→`add_X`, `remove`→`remove_X`, and
`init`→**`set_X`**. An indexer caller is deliberately NOT translated — our key carries no item name
and `[IndexerName]` can change IL's — and the `--project` path has no such edge at all. All of that,
with its reasoning, is in the decision document and is now disclosed in `grader/EDGE_FORMAT.md`.

This is the same boundary at which the arm already renames a constructor to `.ctor`.

## 2. The measurement the forecast rests on

Both versions of the arm were run over all four cells and the raw edge files `sort`-compared:

| cell | edges before | edges after | rows re-spelled | distinct pairs re-spelled |
|---|---|---|---|---|
| Polly / without-tests | 678 | 678 | 6 | 6 |
| FluentValidation / without-tests | 605 | 605 | 7 | 7 |
| serilog / without-tests | 695 | 695 | 6 | 6 |
| serilog / with-tests | 1273 | 1273 | 6 | 6 |

✅ The totals are EQUAL in every cell, as a pure key rewrite must be: nothing added, nothing dropped.
✅ No indexer-shaped caller and no `init`/`add`/`remove` tag occurs anywhere in these corpora — only
`get` and `set` — so the two riskiest branches of the mapping are not exercised by this run and
remain argued rather than measured.

Every re-spelled pair, to be read one by one after grading:

- **Polly** — `AdvancedCircuitBehavior::FailureCount`, `::FailureRate`,
  `CircuitStateController\`1::CircuitState`, `::LastException`, `::LastHandledOutcome`,
  `Outcome::Void` → all `get_`.
- **FluentValidation** — `ValidatorConfiguration::MessageFormatterFactory`, `::PropertyNameResolver`,
  `::DisplayNameResolver`, `::ErrorCodeResolver`, `ValidatorSelectorOptions::CompositeValidatorSelectorFactory`,
  `::MemberNameValidatorSelectorFactory`, `::RulesetValidatorSelectorFactory` → all `set_`.
- **serilog** — `Log::Logger` and `Core.LoggingLevelSwitch::MinimumLevel` → `set_`;
  `LoggerConfiguration::AuditTo`, `::Filter`, `::MinimumLevel`, `::ReadFrom` → `get_`.

🔑 Those six serilog pairs are, name for name, six of the ten junk pairs read out of e8's result —
the evidence that put this item at the top of the queue.

## 3. Predictions — cheburnexus, primary cell

| cell | arm edges | matched | precision | recall |
|---|---|---|---|---|
| Polly / without | 475 → **473–475** | 459 → **463–465** | 0.9663 → **0.975–0.981** | 0.6529 → **0.658–0.662** |
| FluentValidation / without | 451 → **449–451** | 422 → **427–429** | 0.9357 → **0.947–0.953** | 0.6763 → **0.684–0.688** |
| serilog / without | 451 → **449–451** | 443 → **447–449** | 0.9823 → **0.991–0.996** | 0.8250 → **0.832–0.837** |
| serilog / with | 920 → **918–920** | 910 → **914–916** | 0.9891 → **0.993–0.996** | 0.3902 → **0.391–0.393** |

Point estimates: Polly 475/465/0.9789/0.6614 · FluentValidation 451/429/0.9512/0.6875 ·
serilog-without 451/449/0.9956/0.8361 · serilog-with 920/916/0.9957/0.3928.

The bands assume every re-spelled pair becomes a MATCH. That rests on a fact checked in the grader's
source rather than hoped for: **the caller side is never filtered.** `cell_of` classifies an edge
from its CALLEE alone (`grader/grade.py:93`), and `normalize_caller` only un-mangles
compiler-generated lambda/state-machine names — an accessor's IL name contains no `<` and passes
through untouched. So a correctly re-spelled caller cannot be pushed into an excluded cell; it either
matches the oracle's row or stays junk in the same place it already was.

## 4. Named risks, stated before the run

1. **A re-spelled pair may COLLIDE with a pair the arm already emitted** — the grader dedups on
   `(caller, callee)`, so the arm's edge count would fall by one and the gain by one. This is why
   every band's lower end is two below the point estimate rather than at it.
2. **A pair may have been junk for TWO reasons.** If a re-spelled edge's CALLEE is also misspelled
   (an overload the grader's parameter-dropping cannot save, or a target we resolved wrongly), fixing
   the caller alone leaves it junk. Each such survivor must be named individually in the RESULT.
3. **`init`, `add`, `remove` and indexers are unexercised.** The mapping's two riskiest branches —
   `init`→`set_` and the indexer non-translation — have no witness in these corpora. They are argued
   in the decision document and verified against a compiled property, not against this run. ⛔ Do not
   report them as measured.
4. **Polly's `Outcome::Void`** is the one re-spelled caller on a struct rather than a class. Named in
   case struct accessors behave differently in the key; no reason to expect it, and no excuse if it
   does.

## 5. Kill conditions

1. **grep and repowise must be BYTE-IDENTICAL to e8** in all four cells. They are untouched by
   construction, and this run cannot be read at all if they moved.
2. **No cell may lose recall.** A re-spelling that loses a match means the translation is wrong.
3. **Precision must rise in all four cells.** If it falls anywhere, the translation is producing a
   name the oracle does not have and the change is reverted, not tuned.
4. **The arm's raw edge count per cell must be unchanged** — 678 / 605 / 695 / 1273. Already checked
   before this run; re-checked against the published `edges.jsonl` afterwards.
5. **Every one of the 25 re-spelled pairs is read individually in the RESULT**, and any that did not
   become a match is named with the reason.

⛔ Nothing about the translation, the grader or the corpus is touched after these numbers are seen.

Run command: `PYTHONHASHSEED=0 python3 runner/run.py --checkouts corpus --out results/2026-08-28-accessor-caller`
