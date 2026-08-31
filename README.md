# cheburnexus-bench

A polygon for measuring what a code index actually hands an AI agent — and for letting anyone
check the numbers instead of trusting them.

**Status: under construction. Nothing here has been measured yet.**

## The rule this repository exists to enforce

Every graph-quality number in this field is scored against something the publisher controls. That
is the defect: a tool that is confidently wrong in a consistent way scores well, and a reader has
no way to check.

So the answer key here is **not produced by any measured tool**. For C# it is extracted from the
IL the compiler emitted — `dotnet build`, then the call sites read out of the assemblies and
anchored back to source lines through the PDB. Anyone with the .NET SDK regenerates it from the
same pinned commit and gets the same answer key.

## Auditable, not rerunnable

One arm — the Cheburnexus engine — is a commercial product and its binary is **not** published
here. Say so plainly rather than bury it:

- the corpus (pinned by commit), the **oracle builder**, the grader, the free arms and the **raw
  per-edge output of every arm, ours included** are all published;
- a sceptic rebuilds the answer key himself, re-grades our published rows with his own grader, and
  reproduces the other arms end to end;
- what he cannot do is regenerate our rows from scratch. That needs the product.

The claim this repository makes is therefore precise: **the numbers are auditable, not rerunnable.**

## Layout

    oracle/csharp/     the C# answer key: IL -> call edges -> source anchors (Mono.Cecil)

Everything else — corpus pinning, grader, arms, results — is not written yet.

## Reading a result

A published number is a total, and a total does not say where to look next. `grader/bucket.py` takes
one apart, importing the grader's own key and cell functions so the decomposition is of the published
figure and not of a near-miss recomputation — it **reproduces `result.json` exactly before it groups
anything, and refuses to continue otherwise**.

    python3 grader/bucket.py bucket results/<run> --repo Polly --cell without-tests
    python3 grader/bucket.py diff  results/<baseline> results/<run>

`bucket` groups the misses and the junk by callee, by callee and caller type, by member shape, by the
file the ANSWER KEY anchors the caller to, and by whether the arm emits that callee from any other
caller at all. `diff` says what a change moved, and leads with the kill criterion: **any oracle row
the baseline matched and this run does not**. That question is why the tool exists — a total cannot
show a trade, and a silent loss has been measured moving the honest counters the wrong way.

This procedure is not optional advice to ourselves. Every experiment here that ranked the remaining
gaps by how they LOOKED was refuted by measurement; the ones that bucketed first found single causes
behind whole cells.

## Pre-registration

Predictions are committed before a run, and a missed prediction is graded on the same page as the
result. The first one is `PREREGISTRATION-e1-csharp-edge-precision.md`.
