#!/usr/bin/env python3
"""Tests for our own arm's conversion, and for the one behaviour we must never get wrong.

Our engine's output has to be reshaped into the edge contract, and this arm is the one place where a
generous mistake would flatter us. It is also the arm that currently cannot run without a licence,
which makes the second half of this file the important half: being refused must stay distinguishable
from finding nothing.

No engine run is needed — these are the pure decisions.

    python3 arms/cheburnexus/test_cheburnexus.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "arms" / "_lib"))
sys.path.insert(0, str(HERE))

import armkit  # noqa: E402
from run import (  # noqa: E402
    DOTNET_ENV,
    ENGINE_ENV,
    _combined_project_coverage,
    _conversion_operator_caller_method,
    _il_operator_method,
    _key_arity,
    _parse_raw_key,
    _to_repo_relative,
    _write_synthetic_solution,
    backtick_arity,
    engine_identity,
    find_engine,
    withheld_graph_reason,
)


def check_generic_arity() -> list[str]:
    """Arity is kept, closed arguments go. The oracle spells `Cache`1`, so we must too."""
    failures = []
    cases = [
        ("Cache<T>", "Cache`1", "one parameter"),
        ("Pair<K,V>", "Pair`2", "two parameters"),
        ("Map<K,List<V>>", "Map`2", "a nested argument is one parameter, not two"),
        ("Plain", "Plain", "a non-generic name is untouched"),
        ("Cache`1", "Cache`1", "already-suffixed input must not be suffixed twice"),
    ]
    for raw, expected, why in cases:
        got = backtick_arity(raw)
        if got != expected:
            failures.append(f"backtick_arity({raw!r}) = {got!r}, expected {expected!r} — {why}")
    return failures


def check_key_parsing() -> list[str]:
    """The engine's own key shape, split only on the separator it actually inserts."""
    failures = []

    parsed = _parse_raw_key("src/App/Guard.cs::App.Guard::NotNull(System.String)")
    if parsed != ("src/App/Guard.cs", "App.Guard", "NotNull"):
        failures.append(f"ordinary key parsed as {parsed!r}")

    # A Windows-style path carrying a drive letter still splits on '::' alone.
    parsed = _parse_raw_key(r"C:/repo/src/A.cs::App.A::Run()")
    if parsed is None or parsed[2] != "Run":
        failures.append(f"a drive-lettered path broke parsing: {parsed!r}")

    if _parse_raw_key("not-a-key") is not None:
        failures.append("a malformed key was parsed instead of rejected — a bad row must be "
                        "dropped, never turned into an edge that scores")
    return failures


def check_conversion_operator_caller_spelling() -> list[str]:
    """A conversion operator caller must be spelled the way IL spells it, `op_Implicit`/
    `op_Explicit` — see `_conversion_operator_caller_method`'s own comment and
    CallKeyBuilder.ConversionOperatorKey in the product repo, whose source-syntax spelling
    (`implicit operator Func<...>`) is deliberate for the engine's OWN key space but is not what
    the answer key names the same member. Measured on Polly/without-tests: 6 primary-cell oracle
    rows have exactly this caller shape, all missed before this translation existed."""
    failures = []
    cases = [
        ("implicit operator Func`2", "op_Implicit", "the common case: implicit, no qualifier"),
        ("explicit operator System.Int32", "op_Explicit",
         "explicit conversions get their own IL name, not implicit's"),
        ("IFoo.implicit operator Func`2", "IFoo.op_Implicit",
         "an explicit-interface conversion keeps its qualifier, only the kind+type text is renamed"),
        ("Build", "Build", "an ordinary method is untouched"),
        ("get_Count", "get_Count", "an accessor-shaped name is untouched — a different translation "
         "owns that shape"),
    ]
    for raw, expected, why in cases:
        got = _conversion_operator_caller_method(raw)
        if got != expected:
            failures.append(
                f"_conversion_operator_caller_method({raw!r}) = {got!r}, expected {expected!r} — {why}"
            )
    return failures



def check_il_operator_spelling() -> list[str]:
    """An operator caller/callee must be spelled the way IL spells it — see `_il_operator_method`'s
    own comment and CallKeyBuilder.OperatorKey/ConversionOperatorKey in the product repo.

    `_IL_OPERATOR_NAMES` used to carry `"operator true"`/`"operator false"` WITH A SPACE, copied from
    how the member reads in C# source. Compiling a probe and reading `OperatorToken.Text` back shows
    Roslyn spells it bare, `"true"`/`"false"`, the same as every other operator token — so the
    engine's actual key is `operatortrue`/`operatorfalse`, no space, and the space-bearing table
    entries never matched anything: `op_True`/`op_False` were dead code. `>>>` (C# 11 unsigned right
    shift) was simply absent from the table. Both left the untranslated engine spelling standing —
    junk on the callee end (IL never spells a member that way) and a miss on the caller end (the
    oracle row never matches an untranslated caller either) — the same failure shape already measured
    for conversion operators.

    The `operatorchecked...` cases pin the C# 11 checked-operator spelling
    `CallKeyBuilder.OperatorKey` now emits (folded in as `operator` + `checked` + token, no spaces,
    positioned where the source itself writes the keyword) against the CLR's own checked names."""
    failures = []
    cases = [
        ("operatortrue", "op_True", "operator true, no space — the engine's real spelling"),
        ("operatorfalse", "op_False", "operator false, no space — the engine's real spelling"),
        ("operator true", "operator true",
         "the OLD, space-bearing spelling must NOT match — it is not a key the engine ever emits"),
        ("operator>>>", "op_UnsignedRightShift", "C# 11 unsigned right shift"),
        ("operator+", "op_Addition", "an ordinary operator is untouched by the checked/>>>/true-false fixes"),
        ("IAdd.operator+", "IAdd.op_Addition", "an explicit-interface operator keeps its qualifier"),
        ("operatorchecked+", "op_CheckedAddition", "C# 11 checked addition"),
        ("operatorchecked-", "op_CheckedSubtraction", "C# 11 checked subtraction"),
        ("operatorchecked*", "op_CheckedMultiply", "C# 11 checked multiplication"),
        ("operatorchecked/", "op_CheckedDivision", "C# 11 checked division"),
        ("operatorchecked++", "op_CheckedIncrement", "C# 11 checked increment"),
        ("operatorchecked--", "op_CheckedDecrement", "C# 11 checked decrement"),
        ("Build", "Build", "an ordinary method is untouched"),
    ]
    for raw, expected, why in cases:
        got = _il_operator_method(raw)
        if got != expected:
            failures.append(
                f"_il_operator_method({raw!r}) = {got!r}, expected {expected!r} — {why}"
            )
    return failures



