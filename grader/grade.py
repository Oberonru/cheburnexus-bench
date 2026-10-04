#!/usr/bin/env python3
"""Grade one arm's call edges against the compiler-derived answer key.

The rules this file implements were fixed in the pre-registration BEFORE any measured run. They are
kept in one place, named, and printed with the result, so that a reader can see the boundary that
produced a number instead of inferring it. Changing a rule changes every published figure, so a
change belongs in the pre-registration's deviations section, not in a quiet edit here.

Usage:
    grade.py --oracle oracle.jsonl --arm arm.jsonl [--overrides overrides.json] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

# ── Identity ──────────────────────────────────────────────────────────────────
# Cecil writes "System.Void Ns.Type::Method(System.Int32)". Arms write "Ns.Type::Method". Both
# collapse to the same key: type + method name, no return type, no parameter types. EDGE_FORMAT.md
# explains what that costs and why it is still the right trade for the question being asked.

#   /   nested-type separator (Outer/Inner)             — e.g. Pipeline/ReloadFailedArguments
#   !   IL generic placeholder inside a modreq/method sig — e.g. !0, !!0
#   ()  a modreq(...)/modopt(...) clause on the return type
# all appear in real Cecil return-type prefixes and were previously missing from this class,
# so method_key() silently returned None for those rows. See PREREGISTRATION deviations, 2026-08-25.
_CECIL_FULLNAME = re.compile(r"^(?:[\w./!()<>`\[\],&*+ ]+\s+)?(?P<type>[^\s]+)::(?P<method>[^(]+)")

# Roslyn's mangled names for code it moved out of the method a human wrote:
#   <Write>b__4_0   lambda        <Emit>d__7    async / iterator state machine
#   <>c__DisplayClass9_0          closure holder
# Marks the closing `>` immediately before the b__/d__/g__/f__ tag. The enclosing name is found by
# scanning backwards from this marker and balancing brackets to the matching `<` (see
# `_extract_enclosing` below) rather than by a flat `<([^>]+)>` capture, which breaks the moment the
# enclosed name has angle brackets of its own — a generic parameter inside an explicit-interface
# qualifier, or a lambda nested inside another closure. See PREREGISTRATION deviations, 2026-08-25.
_MANGLED_MARKER = re.compile(r">[bdgf]__")

ENUMERATOR_PROTOCOL = frozenset({"GetEnumerator", "MoveNext", "get_Current", "Dispose"})
COMPARABLE_OPS = frozenset({"call", "callvirt", "newobj", "ldftn"})

# A callee can carry MORE THAN ONE declared override target — a base-class virtual AND an explicit
# interface implementation, say — and they do not always land in the same oracle cell. Polly's
# DisposeAsync overrides are the concrete case: one declared target is the first-party base's own
# declaration (primary), the other is `System.IAsyncDisposable`'s (external). Iterating the
# `overrides` set and returning whichever cell was met first depended on Python's per-process
# string-hash order, not on the data: five runs of the SAME inputs gave grep/Polly precision 0.233
# on some and 0.239 on others (found 2026-08-25 verifying G1/G2; see PREREGISTRATION deviation G3).
# EDGE_FORMAT.md already settles the tie — an arm's edge is classified by the DECLARATION it answers
# for, not the implementation it names — so primary loses to every other cell, deterministically,
# regardless of set order.
CELL_PRECEDENCE = ("external", "accessor", "enumerator", "generated", "op_excluded", "primary")

# The messages the Unity engine itself calls on a MonoBehaviour. No IL instruction in the compiled
# game calls them, so the answer key cannot hold an edge into one from "the engine". An arm that
# reports such an edge is describing real behaviour the key has no way to express; it is not wrong
# in the sense the primary cell measures, so it gets a cell of its own (see `unity_invoked_edges`).
# Only the names Unity documents on MonoBehaviour. Messages of other frameworks (Mirror's
# OnStartServer and friends are ordinary virtuals that Mirror calls) are deliberately absent: they
# are not Unity's, and a list that grew by project would be a thumb on the scale.
UNITY_MESSAGES = frozenset({
    "Awake", "Start", "Update", "FixedUpdate", "LateUpdate", "Reset", "OnValidate",
    "OnEnable", "OnDisable", "OnDestroy",
    "OnApplicationFocus", "OnApplicationPause", "OnApplicationQuit",
    "OnGUI", "OnDrawGizmos", "OnDrawGizmosSelected",
    "OnTriggerEnter", "OnTriggerStay", "OnTriggerExit",
    "OnTriggerEnter2D", "OnTriggerStay2D", "OnTriggerExit2D",
    "OnCollisionEnter", "OnCollisionStay", "OnCollisionExit",
    "OnCollisionEnter2D", "OnCollisionStay2D", "OnCollisionExit2D",
    "OnControllerColliderHit", "OnParticleCollision", "OnParticleTrigger", "OnParticleSystemStopped",
    "OnMouseDown", "OnMouseUp", "OnMouseUpAsButton", "OnMouseEnter", "OnMouseExit",
    "OnMouseOver", "OnMouseDrag",
    "OnBecameVisible", "OnBecameInvisible",
    "OnPreCull", "OnPreRender", "OnPostRender", "OnRenderObject", "OnWillRenderObject",
    "OnRenderImage", "OnAnimatorMove", "OnAnimatorIK", "OnJointBreak", "OnJointBreak2D",
    "OnTransformChildrenChanged", "OnTransformParentChanged", "OnBeforeTransformParentChanged",
    "OnRectTransformDimensionsChange", "OnCanvasGroupChanged", "OnDidApplyAnimationProperties",
    "OnAudioFilterRead",
})


def build_callee_cell(cells: dict) -> dict[str, str]:
    """Which oracle cell first claims each callee, built from the answer key itself.

    Every method the compiler records a call to is classified once, and an arm's edge is judged in
    that same cell. A callee the compiler never records anywhere cannot be defended as "a different
    cell" — it is an edge to something the built code does not call, so it lands in the primary cell
    as junk, which is exactly the quantity this benchmark exists to measure.
    """
    callee_cell: dict[str, str] = {}
    for name, cell in cells.items():
        for _, callee in cell.oracle:
            callee_cell.setdefault(callee, name)
    return callee_cell


def cell_of(callee: str, callee_cell: dict[str, str], overrides: dict[str, set[str]]) -> str:
    """The cell an arm's edge to `callee` is judged in. Deterministic; see CELL_PRECEDENCE above.

    Module-level (not a closure) so the byte-ceiling analysis in ceiling_grep_filter.py imports
    THIS function instead of copying it — a copy silently drifts when the grader is fixed, which is
    exactly what G3 was.
    """
    known = callee_cell.get(callee)
    if known is not None:
        return known
    # An arm that named an implementation must be judged in the cell of the DECLARATION it answers
    # for. Classifying by the implementation moves edges between cells: a first-party class
    # implementing IDisposable would drag a standard-library call into the primary cell and count
    # against precision there, punishing the arm precisely for resolving correctly.
    found = {callee_cell[d] for d in overrides.get(callee, ()) if d in callee_cell}
    for name in CELL_PRECEDENCE:
        if name in found:
            return name
    method = callee.rpartition("::")[2]
    if method.startswith(("get_", "set_")):
        return "accessor"
    if method in ENUMERATOR_PROTOCOL:
        return "enumerator"
    return "primary"


def strip_generic_arguments(segment: str) -> str:
    """Drop the closed generic arguments a compiler writes but a source reader never sees the same way.

    IL names an instantiation: `Guard::NotNull<Polly.ResiliencePipelineBuilder>`, and a different one
    for every type it is called with. A tool reading source sees one method, `Guard.NotNull`, and
    would match none of them. Left in, this alone would collapse recall for every arm — on Polly it
    is 1179 of 3103 callee edges, and distinct nodes fall by 39% once the arguments come off.

    Arity stays (`Cache`1`), because an open and a closed generic type are genuinely different types.
    A segment that *starts* with `<` is a compiler-mangled name (`<Run>d__1`, `<>c`), not a generic
    instantiation, and is left exactly as it is — the caller remap depends on reading it.
    """
    if segment.startswith("<"):
        return segment

    # Balanced removal, not a cut at the first `<`. An explicit interface implementation is named
    # `System.Collections.Generic.IEnumerable<T>.GetEnumerator`, and cutting at the first bracket
    # threw away the method name itself, leaving the interface behind as if it were the method.
    out: list[str] = []
    depth = 0
    for char in segment:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out)


def split_nested(type_name: str) -> list[str]:
    """Split `Outer/Inner` at the nesting slashes only, never at a `/` inside `<...>`."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, char in enumerate(type_name):
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif char == "/" and depth == 0:
            parts.append(type_name[start:i])
            start = i + 1
    parts.append(type_name[start:])
    return parts


def method_key(raw: str) -> str | None:
    """Canonical `Namespace.Type::Method`, or None when the string is not a method reference."""
    match = _CECIL_FULLNAME.match(raw.strip())
    if not match:
        return None

    # Nested types are `Outer/Inner`; each part decides for itself whether it is a mangled name.
    # The cut is made only OUTSIDE angle brackets: Cecil writes a nested type used as a generic
    # argument with the same slash (`ClientMessage`1<X/NetMessage>`), and that slash belongs to the
    # argument, which is stripped away whole — not to the nesting of the type being named.
    type_name = "/".join(strip_generic_arguments(part) for part in split_nested(match.group("type").strip()))
    method = strip_generic_arguments(match.group("method").strip())

    # `.ctor` and `.cctor` are NOT the same method. An instance constructor runs per object, a static
    # one runs once for the type, and merging them would let an arm score a hit on the wrong one.
    return f"{type_name}::{method}"


