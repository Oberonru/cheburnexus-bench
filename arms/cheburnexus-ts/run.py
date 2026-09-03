#!/usr/bin/env python3
"""cheburnexus-ts arm — TsAnalyzer's TypeScript call graph, converted to the edge contract.

Drives the product's thin TS engine (`TsAnalyzer`, `D:\\DEV\\LLM-CheburNexus\\TsAnalyzer` in this
tree — a SEPARATE repository from this bench, never vendored in) via its own CLI:

    node dist/core/main.js --path <repo_root> --output-dir <scratch>

which emits `architecture.json` (Files[].Types[], including the metrics we join against) plus two
sidecars this arm actually reads: `architecture.calls.json` (the call graph: caller key -> [{Target,
Line, Kind}]) and `architecture.methods.json` (per-key metrics, of which we use only `Line`, the
1-based line ts-morph's `getStartLineNumber()` gives the member's own declaration).

── NOT behind the C# entitlement wall ─────────────────────────────────────────────────────────────
The `cheburnexus` (C#) arm's call graph is Pro-gated (see that arm's own header): a passport-less
machine gets `architecture.calls-counts.json` (aggregate, no caller identity) instead of the real
edge list. `TsAnalyzer` is a different code path — a standalone Node CLI with no MCP layer, no
passport check, no TIER gate anywhere in `emitCore.ts`/`main.ts` (grepped for
tier/entitlement/license/passport/paid/Pro — the only hits are unrelated comments about the phased
build, "Tier-1 robustness" and "MEMORY (Tier 2)"). It always writes `architecture.calls.json`
directly. So there is no "blocked" branch to report here: a run that produces zero edges on this arm
means the corpus genuinely had none the engine's syntactic call-site classifier could attribute, not
a withheld feature. If that assumption is ever wrong for a *packaged* build behind an MCP server, it
would need re-checking there — this arm drives the raw CLI, never the MCP path.

── THE CENTRAL RISK — the key contract has two ends (see oracle/typescript/README.md) ─────────────
The oracle's key is `{corpus-relative file}::{Class.member}:{line}[(get|set)]`, where `line` is the
1-based source line of the callable's OWN declaration as `ts.SyntaxKind`-based shadow-stack
transformer sees it (`sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1`
— see `oracle/typescript/transformer.js:189-196`). TsAnalyzer's own raw key carries NO line number
at all:

    {absolute file path}::{Namespace.OwnerType}::{"static:" if static}{name}({paramTypes})

— disambiguating overloads by parameter-type spelling instead of by line, and never spelling
"static" or "get"/"set" the way the oracle's label does. Translating one into the other is this
file's whole job, and every step is a fact read from TsAnalyzer's own output, never guessed:

1. **Line**: joined from `architecture.methods.json`, which stores `Line: node.getStartLineNumber()`
   for the SAME key `architecture.calls.json` uses. `getStartLineNumber()` (ts-morph) and
   `node.getStart(sourceFile)` (raw TS compiler API, what the oracle transformer calls) are the same
   underlying computation — ts-morph's method is a thin wrapper over the compiler API's `getStart`,
   both excluding leading trivia/JSDoc by default — so this is not an approximation of the oracle's
   line, it is the same fact read a second time through a different library. NOT independently
   verified line-for-line against the shadow-stack's own emitted labels in this session (no ts-jest
   run of the oracle harness was executed here to diff labels one-for-one); flagged as an assumption
   backed by API equivalence, not by a byte-level cross-check. See "join diagnostics" below for what
   WAS checked (whether the resulting keys strike any oracle rows at all, at scale).
2. **Class qualifier**: the oracle omits it entirely for a module-scope function (`cls` is null when
   no `ts.isClassDeclaration` is on the visitor's stack) and prefixes `ClassName.` for anything
   declared inside a class, constructors included (`ClassName.constructor:line`, never a `.ctor`
   rewrite — TS has no separate IL-style constructor name). TsAnalyzer always carries an owner type
   key, including its own synthetic per-file `$module$<basename>` type for top-level functions
   (`Kind: "module"` in `architecture.json`'s `Types[]`) — this arm reads that `Kind` field and drops
   the qualifier exactly when it says `"module"`, matching the oracle's null-class case.
3. **`static:` tag**: TsAnalyzer's own bookkeeping prefix (to disambiguate a same-named static and
   instance member — see `emitCore.ts`'s comment on `buildSidecarEntry`), stripped unconditionally.
   The oracle's label scheme never distinguishes static from instance at all, so this is not a lossy
   translation — TsAnalyzer's `static:` tag carries a real distinction the oracle key CANNOT express
   at this key granularity, which means a static and an instance member of the same name on the same
   class, declared identically otherwise, will collide onto one key from the C#... no, TS side too:
   nothing in this corpus was observed to hit that (a static and instance method never share a
   parameter-type-identical signature by construction in valid TS unless truly overloaded that way,
   which the language allows but is rare) — not specifically counted; see join diagnostics for what
   collision counting WAS done (dedup collisions inside `armkit.write_edges`, which is silent by
   design and shared by every arm).
4. **Accessor `(get)`/`(set)` suffix**: the oracle tags a get/set accessor's label with a literal
   `(get)`/`(set)` suffix (`ts_isGetAccessorDeclaration`/`SetAccessorDeclaration` in the transformer).
   TsAnalyzer's raw key carries no such tag — a getter and setter of the same class property are two
   separate call-graph nodes distinguished only by their *parameter count* (0 for a getter, exactly 1
   for a setter — TypeScript syntax guarantees this, it is not a heuristic on arbitrary code). This
   arm reads `architecture.json`'s `Types[].Properties[].Accessors` (which names ONLY real
   get/set-accessor property names per class, never a plain method) and, for exactly those
   `(ownerFqn, name)` pairs, appends `(get)` when the joined raw key's own parameter list is empty and
   `(set)` when it has exactly one parameter. A property with neither 0 nor 1 params paired to an
   accessor name cannot happen in valid TypeScript and is counted as a translation failure if it ever
   does (defensive, not expected to fire).
5. **Nested named functions have NO call-graph node at all in TsAnalyzer** (`emitCore.ts`'s own
   comment: "Phase 1: not in the call graph" — `emitNestedMethods` writes a `methods.json` metrics
   entry but never pushes the node into `capturedCalls`/`callTargets`). The oracle instruments every
   nested function shape it can safely wrap. This is a real, uncounted-here recall ceiling on this
   arm for any call made only from inside a nested named function — not fabricated, not patched
   around, reported in NOTES.md rather than silently absorbed into the number.
6. **Anonymous arrows/function-expressions used as inline callbacks are not caller nodes in
   TsAnalyzer's call graph either** (only class members, module-level named functions/consts, and
   class-field arrow-initializer properties get a caller key) — while the oracle gives EVERY arrow
   its own frame (`arrowN:line`), named or not, including inline callbacks. A class-field arrow
   *is* captured by TsAnalyzer, but keyed by the FIELD NAME, while the oracle labels the exact same
   node `Class.arrowN:line` (an anonymous per-file counter, not the property name) — these two
   spellings can never agree, so every class-field-arrow oracle row is unreachable by this arm by
   construction, not a bug to fix here. Recorded as a known gap, not tuned around.

None of the above is fudged into the join: an edge whose caller or callee key cannot be built by the
rules above is DROPPED from this arm's output and counted, never guessed at or silently included
under an approximate key. See `--out`'s `.coverage.json` sidecar (`unresolved_call_sites` = calls
dropped for translation reasons) and this run's stderr for the reason breakdown.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_lib"))
import armkit  # noqa: E402

WORK_ROOT = HERE / ".work"

# ── which engine runs is NEVER guessed — same rule, same cost, as arms/cheburnexus/run.py ─────────
ENGINE_ENV = "TS_ANALYZER_HOME"


def find_engine_dir() -> Path | None:
    """The TsAnalyzer checkout named by $TS_ANALYZER_HOME, or None. No fallback, no search of
    sibling directories: a silently-chosen checkout can be stale (unbuilt `dist/`, a different git
    sha than the one an experiment means) and yield a plausible wrong number — the exact failure
    mode the C# arm's own `find_engine()` comment documents having actually happened once."""
    env = os.environ.get(ENGINE_ENV)
    if not env:
        return None
    d = Path(env)
    entry = d / "dist" / "core" / "main.js"
    if entry.is_file():
        return d
    print(f"cheburnexus-ts: ${ENGINE_ENV}={env!r} has no dist/core/main.js "
          f"(run `npm run build` in it?)", file=sys.stderr)
    return None


