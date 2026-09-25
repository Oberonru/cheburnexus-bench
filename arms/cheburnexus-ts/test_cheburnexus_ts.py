#!/usr/bin/env python3
"""Tests for this arm's join-key conversion — specifically the generic-arity stripping fix.

Engine commit 7cf2e092 (main repo, 2026-09-13) made a TS type declaration's own `Name`/`fqn` keep
its type-parameter list verbatim (`class Box<T>` -> Name="Box<T>"), matching the C# engine's
long-standing convention. The TS oracle never carries any such suffix at all (it labels a class by
`ts.Identifier.text` alone — a plain AST identifier, see oracle/typescript/transformer.js:381) —
unlike the C# oracle, which DOES spell arity, just differently (IL backticks). Left unconverted,
every method on every generic class would silently stop matching the oracle. This file is the
regression test for the fix (`strip_type_param_suffix` in run.py) and for its use at the one place
it must run: building the oracle-facing qualified join key in `Translator.translate`.

No engine run is needed — these are the pure decisions.

    python3 arms/cheburnexus-ts/test_cheburnexus_ts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "arms" / "_lib"))
sys.path.insert(0, str(HERE))

from run import (  # noqa: E402
    Translator,
    TypeIndex,
    strip_type_param_suffix,
)


def check_strip_type_param_suffix() -> list[str]:
    """Arity suffix is dropped outright — the TS oracle has no arity notation to convert to."""
    failures = []
    cases = [
        ("Box<T>", "Box", "one type parameter"),
        ("Pair<K,V>", "Pair", "two type parameters"),
        ("Box<Map<K,V>>", "Box", "a nested instantiated argument must not stop the cut early"),
        ("Plain", "Plain", "a non-generic name is untouched"),
        ("Box<T extends Base<Q>>", "Box", "a constrained type parameter is still just a suffix"),
        ("", "", "an empty name is untouched"),
    ]
    for raw, expected, why in cases:
        got = strip_type_param_suffix(raw)
        if got != expected:
            failures.append(
                f"strip_type_param_suffix({raw!r}) = {got!r}, expected {expected!r} — {why}")
    return failures


def _make_types(fqn: str, kind: str, name: str) -> TypeIndex:
    arch = {"Files": [{"Types": [{"fqn": fqn, "Kind": kind, "Name": name, "Properties": []}]}]}
    return TypeIndex(arch)


def check_translate_strips_generic_owner() -> list[str]:
    """The regression itself: a method on a generic class must join under the class's BARE name,
    the same shape the engine's own architecture.json now reports for a non-generic class, and the
    same shape the oracle has always used for every class, generic or not."""
    failures = []
    repo_root = Path("/repo")
    types = _make_types("src.Box<T>", "class", "Box<T>")

    raw_key = "/repo/src/box.ts::src.Box<T>::get()"
    methods = {raw_key: {"Line": 8}}
    translator = Translator(methods, types, repo_root)
    got = translator.translate(raw_key)
    expected = "src/box.ts::Box.get:8"
    if got != expected:
        failures.append(f"generic-class method key = {got!r}, expected {expected!r} — the "
                         "'<T>' suffix must not reach the oracle-facing qualified key")

    ctor_key = "/repo/src/box.ts::src.Box<T>::constructor(T)"
    methods2 = {ctor_key: {"Line": 4}}
    translator2 = Translator(methods2, types, repo_root)
    got_ctor = translator2.translate(ctor_key)
    expected_ctor = "src/box.ts::Box.constructor:4"
    if got_ctor != expected_ctor:
        failures.append(f"generic-class constructor key = {got_ctor!r}, expected "
                         f"{expected_ctor!r}")
    return failures


def check_translate_non_generic_owner_unchanged() -> list[str]:
    """A non-generic class's key must be byte-identical to before this fix — the stripping must be
    a no-op whenever there was nothing to strip."""
    failures = []
    repo_root = Path("/repo")
    types = _make_types("src.Plain", "class", "Plain")
    raw_key = "/repo/src/plain.ts::src.Plain::run()"
    methods = {raw_key: {"Line": 3}}
    translator = Translator(methods, types, repo_root)
    got = translator.translate(raw_key)
    expected = "src/plain.ts::Plain.run:3"
    if got != expected:
        failures.append(f"non-generic key = {got!r}, expected {expected!r} — must be unaffected")
    return failures


def check_translate_mismatched_owner_spelling() -> list[str]:
    """The zod regression (2026-09-25): a type's own declaration carries its full type-parameter
    list verbatim (`ZodArray<T extends ZodTypeAny, Cardinality extends ArrayCardinality = "many">`,
    the ONLY spelling ever stored in architecture.methods.json — one entry per declaration), but a
    call site inside that class's OWN body can refer back to the enclosing type under a DIFFERENT
    spelling of the same generic suffix — zod's architecture.calls.json was observed spelling the
    same class's owner_fqn as a short `<T>`, as `<this>` (elsewhere in the same repo), and as the
    bare name with no suffix at all.

    Two separate lookups had to agree on this, not one: `TypeIndex.kind_name` (fixed first) AND
    `Translator.methods`, keyed by the ENTIRE raw key including the owner segment — a call site's
    raw key differs from the declaration's raw key by more than the owner's suffix, so
    `self.methods.get(raw_key)` on unnormalized keys fails before `translate()` ever reaches the
    TypeIndex check. Measured on the real zod run: fixing only TypeIndex left the published numbers
    byte-identical (`no-owner-fqn-in-architecture-json` stayed 0 the whole time, no matter which
    spelling was in the input — every one of these calls was dying one step earlier, as
    `no-methods-sidecar-line`). This test builds `methods` with ONLY the declaration's own spelling
    (real shape), and translates raw keys spelled the other three ways — the case the TypeIndex-only
    fix could not have caught."""
    failures = []
    repo_root = Path("/repo")
    declared_fqn = ('packages/zod/src/v3.ZodArray<T extends ZodTypeAny, '
                     'Cardinality extends ArrayCardinality = "many">')
    declared_name = 'ZodArray<T extends ZodTypeAny, Cardinality extends ArrayCardinality = "many">'
    types = _make_types(declared_fqn, "class", declared_name)

    declared_raw_key = f"/repo/packages/zod/src/v3.ts::{declared_fqn}::element()"
    methods = {declared_raw_key: {"Line": 42}}  # ONE entry, the declaration's own spelling only

    for owner_spelling, why in [
        (declared_fqn, "the declaration's own verbatim spelling"),
        ("packages/zod/src/v3.ZodArray<T>", "a short type-parameter spelling"),
        ("packages/zod/src/v3.ZodArray<this>", "the `<this>` spelling"),
        ("packages/zod/src/v3.ZodArray", "the bare name, no suffix at all"),
    ]:
        raw_key = f"/repo/packages/zod/src/v3.ts::{owner_spelling}::element()"
        translator = Translator(methods, types, repo_root)
        got = translator.translate(raw_key)
        expected = "packages/zod/src/v3.ts::ZodArray.element:42"
        if got != expected:
            failures.append(f"owner spelled {owner_spelling!r} ({why}) joined to {got!r}, "
                             f"expected {expected!r} — the edge would silently drop")
        if got is not None and sum(translator.drops.values()):
            failures.append(f"owner spelled {owner_spelling!r} matched but still counted a drop: "
                             f"{translator.drops}")
    return failures


def check_methods_owner_normalization_collision() -> list[str]:
    """A collision guard, not an expected real case (measured 0 collisions on zod's whole
    architecture.methods.json — see this file's header and `_normalize_owner_in_raw_key`'s
    docstring). If normalizing ever DID collapse two genuinely distinct declarations onto the same
    key, neither may be silently served under the other's Line number — both must become permanently
    unlookupable and counted, the same "never fuzz-pick" rule this file already applies to same-line
    closure collisions."""
    failures = []
    repo_root = Path("/repo")
    types = _make_types("src.Box<T>", "class", "Box<T>")
    # Two distinct declarations that normalize to the identical methods key.
    methods = {
        "/repo/src/box.ts::src.Box<T>::run()": {"Line": 4},
        "/repo/src/box.ts::src.Box<U>::run()": {"Line": 9},
    }
    translator = Translator(methods, types, repo_root)
    got = translator.translate("/repo/src/box.ts::src.Box<T>::run()")
    if got is not None:
        failures.append(f"a colliding methods key resolved to {got!r} instead of being refused")
    if translator.drops["methods-owner-normalization-collision"] != 1:
        failures.append("the collision was not counted in "
                         "drops['methods-owner-normalization-collision']")
    return failures


def check_translate_module_scope_unaffected() -> list[str]:
    """A module-scope function never qualifies with a class name at all — stripping the (unused)
    simple_name must not somehow make one appear."""
    failures = []
    repo_root = Path("/repo")
    types = _make_types("src.$module$box", "module", "$module$box")
    raw_key = "/repo/src/box.ts::src.$module$box::useBox()"
    methods = {raw_key: {"Line": 13}}
    translator = Translator(methods, types, repo_root)
    got = translator.translate(raw_key)
    expected = "src/box.ts::useBox:13"
    if got != expected:
        failures.append(f"module-scope key = {got!r}, expected {expected!r}")
    return failures


def main() -> int:
    failures = check_strip_type_param_suffix()
    failures += check_translate_strips_generic_owner()
    failures += check_translate_non_generic_owner_unchanged()
    failures += check_translate_mismatched_owner_spelling()
    failures += check_methods_owner_normalization_collision()
    failures += check_translate_module_scope_unaffected()

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok — generic-arity suffix is stripped from the oracle-facing join key, and only there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