def _extract_enclosing(text: str) -> str | None:
    """Find a `>[bdgf]__` marker in `text` and return the name enclosed by its matching `<`.

    A flat `<([^>]+)>[bdgf]__` regex captures up to the FIRST `>` it meets, which is wrong the
    moment the true enclosing name contains its own angle brackets:
      - `<<ExecuteAsync>b__0>d` (an async lambda: the flat regex captures `<ExecuteAsync`, a
        truncated key with a stray leading `<`);
      - `<FluentValidation-IValidationRuleInternal<T>-ValidateAsync>d__14` (an explicit interface
        implementation's state machine: the embedded `<T>` gives the flat regex no position where
        `<...>` is immediately followed by `[bdgf]__`, so it never matches at all).
    Scanning backwards from the marker and balancing `<`/`>` finds the correct opening bracket in
    both shapes, because it stops at the first `<` whose nesting depth returns to zero rather than
    at the first literal `>`.

    Returns None — not a confident match — both when the brackets never balance AND when they
    balance onto an EMPTY name. `<>f__AnonymousType0` matches the marker (`>f__`) with its `<` and
    `>` adjacent, so the naive span is `""`. An empty string is not None, so a caller that only
    checked `is not None` would build the garbage key `Outer::` and mark it a *confident* remap —
    exactly what EDGE_FORMAT.md says an unremappable caller must not get.
    """
    match = _MANGLED_MARKER.search(text)
    if not match:
        return None
    close = match.start()  # index of the '>' that immediately precedes b__/d__/g__/f__
    depth = 1
    i = close - 1
    while i >= 0:
        if text[i] == ">":
            depth += 1
        elif text[i] == "<":
            depth -= 1
            if depth == 0:
                content = text[i + 1:close]
                return content if content else None
        i -= 1
    return None  # unbalanced brackets — not a confident match