def engine_missing_message() -> str:
    return (
        f"cheburnexus-ts: TsAnalyzer checkout not found — set ${ENGINE_ENV} to the TsAnalyzer "
        f"repo root (containing dist/core/main.js, built via `npm run build`). There is no "
        f"default on purpose, same reasoning as arms/cheburnexus/run.py's find_engine(): a "
        f"silently-chosen checkout can be older than the change under test."
    )


_IDENTITY_CACHE: dict[tuple[str, int, int], str] = {}


def engine_identity(engine_dir: Path) -> str:
    """Build date + content hash of dist/core/main.js — the compiled artifact actually run, not a
    package.json version string that can go stale without the dist/ being rebuilt. Same reasoning
    as arms/cheburnexus/run.py's engine_identity()."""
    entry = (engine_dir / "dist" / "core" / "main.js").resolve()
    try:
        st = entry.stat()
    except OSError as exc:
        return f"main.js UNREADABLE ({exc.strerror or exc}) at {entry}"
    key = (str(entry), st.st_mtime_ns, st.st_size)
    cached = _IDENTITY_CACHE.get(key)
    if cached is not None:
        return cached
    built = time.strftime("%Y-%m-%d", time.gmtime(st.st_mtime))
    try:
        digest = hashlib.sha256(entry.read_bytes()).hexdigest()[:12]
    except OSError as exc:
        return f"main.js built {built} UNREADABLE ({exc.strerror or exc}) at {entry}"
    identity = f"dist/core/main.js built {built} sha256:{digest} at {engine_dir}"
    _IDENTITY_CACHE[key] = identity
    return identity