def check_unary_vs_binary_operator_arity() -> list[str]:
    """`operator -` is TWO members, and the token cannot tell them apart — only the parameter count.

    IL names them `op_UnaryNegation` and `op_Subtraction`; the engine key spells both `operator-`,
    because CallKeyBuilder.OperatorKey carries the source token and the signature separately. The
    table alone therefore mapped every unary minus onto the binary name: a callee spelled as a member
    that does not exist (junk) and, at the same time, the oracle's real `op_UnaryNegation` row left
    unmatched (a miss) — the two-ends shape this arm has now hit four times.

    Dormant until operator USES became call sites: before that, a unary operator appeared only as a
    caller, and only inside its own body's edges.

    `_key_arity` counts at depth zero on purpose — `Box<A,B>` holds a comma that belongs to a type,
    not to the parameter list, and a naive split would call a unary operator binary."""
    failures = []
    cases = [
        ("operator-", 1, "op_UnaryNegation", "one operand is negation, not subtraction"),
        ("operator-", 2, "op_Subtraction", "two operands is subtraction"),
        ("operator+", 1, "op_UnaryPlus", "one operand is unary plus, not addition"),
        ("operator+", 2, "op_Addition", "two operands is addition"),
        ("operatorchecked-", 1, "op_CheckedUnaryNegation", "C# 11 checked unary minus"),
        ("operatorchecked-", 2, "op_CheckedSubtraction", "C# 11 checked subtraction"),
        ("operator-", None, "op_Subtraction", "no signature to read: the binary reading is the default"),
        ("operator*", 1, "op_Multiply", "an unambiguous token ignores arity — there is no unary `*`"),
        ("IAdd.operator-", 1, "IAdd.op_UnaryNegation", "an explicit-interface operator keeps its qualifier"),
    ]
    for raw, arity, expected, why in cases:
        got = _il_operator_method(raw, arity)
        if got != expected:
            failures.append(
                f"_il_operator_method({raw!r}, {arity!r}) = {got!r}, expected {expected!r} — {why}"
            )

    arity_cases = [
        ("f.cs::N.A::operator-(A)", 1, "one parameter"),
        ("f.cs::N.A::operator-(A,A)", 2, "two parameters"),
        ("f.cs::N.A::operator-(Box<A,B>)", 1, "a generic argument's comma is not a parameter boundary"),
        ("f.cs::N.A::operator-(int[,])", 1, "an array rank's comma is not a parameter boundary"),
        ("f.cs::N.A::Build()", 0, "an empty signature is zero parameters, not one"),
        ("f.cs::N.A::Build", None, "no signature at all reads as unknown, never as zero"),
    ]
    for raw, expected_arity, why in arity_cases:
        got = _key_arity(raw)
        if got != expected_arity:
            failures.append(f"_key_arity({raw!r}) = {got!r}, expected {expected_arity!r} — {why}")
    return failures


def check_checked_conversion_operator_caller_spelling() -> list[str]:
    """C# 11 lets an EXPLICIT conversion be checked (`explicit operator checked int`) alongside a
    plain one — `CallKeyBuilder.ConversionOperatorKey` folds the keyword in where the source writes
    it, and this arm must translate it to IL's `op_CheckedExplicit`, a genuinely different member
    from `op_Explicit` (verified: the compiler rejects `implicit operator checked T` outright,
    CS9024, so no checked-implicit case exists to test). The checked keyword entry must be tried
    BEFORE the plain "explicit operator " entry — the plain one is a strict prefix of the checked
    spelling, so trying it first would truncate `... checked int` down to `op_Explicit` and silently
    lose the checked-ness."""
    failures = []
    cases = [
        ("explicit operator checked int", "op_CheckedExplicit", "the checked keyword must win over the plain prefix match"),
        ("explicit operator int", "op_Explicit", "an ordinary explicit conversion is unaffected"),
        ("implicit operator int", "op_Implicit", "an ordinary implicit conversion is unaffected"),
        ("IFoo.explicit operator checked System.Int32", "IFoo.op_CheckedExplicit",
         "an explicit-interface checked conversion keeps its qualifier"),
    ]
    for raw, expected, why in cases:
        got = _conversion_operator_caller_method(raw)
        if got != expected:
            failures.append(
                f"_conversion_operator_caller_method({raw!r}) = {got!r}, expected {expected!r} — {why}"
            )
    return failures


def check_engine_binary_is_never_guessed() -> list[str]:
    """With $CHEBURNEXUS_ENGINE unset, the arm must find NO engine — not fall back to one.

    A hardcoded fallback used to live in `find_engine`, pointing at this machine's packaged
    distributable. On 2026-08-29 it silently spent a whole four-cell run: that binary predates the
    passport signing-key rotation, so it fails closed to Free and every cell came back BLOCKED as
    though the machine were unlicensed, while the passport on disk was healthy. The arm cannot know
    whether a binary it picked by itself is the one the experiment means, so it must pick none.
    This check fails on the pre-fix code, where the fallback resolves on this very machine."""
    failures = []
    saved = os.environ.pop(ENGINE_ENV, None)
    try:
        found = find_engine()
        if found is not None:
            failures.append(
                f"find_engine() returned {str(found)!r} with ${ENGINE_ENV} unset — a silently "
                "chosen engine is exactly the defect: it can be older than the change under test, "
                "or older than the passport signing key, and yields a plausible wrong number")
        os.environ[ENGINE_ENV] = "/nonexistent/arch-computer.exe"
        if find_engine() is not None:
            failures.append(
                f"find_engine() returned an engine with ${ENGINE_ENV} pointing at a missing file — "
                "a bad explicit path must refuse, not fall through to some other binary")
    finally:
        os.environ.pop(ENGINE_ENV, None)
        if saved is not None:
            os.environ[ENGINE_ENV] = saved
    return failures