def enclosing_user_method(key: str) -> tuple[str, bool]:
    """Remap a compiler-generated caller onto the user method it was lifted out of.

    Returns (key, remapped). A caller we cannot resolve confidently is reported, never silently
    attributed to a method that may not be the right one.
    """
    type_name, _, method = key.partition("::")

    for candidate in (method, type_name):
        name = _extract_enclosing(candidate)
        if name is not None:
            outer_type = split_nested(type_name)[0]
            # Roslyn dash-encodes the qualifier of an explicit interface implementation inside a
            # mangled name, because a type-name segment cannot contain a literal '.':
            # `FluentValidation-IValidationRuleInternal<T>-ValidateAsync` stands for
            # `FluentValidation.IValidationRuleInternal<T>.ValidateAsync`. Undo that, then strip
            # generic arguments exactly as an ordinary method name would be stripped, so the
            # recovered key lands on the same spelling EDGE_FORMAT.md fixes for that case
            # ("Explicit interface implementations": `Namespace.IContract.Method`) — and the same
            # spelling the oracle's own non-mangled row for that method already uses.
            name = strip_generic_arguments(name.replace("-", "."))
            return f"{outer_type}::{name}", True

    # A generated type whose name reveals no enclosing method (`<>c`, `<>f__AnonymousType0`), or
    # one where the marker was found but the brackets around it never balance.
    if "<" in type_name or "<" in method:
        return key, False

    return key, False