def version() -> str:
    engine_dir = find_engine_dir()
    if engine_dir is None:
        return f"unavailable (checkout not found — set ${ENGINE_ENV})"
    return f"{engine_identity(engine_dir)} (TsAnalyzer CLI, ts-morph syntactic+semantic front end)"


NODE_ENV = "CHEBURNEXUS_TS_NODE"


def _node_bin() -> str:
    """Which `node` runs the engine. Unlike the engine checkout itself, an unpinned `node` is not a
    correctness risk to the SAME degree — it changes nothing about which facts the engine can see,
    only (in principle) JS runtime behavior — so this follows the C# arm's `_dotnet_bin()` precedent
    (overridable, not required) rather than its `find_engine()` precedent (required, no default)."""
    return os.environ.get(NODE_ENV) or "node"


# ── engine invocation, patterned on arms/cheburnexus/run.py's _invoke (defect #9: never trust a
# leftover artifact from a previous run) ────────────────────────────────────────────────────────
_MTIME_SLOP_SECONDS = 2.0


def _reset_work_dir(out_dir: Path) -> None:
    resolved = out_dir.resolve()
    work_root = WORK_ROOT.resolve()
    if resolved == work_root or work_root not in resolved.parents:
        raise RuntimeError(f"refusing to clean {resolved} — not a path strictly inside {work_root}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def _run_engine(engine_dir: Path, repo_root: Path, out_dir: Path) -> dict:
    _reset_work_dir(out_dir)
    main_js = engine_dir / "dist" / "core" / "main.js"
    arch = out_dir / "architecture.json"
    invoked_at = time.time()
    try:
        proc = subprocess.run(
            [_node_bin(), str(main_js), "--path", str(repo_root), "--output-dir", str(out_dir)],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "engine timed out after 600s"}

    (out_dir / "engine.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out_dir / "engine.stderr.txt").write_text(proc.stderr, encoding="utf-8")

    def _fresh(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_mtime >= invoked_at - _MTIME_SLOP_SECONDS
        except OSError:
            return False

    def _tail_reason() -> str:
        tail = [line for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip()]
        return tail[-1] if tail else f"exit {proc.returncode}, no output and no message"

    if proc.returncode != 0:
        return {"ok": False, "reason": f"exit {proc.returncode}: {_tail_reason()}"}
    if not _fresh(arch):
        reason = (f"exit 0, but architecture.json predates this invocation — stale artifact, "
                   f"treating as absent" if arch.is_file() else f"exit 0: {_tail_reason()}")
        return {"ok": False, "reason": reason}

    calls = out_dir / "architecture.calls.json"
    methods = out_dir / "architecture.methods.json"
    if not (_fresh(calls) and _fresh(methods)):
        return {"ok": False, "reason": "exit 0 but architecture.calls.json/architecture.methods.json "
                                        "missing or stale"}
    return {"ok": True, "arch": arch, "calls": calls, "methods": methods}


# ── raw-key parsing (mirrors buildSidecarEntry in TsAnalyzer/src/core/emitCore.ts) ─────────────────
def _parse_raw_key(raw: str) -> tuple[str, str, str, str] | None:
    """`{filePath}::{OwnerFqn}::{"static:"?}{name}({paramTypes})` -> (filePath, ownerFqn, name, params).

    Splits on '::' at most into 3 pieces (file/owner never contain it; a parameter TYPE spelling
    could in principle, e.g. a namespace-qualified generic — none observed on class-validator, and
    `rsplit` on '::' would be wrong here since the SIGNATURE is the part that could contain more
    separators, not the owner). `name` containing '/' (TsAnalyzer's nested-function ownerMethod/name
    shape) is rejected: nested named functions are never pushed into capturedCalls/callTargets (see
    this file's header, point 5) so this shape should never actually reach a caller/Target key in
    calls.json — returning None here rather than assuming makes that structural claim self-checking.
    """
    parts = raw.split("::")
    if len(parts) < 3:
        return None
    file_path = parts[0]
    owner_fqn = parts[1]
    member_sig = "::".join(parts[2:])
    if member_sig.startswith("static:"):
        member_sig = member_sig[len("static:"):]
    name, paren, rest = member_sig.partition("(")
    if not paren:
        return None
    params = rest[:-1] if rest.endswith(")") else rest
    if "/" in name:
        return None
    return file_path, owner_fqn, name, params


def _param_count(params: str) -> int:
    """Depth-aware comma count — a parameter TYPE can itself contain a comma (`Box<A, B>`,
    `{ new (...args: any[]): T }`), so a naive split would over-count. Same algorithm as the C# arm's
    `_key_arity`, ported to the TS key shape."""
    if not params.strip():
        return 0
    depth = 0
    count = 1
    for ch in params:
        if ch in "<[({":
            depth += 1
        elif ch in ">])}":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


def _to_repo_relative(abs_path: str, repo_root: Path) -> str:
    posix = abs_path.replace("\\", "/")
    root_posix = str(repo_root.resolve()).replace("\\", "/")
    if posix.startswith(root_posix + "/"):
        return posix[len(root_posix) + 1:]
    try:
        return Path(abs_path).relative_to(repo_root).as_posix()
    except ValueError:
        return posix  # never silently invent a path


class TypeIndex:
    """fqn -> (Kind, simple Name), and fqn -> the set of property names that are get/set accessors
    (never a plain method or field name — see `extractProperties` in emitCore.ts), built fresh from
    this run's own architecture.json. Never reads the oracle."""

    def __init__(self, arch: dict) -> None:
        self.kind_name: dict[str, tuple[str, str]] = {}
        self.accessor_names: dict[str, set[str]] = {}
        for file_obj in arch.get("Files", []):
            for t in file_obj.get("Types", []):
                fqn = t.get("fqn")
                if not fqn:
                    continue
                self.kind_name[fqn] = (t.get("Kind", ""), t.get("Name", ""))
                names = {p.get("Name") for p in t.get("Properties", []) if p.get("Accessors")}
                names.discard(None)
                if names:
                    self.accessor_names[fqn] = names


_DROP_REASONS = (
    "no-owner-fqn-in-architecture-json",
    "no-methods-sidecar-line",
    "unparseable-raw-key",
    "accessor-param-count-neither-0-nor-1",
)


class Translator:
    """Raw TsAnalyzer key -> oracle-shape key, or a drop reason. Every drop is counted — see this
    file's header, "None of the above is fudged into the join"."""

    def __init__(self, methods: dict, types: TypeIndex, repo_root: Path) -> None:
        self.methods = methods
        self.types = types
        self.repo_root = repo_root
        self.drops: dict[str, int] = {r: 0 for r in _DROP_REASONS}
        self.ok = 0

    def _drop(self, reason: str) -> None:
        self.drops[reason] = self.drops.get(reason, 0) + 1

    def translate(self, raw_key: str) -> str | None:
        parsed = _parse_raw_key(raw_key)
        if parsed is None:
            self._drop("unparseable-raw-key")
            return None
        file_path, owner_fqn, name, params = parsed

        entry = self.methods.get(raw_key)
        if entry is None or entry.get("Line") is None:
            self._drop("no-methods-sidecar-line")
            return None
        line = entry["Line"]

        info = self.types.kind_name.get(owner_fqn)
        if info is None:
            self._drop("no-owner-fqn-in-architecture-json")
            return None
        kind, simple_name = info
        qualified = name if kind == "module" else f"{simple_name}.{name}"

        suffix = ""
        accessors = self.types.accessor_names.get(owner_fqn) or set()
        if name in accessors:
            pc = _param_count(params)
            if pc == 0:
                suffix = "(get)"
            elif pc == 1:
                suffix = "(set)"
            else:
                self._drop("accessor-param-count-neither-0-nor-1")
                return None

        rel = _to_repo_relative(file_path, self.repo_root)
        self.ok += 1
        return f"{rel}::{qualified}:{line}{suffix}"


def _edges_from_calls(calls_data: dict, translator: Translator, repo_root: Path) -> Iterator[armkit.Edge]:
    for raw_caller, entry in calls_data.items():
        caller_key = translator.translate(raw_caller)
        caller_rel = _to_repo_relative(raw_caller.split("::", 1)[0], repo_root)
        for call in entry.get("Calls", []) or []:
            target = call.get("Target")
            if not target:
                continue
            callee_key = translator.translate(target)
            if caller_key is None or callee_key is None:
                continue  # counted inside translator.drops already
            yield armkit.Edge(
                caller=caller_key, callee=callee_key,
                caller_file=caller_rel, caller_line=call.get("Line"),
            )


def collect(repo_root: Path, cell: str) -> tuple[list[armkit.Edge], armkit.Coverage]:
    engine_dir = find_engine_dir()
    if engine_dir is None:
        raise RuntimeError(engine_missing_message())

    scratch = WORK_ROOT / repo_root.name / cell
    result = _run_engine(engine_dir, repo_root, scratch)
    if not result["ok"]:
        # TsAnalyzer has no entitlement wall (see this file's header) — a refusal here is a real
        # failure to run, not a withheld feature, so this is a plain error, never armkit.Blocked.
        raise RuntimeError(f"TsAnalyzer failed on {repo_root}: {result['reason']}")

    arch = json.loads(result["arch"].read_text(encoding="utf-8"))
    calls_env = json.loads(result["calls"].read_text(encoding="utf-8"))
    methods_env = json.loads(result["methods"].read_text(encoding="utf-8"))
    calls_data = calls_env.get("Data") or {}
    methods_data = methods_env.get("Data") or {}

    types = TypeIndex(arch)
    translator = Translator(methods_data, types, repo_root)
    edges = list(_edges_from_calls(calls_data, translator, repo_root))

    total_calls = sum(len(e.get("Calls") or []) for e in calls_data.values())

    # Scope: corpus.json's typestack/class-validator entry declares no product_projects/
    # test_assemblies_projects (that field only exists for the C# corpora today), so
    # armkit.in_scope is unscoped (whole checkout) for every TS entry — armkit.py's own
    # corpus_scope_dirs() prints one stderr note about this per repo, which is correct: nothing here
    # invents a scope corpus.json never declared. is_test_path (TEST_DIR_MARKERS) still applies for
    # the without-tests cell.
    edges = [e for e in edges
             if e.caller_file is None or armkit.in_scope(repo_root / e.caller_file, repo_root, cell)]
    if cell == "without-tests":
        edges = [e for e in edges
                 if e.caller_file is None or not armkit.is_test_path(repo_root / e.caller_file, repo_root)]

    dropped_calls = sum(translator.drops.values())
    # This is deliberately NOT "the engine couldn't resolve a callee" (TsAnalyzer's own
    # CallGraphCoverage.DroppedUnresolved/DroppedExternal already say that, separately, and aren't
    # surfaced here) — it is "this arm's oracle-key TRANSLATION failed for a call TsAnalyzer DID
    # resolve". Distinct fact, reported honestly as its own count rather than folded into the other.
    breakdown = ", ".join(f"{k}={v}" for k, v in translator.drops.items() if v)
    note = (f"TsAnalyzer resolved {total_calls} call sites; {translator.ok} key(s) translated "
            f"successfully, {dropped_calls} raw key lookups failed translation ({breakdown or 'none'}) "
            f"-- see this arm's run.py header for what each reason means and the known coverage gaps "
            f"(nested named functions and anonymous-arrow callbacks have no call-graph node in "
            f"TsAnalyzer at all, so they never reach this translator to begin with).")
    print(f"cheburnexus-ts: {note}", file=sys.stderr)

    coverage = armkit.Coverage(
        is_exact=False,
        reason="key_translation_loss",
        unresolved_call_sites=dropped_calls,
        note=note,
    )
    return edges, coverage


if __name__ == "__main__":
    sys.exit(armkit.main(name="cheburnexus-ts", version=version, collect=collect, mode="live"))