def check_engine_identity_tells_two_builds_apart() -> list[str]:
    """Every run must record WHICH engine ran, in terms that go stale audibly.

    The old answer parsed a version out of the distributable's folder name: the two 2026-08-29 runs
    recorded "unknown" and "1.7.0", and neither string revealed that one of them was a 20-day-old
    binary. Identity must therefore key on CONTENT and BUILD DATE, and must key on the analysis
    assembly rather than the launcher — two different publishes were observed sharing a
    byte-identical arch-computer.exe, so hashing the launcher would certify them as one engine."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        ids = {}
        for name, core in (("a", b"engine-code-A"), ("b", b"engine-code-B")):
            d = tmp / name
            d.mkdir()
            (d / "arch-computer.exe").write_bytes(b"identical-generic-apphost")
            (d / "ArchitectureAnalyzer.Core.dll").write_bytes(core)
            ids[name] = engine_identity(d / "arch-computer.exe")
        if ids["a"] == ids["b"]:
            failures.append(
                "two builds whose analysis assembly differs got the SAME identity "
                f"{ids['a']!r} — the launcher is a generic apphost and cannot identify an engine")
        if "ArchitectureAnalyzer.Core.dll" not in ids["a"]:
            failures.append(f"identity {ids['a']!r} does not name the assembly it hashed")
        bare = tmp / "c"
        bare.mkdir()
        (bare / "arch-computer.exe").write_bytes(b"single-file-publish")
        old = time.time() - 20 * 24 * 3600
        os.utime(bare / "arch-computer.exe", (old, old))
        ident = engine_identity(bare / "arch-computer.exe")
        stale_date = time.strftime("%Y-%m-%d", time.gmtime(old))
        if stale_date not in ident:
            failures.append(
                f"identity {ident!r} of a 20-day-old binary does not state its build date "
                f"({stale_date}) — a stale engine must say its own age in every result")
    return failures


def check_withheld_graph_reason_asserts_no_cause() -> list[str]:
    """When the engine withholds the call graph the arm reports WHAT it saw, never WHY.

    The old wording asserted "no valid passport is present on this machine" — a fact about the
    machine this arm cannot check, and one that was plainly false on 2026-08-29: an unexpired
    passport sat at the canonical path the entire time and the real cause was an engine binary older
    than the signing key. Reading that sentence sent the diagnosis at the wrong half of the system."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "arch-computer.exe").write_bytes(b"apphost")
        (tmp / "ArchitectureAnalyzer.Core.dll").write_bytes(b"code")
        reason = withheld_graph_reason(tmp / "arch-computer.exe")
    if "no valid passport is present on this machine" in reason:
        failures.append(
            "withheld_graph_reason still asserts 'no valid passport is present on this machine' — "
            "the arm cannot check that, and it was false the one time it mattered")
    if "signing key" not in reason:
        failures.append(
            "withheld_graph_reason names only one cause — an engine binary older than the passport "
            "signing key produces the identical symptom with a healthy passport, and must be named")
    if "sha256:" not in reason:
        failures.append(
            f"withheld_graph_reason {reason!r} does not name the engine that was used — naming it "
            "is what lets a reader settle the two causes in one look")
    return failures


def check_paths_are_repo_relative() -> list[str]:
    """Anchors must be comparable across machines, and never invented when they are not."""
    failures = []
    repo = Path("/home/u/repo")

    got = _to_repo_relative("/home/u/repo/src/App/Service.cs", repo)
    if got != "src/App/Service.cs":
        failures.append(f"a path under the checkout was not made relative: {got!r}")

    got = _to_repo_relative(r"\\home\\u\\repo\\src\\App\\Service.cs".replace("\\\\", "\\"), repo)
    if "\\" in got:
        failures.append(f"backslashes survived into the anchor: {got!r} — rows must be portable")

    outside = _to_repo_relative("/elsewhere/Other.cs", repo)
    if outside != "/elsewhere/Other.cs":
        failures.append(f"a path outside the checkout was rewritten to {outside!r} — better an "
                        "honest absolute path than an invented relative one")
    return failures


def check_refused_is_not_empty() -> list[str]:
    """The licence wall must surface as `blocked`, never as an arm that looked and found nothing.

    This is the assertion that matters most in this file. Our engine analyses a repository cleanly
    and then withholds the call graph, because the graph is the paid feature. Reporting that as zero
    edges would publish `recall 0.000` beside our own product — a claim that it searched and failed,
    when it was never allowed to search.
    """
    failures = []
    source = (HERE / "run.py").read_text(encoding="utf-8")
    if "armkit.Blocked" not in source:
        failures.append("the arm no longer raises armkit.Blocked — a licence refusal would be "
                        "graded as a genuine zero")
    if "calls-counts.json" not in source:
        failures.append("the arm no longer recognises the counts-only output that marks the wall")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        probe = tmp / "arm.py"
        probe.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'arms' / '_lib')!r})\n"
            "import armkit\n"
            "def collect(repo, cell):\n"
            "    raise armkit.Blocked('analyzer.semantic is a Pro feature')\n"
            "sys.exit(armkit.main('cheburnexus', lambda: 'test', collect))\n",
            encoding="utf-8")
        (tmp / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(probe), "--repo", str(tmp / "repo"),
             "--cell", "without-tests", "--out", str(tmp / "out.jsonl")],
            capture_output=True, text=True)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(f"a refused arm exited {result.returncode}, expected {armkit.EXIT_BLOCKED}")
        if (tmp / "out.jsonl").exists():
            failures.append("a refused arm produced an output file, which the grader would score")
    return failures


def _describe(env_engine: Path | None) -> tuple[subprocess.CompletedProcess, dict]:
    env = dict(os.environ)
    env.pop(ENGINE_ENV, None)
    if env_engine is not None:
        env[ENGINE_ENV] = str(env_engine)
    result = subprocess.run([sys.executable, str(HERE / "run.py"), "--describe"],
                            capture_output=True, text=True, env=env)
    try:
        return result, json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return result, {}


