#!/usr/bin/env python3
"""The grader must not introduce error of its own.

Graded against itself, the answer key has to score exactly 1.000 — anything less means the two
sides of the comparison disagree about which edges are in play, and that disagreement would be
charged to whichever arm is being measured. This is the one property that has to hold before a
single published number can be trusted, so it is asserted on a fixture that carries every case
known to break symmetry: a lambda caller, a caller no name can be traced back to, a property
accessor, a callee outside the corpus, and an indirect call op.

The last two checks prove the test can fail: remove an edge and recall must drop; invent one and
precision must drop. A green test that cannot go red proves nothing.

    python3 test_identity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

# Each row is what the C# oracle emits, with the flags that drive classification.
FIXTURE = [
    # ordinary first-party call — the primary cell
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.Repo::Load()",
         Op="call", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # constructor — also primary
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.Repo::.ctor()",
         Op="newobj", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # real call written inside a lambda: caller is generated, but must be remapped, not dropped
    dict(Caller="System.Void App.Service/<>c__DisplayClass3_0::<Run>b__0()",
         Callee="System.Void App.Repo::Save()",
         Op="callvirt", CalleeAssembly="App", VirtualDispatch=True,
         CallerCompilerGenerated=True, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # generated caller whose name reveals no enclosing method — counts for nobody, both sides
    dict(Caller="System.Void App.Service/<>c::.cctor()", Callee="System.Void App.Repo::Init()",
         Op="call", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=True, CalleeCompilerGenerated=False, NoDebugInfo=True),
    # property accessor — excluded cell
    dict(Caller="System.Void App.Service::Run()", Callee="System.String App.Repo::get_Name()",
         Op="callvirt", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # outside the corpus — excluded cell
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void System.Console::WriteLine()",
         Op="call", CalleeAssembly="System.Private.CoreLib", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    # indirect op — excluded cell, but the callee stays classifiable
    dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.Repo::Hook()",
         Op="calli", CalleeAssembly="App", VirtualDispatch=False,
         CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
]


def run_grader(oracle: Path, arm: Path, out: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(HERE / "grade.py"), "--oracle", str(oracle), "--arm", str(arm),
         "--first-party", "App", "--json", str(out)],
        capture_output=True, text=True, check=True)
    sys.stdout.write(result.stdout)
    return json.loads(out.read_text())


def cell(report: dict, prefix: str) -> dict:
    for entry in report["cells"]:
        if entry["cell"].startswith(prefix):
            return entry
    raise AssertionError(f"no cell starting with {prefix!r}")


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def check_key_agreement() -> list[str]:
    """The same method, written the way IL spells it and the way an arm spells it, must key alike.

    Grading the answer key against itself cannot catch a mistake in the key function: both sides
    make the identical mistake and still agree perfectly. That is exactly how closed generic
    arguments survived — `Guard::NotNull<Polly.Builder>` on one side, `Guard.NotNull` on the other,
    a third of Polly's nodes unmatchable, and an identity score of 1.000 the whole time. So this
    check compares the two *forms*, which is the thing identity structurally cannot test.
    """
    sys.path.insert(0, str(HERE))
    from grade import enclosing_user_method, method_key

    same = [
        ("generic method instantiation",
         "System.Void App.Guard::NotNull<App.Builder>(System.Object)", "App.Guard::NotNull"),
        ("generic declaring type",
         "System.Void App.Cache`1<System.String>::Get(System.Int32)", "App.Cache`1::Get"),
        ("nested generic argument",
         "T App.Guard::NotNull<App.Builder`1<System.String>>(T)", "App.Guard::NotNull"),
        ("plain method",
         "System.Void App.Service::Run()", "App.Service::Run"),
        ("explicit interface implementation keeps its method name",
         "T App.Box::System.Collections.Generic.IEnumerable<T>.GetEnumerator()",
         "App.Box::System.Collections.Generic.IEnumerable.GetEnumerator"),
    ]
    differ = [
        ("instance vs static constructor",
         "System.Void App.Repo::.ctor()", "System.Void App.Repo::.cctor()"),
    ]

    failures = []
    for label, il_form, arm_form in same:
        left, right = method_key(il_form), method_key(arm_form)
        if left != right:
            failures.append(f"{label}: IL form keys as {left!r}, arm form as {right!r} — they must agree")

    for label, first, second in differ:
        if method_key(first) == method_key(second):
            failures.append(f"{label}: both key as {method_key(first)!r} — they are different methods")

    # A mangled name is not a generic instantiation and must survive intact for the caller remap.
    lifted = method_key("System.Void App.Service/<>c__DisplayClass3_0::<Run>b__0()")
    if lifted is None or "<Run>b__0" not in lifted:
        failures.append(f"mangled caller name was mutilated: {lifted!r} — the remap reads that name")
    else:
        remapped, ok = enclosing_user_method(lifted)
        if not ok or remapped != "App.Service::Run":
            failures.append(f"lambda caller remapped to {remapped!r}, expected 'App.Service::Run'")

    # Two shapes a flat `<([^>]+)>[bdgf]__` regex cannot reach, found live on the corpus 2026-08-25
    # (PREREGISTRATION deviations, G2). Real callers, kept verbatim for traceability:
    #   Polly.ResiliencePipeline/<>c__DisplayClass0_0/<<ExecuteAsync>b__0>d::MoveNext
    #   FluentValidation.Internal.CollectionPropertyRule`2/
    #     <FluentValidation-IValidationRuleInternal<T>-ValidateAsync>d__14::MoveNext
    nested_lambda = method_key(
        "System.Void Polly.ResiliencePipeline/<>c__DisplayClass0_0/<<ExecuteAsync>b__0>d::MoveNext()")
    remapped, ok = enclosing_user_method(nested_lambda)
    if not ok or remapped != "Polly.ResiliencePipeline::ExecuteAsync":
        failures.append(
            f"async lambda nested inside a closure remapped to {remapped!r}, "
            f"expected 'Polly.ResiliencePipeline::ExecuteAsync' (ok={ok})")

    explicit_iface_async = method_key(
        "System.Void FluentValidation.Internal.CollectionPropertyRule`2/"
        "<FluentValidation-IValidationRuleInternal<T>-ValidateAsync>d__14::MoveNext()")
    # The non-mangled oracle row for the SAME method, to prove the remap lands on the spelling the
    # oracle already uses elsewhere (EDGE_FORMAT.md, "Explicit interface implementations").
    explicit_iface_direct = method_key(
        "System.Threading.Tasks.ValueTask FluentValidation.Internal.CollectionPropertyRule`2::"
        "FluentValidation.IValidationRuleInternal<T>.ValidateAsync"
        "(FluentValidation.ValidationContext`1<T>,System.Threading.CancellationToken)")
    remapped, ok = enclosing_user_method(explicit_iface_async)
    if not ok or remapped != explicit_iface_direct:
        failures.append(
            f"explicit-interface async state machine remapped to {remapped!r}, expected it to match "
            f"the direct caller's own key {explicit_iface_direct!r} (ok={ok})")

    # Regression: `<>f__AnonymousType0` matches the `>f__` marker with its `<` and `>` adjacent, so
    # a naive balanced-scan returns "" (empty), not None. `"" is not None` reads as a CONFIDENT
    # remap to the garbage key "Outer::" unless empty content is treated the same as no match.
    # Two more shapes that must likewise stay unattributable: a closure holder with no call of its
    # own (`<>c`), and a display class named only by number (`<>c__DisplayClass9_0`, no marker at
    # all). Found by review 2026-08-25 alongside G2, before it ever reached a published number.
    unattributable = [
        ("anonymous-type accessor", "System.Object Outer/<>f__AnonymousType0`1::get_Item()"),
        ("closure holder cctor", "System.Void App.Service/<>c::.cctor()"),
        ("display class ctor", "System.Void App.Service/<>c__DisplayClass9_0::.ctor()"),
    ]
    for label, raw in unattributable:
        key = method_key(raw)
        remapped, ok = enclosing_user_method(key)
        if ok:
            failures.append(
                f"{label}: {raw!r} remapped to {remapped!r} with ok=True — should be unattributable")
        elif remapped.endswith("::"):
            failures.append(f"{label}: remap produced an empty method name {remapped!r}")

    return failures


def check_return_type_character_class() -> list[str]:
    """A return-type prefix using `/`, `!` or `()` must still parse (G1).

    Before the fix, `_CECIL_FULLNAME`'s return-type character class did not include a nested-type
    separator, an IL generic placeholder, or a modreq/modopt clause — so a Cecil row whose return
    type used any of them failed to match at all, and `method_key()` silently returned None. In
    `load_oracle` that is not a no-op: the row is dropped from `stats['unparsed_row']` and the
    edge disappears from the answer key entirely, so if an arm still reports it, the grader
    charges it as a false positive nobody can defend — the answer key "forgot" an edge that was
    really there. Reverting `_CECIL_FULLNAME`'s character class (dropping `/!()` from it) turns
    every case below back into a None, which is exactly why this checks `method_key()` directly
    rather than a full grade.py run: the failure is invisible to an identity run, because both
    sides of an identity comparison make the identical mistake and still agree perfectly — the
    same reason `check_key_agreement` above exists for the generic-arguments bug.
    """
    sys.path.insert(0, str(HERE))
    from grade import method_key

    cases = [
        ("nested-type separator in the return type",
         "App.Outer/Inner App.Service::Get()", "App.Service::Get"),
        ("IL generic placeholder in the return type",
         "!0 App.Generic`1::GetValue()", "App.Generic`1::GetValue"),
        ("modreq(...) clause on the return type",
         "System.Void modreq(System.Runtime.CompilerServices.IsExternalInit) App.Repo::Load()",
         "App.Repo::Load"),
    ]
    failures = []
    for label, raw, expected in cases:
        key = method_key(raw)
        if key != expected:
            failures.append(
                f"{label}: method_key({raw!r}) = {key!r}, expected {expected!r} — a real oracle "
                f"row shaped like this would be dropped from the answer key, not scored")
    return failures


def check_override_map(tmp: Path) -> list[str]:
    """An arm that resolves a virtual call to the implementation must not be scored as wrong.

    IL names the declaration; a source-level tool often names the override. Without the map the
    grader calls that a miss, and the penalty falls hardest on the arm that resolves best — the
    exact opposite of what is being measured. On the real corpus the map moves precision from 0.759
    to 0.943 and recall from 0.884 to 0.998, so it is not a rounding detail.
    """
    failures = []
    oracle_rows = [
        dict(Caller="System.Void App.Service::Run()", Callee="System.Void App.ISink::Emit()",
             Op="callvirt", CalleeAssembly="App", VirtualDispatch=True,
             CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    ]
    oracle = tmp / "ovr-oracle.jsonl"
    write(oracle, oracle_rows)

    # The arm names the concrete sink, which is what reading the source tends to give you.
    arm = tmp / "ovr-arm.jsonl"
    write(arm, [dict(caller="App.Service::Run", callee="App.FileSink::Emit")])

    mapping = tmp / "ovr-map.json"
    mapping.write_text(json.dumps({"System.Void App.FileSink::Emit()": ["System.Void App.ISink::Emit()"]}),
                       encoding="utf-8")

    def grade(extra: list[str], out: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(HERE / "grade.py"), "--oracle", str(oracle), "--arm", str(arm),
             "--first-party", "App", "--json", str(tmp / out)] + extra,
            capture_output=True, text=True, check=True)
        return json.loads((tmp / out).read_text())

    without = cell(grade([], "without.json"), "primary")
    with_map = cell(grade(["--overrides", str(mapping)], "with.json"), "primary")

    if without["recall"] not in (0.0, None):
        failures.append(
            f"without the map the implementation should not match, got recall={without['recall']}")
    if with_map["recall"] != 1.0:
        failures.append(
            f"with the map the implementation must match its declaration, got recall={with_map['recall']}")
    return failures


# Every seed the PREREGISTRATION deviations note (G3) used to reproduce the flap on the real
# corpus (grep/Polly precision 0.233 vs 0.239): five of these landed on the wrong answer with the
# pre-G3 code, three landed on the right one by luck. That spread is the point — a check that only
# tries seed 0, or only compares two runs to each other, can pass on a lucky pairing even when the
# underlying classification is order-dependent (both runs agreeing does not mean either is right).
_HASH_SEEDS_TO_PROBE = ("0", "1", "2", "3", "4", "5", "42", "99")


def check_cell_precedence(tmp: Path) -> list[str]:
    """A callee with override candidates in two different cells must land in ONE cell, always (G3).

    The real shape: Polly's `DisposeAsync` has two declared override targets — a first-party
    base's own declaration (which oracle rows put in the primary cell) and
    `System.IAsyncDisposable`'s (external, outside the corpus). EDGE_FORMAT.md settles which cell
    an arm's edge to the concrete override is judged in: "classified by the declaration it
    answers for... Otherwise a first-party class implementing IDisposable would drag a
    standard-library call into the primary cell and lose precision there." So the concrete
    override must classify as external, never primary — a fixed outcome, not a coin flip.

    `cell_of()` used to iterate `overrides.get(callee, ())`, a `set`, and return whichever
    declared cell it met first — dependent on Python's per-process string-hash order, not on the
    data. This check runs the SAME fixture across a spread of `PYTHONHASHSEED` values and asserts
    the one correct classification on every single run, not merely that the runs agree with each
    other (two runs CAN agree and both be wrong — verified live against the pre-G3 grader: seeds
    4, 5 and 42 all agreed with each other on the wrong cell, "primary", while seeds 0-3 and 99
    agreed with each other on the right one, "external" — pairwise agreement alone would not have
    caught this).
    """
    oracle_rows = [
        # First-party base's own declaration — primary cell.
        dict(Caller="System.Void App.Service::Run()",
             Callee="System.Threading.Tasks.ValueTask App.DisposableBase::DisposeAsync()",
             Op="callvirt", CalleeAssembly="App", VirtualDispatch=True,
             CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
        # System.IAsyncDisposable's declaration — external cell (outside the corpus).
        dict(Caller="System.Void App.Service::Run()",
             Callee="System.Threading.Tasks.ValueTask System.IAsyncDisposable::DisposeAsync()",
             Op="callvirt", CalleeAssembly="System.Private.CoreLib", VirtualDispatch=True,
             CallerCompilerGenerated=False, CalleeCompilerGenerated=False, NoDebugInfo=False),
    ]
    oracle = tmp / "prec-oracle.jsonl"
    write(oracle, oracle_rows)

    # The arm names the concrete override — never appears in the oracle rows above, so this
    # checks classification (which cell the edge is judged in), not a match.
    arm = tmp / "prec-arm.jsonl"
    write(arm, [dict(caller="App.Other::Trigger", callee="App.FileDisposable::DisposeAsync")])

    mapping = tmp / "prec-map.json"
    mapping.write_text(json.dumps({
        "System.Threading.Tasks.ValueTask App.FileDisposable::DisposeAsync()": [
            "System.Threading.Tasks.ValueTask App.DisposableBase::DisposeAsync()",
            "System.Threading.Tasks.ValueTask System.IAsyncDisposable::DisposeAsync()",
        ]
    }), encoding="utf-8")

    failures = []
    for seed in _HASH_SEEDS_TO_PROBE:
        out = tmp / f"prec-out-{seed}.json"
        result = subprocess.run(
            [sys.executable, str(HERE / "grade.py"), "--oracle", str(oracle), "--arm", str(arm),
             "--overrides", str(mapping), "--first-party", "App", "--json", str(out)],
            capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed}, check=True)
        report = json.loads(out.read_text())
        primary = cell(report, "primary")
        external = cell(report, "excluded — callee outside")
        if primary["arm_edges_in_cell"] != 0 or external["arm_edges_in_cell"] != 1:
            failures.append(
                f"PYTHONHASHSEED={seed}: the ambiguous override landed in primary "
                f"({primary['arm_edges_in_cell']} edges) instead of external "
                f"({external['arm_edges_in_cell']} edges) — classification depends on hash order")
    return failures


def main() -> int:
    failures: list[str] = check_key_agreement()
    failures += check_return_type_character_class()

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        oracle = tmp / "oracle.jsonl"
        write(oracle, FIXTURE)

        # 1. identity — the answer key graded against itself
        arm = tmp / "arm.jsonl"
        write(arm, FIXTURE)
        report = run_grader(oracle, arm, tmp / "identity.json")

        for name in ("primary", "excluded — callee outside", "excluded — property"):
            entry = cell(report, name)
            if entry["oracle_edges"] == 0:
                failures.append(f"fixture covers no edges for cell {entry['cell']!r}")
            elif entry["precision"] != 1.0 or entry["recall"] != 1.0:
                failures.append(
                    f"identity broken in {entry['cell']!r}: "
                    f"precision={entry['precision']} recall={entry['recall']}")

        primary = cell(report, "primary")
        if primary["oracle_edges"] != 3:
            failures.append(
                f"expected 3 primary edges (call, ctor, lambda-remapped), got {primary['oracle_edges']}")

        failures += check_override_map(tmp)
        failures += check_cell_precedence(tmp)

        # 2. the test must be able to fail: a missing edge has to cost recall
        write(arm, [r for r in FIXTURE if "Load" not in r["Callee"]])
        report = run_grader(oracle, arm, tmp / "missing.json")
        if cell(report, "primary")["recall"] >= 1.0:
            failures.append("dropping a real edge did not reduce recall — the check is inert")

        # 3. and an invented edge has to cost precision
        write(arm, FIXTURE + [dict(caller="App.Service::Run", callee="App.Ghost::Never")])
        report = run_grader(oracle, arm, tmp / "invented.json")
        if cell(report, "primary")["precision"] >= 1.0:
            failures.append("an edge to a method nobody calls did not reduce precision")

    if failures:
        print("\nFAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\nOK — grader is neutral, and the check can fail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
