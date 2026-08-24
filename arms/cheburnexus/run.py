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
import subprocess
import sys
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
class ClassIndex:
    def __init__(self) -> None:
        self.by_full: dict[str, tuple[str, str, frozenset[str]]] = {}

    def add_file(self, file_obj: dict) -> None:
        namespace = file_obj.get("Namespace") or ""
        for cls in file_obj.get("Types", []):
            name = cls.get("Name", "")
            if not name:
                continue
            full_key = f"{namespace}.{name}" if namespace else name
            type_path = backtick_arity(name)
            ctor_names = frozenset(
                m.get("Name", "") for m in cls.get("Methods", []) if m.get("IsConstructor")
            )
            self.by_full[full_key] = (namespace, type_path, ctor_names)

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


def _edges_from_calls(calls_path: Path, index: ClassIndex, repo_root: Path) -> Iterator[armkit.Edge]:
    envelope = json.loads(calls_path.read_text(encoding="utf-8"))
    data = envelope.get("Data") or {}
    for raw_caller, entry in data.items():
        parsed = _parse_raw_key(raw_caller)
        if parsed is None:
            continue
        caller_file, caller_type, caller_method = parsed
        caller_key = index.contract_key(caller_type, caller_method)
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

            yield armkit.Edge(
                caller=caller_key,
                callee=callee_key,
                caller_file=caller_rel,
                caller_line=call.get("Line"),
            )


# ── project discovery ───────────────────────────────────────────────────────────────────────────
def _discover_csproj(repo_root: Path) -> tuple[list[Path], list[Path]]:
    """Product vs test .csproj files, classified purely by path — the same TEST_DIR_MARKERS rule
    every arm shares (armkit.is_test_path), never corpus.json (off limits by the hard rule)."""
    product: list[Path] = []
    test: list[Path] = []
    for path in sorted(repo_root.rglob("*.csproj")):
        parts = {p.lower() for p in path.parts}
        if "obj" in parts or "bin" in parts or ".git" in parts:
            continue
        (test if armkit.is_test_path(path, repo_root) else product).append(path)
    return product, test


_NO_SOURCES_MARKER = "No in-scope C# sources found"


def _invoke(engine: Path, args: list[str], out_dir: Path, tag: str) -> dict:
    """Run one engine invocation, capture its streams under out_dir/<tag>.*, and report what
    happened — never what it means; the caller decides."""
    arch = out_dir / "architecture.json"
    try:
        proc = subprocess.run(
            [str(engine), *args, "--output", str(arch)],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "engine timed out after 600s"}

    (out_dir / f"{tag}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out_dir / f"{tag}.stderr.txt").write_text(proc.stderr, encoding="utf-8")

    if not arch.is_file():
        tail = [line for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip()]
        reason = tail[-1] if tail else f"exit {proc.returncode}, no output and no message"
        return {"ok": False, "reason": reason}

    calls = out_dir / "architecture.calls.json"
    counts = out_dir / "architecture.calls-counts.json"
    return {
        "ok": True,
        "arch": arch,
        "calls": calls if calls.is_file() else None,
        "counts": counts if counts.is_file() else None,
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
    out_dir.mkdir(parents=True, exist_ok=True)
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
    if not candidates:
        raise RuntimeError(f"no .csproj found under {repo_root}")

    scratch = WORK_ROOT / repo_root.name / cell
    edges: list[armkit.Edge] = []
    successes = 0
    wall_hit = False

    for csproj in sorted(candidates):
        out_dir = scratch / csproj.stem
        result = _run_engine(engine, csproj, out_dir)
        rel = csproj.relative_to(repo_root)

        if not result["ok"]:
            print(f"cheburnexus: skip {rel}: {result['reason']}", file=sys.stderr)
            continue
        successes += 1
        if result["mode_used"] != "solution":
            print(f"cheburnexus: {rel} analyzed via {result['mode_used']}", file=sys.stderr)

        if result["calls"] is not None:
            index = ClassIndex()
            arch_model = json.loads(result["arch"].read_text(encoding="utf-8"))
            for file_obj in arch_model.get("Files", []):
                index.add_file(file_obj)
            edges.extend(_edges_from_calls(result["calls"], index, repo_root))
        elif result["counts"] is not None:
            wall_hit = True
            print(
                f"cheburnexus: {rel} analyzed but the call graph was withheld — "
                f"architecture.calls-counts.json only (aggregate counts, no caller identity), "
                f"no architecture.calls.json. This is the entitlement wall (analyzer.semantic is a "
                f"Pro feature), not a failure — see NOTES.md.",
                file=sys.stderr,
            )
        else:
            print(f"cheburnexus: {rel} produced neither calls.json nor calls-counts.json "
                  f"(unexpected — treating as zero edges from this project)", file=sys.stderr)

    if successes == 0:
        raise RuntimeError(
            f"could not analyze any of {len(candidates)} candidate project(s) under {repo_root} "
            f"for cell {cell!r} — see {scratch} for per-project engine logs"
        )

    if wall_hit and not edges:
        print(
            "cheburnexus: 0 edges is the correct result of this run, not a bug — the licence wall "
            "withheld the call graph on every project that otherwise analyzed cleanly. See NOTES.md.",
            file=sys.stderr,
        )

    if cell == "without-tests":
        edges = [
            e for e in edges
            if e.caller_file is None or not armkit.is_test_path(repo_root / e.caller_file, repo_root)
        ]

    return edges


if __name__ == "__main__":
    sys.exit(armkit.main(name="cheburnexus", version=version, collect=collect, mode="live"))