def normalize_caller(key: str) -> tuple[str, bool]:
    """The caller rule, in the only form both sides can apply: from the name alone.

    An arm sees names and nothing else — no attributes, no metadata. So the answer key must decide
    by name too, or the two sides drop different edges and the grader charges its own asymmetry to
    the arm. Source-generator output proved this the hard way: `<RegexGenerator_g>…/Runner` carries
    no CompilerGeneratedAttribute on the nested method, so a flag-based rule kept it while a
    name-based rule dropped it — 186 edges of disagreement on one repository.

    Returns (key, attributable). Not attributable means the call site cannot be traced to a method a
    human wrote, and the edge counts for nobody on either side.
    """
    remapped, ok = enclosing_user_method(key)
    if ok:
        return remapped, "<" not in remapped
    return key, "<" not in key


@dataclass
class Cell:
    """One published number plus the material behind it."""

    name: str
    oracle: set[tuple[str, str]] = field(default_factory=set)

    def score(self, arm: set[tuple[str, str]], overrides: dict[str, set[str]]) -> dict:
        """Precision is computed on the arm edges that BELONG to this cell, never on all of them.

        An arm that correctly reports a property read or a call into the standard library has not
        made a mistake — that edge simply lives in a different cell. Dividing by the arm's whole
        output would punish every arm for being more complete than the cell it is judged in, and
        would punish the most complete arm hardest.
        """
        matched = set()
        for edge in arm:
            if edge in self.oracle:
                matched.add(edge)
                continue
            # Accept an override of the declared target: IL names the declaration, a source-level
            # tool may name an implementation. Reduction happens on the arm side only.
            # `overrides.get(callee, ())` is a `set` — iterated `sorted()` so which declaration
            # gets recorded in `matched` never depends on Python's per-process hash order. This
            # loop's own COUNT can't change with iteration order (only the FIRST hit is kept, and
            # a break stops it), but it is sorted anyway so nothing here is left unproven. See the
            # module-level `cell_of` for the case where hash order previously did change a number.
            caller, callee = edge
            for declared in sorted(overrides.get(callee, ())):
                if (caller, declared) in self.oracle:
                    matched.add((caller, declared))
                    break

        true_positive = len(matched)
        # An empty cell has nothing to measure. Printing 0.000 there would read as a failed cell
        # rather than an absent one, and a reader cannot tell the two apart from the number alone.
        precision = true_positive / len(arm) if arm else None
        recall = true_positive / len(self.oracle) if self.oracle else None
        return {
            "cell": self.name,
            "oracle_edges": len(self.oracle),
            "arm_edges_in_cell": len(arm),
            "matched": true_positive,
            "precision": None if precision is None else round(precision, 4),
            "recall": None if recall is None else round(recall, 4),
        }


def load_unity_components(path: str) -> set[str]:
    """Type keys of the first-party classes that derive from UnityEngine.MonoBehaviour.

    Written by the oracle (`unity-components.json`, resolved through the base chain from the IL, so
    a class reaching MonoBehaviour through another assembly's base class counts). Names go through
    the same key function as every other type so they join on the grader's own spelling.
    """
    with open(path, encoding="utf-8") as handle:
        names = json.load(handle)
    types: set[str] = set()
    for name in names:
        key = method_key(f"{name}::x")
        if key is not None:
            types.add(key.rpartition("::")[0])
    return types


def is_unity_message(callee: str, unity_types: set[str]) -> bool:
    """Is `callee` (a `Type::Method` key) a Unity message on a MonoBehaviour-derived type?"""
    type_name, _, method = callee.rpartition("::")
    return method in UNITY_MESSAGES and type_name in unity_types


def edge_in_oracle(edge: tuple[str, str], oracle: set[tuple[str, str]],
                   overrides: dict[str, set[str]]) -> bool:
    """The membership test `Cell.score` applies, as a function: the edge itself, or the same call
    to a declaration the named method overrides."""
    if edge in oracle:
        return True
    caller, callee = edge
    return any((caller, declared) in oracle for declared in overrides.get(callee, ()))