def check_describe_carries_identity() -> list[str]:
    """A published row set is worthless if it cannot be tied to the BUILD that produced it.

    This check used to accept any non-empty version string other than the literal "unknown", and it
    used to be exercised by accident: the arm had a hardcoded default engine, so on the author's
    machine `--describe` always resolved a real binary. Deleting that default (correctly) took the
    accident with it — with no engine configured the arm honestly answers "unavailable ...", which
    is non-empty and not "unknown", so the check passed while testing nothing at all. A test whose
    coverage depends on the ambient environment is a test that reports green on a fresh clone and on
    CI without ever running the code it guards. It now supplies its own engine and demands the
    identity actually carry a build date and a content hash."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "arch-computer.exe").write_bytes(b"apphost")
        (tmp / "ArchitectureAnalyzer.Core.dll").write_bytes(b"engine-code")
        result, described = _describe(tmp / "arch-computer.exe")
        if result.returncode != 0:
            return [f"--describe failed: {result.stderr.strip()[:200]}"]
        if not described:
            return [f"--describe did not print one JSON object: {result.stdout[:200]!r}"]
        if described.get("name") != "cheburnexus":
            failures.append(f"--describe reports name {described.get('name')!r}")
        version = described.get("version") or ""
        if "sha256:" not in version:
            failures.append(
                f"--describe reports version {version!r} with no content hash — a build label alone "
                "cannot distinguish two builds, and did not: both 2026-08-29 runs recorded one")
        if not re.search(r"built \d{4}-\d{2}-\d{2}", version):
            failures.append(
                f"--describe reports version {version!r} with no build date — a stale engine must "
                "state its own age in every row set it produces")
        # And the honest answer when there is none must not look like a version.
        _, none_described = _describe(None)
        if "sha256:" in (none_described.get("version") or ""):
            failures.append(
                f"--describe invented an identity with no engine configured: "
                f"{none_described.get('version')!r}")
    return failures


def check_engine_identity_survives_an_unreadable_binary() -> list[str]:
    """Provenance is not worth crashing a run over.

    `engine_identity` reads and hashes a file, which the folder-name regex it replaced never did.
    An unreadable or locked assembly, or one that vanishes between the `is_file()` test and the
    read, would raise out of `version()` (called on EVERY --describe) and out of the per-project
    loop. `armkit.main` catches only `Blocked`, so the arm would die with an unnamed traceback and
    the runner would file the cell as "could not run here" instead of naming what happened."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        exe = tmp / "arch-computer.exe"
        exe.write_bytes(b"apphost")
        code = tmp / "ArchitectureAnalyzer.Core.dll"
        code.write_bytes(b"engine-code")
        code.chmod(0o000)
        try:
            identity = engine_identity(exe)
        except OSError as exc:
            code.chmod(0o600)
            return [f"engine_identity raised {type(exc).__name__} on an unreadable assembly instead "
                    f"of reporting it — this propagates out of --describe as an unnamed crash"]
        code.chmod(0o600)
        if "UNREADABLE" not in identity:
            failures.append(
                f"engine_identity returned {identity!r} for an unreadable assembly — an engine we "
                "cannot read is a fact to state, not one to paper over")
    return failures


# ── defect #9 fixtures: a controllable fake engine + a throwaway repo ──────────────────────────
# These four tests reproduce the harness defect found 2026-08-24: a stale architecture.calls.json
# left in arms/cheburnexus/.work/<repo>/<cell>/<project>/ from a PREVIOUS run was silently reported
# as THIS run's result when the engine refused (exit 2, wrote nothing) or exited 0 without writing
# anything. _invoke() checked only arch.is_file() — never the exit code, never the file's age
# against the start of this run — and the per-project scratch directory was never cleaned between
# runs. See the PREREGISTRATION doc's 2026-08-24 "Отклонения от плана" entry for the full account.
#
# The fake engine below stands in for arch-computer.exe: given a --solution/--project target whose
# path contains one of three markers, it behaves the way the real engine was observed to behave in
# each of the three situations that mattered:
#   "RefuseRestore"    — exit 2, writes nothing, stderr is a real (non-entitlement) refusal message
#   "ExitZeroNoOutput"  — exit 0, writes nothing (a clean run that simply produced no sidecar)
#   "Good"              — exit 0, writes a genuine architecture.json + architecture.calls.json
_FAKE_ENGINE_SOURCE = '''#!/usr/bin/env python3
import json
import pathlib
import sys

args = sys.argv[1:]
target = args[1]
out_path = pathlib.Path(args[args.index("--output") + 1])

if "RefuseRestore" in target:
    sys.stderr.write(
        "[warn] no restore output for RefuseRestore.csproj "
        "(expected .../RefuseRestore/obj/project.assets.json)\\n"
    )
    sys.stderr.write(
        "Refusing to analyze: at least one project has no restore output "
        "(obj/project.assets.json). Run 'dotnet restore' first — without it every referenced "
        "package type is unresolvable and the call graph would be silently incomplete.\\n"
    )
    sys.exit(2)

if "ExitZeroNoOutput" in target:
    sys.exit(0)

if "Good" in target:
    out_path.write_text(json.dumps({
        "Files": [{"Namespace": "App", "Types": [{"Name": "Widget", "Methods": []}]}]
    }))
    out_path.with_name("architecture.calls.json").write_text(json.dumps({
        "Data": {
            "src/Good/Widget.cs::App.Widget::Run": {
                "Calls": [{"Target": "src/Good/Widget.cs::App.Widget::Helper()", "Line": 10}]
            }
        }
    }))
    sys.exit(0)

sys.stderr.write(f"fake-engine: unrecognized target {target!r}\\n")
sys.exit(1)
'''


def _write_fake_engine(tmp: Path) -> Path:
    engine = tmp / "fake_engine.py"
    engine.write_text(_FAKE_ENGINE_SOURCE, encoding="utf-8")
    engine.chmod(0o755)
    return engine


