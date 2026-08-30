# e17 — an operator USE is a call site (pre-registration)

Written BEFORE the graded run, after raw-edge sizing, and against the e16b-postreview baseline.

## What changed

**Engine** (`SemanticCallsResolver`): `NodesIn` now yields the operator-use shapes —
`BinaryExpressionSyntax`, prefix/postfix unary, compound assignment, `CastExpressionSyntax` — as
CANDIDATES, and `CollectEdges` keeps only those that bound to a DECLARED operator
(`MethodKind.UserDefinedOperator` / `Conversion`). Two exclusions move no counter: a built-in
operator is an IL instruction, not a call (`i + 1` is `add`), and `e += handler` binds to an event's
add accessor, for which pass 1 has no key — taking it would drop a first-party member as
DroppedExternal, i.e. publish that it lives in another assembly.

**Arm** (`_il_operator_method`): now takes the declared arity. `operator-` with ONE parameter is
`op_UnaryNegation`, not `op_Subtraction`; same for `operator+` → `op_UnaryPlus` and
`operatorchecked-` → `op_CheckedUnaryNegation`. Arity is read at depth zero (`Box<A,B>` holds a comma
that belongs to a type, not to the parameter list).

## Sized by running the mechanism, not by reading

Both binaries published (`_bench-engine-e17-baseline` at HEAD `d86c974b`, `_bench-engine-e17-operator-uses`),
cheburnexus arm run over the whole corpus with each, `edges.jsonl` diffed per cell:

| cell | base edges | feat edges | added | removed |
|---|---|---|---|---|
| Polly / without-tests | 785 | 785 | 0 | 0 |
| FluentValidation / without-tests | 792 | 792 | 0 | 0 |
| serilog / without-tests | 847 | 847 | 0 | 0 |
| serilog / with-tests | 1541 | 1541 | 0 | 0 |

Byte-identical in all four, reproduced twice from clean `.work`. What DID move is the honest drop
counter: DroppedExternal +1 / +10 / +8 / +12. Every newly-seen site traced to `== null`,
`typeof(X) == typeof(Y)`, `.GetType() != …` or a BCL struct comparison (`ActivityTraceId`,
`ActivitySpanId`) — genuinely external, previously counted NOWHERE. A silent loss became a disclosed
one; the graph itself does not move because this corpus declares no first-party operator that is
USED anywhere in first-party code.

⚠ That sizing run used the pre-narrowing feature binary (before the UserDefinedOperator/Conversion
gate), so some of those +31 were event `+=` accessors and are now gone again. The zero-added-edges
result is unaffected.

Arm side, measured on the same artifacts: the four oracles contain **zero** rows naming
`op_UnaryNegation`, `op_UnaryPlus` or `op_CheckedUnaryNegation`, and the arm emits **zero** edges
named `op_Subtraction`/`op_Addition`. The arity fix therefore cannot move a graded number on this
corpus — it is a correctness fix landing on ground the corpus does not cover.

## Forecast (kill criterion: any deviation refutes the reconstruction)

Every cell of the full matrix, for ALL THREE arms, is **identical to `2026-08-29-e16b-postreview`**.
For cheburnexus/Polly/without-tests specifically: oracle 703 · matched 576 · precision 0.9983 ·
recall 0.8193, and `edges.jsonl` byte-identical to the baseline's.

If any number moves, the reconstruction above is wrong and the change is not understood — the
finding is then the deviation, not the score.

## What this experiment cannot show

The benchmark is blind to a defect its corpus never exercises. Zero movement here is NOT evidence
the fix is correct; the evidence for correctness is the three behaviour tests
(`OperatorUse_BinaryUnaryCompoundAndCast_AreCallSites`,
`OperatorUse_OnARecord_ReachesTheSynthesizedOperator`,
`BuiltinOperatorUse_IsNotACallSite_AndMovesNoCounter`), the first two proven to fail on the pre-fix
binary by BEHAVIOUR (`Sequence contains no matching element`, in a compiling run), and the arm's
arity check, proven to fail against an arity-ignoring stub of the same interface.