def unity_invoked_edges(edges: set[tuple[str, str]], oracle: set[tuple[str, str]],
                        overrides: dict[str, set[str]], unity_types: set[str]
                        ) -> set[tuple[str, str]]:
    """The arm edges the answer key cannot hold because the Unity engine makes the call.

    An edge qualifies when its callee is a Unity message on a MonoBehaviour-derived type AND the
    key has no such edge. An edge the key does have (a real `base.Awake()` call in IL) stays where
    it was and is scored as before. The caller is not looked at: the engine has no method to name.
    """
    return {e for e in edges
            if is_unity_message(e[1], unity_types) and not edge_in_oracle(e, oracle, overrides)}


def load_oracle(path: str, first_party: set[str] | None) -> tuple[dict[str, Cell], dict[str, set[str]], dict]:
    """Split the answer key into the primary comparable cell and the excluded cells."""
    cells = {
        "primary": Cell("primary — first-party ordinary calls and constructors"),
        "external": Cell("excluded — callee outside the corpus"),
        "accessor": Cell("excluded — property accessors"),
        "enumerator": Cell("excluded — foreach protocol"),
        "generated": Cell("excluded — compiler-generated callee"),
        "op_excluded": Cell("excluded — indirect call op (calli / ldvirtftn)"),
    }
    overrides: dict[str, set[str]] = defaultdict(set)
    stats = {"rows": 0, "unremappable_caller": 0, "no_source_anchor": 0, "unparsed_row": 0}
    # Callers the answer key itself could not trace back to a human-written method. Handed to the
    # arm loader so both sides drop the same edges: the arm cannot see a CompilerGeneratedAttribute,
    # only a mangled name, so without this list the two sides disagree and the grader manufactures
    # false positives out of its own asymmetry.


    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            stats["rows"] += 1

            caller = method_key(row["Caller"])
            callee = method_key(row["Callee"])
            if caller is None or callee is None:
                # A row _CECIL_FULLNAME cannot even parse — never dropped silently: a callee the
                # answer key silently forgot would resurface as an arm's false positive, exactly
                # like the op_excluded branch below already guards against, on the path that used
                # to have no guard at all. See PREREGISTRATION deviations, 2026-08-25 (G1).
                stats["unparsed_row"] += 1
                continue

            caller, attributable = normalize_caller(caller)
            if not attributable:
                stats["unremappable_caller"] += 1
                continue
            if row.get("NoDebugInfo"):
                stats["no_source_anchor"] += 1

            edge = (caller, callee)
            callee_method = callee.rpartition("::")[2]

            if row.get("Op") not in COMPARABLE_OPS:
                # Kept as its own cell rather than dropped, so that the callee stays classifiable.
                # A callee the answer key silently forgot would resurface as an arm's false
                # positive — the grader would invent junk that the arm never produced.
                cells["op_excluded"].oracle.add(edge)
            elif first_party is not None and row.get("CalleeAssembly") not in first_party:
                cells["external"].oracle.add(edge)
            elif callee_method.startswith(("get_", "set_")):
                cells["accessor"].oracle.add(edge)
            elif callee_method in ENUMERATOR_PROTOCOL:
                cells["enumerator"].oracle.add(edge)
            elif row.get("CalleeCompilerGenerated"):
                cells["generated"].oracle.add(edge)
            else:
                cells["primary"].oracle.add(edge)

    return cells, overrides, stats