def _make_fake_repo(tmp: Path, project_names: list[str]) -> Path:
    # Named after tmp's own (already-unique) basename, not a fixed "repo" — WORK_ROOT is keyed by
    # repo_root.name alone, and a fixed name would let one test's scratch directory collide with
    # another's inside the shared arms/cheburnexus/.work/ tree.
    repo = tmp / f"repo-{tmp.name}"
    for name in project_names:
        proj_dir = repo / "src" / name
        proj_dir.mkdir(parents=True)
        (proj_dir / f"{name}.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"></Project>', encoding="utf-8")
        (proj_dir / "Widget.cs").write_text("namespace App { class Widget {} }", encoding="utf-8")
    return repo


def _plant_stale_artifacts(repo: Path, cell: str, project_stem: str, files: dict[str, str]) -> Path:
    """Drop files into the exact per-project scratch path run.py will use, backdated three days —
    reproducing a leftover from a previous run that a fixed arm must never read as this run's own."""
    stale_dir = HERE / ".work" / repo.name / cell / project_stem
    stale_dir.mkdir(parents=True, exist_ok=True)
    old = time.time() - 3 * 24 * 3600
    for name, content in files.items():
        p = stale_dir / name
        p.write_text(content, encoding="utf-8")
        os.utime(p, (old, old))
    return stale_dir


def _run_arm_cli(repo: Path, cell: str, out: Path, engine: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CHEBURNEXUS_ENGINE"] = str(engine)
    return subprocess.run(
        [sys.executable, str(HERE / "run.py"), "--repo", str(repo), "--cell", cell, "--out", str(out)],
        capture_output=True, text=True, env=env)


_STALE_ARCH = json.dumps({"Files": [{"Namespace": "Stale", "Types": [{"Name": "Ghost", "Methods": []}]}]})
_STALE_CALLS = json.dumps({
    "Data": {"stale/Ghost.cs::Stale.Ghost::Run": {
        "Calls": [{"Target": "stale/Ghost.cs::Stale.Ghost::Boo()", "Line": 1}]
    }}
})
_STALE_COUNTS = json.dumps({"Data": {}})


def check_stale_artifact_with_nonzero_exit_is_not_reported() -> list[str]:
    """Defect #9, mode 1: the engine exits non-zero and writes nothing, but a stale
    architecture.calls.json from three days ago is still sitting in the scratch directory. A fixed
    arm must not read it — it must report the cell as blocked, not as a set of (stale) edges."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["RefuseRestore"])
        _plant_stale_artifacts(repo, "without-tests", "RefuseRestore",
                               {"architecture.json": _STALE_ARCH, "architecture.calls.json": _STALE_CALLS})
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(
                f"engine exited 2 with a stale calls.json present: arm exited {result.returncode}, "
                f"expected {armkit.EXIT_BLOCKED} (BLOCKED) — stderr tail: {result.stderr[-500:]!r}")
        if out.exists():
            failures.append(
                "engine exited 2 with a stale calls.json present: arm wrote edges.jsonl anyway "
                f"(the stale 3-day-old edge leaked through) — {out.read_text(encoding='utf-8')[:300]!r}")
    return failures


def check_freshness_catches_exit_zero_with_no_output() -> list[str]:
    """Defect #9, mode 2: the engine exits 0 (a "successful" run by exit-code alone) but writes
    nothing new — belt-and-braces for when the exit-code check alone is not enough, since a stale
    architecture.json sitting in the scratch dir would otherwise look like this run's own output."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["ExitZeroNoOutput"])
        _plant_stale_artifacts(repo, "without-tests", "ExitZeroNoOutput",
                               {"architecture.json": _STALE_ARCH, "architecture.calls.json": _STALE_CALLS})
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(
                f"engine exited 0 but wrote nothing, with a stale calls.json present: arm exited "
                f"{result.returncode}, expected {armkit.EXIT_BLOCKED} — the freshness check did not "
                f"catch the stale file. stderr tail: {result.stderr[-500:]!r}")
        if out.exists():
            failures.append(
                "engine exited 0 but wrote nothing, with a stale calls.json present: arm wrote "
                f"edges.jsonl from the stale file — {out.read_text(encoding='utf-8')[:300]!r}")
    return failures


def check_non_entitlement_refusal_is_not_mislabelled() -> list[str]:
    """Defect #9, mode 3: the engine refuses for an ordinary reason (missing dotnet restore) — not
    the licence wall. A stale calls-counts.json (no calls.json) is also sitting there, which is
    exactly the shape the old code mistook for "entitlement wall, not a failure". The reported
    reason must quote the engine's own refusal message, and must never say "entitlement wall"."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["RefuseRestore"])
        _plant_stale_artifacts(repo, "without-tests", "RefuseRestore",
                               {"architecture.json": _STALE_ARCH, "architecture.calls-counts.json": _STALE_COUNTS})
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        combined = result.stdout + result.stderr
        if "entitlement wall" in combined.lower():
            failures.append(
                f"a plain 'no restore output' refusal was reported as the entitlement wall — "
                f"stderr: {result.stderr[-800:]!r}")
        if "Refusing to analyze: at least one project has no restore output" not in combined:
            failures.append(
                "the engine's own refusal message did not survive into the arm's report — "
                f"stderr: {result.stderr[-800:]!r}")
        if out.exists():
            failures.append("a refused project produced edges.jsonl anyway")
    return failures


def check_partial_project_coverage_fails_the_cell_loudly() -> list[str]:
    """Defect #9, mode 4 (the masking bug): one project analyzes cleanly and produces real edges,
    a second refuses outright. The old code graded the cell on the one project that worked and
    never mentioned the one that didn't — "378 edges" printed as though the run were complete. A
    fixed arm must fail the whole cell rather than publish a partial result as if it were whole."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)
        repo = _make_fake_repo(tmp, ["Good", "RefuseRestore"])
        out = tmp / "out.jsonl"
        result = _run_arm_cli(repo, "without-tests", out, engine)
        if result.returncode != armkit.EXIT_BLOCKED:
            failures.append(
                f"1 of 2 projects produced no call graph, but arm exited {result.returncode} "
                f"(expected {armkit.EXIT_BLOCKED}) — a partial analysis must fail the cell, not be "
                f"graded as complete. stderr tail: {result.stderr[-800:]!r}")
        if out.exists():
            failures.append(
                "1 of 2 projects produced no call graph, but the arm still wrote edges.jsonl from "
                f"the project that succeeded — {out.read_text(encoding='utf-8')[:300]!r}")
    return failures


_FAKE_DOTNET_OK_SOURCE = '''#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if args[:2] == ["new", "sln"]:
    name = args[args.index("-n") + 1]
    # Honour `--format <fmt>` the way SDK 9+ does — the arm now passes `--format sln` to force the
    # classic format, so the produced file must follow the requested format's extension.
    fmt = args[args.index("--format") + 1] if "--format" in args else "sln"
    pathlib.Path(f"{name}.{fmt}").write_text("FAKESLN\\n")
    sys.exit(0)
if args and args[0] == "sln" and len(args) > 2 and args[2] == "add":
    sln = pathlib.Path(args[1])
    with sln.open("a") as fh:
        for project in args[3:]:
            fh.write(f"PROJECT:{project}\\n")
    sys.exit(0)
sys.stderr.write(f"fake-dotnet: unrecognized args {args!r}\\n")
sys.exit(1)
'''

_FAKE_DOTNET_FAIL_SOURCE = '''#!/usr/bin/env python3
import sys
sys.stderr.write("fake-dotnet: simulated failure (no SDK on this fake PATH)\\n")
sys.exit(1)
'''

# SDK 9+ behaviour: `dotnet new sln` DEFAULTS to the new `.slnx` XML format, and only produces a
# classic `.sln` when `--format sln` is passed explicitly. This is the shim that reproduces the e18
# regression: without the fix (no `--format`), it writes `probe.slnx`, the arm's `.sln` lookup misses,
# and the whole cell silently drops to per-project analysis.
_FAKE_DOTNET_SLNX_DEFAULT_SOURCE = '''#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if args[:2] == ["new", "sln"]:
    name = args[args.index("-n") + 1]
    if "--format" in args:
        fmt = args[args.index("--format") + 1]
        pathlib.Path(f"{name}.{fmt}").write_text("FAKESLN\\n")
    else:
        pathlib.Path(f"{name}.slnx").write_text("<Solution/>\\n")  # SDK 9+ default
    sys.exit(0)
if args and args[0] == "sln" and len(args) > 2 and args[2] == "add":
    sln = pathlib.Path(args[1])
    with sln.open("a") as fh:
        for project in args[3:]:
            fh.write(f"PROJECT:{project}\\n")
    sys.exit(0)
sys.stderr.write(f"fake-dotnet: unrecognized args {args!r}\\n")
sys.exit(1)
'''

# Older SDK (8/7/3.1) behaviour: `--format` is not a known argument, so `dotnet new sln --format sln`
# is REJECTED as unrecognised — but a plain `dotnet new sln` already defaults to the classic `.sln`.
# The fix must notice the unrecognised-flag failure and retry once without the flag.
_FAKE_DOTNET_OLD_SDK_REJECTS_FORMAT_SOURCE = '''#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if args[:2] == ["new", "sln"]:
    if "--format" in args:
        sys.stderr.write("Unrecognized command or argument '--format'.\\n")
        sys.exit(1)
    name = args[args.index("-n") + 1]
    pathlib.Path(f"{name}.sln").write_text("FAKESLN\\n")
    sys.exit(0)
if args and args[0] == "sln" and len(args) > 2 and args[2] == "add":
    sln = pathlib.Path(args[1])
    with sln.open("a") as fh:
        for project in args[3:]:
            fh.write(f"PROJECT:{project}\\n")
    sys.exit(0)
sys.stderr.write(f"fake-dotnet: unrecognized args {args!r}\\n")
sys.exit(1)
'''


def _write_fake_dotnet(tmp: Path, source: str) -> Path:
    dotnet = tmp / "fake_dotnet.py"
    dotnet.write_text(source, encoding="utf-8")
    dotnet.chmod(0o755)
    return dotnet


def check_synthetic_solution_lists_exactly_in_scope_projects() -> list[str]:
    """The synthetic solution is the ONLY thing that tells the engine which sibling
    ProjectReferences to resolve as source instead of a compiled DLL. If it ever listed a project
    outside the caller's candidate set, that project's calls would go from a disclosed
    DroppedExternal gap to a silently WRONG attribution (a call resolved against a project the cell
    never scoped in); if it dropped a candidate, that candidate's calls into its own siblings would
    silently keep failing to resolve — the exact defect this whole change exists to fix."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dotnet = _write_fake_dotnet(tmp, _FAKE_DOTNET_OK_SOURCE)
        out_dir = tmp / "out"
        out_dir.mkdir()
        candidates = [Path("/repo/src/Beta/Beta.csproj"), Path("/repo/src/Alpha/Alpha.csproj")]
        os.environ[DOTNET_ENV] = str(dotnet)
        try:
            sln_path, reason = _write_synthetic_solution(out_dir, "probe", candidates)
        finally:
            os.environ.pop(DOTNET_ENV, None)
        if sln_path is None:
            return [f"_write_synthetic_solution failed unexpectedly: {reason}"]
        listed = {
            line.split("PROJECT:", 1)[1]
            for line in sln_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("PROJECT:")
        }
        expected = {str(c) for c in candidates}
        if listed != expected:
            failures.append(
                f"synthetic solution listed {sorted(listed)}, expected exactly {sorted(expected)} "
                "— a project missing here never gets its siblings resolved as source, and an extra "
                "one leaks a project outside the cell's own scope into the run")
    return failures


def check_synthetic_solution_forces_classic_sln_on_sdk9_plus() -> list[str]:
    """SDK 9+ `dotnet new sln` defaults to `.slnx`; the arm must force `.sln` with `--format sln`.

    This is the e18 regression, reproduced at unit level: the shim writes `<name>.slnx` when NOT
    given `--format` (SDK 9+ default) and `<name>.sln` when it is. A fixed `_write_synthetic_solution`
    passes `--format sln`, so it must come back with a real `.sln` path. Against the PRE-FIX code
    (which passed no `--format`), this same shim would have written `probe.slnx`, the `<name>.sln`
    lookup would have missed, and the function would have returned `(None, reason)` — the silent
    fallback that dropped Polly's combined-solution recall from 0.8734 to 0.8193. So this test is red
    on the old code and green on the new."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dotnet = _write_fake_dotnet(tmp, _FAKE_DOTNET_SLNX_DEFAULT_SOURCE)
        out_dir = tmp / "out"
        out_dir.mkdir()
        candidates = [Path("/repo/src/Alpha/Alpha.csproj")]
        os.environ[DOTNET_ENV] = str(dotnet)
        try:
            sln_path, reason = _write_synthetic_solution(out_dir, "probe", candidates)
        finally:
            os.environ.pop(DOTNET_ENV, None)
        if sln_path is None:
            failures.append(
                f"_write_synthetic_solution fell back to failure ({reason!r}) against an SDK-9+ shim "
                "— the fix must pass `--format sln` so a classic `.sln` is produced, not the `.slnx` "
                "default the arm's own lookup cannot see")
            return failures
        if sln_path.suffix != ".sln":
            failures.append(f"produced {sln_path.name!r}, expected a classic `.sln` file")
        if (out_dir / "probe.slnx").is_file():
            failures.append("an unforced `.slnx` was created — the `--format sln` flag did not take")
    return failures


def check_synthetic_solution_retries_without_format_on_old_sdk() -> list[str]:
    """An older SDK rejects `--format` as unknown but already defaults to `.sln`.

    The fix tries `--format sln` first; when THAT specific failure is an unrecognised-flag error, it
    must retry once WITHOUT the flag rather than give up. The shim here rejects any invocation
    carrying `--format` (stderr: "Unrecognized command or argument '--format'.") and succeeds on the
    plain one, writing `probe.sln`. So a robust `_write_synthetic_solution` must still return a real
    `.sln` path — proving the fix does not break the very SDKs CORPUS_NOTES.md pins to (8/7/3.1)."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dotnet = _write_fake_dotnet(tmp, _FAKE_DOTNET_OLD_SDK_REJECTS_FORMAT_SOURCE)
        out_dir = tmp / "out"
        out_dir.mkdir()
        candidates = [Path("/repo/src/Alpha/Alpha.csproj")]
        os.environ[DOTNET_ENV] = str(dotnet)
        try:
            sln_path, reason = _write_synthetic_solution(out_dir, "probe", candidates)
        finally:
            os.environ.pop(DOTNET_ENV, None)
        if sln_path is None:
            failures.append(
                f"_write_synthetic_solution gave up ({reason!r}) on an SDK that rejects `--format` — "
                "it must retry once without the flag, since the old default is already `.sln`")
            return failures
        if sln_path.suffix != ".sln":
            failures.append(f"produced {sln_path.name!r}, expected a classic `.sln` file")
    return failures


def check_combined_mode_reports_zero_file_project_as_unproductive() -> list[str]:
    """`_combined_project_coverage` stands in, for a combined run, for the per-project exit code the
    old loop used to catch a project that analyzed cleanly overall but produced nothing of its own —
    a project entirely gated out for this TFM, say. If this silently called every candidate
    "productive" merely because the COMBINED run as a whole exited 0, a partial result would again
    be graded as a complete one — the exact defect #9 masking bug the per-project loop was built to
    catch, reappearing one level up."""
    failures = []
    repo_root = Path("/repo")
    alpha = repo_root / "src" / "Alpha" / "Alpha.csproj"
    beta = repo_root / "src" / "Beta" / "Beta.csproj"
    # Beta contributes nothing; a sibling "src/Beta.Extra" must not be mistaken for it — a
    # string-prefix test would wrongly credit Beta with files that are actually a DIFFERENT project.
    files = [
        {"Path": "src/Alpha/A.cs"},
        {"Path": "src/Alpha/Sub/B.cs"},
        {"Path": "src/Beta.Extra/C.cs"},
    ]
    productive, unproductive = _combined_project_coverage(files, [alpha, beta], repo_root)
    if productive != ["src/Alpha/Alpha.csproj"]:
        failures.append(f"productive = {productive!r}, expected only Alpha")
    unproductive_paths = [rel for rel, _why in unproductive]
    if unproductive_paths != ["src/Beta/Beta.csproj"]:
        failures.append(
            f"unproductive = {unproductive_paths!r}, expected only Beta — a sibling directory "
            "(src/Beta.Extra) must not be credited to Beta by a string-prefix match")
    return failures


# ── combined-mode CLI integration: a second fake engine that understands --project <root> ─────────
# The defect #9 fake engine above keys on a marker INSIDE the target path it is given (args[1]),
# which is a per-project .csproj path in the old per-project mode. Combined mode's target is a
# synthetic SOLUTION path that carries none of those markers — a repo-shaped fake engine is needed
# instead, keyed on which candidate directories it can see under --project.
_COMBINED_FAKE_ENGINE_SOURCE = '''#!/usr/bin/env python3
import json
import pathlib
import sys

args = sys.argv[1:]
out_path = pathlib.Path(args[args.index("--output") + 1])

if "--project" not in args:
    sys.stderr.write("fake-combined-engine: expected --project alongside --solution\\n")
    sys.exit(1)

project_root = pathlib.Path(args[args.index("--project") + 1])
alpha_dir = project_root / "src" / "Alpha"
if not alpha_dir.is_dir():
    sys.stderr.write(f"fake-combined-engine: no Alpha under {project_root}\\n")
    sys.exit(1)

# Only Alpha ever contributes a file — a Beta directory, when present under the same root, never
# does, so a test can assert on which of the two the coverage accounting names.
out_path.write_text(json.dumps({
    "Files": [{"Path": "src/Alpha/A.cs", "Namespace": "App", "Types": [{"Name": "Alpha", "Methods": []}]}]
}))
out_path.with_name("architecture.calls.json").write_text(json.dumps({
    "Data": {
        "src/Alpha/A.cs::App.Alpha::Run": {
            "Calls": [{"Target": "src/Alpha/A.cs::App.Alpha::Helper()", "Line": 7}]
        }
    }
}))
sys.exit(0)
'''


def _write_combined_fake_engine(tmp: Path) -> Path:
    engine = tmp / "fake_combined_engine.py"
    engine.write_text(_COMBINED_FAKE_ENGINE_SOURCE, encoding="utf-8")
    engine.chmod(0o755)
    return engine


def _make_two_project_repo(tmp: Path, names: list[str]) -> Path:
    repo = tmp / f"repo-{tmp.name}"
    for name in names:
        proj_dir = repo / "src" / name
        proj_dir.mkdir(parents=True)
        (proj_dir / f"{name}.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"></Project>', encoding="utf-8")
        (proj_dir / "Widget.cs").write_text("namespace App { class Widget {} }", encoding="utf-8")
    return repo


def _run_arm_cli_combined(
    repo: Path, cell: str, out: Path, engine: Path, dotnet: Path
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CHEBURNEXUS_ENGINE"] = str(engine)
    env[DOTNET_ENV] = str(dotnet)
    return subprocess.run(
        [sys.executable, str(HERE / "run.py"), "--repo", str(repo), "--cell", cell, "--out", str(out)],
        capture_output=True, text=True, env=env)


def check_manifest_states_combined_mode() -> list[str]:
    """A reader of the manifest must not have to guess whether a row came from the combined-solution
    run or the per-project fallback — the two modes measure genuinely different things (the whole
    point of this change), so silently blending them into one unlabelled "cheburnexus" row would
    hide exactly the improvement being sized."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_combined_fake_engine(tmp)
        dotnet = _write_fake_dotnet(tmp, _FAKE_DOTNET_OK_SOURCE)
        repo = _make_two_project_repo(tmp, ["Alpha"])
        out = tmp / "out.jsonl"
        result = _run_arm_cli_combined(repo, "without-tests", out, engine, dotnet)
        if result.returncode != 0:
            return [f"combined run failed unexpectedly: exit {result.returncode}, "
                    f"stderr {result.stderr[-800:]!r}"]
        coverage_path = Path(str(out) + ".coverage.json")
        if not coverage_path.is_file():
            return ["a successful combined run wrote no coverage sidecar — the manifest has "
                    "nothing to read the mode from"]
        note = json.loads(coverage_path.read_text(encoding="utf-8")).get("note", "")
        if "combined solution mode" not in note:
            failures.append(f"coverage note {note!r} does not say the graph came from combined "
                            "solution mode")
        if not out.exists() or out.read_text(encoding="utf-8").strip() == "":
            failures.append("combined mode reported success but wrote no edges")
    return failures


def check_manifest_states_fallback_mode_when_combined_unavailable() -> list[str]:
    """When `dotnet` cannot assemble the synthetic solution at all, the arm must still produce a
    result via the per-project path — but the manifest must say it took the fallback, not present a
    per-project result as though it came from the (absent) combined run. Silence here would make a
    fallback row indistinguishable from a combined one, hiding a real difference in what was
    measured."""
    failures = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = _write_fake_engine(tmp)  # the ORIGINAL per-project fake engine (keys on "Good")
        dotnet = _write_fake_dotnet(tmp, _FAKE_DOTNET_FAIL_SOURCE)
        repo = _make_fake_repo(tmp, ["Good"])
        out = tmp / "out.jsonl"
        result = _run_arm_cli_combined(repo, "without-tests", out, engine, dotnet)
        if result.returncode != 0:
            return [f"fallback run failed unexpectedly: exit {result.returncode}, "
                    f"stderr {result.stderr[-800:]!r}"]
        if "falling back to per-project analysis" not in result.stderr:
            failures.append("no stderr line disclosed that combined mode was unavailable and the "
                            f"arm fell back — stderr: {result.stderr[-800:]!r}")
        coverage_path = Path(str(out) + ".coverage.json")
        if not coverage_path.is_file():
            return ["a fallback run wrote no coverage sidecar"]
        note = json.loads(coverage_path.read_text(encoding="utf-8")).get("note", "")
        if "per-project fallback mode" not in note:
            failures.append(f"coverage note {note!r} does not say the graph came from the "
                            "per-project fallback")
        if not out.exists() or out.read_text(encoding="utf-8").strip() == "":
            failures.append("the fallback produced no edges even though the Good project should "
                            "have analyzed cleanly")
    return failures


def main() -> int:
    failures = check_generic_arity()
    failures += check_key_parsing()
    failures += check_conversion_operator_caller_spelling()
    failures += check_il_operator_spelling()
    failures += check_unary_vs_binary_operator_arity()
    failures += check_checked_conversion_operator_caller_spelling()
    failures += check_engine_binary_is_never_guessed()
    failures += check_engine_identity_tells_two_builds_apart()
    failures += check_engine_identity_survives_an_unreadable_binary()
    failures += check_withheld_graph_reason_asserts_no_cause()
    failures += check_paths_are_repo_relative()
    failures += check_refused_is_not_empty()
    failures += check_describe_carries_identity()
    failures += check_stale_artifact_with_nonzero_exit_is_not_reported()
    failures += check_freshness_catches_exit_zero_with_no_output()
    failures += check_non_entitlement_refusal_is_not_mislabelled()
    failures += check_partial_project_coverage_fails_the_cell_loudly()
    failures += check_synthetic_solution_lists_exactly_in_scope_projects()
    failures += check_synthetic_solution_forces_classic_sln_on_sdk9_plus()
    failures += check_synthetic_solution_retries_without_format_on_old_sdk()
    failures += check_combined_mode_reports_zero_file_project_as_unproductive()
    failures += check_manifest_states_combined_mode()
    failures += check_manifest_states_fallback_mode_when_combined_unavailable()

    if failures:
        print("FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok — key shape, arity, anchors, identity, refusal-is-not-emptiness, staleness, exit "
          "codes, honest refusal reasons, and partial-coverage masking all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
