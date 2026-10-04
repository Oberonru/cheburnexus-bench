# Decision — a nested type inside generic arguments no longer splits the type name

This is a defect in the grader, not a conflict between two spellings. It is recorded here because
`grader/grade.py` says a rule change belongs in writing, not in a quiet edit.

## What was wrong

`method_key` cut the declaring type at every `/` first, and only then removed generic arguments from
each piece. IL writes a nested type used as a generic argument with the same slash:

    ClientMessage`1<Mirror.X/NetMessage>::Send

The old code cut inside `<...>` and produced the key

    ClientMessage`1/NetMessage::Send

The engine, reading source, writes ``ClientMessage`1::Send``, which is right. So the answer key spelled
a method wrongly, and every edge to it counted as a miss for the arm. The error was on the grader's
side, and it was charged to the arm.

## What changed

- `grader/grade.py`: new `split_nested()` cuts `Outer/Inner` only at a `/` outside angle brackets.
  `method_key` uses it, and so does the outer-type lookup in `enclosing_user_method` (the only other
  place that cut a type name at `/`). The rule is general; it reads no project name.
- `grader/test_identity.py`: three new key-agreement cases (nested type as an argument of the
  declaring type, the same inside an already nested type, and as an argument of the method). The first
  two fail on the old code, all pass on the new. Verified by running the new test against the old
  `grade.py`.

## Numbers, before and after

Replayed from the stored `edges.jsonl` with the stored oracle; no engine was run.

| project / cell | arm | before (prec / recall) | after |
|---|---|---|---|
| unitystation without-tests (scout2, fix) | cheburnexus | 0.9824 / 0.9724 | 0.9968 / 0.9868 |
| unitystation with-tests (fix) | cheburnexus | 0.9826 / 0.9728 | 0.9969 / 0.9869 |

Matched edges in the primary cell on unitystation without-tests: 39440 -> 40021.

Published results (`results/published`, all arms: cheburnexus, grep, repowise, cheburnexus-ts): the
primary cell of every row is **unchanged to four digits**. The only thing that moved is the oracle
count of the cell "excluded - callee outside the corpus", by a few to a few dozen edges
(for example serilog with-tests 3595 -> 3585, Humanizer 9826 -> 9769). That cell is not a score.
The published `result.json` files were not rewritten.

## Where else this shows

`grader/test_bucket.py` compares a stored local result (`results/published-candidate-2026-09-25/serilog/with-tests`,
not committed) with a fresh grade. That stored copy has the old oracle count 3595 in the excluded cell
"callee outside the corpus"; a fresh grade gives 3585, so the test now refuses to reproduce it. The
primary cell is identical. The stored copy was not overwritten; regrade it to make the test pass again.