def load_executed_callers(path: str) -> set[str]:
    """The set of methods the answer key has DIRECT EVIDENCE executed — every Caller AND every
    Callee it ever recorded, normalized exactly like an arm's own caller space (`method_key` +
    `normalize_caller`). A Callee is proof the callee ran; a Caller is proof too (it had to run to
    make the call). Union of both, because either role is enough evidence of execution.

    ⛔⛔ RUNTIME ORACLES ONLY (TypeScript's shadow-stack key). Do NOT call this for the C# oracle:
    Cecil's IL key is EXHAUSTIVE — it lists every call the compiler emitted, executed or not — so
    there is no such thing as "never observed executing" there, and filtering by this set would
    silently discard genuine false positives instead of coverage gaps.

    There is NO guard inside this file: the grader cannot tell the two answer keys apart from their
    rows alone (both carry Caller/Callee/Op/CalleeAssembly). The protection is structural and lives
    one level up — runner/run.py passes --executed-only only when corpus.json says the cell's
    language is TypeScript. If you ever invoke grade.py by hand, that is on you.

    This is the same computation `diagnose_precision.py` already does by hand (bucket a); pulled
    in here, named, so a published number can apply it instead of a one-off diagnostic script.
    """
    executed: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            for raw in (row.get("Caller"), row.get("Callee")):
                if not raw:
                    continue
                key = method_key(raw)
                if key is None:
                    continue
                normalized, attributable = normalize_caller(key)
                if attributable:
                    executed.add(normalized)
    return executed


