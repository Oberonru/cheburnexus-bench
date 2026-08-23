# The arms layer

The oracle says what the truth is. The grader says who matched it. This layer is everything in
between: the three tools under test, and the runner that drives them.

## The shape

```
corpus.json ──▶ runner ──▶ arm (live or replayed) ──▶ edges.jsonl ──▶ grader ──▶ result.json
                   │                                       │
                   └────────────── manifest.json ──────────┘
```

Each arm is a separate program behind one CLI contract. The runner knows nothing about how an arm
works, and an arm knows nothing about the oracle, the grader, or the other arms. That isolation is
not tidiness — an arm that could see the answer key would be able to score against it.

## The arm contract

```
python3 arms/<name>/run.py --repo <checkout> --cell with-tests|without-tests --out <edges.jsonl>
```

- writes the edge format from `grader/EDGE_FORMAT.md`, one JSON object per line;
- writes nothing else to stdout — diagnostics go to stderr;
- exits non-zero only when it could not run at all. Producing *no* edges is a result, not a failure;
- **never reads** `oracle/`, `*-oracle.jsonl`, `overrides.json`, or `corpus.json`. The runner passes
  a checkout path and a cell name; that is the whole input.

Every arm additionally answers `--describe`, printing one JSON object with its `name`, `version`
(the tool's own version, not ours), and `mode` (`live` or `replay`). That line is copied verbatim
into the manifest, so a result always carries what produced it.

## Live arms and replayed arms

This is the mechanism behind "auditable, not rerunnable", and it must be a real feature rather than
a sentence in the README.

- A **live** arm executes the tool. `grep` and `repowise` are live for everyone: both are free, and
  an outsider running the polygon reproduces those columns end to end.
- A **replayed** arm reads its rows from `results/published/<arm>/<repo>/<cell>/edges.jsonl`,
  committed to this repository. The Cheburnexus arm is replayed for anyone without a licence: its
  raw per-edge output is published, so the grader regrades it, the oracle can be rebuilt from
  scratch, and every number can be checked — what cannot be repeated without the product is the
  *generation* of those rows.

The runner picks the mode per arm, records it in the manifest, and **prints it in the result table**.
A replayed column is never presented as if it had been executed on the reader's machine.

An arm that is neither runnable nor published is reported as `unavailable`, and the cell is empty
rather than zero. A missing measurement and a measurement of zero are different facts.

## Cells

Each repository is measured twice: `with-tests` and `without-tests`. The split lives in
`corpus.json` — assemblies for the oracle side, source globs for the arm side. The competitor's own
numbers fall hardest on the with-tests cell, so it is not optional.

The runner passes the cell to the arm; the arm decides what that means for its own input (which
source files it reads). The oracle side is selected by assembly, and the two must agree — a repo
whose test sources are not separable by path does not get a with-tests cell at all, and that is
recorded.

## Determinism and provenance

`manifest.json` beside every result carries: corpus name and sha, the arm's `--describe` line, the
oracle build id, the grader rules version, the host OS and .NET SDK, the UTC timestamp, and the
wall-clock duration. A number without that block is not publishable.

Runs are independent: no arm sees another arm's output, and order does not matter. If we ever add a
measurement where order could matter (anything touching a warm cache), it gets randomised and the
seed goes in the manifest.

## What each arm is

| arm | what it does | mode |
|---|---|---|
| `grep` | the baseline an agent falls back to: find declarations by regex, grep for call sites, attribute each to the nearest enclosing declaration above it | live |
| `repowise` | the competitor, pinned by version, driven through its own export path | live |
| `cheburnexus` | our engine, output converted to the edge contract | live for us, replayed for everyone else |

`grep` is not a strawman and must not be built as one. It is what the tooling actually competes
with, and if the compiler-grade arm cannot beat it by a wide margin the whole thesis is wrong. Its
rules are fixed in the pre-registration before it is run, exactly like everything else.
