#!/usr/bin/env python3
"""cheburnexus arm — our own C# analyzer's call graph, converted to the edge contract.

Drives the product's own CLI engine (ArchitectureAnalyzer.CLI, published under this build's
misleading executable name `arch-computer.exe` — see the product repo's package-all.sh/.ps1; do
not confuse it with `cheburnexus.exe`, the unrelated MCP server) in `--solution` mode, which is the
Roslyn/semantic front end: the same compiler-grade parse the product markets, as opposed to
`--project` (syntactic, no semantics).

THE ENTITLEMENT WALL (read this before reading the rest of the file — see NOTES.md for the full
account with evidence): the call graph is a Pro-tier feature, gated on TIER, not on analysis mode.
On a machine with no licence passport (the default, and — per the task — the one this arm must
never try to work around), ModelProjectionService nulls the semantic call-graph source before
export, and the CLI writes ONLY `architecture.calls-counts.json` (an aggregate
{calleeKey: incoming-call-count} map with NO caller identity) instead of
`architecture.calls.json` (the real per-edge list). Zero edges is therefore the correct, honest
result of a live run here — not a bug in this arm, and not something to patch around.

The conversion logic below (raw calls.json key -> contract key) is written and exercised against a
recorded sample so it is correct WHEN a licensed run does produce `architecture.calls.json` — for
this arm to be useful the moment a passport is available, and so a reader can audit the mapping
without needing one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_lib"))
import armkit  # noqa: E402  (path inserted above, matching the existing _lib/__pycache__ convention)

WORK_ROOT = HERE / ".work"

ENGINE_ENV = "CHEBURNEXUS_ENGINE"
# Only resolves on the machine that built this arm — the "live for us" half of the live/replay
# split in ARCHITECTURE.md. Anyone else sets $CHEBURNEXUS_ENGINE or gets the published replay rows.
DEFAULT_ENGINE_CANDIDATES = [
    Path.home() / "dev/Cheburnexus/LLM-CheburNexus/dist-all/cheburnexus-all-1.7.0-osx-x64/arch-computer.exe",
]

_DIST_VERSION_RE = re.compile(r"cheburnexus-all-([0-9][0-9.]*)-")


def find_engine() -> Path | None:
    env = os.environ.get(ENGINE_ENV)
    if env:
        p = Path(env)
        if p.is_file():
            return p
        print(f"cheburnexus: ${ENGINE_ENV}={env!r} does not exist", file=sys.stderr)
    for candidate in DEFAULT_ENGINE_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def version() -> str:
    """The tool's own version — cheap and fast, since --describe is called on every run. Parsed from
    the distributable folder name rather than from a live analysis (which would be the only way to
    read the assembly-level EngineVersion field, and far too slow for a --describe call). See
    NOTES.md: the assembly-level EngineVersion observed in architecture.json output was "1.1.0" at
    time of writing, distinct from this 1.7.0 distributable — and no git sha is exposed anywhere in
    the CLI's own output for us to surface.
    """
    engine = find_engine()
    if engine is None:
        return "unavailable (engine binary not found — set $CHEBURNEXUS_ENGINE)"
    match = _DIST_VERSION_RE.search(str(engine))
    dist_version = match.group(1) if match else "unknown"
    return f"{dist_version} (ArchitectureAnalyzer.CLI --solution, Roslyn semantic front end)"


# ── generic-arity conversion ────────────────────────────────────────────────────────────────────
# The engine emits source-level open-generic syntax ("Cache<T>"). The grader's own key function
# strips ANYTHING inside <...> on the assumption that it is a closed generic argument the oracle
# wrote — which is right for the oracle's Cecil-derived IL names, but would silently erase the
# arity distinction from an open generic type's OWN declaration if we passed "Cache<T>" through
# unconverted. So we convert to IL-style arity ("Cache`1") ourselves, matching what the oracle
# already carries and what the grader's stripper is written to leave alone (see EDGE_FORMAT.md
# "Generic arity" and grader/grade.py's strip_generic_arguments).
_GENERIC_SUFFIX = re.compile(r"^(.*?)<(.+)>$")


def backtick_arity(segment: str) -> str:
    match = _GENERIC_SUFFIX.match(segment)
    if not match:
        return segment
    name, params = match.group(1), match.group(2)
    depth = 0
    arity = 1
    for ch in params:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "," and depth == 0:
            arity += 1
    return f"{name}`{arity}"


# ── class index: namespace + constructor-name lookup, built per engine run ─────────────────────
# Built from that run's own architecture.json Files[].Types[]. Two things it exists to fix:
#
# 1. The calls sidecar's caller/callee keys are "filePath::Namespace.TypeName::Method(sig)" — a
#    single dotted string that does not itself say where the namespace ends. We do NOT need to
#    solve that split in general: the engine's calls graph only ever covers TOP-LEVEL types
#    (SemanticCallsResolver.EnumerateMembers explicitly walks `t.Parent is not TypeDeclarationSyntax`
#    — a deliberate, documented gap in the product itself, see NOTES.md), so every dotted string is
#    exactly "Namespace.TopLevelTypeName" and the file's own Namespace field gives the split for
#    free. Nested types (the contract's "Outer/Inner" notation) are consequently never exercised by
#    this arm — there is nothing for the engine to emit there.
# 2. A constructor is keyed by the class's own simple name (Roslyn's ConstructorDeclarationSyntax
#    .Identifier.Text literally equals the class name), not IL's ".ctor"/".cctor". We rename any
#    call whose method-name segment matches a name the class's own Methods[] marks
#    IsConstructor=true to ".ctor", matching the oracle's IL convention (see EDGE_FORMAT.md and
#    grader/test_identity.py, which asserts .ctor/.cctor must NOT collapse into one key). We cannot
#    tell an explicit static constructor apart from a same-signature instance one this way — both
#    key as ".ctor" — a rare, documented limitation (see NOTES.md).
# 3. (2026-08-25, follow-up to the namespace-leak engine fix) A file may declare several top-level
#    types across several actual namespaces — a namespace block followed by more code at GLOBAL
#    scope is the witness case, Serilog's src/Serilog/Guard.cs. FileModel.Namespace reports only the
#    FIRST such type's namespace (see the product repo's RoslynFileParser.cs), so keying every type
#    in the file off it, as adaptation #1 above does, silently mis-keys every OTHER top-level type.
#    The engine now flags exactly this with FqnNotReconstructible and serializes that type's real
#    fully-qualified name as "fqn" (ClassModel.cs) — we prefer it here when present. A type with no
#    namespace at all (GLOBAL scope, like Guard itself) additionally keys under a SYNTHETIC join key
#    the engine invents purely to keep same-named global types in different folders from colliding:
#    "~global.<parentdir>.<filename>" (GlobalNamespaceKey.cs). That prefix is the engine's OWN
#    internal bookkeeping, not a claim about the C# language — the compiler, and therefore the
#    oracle (which reads Cecil's IL), knows no such namespace and names the type by its bare simple
#    name (oracle: "Guard::AgainstNull", never "~global.Serilog.Guard.Guard::AgainstNull"). We strip
#    the synthetic prefix at the contract boundary in contract_key() below so this arm's keys land
#    in the cell the oracle actually recorded, instead of trading one mis-spelling for another.
GLOBAL_NAMESPACE_PREFIX = "~global."


class ClassIndex:
    def __init__(self) -> None:
        self.by_full: dict[str, tuple[str, str, frozenset[str]]] = {}

    def add_file(self, file_obj: dict) -> None:
        namespace = file_obj.get("Namespace") or ""
        for cls in file_obj.get("Types", []):
            name = cls.get("Name", "")
            if not name:
                continue
            # Adaptation #3: prefer the type's OWN fqn (set exactly when it would otherwise be
            # mis-keyed under the file's first-type namespace — see the comment block above).
            # Falls back to file Namespace + "." + name, unchanged from before this fix, for the
            # overwhelming majority of files where fqn is omitted because the reconstruction is
            # already correct.
            fqn = cls.get("fqn")
            if fqn:
                full_key = fqn
                type_namespace = fqn.rsplit(".", 1)[0] if "." in fqn else ""
            else:
                full_key = f"{namespace}.{name}" if namespace else name
                type_namespace = namespace
            type_path = backtick_arity(name)
            ctor_names = frozenset(
                m.get("Name", "") for m in cls.get("Methods", []) if m.get("IsConstructor")
            )
            self.by_full[full_key] = (type_namespace, type_path, ctor_names)

    def contract_key(self, dotted_type: str, method_name: str) -> str:
        entry = self.by_full.get(dotted_type)
        if entry is None:
            # A type our own file walk did not cover — should not happen for same-project calls
            # (calls.json and architecture.json come from the same run), but a raw dot-split beats
            # silently dropping the edge if it ever does.
            namespace, _, simple = dotted_type.rpartition(".")
            type_path = backtick_arity(simple) if simple else backtick_arity(dotted_type)
        else:
            namespace, type_path, ctor_names = entry
            if method_name in ctor_names:
                method_name = ".ctor"
        # Adaptation #3 (see the class-level comment above): the namespace we already resolved —
        # never a fresh count of dot-segments off the front of the string, which would also catch a
        # REAL namespace that merely started with the literal text "~global." — is what tells us
        # this type lives at global scope by the engine's synthetic join-key convention. Render it
        # by its bare type name, matching the compiler/oracle's own naming for global-scope types.
        if namespace.startswith(GLOBAL_NAMESPACE_PREFIX):
            type_full = type_path
        else:
            type_full = f"{namespace}.{type_path}" if namespace else type_path
        return f"{type_full}::{method_name}"


def _parse_raw_key(raw: str) -> tuple[str, str, str] | None:
    """Split 'filePath::Namespace.Type::Method(paramTypes)' into (filePath, dottedType, methodName).
    Only ever splits on the literal '::' the engine's CallKeyBuilder inserts — file paths use '/',
    the type chain uses '.', and the signature uses '(...)', so none of those collide with it."""
    parts = raw.split("::")
    if len(parts) < 3:
        return None
    file_path = parts[0]
    dotted_type = parts[1]
    method_sig = "::".join(parts[2:])
    method_name = method_sig.split("(", 1)[0]
    return file_path, dotted_type, method_name


def _to_repo_relative(abs_path: str, repo_root: Path) -> str:
    posix = abs_path.replace("\\", "/")
    root_posix = str(repo_root).replace("\\", "/")
    if posix.startswith(root_posix + "/"):
        return posix[len(root_posix) + 1 :]
    try:
        return Path(abs_path).relative_to(repo_root).as_posix()
    except ValueError:
        return posix  # never silently invent a path; worst case the caller_file is absolute


# ── accessor-caller translation ─────────────────────────────────────────────────────────────────
# A call written inside a property/indexer/event accessor is keyed by the engine's CallKeyBuilder
# as the MEMBER a human wrote — MemberKey/IndexerKey deliberately keep ONE node for a whole
# property/indexer/event, see that file's own comment — while the accessor it was written in is
# recorded separately, per CALL, as CallEdge.Accessor ∈ {get, set, init, add, remove}. IL, and
# therefore this polygon's oracle, names the accessor, not the member. We translate the CALLER's
# method-name segment to match, for each individual call — the same class of translation
# ClassIndex.contract_key already does one section up for a constructor (renaming its
# class-simple-name spelling to ".ctor"): reading a fact the engine already recorded, at the same
# boundary function where this arm expresses the engine's key space in the oracle's vocabulary, not
# a new seam. See DECISION-accessor-caller-spelling-2026-08-28.md for the full argument.
#
# `init` maps to `set_X`, NOT `init_X` — verified empirically against a compiled `{ get; init; }`
# property (PropertyInfo.SetMethod.Name -> "set_X"; there is no "init method" in metadata, only an
# IsExternalInit-marked setter). RoslynMethodParser.ParseAccessor synthesizes "init_" + name for its
# own separate, in-memory, never-serialised ClassModel.Accessors list — that convention must NOT be
# reused here, since it would ship the same wrong spelling against this arm's oracle.
_ACCESSOR_PREFIX = {"get": "get_", "set": "set_", "init": "set_", "add": "add_", "remove": "remove_"}


def _accessor_caller_method(method_name: str, accessor: str | None) -> str:
    """Rewrite one call's caller method-name segment to the accessor IL actually names.

    Deliberately leaves an INDEXER caller untouched. Our key for an indexer is
    `this[paramTypes]` (CallKeyBuilder.IndexerKey) and carries no item name at all; IL defaults to
    `get_Item`/`set_Item`, but a `[IndexerName("...")]` attribute can rename it and the engine
    records nothing about that attribute anywhere in this sidecar. Translating would mean assuming
    a name we never read, and this engine does not guess — so an indexer-shaped caller stays junk
    against the ruler, disclosed rather than silently wrong. Detected here by the key shape itself
    (the literal "this[" prefix IndexerKey always emits), not by a separate flag.
    """
    if accessor is None or method_name.startswith("this[") or ".this[" in method_name:
        return method_name
    prefix = _ACCESSOR_PREFIX.get(accessor)
    if not prefix:
        return method_name
    # The prefix goes on the MEMBER, not on the whole name. An explicit interface implementation is
    # spelled qualifier-first in metadata (`MyNs.IFoo.get_P`, never `get_MyNs.IFoo.P` — measured on a
    # compiled probe), and by this point method_name may already carry that qualifier, either from the
    # engine's source-text key or from MetadataName. A name with no dot is unaffected: rpartition
    # returns ("", "", name) and the result is byte-identical to the old plain concatenation.
    head, dot, member = method_name.rpartition(".")
    return f"{head}{dot}{prefix}{member}"


def _metadata_caller_method(method_name: str, entry: dict) -> str:
    """Prefer the name the COMPILER gave this member, when the engine recorded one.

    `CallSidecarEntry.MetadataName` is written only where a member explicitly implements an
    interface and only in semantic mode, where a symbol exists. The engine's own key spells such a
    member with the qualifier the SOURCE writes (`IValidator.Validate`) because that key must also be
    producible without a semantic model; IL spells it with the interface's FULLY-QUALIFIED name
    (`FluentValidation.IValidator.Validate`), and the two are genuinely different strings — measured
    on this corpus, not assumed. This arm copies the recorded fact rather than reconstructing a
    namespace it would have to guess, the same way it copies `CallEdge.Accessor`.

    Absent field -> the name is returned untouched, so every other caller is byte-identical and the
    syntactic fallback (which records nothing) is never given an invented qualification.
    """
    recorded = entry.get("MetadataName")
    return recorded if recorded else method_name


def _edges_from_calls(calls_path: Path, index: ClassIndex, repo_root: Path) -> Iterator[armkit.Edge]:
    envelope = json.loads(calls_path.read_text(encoding="utf-8"))
    data = envelope.get("Data") or {}
    for raw_caller, entry in data.items():
        parsed = _parse_raw_key(raw_caller)
        if parsed is None:
            continue
        caller_file, caller_type, caller_method = parsed
        caller_rel = _to_repo_relative(caller_file, repo_root)

        for call in entry.get("Calls", []) or []:
            target = call.get("Target")
            if not target:
                continue
            tparsed = _parse_raw_key(target)
            if tparsed is None:
                continue
            _, callee_type, callee_method = tparsed
            callee_key = index.contract_key(callee_type, callee_method)

            # Accessor translation happens per CALL, not once per raw_caller entry: a property's
            # getter and setter share this SAME entry (one member, per CallKeyBuilder.MemberKey),
            # so only each call's own Accessor tag says which body it came from.
            effective_caller_method = _accessor_caller_method(
                _metadata_caller_method(caller_method, entry), call.get("Accessor")
            )
            caller_key = index.contract_key(caller_type, effective_caller_method)

            yield armkit.Edge(
                caller=caller_key,
                callee=callee_key,
                caller_file=caller_rel,
                caller_line=call.get("Line"),
            )


# ── project discovery ───────────────────────────────────────────────────────────────────────────
def _discover_csproj(repo_root: Path) -> tuple[list[Path], list[Path]]:
    """Product vs test .csproj files under the checkout, classified purely by path — the same
    TEST_DIR_MARKERS rule every arm shares (armkit.is_test_path).

    Defect #10 (2026-08-24): this used to be the whole story, and every .csproj that merely failed
    to look like a test directory was treated as a product candidate — including Polly's legacy
    `src/Polly/`, a different, excluded assembly that happens to share the `Polly` namespace with
    `Polly.Core`. `collect()` below now additionally scopes these candidates to corpus.json's own
    `product_projects`/`test_assemblies_projects` (via `armkit.in_scope`) before running the engine
    on any of them — see armkit.py's "first-party scope" section for why that is not the same thing
    as reading the oracle."""
    product: list[Path] = []
    test: list[Path] = []
    for path in sorted(repo_root.rglob("*.csproj")):
        parts = {p.lower() for p in path.parts}
        if "obj" in parts or "bin" in parts or ".git" in parts:
            continue
        (test if armkit.is_test_path(path, repo_root) else product).append(path)
    return product, test


_NO_SOURCES_MARKER = "No in-scope C# sources found"

# Filesystem mtime resolution/clock-skew slack for the freshness check below. Generous on purpose —
# this check exists as a belt-and-braces backstop to _reset_work_dir(), not as the primary defence,
# so a false "stale" verdict caused by a coarse filesystem clock would be worse than a slightly loose
# window.
_MTIME_SLOP_SECONDS = 2.0


def _reset_work_dir(out_dir: Path) -> None:
    """Destroy and recreate exactly this per-(repo, cell, project) scratch directory before the
    engine writes into it, so a previous run's artifacts can never be mistaken for this run's own.

    Defect #9 (2026-08-24): this directory was never cleaned between runs. A three-day-old
    architecture.calls.json from a prior run sat here, and when the engine refused this run (exit
    2, wrote nothing) the arm read that stale file and reported it as today's result.

    Scoped to the literal path the caller is about to write into — never WORK_ROOT itself, never
    anything above it — checked here rather than trusted, so a future refactor that passes a wrong
    (too broad) path fails loudly instead of deleting more than one project's scratch space.
    """
    resolved = out_dir.resolve()
    work_root = WORK_ROOT.resolve()
    if resolved == work_root or work_root not in resolved.parents:
        raise RuntimeError(f"refusing to clean {resolved} — not a path strictly inside {work_root}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def _invoke(engine: Path, args: list[str], out_dir: Path, tag: str) -> dict:
    """Run one engine invocation, capture its streams under out_dir/<tag>.*, and report what
    happened — never what it means; the caller decides.

    "Happened" means three things, all defect #9 fixes: the exit code (a non-zero exit is never
    "ok", regardless of what files happen to be sitting in out_dir), and — belt-and-braces, in case
    _reset_work_dir() was skipped, defeated by a partial write, or a future refactor drops it — the
    mtime of anything read back, which must be no older than the moment THIS invocation started.
    """
    arch = out_dir / "architecture.json"
    invoked_at = time.time()
    try:
        proc = subprocess.run(
            [str(engine), *args, "--output", str(arch)],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "engine timed out after 600s", "returncode": None, "stderr": ""}

    (out_dir / f"{tag}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out_dir / f"{tag}.stderr.txt").write_text(proc.stderr, encoding="utf-8")

    def _fresh(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_mtime >= invoked_at - _MTIME_SLOP_SECONDS
        except OSError:
            return False

    def _tail_reason() -> str:
        tail = [line for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip()]
        return tail[-1] if tail else f"exit {proc.returncode}, no output and no message"

    if proc.returncode != 0:
        return {"ok": False, "reason": f"exit {proc.returncode}: {_tail_reason()}",
                "returncode": proc.returncode, "stderr": proc.stderr}

    if not _fresh(arch):
        if arch.is_file():
            reason = (f"exit 0, but {arch.name} predates this invocation by more than "
                      f"{_MTIME_SLOP_SECONDS}s — a stale artifact from a previous run, not this "
                      f"run's output; treating it as absent")
        else:
            reason = f"exit 0: {_tail_reason()}"
        return {"ok": False, "reason": reason, "returncode": proc.returncode, "stderr": proc.stderr}

    calls = out_dir / "architecture.calls.json"
    counts = out_dir / "architecture.calls-counts.json"
    return {
        "ok": True,
        "arch": arch,
        "calls": calls if _fresh(calls) else None,
        "counts": counts if _fresh(counts) else None,
        "returncode": proc.returncode,
        "stderr": proc.stderr,
    }


def _run_engine(engine: Path, csproj: Path, out_dir: Path) -> dict:
    """Invoke the engine on exactly one project: --solution (Roslyn/semantic — the mode that can
    ever produce a real edge list) first.

    One fallback, deliberately narrow: when --solution finds ZERO in-scope sources for a project
    that plainly has some — observed on 2 of 3 corpus repos, always paired with an explicit
    <Compile Include="..\\Shared\\X.cs"> item pointing outside the project's own directory
    (FluentValidation's every project via a shared CommonAssemblyInfo.cs; three of Polly's four
    src/ projects via $(MSBuildThisFileDirectory)..\\Shared\\*.cs) — retry with --project
    (syntactic, no restore needed) on that project's own directory so the repo is not reported
    unavailable over what looks like a DotNetProjectInputProvider discovery gap rather than a real
    absence of source. See NOTES.md for the evidence (--project finds the same files fine).

    Deliberately NOT extended to a missing-restore refusal: that refusal is the product's own
    correctness guardrail ("without restore the call graph would be silently incomplete"), and
    silently downgrading to syntactic mode there would defeat it, not work around a discovery bug.
    """
    _reset_work_dir(out_dir)  # defect #9: must happen once, before ANY invocation writes here
    result = _invoke(engine, ["--solution", str(csproj)], out_dir, "solution")
    if result["ok"] or _NO_SOURCES_MARKER not in result.get("reason", ""):
        result["mode_used"] = "solution"
        return result

    fallback = _invoke(engine, ["--project", str(csproj.parent)], out_dir, "project-fallback")
    fallback["mode_used"] = "project (fallback after --solution found 0 sources)"
    if not fallback["ok"]:
        fallback["reason"] = f"{result['reason']} | fallback --project also failed: {fallback['reason']}"
    return fallback


def collect(repo_root: Path, cell: str) -> Iterable[armkit.Edge]:
    engine = find_engine()
    if engine is None:
        raise RuntimeError(
            f"cheburnexus engine binary not found — set ${ENGINE_ENV} to arch-computer.exe "
            f"(ArchitectureAnalyzer.CLI). This arm is live only on a machine that has the product "
            f"built; see arms/ARCHITECTURE.md's live/replay split and NOTES.md."
        )

    product, test = _discover_csproj(repo_root)
    candidates = list(product) + (test if cell == "with-tests" else [])
    # Defect #10: a sibling, non-first-party project (same repo, different assembly, e.g. Polly's
    # legacy src/Polly/) is not a test directory, so it survived _discover_csproj unfiltered. Scope
    # it out here against corpus.json's own product_projects/test_assemblies_projects — the same
    # check armkit.source_files applies to raw source files for grep, and armkit.in_scope applies
    # per-edge for repowise.
    candidates = [c for c in candidates if armkit.in_scope(c, repo_root, cell)]
    if not candidates:
        raise RuntimeError(f"no .csproj found under {repo_root}")

    scratch = WORK_ROOT / repo_root.name / cell
    edges: list[armkit.Edge] = []
    # Defect #9 (masking): a project that ran but produced no call graph, and a project that
    # produced real edges, must not be allowed to average out into a plausible-looking partial
    # number. Every candidate lands in exactly one of these two lists.
    productive: list[str] = []
    unproductive: list[tuple[str, str]] = []  # (relative csproj path, why it produced no call graph)

    for csproj in sorted(candidates):
        out_dir = scratch / csproj.stem
        result = _run_engine(engine, csproj, out_dir)
        rel = str(csproj.relative_to(repo_root))

        if not result["ok"]:
            # The reason is whatever the engine itself said (see _invoke: last non-blank line of
            # its own stdout/stderr, or the freshness verdict) — never a guessed label. Defect #9
            # mislabelled exactly this kind of refusal as "the entitlement wall".
            print(f"cheburnexus: skip {rel}: {result['reason']}", file=sys.stderr)
            unproductive.append((rel, result["reason"]))
            continue
        if result["mode_used"] != "solution":
            print(f"cheburnexus: {rel} analyzed via {result['mode_used']}", file=sys.stderr)

        if result["calls"] is not None:
            index = ClassIndex()
            arch_model = json.loads(result["arch"].read_text(encoding="utf-8"))
            for file_obj in arch_model.get("Files", []):
                index.add_file(file_obj)
            edges.extend(_edges_from_calls(result["calls"], index, repo_root))
            productive.append(rel)
        elif result["counts"] is not None:
            # A fresh, successful (exit 0) run that emitted only the aggregate counts sidecar and no
            # per-edge one. The engine itself prints no message confirming why — this reading comes
            # from having read ModelProjectionService.cs (see NOTES.md), not from anything on
            # stderr, so it is stated as our own inference, not attributed to the engine.
            reason = (
                "the engine analyzed this project (exit 0) but emitted only "
                "architecture.calls-counts.json (aggregate counts, no caller identity) — no "
                "architecture.calls.json. Consistent with the documented entitlement wall "
                "(analyzer.semantic is a Pro feature and no valid passport is present on this "
                "machine — see NOTES.md); the engine's own output does not state a reason."
            )
            print(f"cheburnexus: {rel} analyzed but the call graph was withheld — {reason}",
                  file=sys.stderr)
            unproductive.append((rel, reason))
        else:
            reason = "produced neither calls.json nor calls-counts.json (unexpected)"
            print(f"cheburnexus: {rel} {reason}", file=sys.stderr)
            unproductive.append((rel, reason))

    if not productive:
        # Nothing at all produced a call graph — refused, not empty. Returning zero edges here
        # would be graded as recall 0.000 and printed beside our own engine as though it had looked
        # and found nothing. The runner turns Blocked into a gap carrying the reason instead.
        detail = "; ".join(f"{rel}: {why}" for rel, why in unproductive)
        raise armkit.Blocked(
            f"none of {len(candidates)} candidate project(s) produced a call graph for cell "
            f"{cell!r} — {detail}. Per-project engine logs: {scratch}"
        )

    if unproductive:
        # Defect #9 (masking): some projects DID produce real edges, but not all of them did.
        # Grading the cell on the projects that happened to work — as if the others were never
        # candidates at all — is exactly the "378 edges" false-complete result this fix exists to
        # stop. Fail the whole cell loudly instead.
        detail = "; ".join(f"{rel}: {why}" for rel, why in unproductive)
        raise armkit.Blocked(
            f"{len(unproductive)} of {len(candidates)} project(s) produced no call graph for cell "
            f"{cell!r} even though {len(productive)} did ({', '.join(productive)}) — refusing to "
            f"grade a partial result as complete. {detail}. Per-project engine logs: {scratch}"
        )

    if cell == "without-tests":
        edges = [
            e for e in edges
            if e.caller_file is None or not armkit.is_test_path(repo_root / e.caller_file, repo_root)
        ]

    return edges


if __name__ == "__main__":
    sys.exit(armkit.main(name="cheburnexus", version=version, collect=collect, mode="live"))