def load_arm(path: str) -> tuple[set[tuple[str, str]], int]:
    """Load an arm's edges under exactly the caller discipline the answer key uses.

    The two sides must drop the same things. An edge whose caller is compiler-generated and cannot
    be traced back to the method a human wrote counts for nobody — so it leaves the arm's output
    too. Applying the rule to one side only turns the grader itself into a source of error, which
    an identity run (grading the answer key against itself) exposes immediately.
    """
    edges: set[tuple[str, str]] = set()
    dropped = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            caller = method_key(row.get("caller") or row.get("Caller") or "")
            callee = method_key(row.get("callee") or row.get("Callee") or "")
            if caller is None or callee is None:
                continue
            caller, attributable = normalize_caller(caller)
            if not attributable:
                dropped += 1
                continue
            edges.add((caller, callee))
    return edges, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--first-party", nargs="*", default=None,
                        help="assembly names of the corpus; omit to treat every callee as in-corpus")
    parser.add_argument("--overrides", default=None,
                        help="JSON map {override_key: [declared_key, ...]} emitted by the oracle")
    parser.add_argument("--unity-components", default=None,
                        help="JSON list of first-party MonoBehaviour-derived types, written by the "
                             "oracle. Adds the 'invoked by the Unity engine' cell. Omit for any "
                             "repository without Unity code: nothing else changes.")
    parser.add_argument("--json", default=None)
    parser.add_argument("--executed-only", action="store_true",
                        help="RUNTIME ORACLES ONLY (TypeScript). Score a primary-cell arm edge "
                             "only when the oracle has direct evidence its CALLER executed "
                             "(the oracle is a record of one test run, not an exhaustive key — "
                             "see load_executed_callers). An edge whose caller the key never saw "
                             "run is withheld into a separate 'unjudged_caller_not_executed' cell "
                             "instead of counting as a false positive. Never pass this for the "
                             "C# oracle: Cecil's IL key is exhaustive-static, every callable is "
                             "listed whether it ran or not, so there is nothing to withhold and "
                             "this flag would silently erase real false positives.")
    args = parser.parse_args()

    first_party = set(args.first_party) if args.first_party else None
    cells, overrides, stats = load_oracle(args.oracle, first_party)

    if args.overrides:
        with open(args.overrides, encoding="utf-8") as handle:
            raw = json.load(handle)
        # The map arrives in raw compiler spelling, exactly like the edges do, and goes through the
        # same key function. Canonicalising it anywhere else would give the two sides a second
        # chance to disagree about what a method is called — the bug this grader already paid for.
        overrides = defaultdict(set)
        for override, declarations in raw.items():
            key = method_key(override)
            if key is None:
                continue
            for declaration in declarations:
                declared_key = method_key(declaration)
                if declared_key is not None and declared_key != key:
                    overrides[key].add(declared_key)

    arm, arm_dropped = load_arm(args.arm)

    # Which cell does each callee belong to? Built from the answer key itself: every method the
    # compiler ever records a call to is classified once, and an arm's edge is judged in that same
    # cell. A callee the compiler never records anywhere cannot be defended as "a different cell" —
    # it is an edge to something the built code does not call, so it lands in the primary cell as
    # junk, which is exactly the quantity this benchmark exists to measure.
    # Built from the answer key itself; classification and the tie-break rule now live at module
    # scope (build_callee_cell / cell_of / CELL_PRECEDENCE) so the byte-ceiling analysis imports the
    # exact same logic instead of copying it — see those definitions for the G3 reasoning.
    callee_cell = build_callee_cell(cells)

    arm_by_cell: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for edge in arm:
        arm_by_cell[cell_of(edge[1], callee_cell, overrides)].add(edge)

    # ── executed-only filter (RUNTIME ORACLES ONLY — see --executed-only help above) ────────────
    # The unfiltered primary cell is scored FIRST and kept, unconditionally, as the "old strict"
    # number (denominator = every arm edge in the primary cell). Then, only if the flag is set, the
    # primary cell's arm set is split: edges whose caller the oracle never saw execute move to a
    # new 'unjudged_caller_not_executed' cell (empty oracle set — the key has NO OPINION on them,
    # so they are neither a match nor a false positive) and primary is re-scored on what remains.
    # Recall is untouched either way: only cell.oracle sets decide recall's denominator, and this
    # filter never adds, removes, or reclassifies an oracle edge — it only moves ARM edges between
    # cells.
    strict_primary = cells["primary"].score(arm_by_cell["primary"], overrides)
    strict_primary["cell"] = "primary (strict — no executed-caller filter)"

    if args.executed_only:
        executed_callers = load_executed_callers(args.oracle)
        judged, unjudged = set(), set()
        for edge in arm_by_cell["primary"]:
            (judged if edge[0] in executed_callers else unjudged).add(edge)
        arm_by_cell["primary"] = judged
        cells["unjudged_caller_not_executed"] = Cell(
            "unjudged — arm's caller has no oracle evidence of executing (runtime key: not "
            "wrong, just never observed)")
        arm_by_cell["unjudged_caller_not_executed"] = unjudged

    # ── Unity messages (Unity repositories only — see UNITY_MESSAGES) ───────────────────────────
    # Applied after the strict primary number is taken and before the executed-only filter's
    # result is scored, and it only MOVES arm edges: no oracle edge is added, removed or
    # reclassified, so recall is untouched and every repository without the flag grades as before.
    if args.unity_components:
        unity_types = load_unity_components(args.unity_components)
        moved = unity_invoked_edges(arm_by_cell["primary"], cells["primary"].oracle, overrides,
                                    unity_types)
        arm_by_cell["primary"] = arm_by_cell["primary"] - moved
        cells["unity_invoked"] = Cell(
            "excluded — Unity engine calls a MonoBehaviour message (no call site in IL)")
        arm_by_cell["unity_invoked"] = moved

    results = [cell.score(arm_by_cell[name], overrides) for name, cell in cells.items()]

    width = max(len(r["cell"]) for r in results + [strict_primary])
    print(f"{'cell':<{width}}  {'oracle':>7} {'arm':>7} {'match':>7} {'prec':>7} {'recall':>7}")
    for r in results:
        fmt = lambda v: "    n/a" if v is None else f"{v:7.3f}"
        print(f"{r['cell']:<{width}}  {r['oracle_edges']:>7} {r['arm_edges_in_cell']:>7} "
              f"{r['matched']:>7} {fmt(r['precision'])} {fmt(r['recall'])}")
    if args.executed_only:
        fmt = lambda v: "    n/a" if v is None else f"{v:7.3f}"
        r = strict_primary
        print(f"{r['cell']:<{width}}  {r['oracle_edges']:>7} {r['arm_edges_in_cell']:>7} "
              f"{r['matched']:>7} {fmt(r['precision'])} {fmt(r['recall'])}"
              f"   <- for comparison: same cell, filter OFF")

    print(f"\noracle rows read      : {stats['rows']}")
    print(f"oracle rows unparsed   : {stats['unparsed_row']}  (Caller/Callee did not match "
          f"_CECIL_FULLNAME at all — dropped, counted for nobody)")
    print(f"caller not remappable : {stats['unremappable_caller']}  (counted for nobody)")
    print(f"no source anchor      : {stats['no_source_anchor']}")
    print(f"arm rows dropped      : {arm_dropped}  (same caller rule as the answer key)")

    if args.json:
        payload = {"cells": results, "stats": {**stats, "arm_dropped": arm_dropped}}
        if args.executed_only:
            payload["primary_strict_no_filter"] = strict_primary
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
