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
5. **UPDATE (2026-09-03 follow-up, engine commit 2a2014e1): nested named functions, named function
   expressions, arrows-on-consts, and anonymous/IIFE closures now ALL get their own call-graph node**
   (`collectCallables`/`describeCallable`/`emitClosures` in `emitCore.ts` — see
   `finding-ts-arm-first-numbers-arrow-fold-2026-09-03.md`). This arm's raw-key parser now accepts the
   resulting `{ownerMethod}/{name}` shape (previously rejected outright, silently dropping every
   closure edge — see git history for the pre-fix version of this file and its point 5/6, now stale).
   Named closures translate exactly like any other member (their real identifier + line, the same as
   before this fix — the oracle's `labelFor` never qualifies by the enclosing FUNCTION, only by the
   enclosing CLASS, so the `ownerMethod` prefix is stripped for the join). Anonymous/IIFE closures
   still cannot be translated — see point 6 below, now the real limit rather than "not implemented".
6. **UPDATE (2026-09-03, second follow-up): anonymous arrows/function-expressions/IIFEs now DO
   translate.** The gap described below was real when first written (oracle labeled anonymous
   callables with a per-file VISIT-ORDER counter — `anon${counter}`/`anonFnExpr${counter}`/
   `arrow${counter}`, fragile the same way project memory already flagged for the shadow-stack edge
   count: an unrelated earlier edit renumbers every later anonymous callable). Rather than leave that
   fragile scheme in place and declare the join permanently impossible, the ORACLE side was changed
   (`oracle/typescript/transformer.js`'s `anonLabel()`) to the SAME positionally-stable choice
   TsAnalyzer's `describeCallable` already made: label by the callable's own start line, not by visit
   order. `_anon_join_name` in this file now converts TsAnalyzer's raw `<anon:L<line>>`/
   `<iife:L<line>>` tag to the oracle's `anonL<line>` spelling and lets it join exactly, through the
   same machinery as every other member.
   ⚠ MEASURED PITFALL, not merely theoretical: the first version of `anonLabel()` reused
   TsAnalyzer's own bracketed spelling (`<anon:L<line>>`) verbatim for byte-for-byte match. That
   made naive recall jump 0.095 -> 0.397 with `matched` STUCK at exactly 240 — the entire "gain" was
   ~1900 oracle primary-cell rows silently vanishing into `grade.py`'s `normalize_caller`
   "compiler-generated caller, drop it" rule (built for C#'s `<>c__DisplayClass` mangling, which
   fires on ANY '<' in a caller key) — a shrunk denominator dressed up as recall, exactly the
   "ruler that deletes the hard half of the exam" failure this project keeps catching. The
   bracket-free `anonL<line>` spelling side-steps that rule entirely and gave the REAL number:
   recall 0.511, naive precision 0.979, `matched` 1279 (up from 239 pre-engine-fix) — see git log
   on this file and `oracle/typescript/transformer.js`'s `anonLabel()` comment for the full
   before/after. Same-line collisions (two anonymous callables starting on the identical line) are
   disambiguated on the oracle side by column (`anonL<line>C<col>` for the second-or-later one on a
   line); this arm's join key never carries a column, so such a SECOND colliding closure is simply
   unreachable by this arm (falls through unmatched, not fabricated) — measured at 0 occurrences on
   class-validator (see `Translator`'s `closure_collision_keys` and the run's coverage note).
   A class-field arrow initializer *is* still captured by TsAnalyzer under the FIELD NAME (a
   different code path entirely — see `methodNodesOf`/`buildSidecarEntry` above, not this closure
   path), which the oracle's `anonL<line>` label does not carry either — that specific shape remains
   a real, separate, unaddressed gap (not measured how many edges it affects on this corpus).

None of the above is fudged into the join: an edge whose caller or callee key cannot be built by the
rules above is DROPPED from this arm's output and counted, never guessed at or silently included
under an approximate key. See `--out`'s `.coverage.json` sidecar (`unresolved_call_sites` = calls
dropped for translation reasons) and this run's stderr for the reason breakdown.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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


_IDENTITY_CACHE: dict[str, str] = {}


def engine_identity(engine_dir: Path) -> str:
    """Build date + content hash of ALL of dist/core/*.js — every compiled module the engine's own
    require() graph can load at runtime, not just the entry point.

    2026-09-03 defect (found while measuring the TS polygon, fixed here): this used to hash only
    `dist/core/main.js`. TsAnalyzer's actual analysis logic lives in `emitCore.ts` (imported by
    main.js as `./emitCore`, and itself pulling in `./prof.js`) — main.js is a thin CLI wrapper.
    A rebuild that only touches emitCore.ts changes `dist/core/emitCore.js` and leaves
    `dist/core/main.js` byte-identical (same mtime bump from `npm run build`'s bundler, same
    content), so the old identity string was blind to exactly the edits that matter: an agent had
    to check `dist/` by hand today to be sure a fresh build was actually measured. Same shape as
    the C# arm's engine_identity() comment: an artifact older than the code it claims to represent
    is a SILENT plausible-negative, and hashing the wrong file is how the manifest keeps lying
    after the fix has landed.

    Hashing dist/core/ (not the whole dist/, which also holds __tests__/) matches what main.js's
    own require() graph can reach: core/*.js only, sorted by relative path so the digest is
    order-independent and a renamed/added module changes it. node_modules (ts-morph, svelte,
    @vue/compiler-sfc) is deliberately OUT of scope — that is third-party dependency identity, a
    different question from "did our own source change," and hashing it would make an unrelated
    `npm install` look like an engine change.
    """
    core_dir = (engine_dir / "dist" / "core").resolve()
    try:
        files = sorted(p for p in core_dir.rglob("*.js") if p.is_file())
    except OSError as exc:
        return f"dist/core UNREADABLE ({exc.strerror or exc}) at {core_dir}"
    if not files:
        return f"dist/core has no .js files at {core_dir}"
    try:
        stats = [(p, p.stat()) for p in files]
    except OSError as exc:
        return f"dist/core UNREADABLE ({exc.strerror or exc}) at {core_dir}"
    key = (str(core_dir), tuple((str(p), st.st_mtime_ns, st.st_size) for p, st in stats))
    cache_key = repr(key)
    cached = _IDENTITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    newest_mtime = max(st.st_mtime for _, st in stats)
    built = time.strftime("%Y-%m-%d", time.gmtime(newest_mtime))
    hasher = hashlib.sha256()
    try:
        for p, _ in stats:
            hasher.update(p.relative_to(core_dir).as_posix().encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(p.read_bytes())
            hasher.update(b"\0")
    except OSError as exc:
        return f"dist/core built {built} UNREADABLE ({exc.strerror or exc}) at {core_dir}"
    digest = hasher.hexdigest()[:12]
    identity = (f"dist/core/*.js ({len(files)} files) built {built} sha256:{digest} "
                f"at {engine_dir}")
    _IDENTITY_CACHE[cache_key] = identity
    return identity


def version() -> str:
    engine_dir = find_engine_dir()
    if engine_dir is None:
        return f"unavailable (checkout not found — set ${ENGINE_ENV})"
    return f"{engine_identity(engine_dir)} (TsAnalyzer CLI, ts-morph syntactic+semantic front end)"


# ── build-freshness gate — a HONEST identity string is not enough ──────────────────────────────
# 2026-09-03: fixing engine_identity() to hash all of dist/core/*.js (above) only makes a stale
# build DESCRIBABLE — a reader who compares two manifests by hand will notice the digest changed.
# It does nothing on its own: a run against an unbuilt dist/ still exits 0 and writes edges that
# look exactly as real as a fresh run's, because nothing here ever checked whether dist/ was built
# from the src/ sitting on disk right now. That is the same failure shape as the five prior
# "artifact older than the code" incidents (see gotcha-artifact-older-than-code-fails-silently.md
# in the product repo) and the same fix pattern the TS ORACLE already uses for its own silent
# no-op (the zero-wrap guard: don't describe the problem, refuse to run). So this checks build
# freshness BEFORE the engine is invoked, and refuses — raises, not a printed note — if dist/core
# does not look like it was built from the src/core currently on disk.
#
# The check: dist/core/*.ts's compiled counterpart is dist/core/*.js (1:1, see src/core's three
# files vs dist/core's three files) — the newest mtime among src/core/*.ts must not be newer than
# the OLDEST mtime among dist/core/*.js. If some source file was edited after the least-recently
# rebuilt dist file, `npm run build` was not re-run since that edit — the exact scenario that let
# 2026-09-03's emitCore.ts change ship unmeasured under the old main.js-only signature. Comparing
# to the oldest dist file (not the newest) matters: a bundler can touch every dist file's mtime on
# a build that only recompiled one of them, but it cannot make an untouched dist file's mtime run
# AHEAD of a source edit made after that build finished.
#
# This is deliberately mtime-based, not a stored source hash: TsAnalyzer's build has no existing
# step that records one, adding it would touch the product build (out of scope — "движок не
# трогать"), and mtime is exactly what `npm run build` already produces as a side effect of doing
# its job, so the gate costs nothing beyond the two directory listings it already needs for
# engine_identity().
def _stale_build_reason(engine_dir: Path) -> str | None:
    """None if dist/core looks built from src/core as it is right now; else why it does not."""
    src_dir = engine_dir / "src" / "core"
    core_dir = engine_dir / "dist" / "core"
    if not src_dir.is_dir():
        return None  # no src/ shipped with this checkout (e.g. a packaged dist-only build) — can't check, don't guess
    try:
        src_files = sorted(src_dir.glob("*.ts"))
        dist_files = sorted(core_dir.glob("*.js"))
    except OSError as exc:
        return f"could not list src/core or dist/core: {exc.strerror or exc}"
    if not src_files:
        return None
    if not dist_files:
        return f"dist/core has no .js files but src/core/*.ts exists — never built (run `npm run build` in {engine_dir})"
    try:
        # key=... by mtime explicitly — max()/min() over plain (Path, mtime) tuples compares the
        # Path FIRST (alphabetically) and only falls back to mtime on a tie, which silently picks
        # the wrong file whenever the alphabetically-last file isn't the most recently edited one.
        # Caught by actually running this against a real edit (below), not by reading the code.
        newest_src = max(((p, p.stat().st_mtime) for p in src_files), key=lambda t: t[1])
        oldest_dist = min(((p, p.stat().st_mtime) for p in dist_files), key=lambda t: t[1])
    except OSError as exc:
        return f"could not stat src/core or dist/core: {exc.strerror or exc}"
    src_path, src_mtime = newest_src
    dist_path, dist_mtime = oldest_dist
    if src_mtime > dist_mtime:
        src_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(src_mtime))
        dist_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(dist_mtime))
        return (f"dist/core is STALE relative to src/core: {src_path.name} was edited {src_str} "
                f"(newer than any run must trust) but {dist_path.name} was last built {dist_str} — "
                f"run `npm run build` in {engine_dir} before measuring. Refusing to run rather than "
                f"produce plausible-looking numbers from an old build (see run.py header).")
    return None


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
# CLOSURE FOLD FIX (2026-09-03 follow-up): TsAnalyzer now gives arrows/inline callbacks/named nested
# functions their own caller node (`finding-ts-arm-first-numbers-arrow-fold-2026-09-03.md` +
# `emitCore.ts`'s collectCallables/describeCallable/emitClosures, engine commit 2a2014e1). Their raw
# key's `name` segment is `{ownerMethod}/{name}` — `ownerMethod` is the ONE immediate enclosing
# callable, `name` is either the closure's real identifier (named nested function) or a positionally
# stable `<anon:L<line>>` / `<iife:L<line>>` tag (see emitCore.ts's describeCallable comment for why
# that beats a visit-order counter). Previously ANY '/' in `name` returned None here, silently
# dropping every closure edge before it could be counted — see this run's `.coverage.json` for how
# many that discarded (1179 unparseable-raw-key rows on the same class-validator run this fix was
# checked against, alongside 0.930/0.988 precision measured on a denominator quietly missing the
# hardest edges).
_COLLISION_TAG_RE = re.compile(r"@L\d+(?:#\d+)?$")


def _parse_raw_key(raw: str) -> tuple[str, str, str, str, str | None] | None:
    """`{filePath}::{OwnerFqn}::{"static:"?}{[ownerMethod/]name}({paramTypes})` ->
    (filePath, ownerFqn, name, params, closureOwnerOrNone).

    Splits on '::' at most into 3 pieces (file/owner never contain it; a parameter TYPE spelling
    could in principle, e.g. a namespace-qualified generic — none observed on class-validator, and
    `rsplit` on '::' would be wrong here since the SIGNATURE is the part that could contain more
    separators, not the owner). `name` containing exactly one '/' is TsAnalyzer's closure shape
    (`ownerMethod/name` — see comment above); it is split on the LAST '/' (the owner chain is only
    ever one hop deep by construction — collectCallables recurses with `chain = rec.name`, never a
    multi-hop path) and the closure-ness is signalled back to the caller via the 5th tuple element
    instead of being resolved here, because whether a closure key can be TRANSLATED (named — yes;
    anonymous/IIFE — no, see `translate()`) is a join-time decision, not a parse-time one.
    """
    parts = raw.split("::")
    if len(parts) < 3:
        return None
    file_path = parts[0]
    owner_fqn = parts[1]
    member_sig = "::".join(parts[2:])
    if member_sig.startswith("static:"):
        member_sig = member_sig[len("static:"):]
    # Key-collision disambiguator (TsAnalyzer putSidecar): when two DIFFERENT declarations build the
    # same base key, the loser is stored under `<baseKey>@L<line>` (plus `#<startOffset>` if even that
    # line is taken) instead of silently overwriting the winner. It is an IDENTITY tag, not part of the
    # name or the signature — the oracle spells the runtime name, and the line is read from the entry's
    # own `Line` field either way — so it is stripped here. Stripping it AFTER the `(`-partition would
    # be wrong: the tag sits past the closing paren, so it would otherwise land in `params` and break
    # the accessor param-count test below.
    member_sig = _COLLISION_TAG_RE.sub("", member_sig)
    name, paren, rest = member_sig.partition("(")
    if not paren:
        return None
    params = rest[:-1] if rest.endswith(")") else rest
    closure_owner = None
    if "/" in name:
        closure_owner, _, name = name.rpartition("/")
    return file_path, owner_fqn, name, params, closure_owner


# ── generic-arity stripping (join-key side only) ────────────────────────────────────────────────
# Engine commit 7cf2e092 (main repo, 2026-09-13) made a type declaration's own `Name`/`fqn` keep its
# type-parameter list VERBATIM, as written (`class Box<T>` -> Name="Box<T>"), matching the C# engine's
# long-standing convention (ArchitectureAnalyzer.Core/Roslyn/CallKeyBuilder.cs) of never guessing arity
# away at the engine boundary — a canon layer downstream is meant to derive arity, not the engine.
#
# The C# arm (arms/cheburnexus/run.py, backtick_arity()) converts that verbatim `<...>` suffix to IL
# arity notation (`Cache<T>` -> `` Cache`1 ``) because the C# ORACLE's keys are Cecil-derived IL
# FullNames, which DO spell arity that way. The TS oracle is different: it labels a class by
# `ts.Identifier.text` alone (oracle/typescript/transformer.js:381, `node.name.text` — a plain AST
# identifier node, which can never include a `<...>` type-parameter list; that list is a SEPARATE
# `typeParameters` property the oracle never reads for the label). So the TS oracle carries no generic
# marker of any kind, unlike C#'s backtick — the correct join-side transform here is not "convert
# arity notation" but "drop the suffix outright", the same fact stated a different way.
#
# Cutting at the FIRST '<' is correct here, not a naive shortcut: a type declaration's Name/fqn carry
# at most ONE type-parameter list, always as a trailing suffix (`Box<T>`, `Map<K,V>`,
# `Box<Map<K,V>>`, even `Box<T extends Base<Q>>`) — there is no second, unrelated '<' earlier in the
# string for a naive cut to mistake, so no bracket-depth counting is needed to find where the REAL
# suffix begins (contrast the C# arm's backtick_arity, which counts commas AT DEPTH ZERO because it
# must also compute an arity NUMBER from what is inside the brackets — this function only ever
# discards what is inside, so nesting inside the brackets cannot affect the result either way).
# A name with no suffix at all is returned unchanged (`.find` returns -1, slice is the whole string).
#
# NOT applied to BaseTypes/Interfaces: this arm never reads those fields (it builds only the calls
# axis, unlike the C# arm's implements axis) — see this file's header, "the only sidecars this arm
# actually reads: architecture.calls.json ... architecture.methods.json". `fqn` itself is also left
# alone everywhere else it appears in this file (TypeIndex.kind_name's key, `owner_fqn` parsed from a
# raw key) because those are engine-output-to-engine-output lookups — both sides come from the SAME
# helper the fixing commit introduced (declaredTypeName/typeParamsSuffix), so they still match each
# other exactly and never touch the oracle's arity-free spelling. Only `simple_name`, read out of
# `TypeIndex.kind_name` to build the oracle-facing `qualified` join key below, crosses that boundary.
def strip_type_param_suffix(name: str) -> str:
    """A type declaration's own Name/fqn, with its verbatim type-parameter suffix removed for the
    oracle join (`Box<T>` -> `Box`, `Map<K,V>` -> `Map`, `Box<Map<K,V>>` -> `Box`, `Plain` -> `Plain`).
    """
    idx = name.find("<")
    return name if idx == -1 else name[:idx]


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
    "anonymous-closure-tag-line-mismatch",
)

# UPDATE (2026-09-03, second follow-up): the oracle's shadow-stack transformer USED TO label an
# anonymous closure with a per-file VISIT-ORDER counter (`anon${counter}`/`anonFnExpr${counter}`/
# `arrow${counter}`, transformer.js:308/354/369/389) — not deterministically reconstructible from
# TsAnalyzer's output, so this arm used to count every such closure as untranslatable
# ("anonymous-closure-unspellable"). The oracle's `anonLabel()` now labels anonymous closures the
# SAME way TsAnalyzer's `describeCallable` always did — positionally, by the callable's own start
# line (`anonL<line>`, same-line collisions disambiguated by column, see that function's comment) —
# so the two ends finally agree and this arm can build an EXACT join key instead of refusing to try.
# `_ANON_LABEL_RE` recognizes TsAnalyzer's raw tag (`<anon:L<line>>`/`<iife:L<line>>`) and
# `_anon_join_name` converts it to the oracle's bracket-free spelling — deliberately BRACKET-FREE,
# not because TsAnalyzer's own `<...>` spelling is wrong (it is fine as an internal identifier), but
# because grade.py's `normalize_caller` drops ANY caller key containing '<' as compiler-generated
# noise (a rule built for C#'s `<>c__DisplayClass` mangling). Measured: translating straight to
# `<anon:L<line>>` made recall jump 0.095 -> 0.397 with `matched` UNCHANGED at 240 — the entire
# "gain" was ~1900 oracle primary-cell rows silently vanishing into "caller not remappable", a
# shrunk denominator, not a real match. This bracket-free spelling avoids that trap entirely.
_ANON_LABEL_RE = re.compile(r"^<(anon|iife):L(\d+)>$")


def _anon_join_name(engine_name: str) -> str | None:
    """TsAnalyzer's raw anonymous-closure tag -> the oracle's positionally-stable spelling for the
    SAME node, or None if `engine_name` is not this shape (i.e. a NAMED closure, translated
    unchanged by its caller). The iife/anon distinction is TsAnalyzer-internal bookkeeping the
    oracle's transformer never makes at all (every anonymous shape gets the identical `anonL<line>`
    scheme) — collapsed here on purpose: it was never observable from the oracle side to begin with,
    so dropping it loses no fact either side could have joined on.
    """
    m = _ANON_LABEL_RE.match(engine_name)
    return f"anonL{m.group(2)}" if m else None


class Translator:
    """Raw TsAnalyzer key -> oracle-shape key, or a drop reason. Every drop is counted — see this
    file's header, "None of the above is fudged into the join"."""

    def __init__(self, methods: dict, types: TypeIndex, repo_root: Path) -> None:
        self.methods = methods
        self.types = types
        self.repo_root = repo_root
        self.drops: dict[str, int] = {r: 0 for r in _DROP_REASONS}
        self.ok = 0
        # Same-line-collision guard for EVERY closure shape (named or anonymous): translated key ->
        # the distinct raw keys that produced it. Two closures starting on the same source line is
        # rare but real (e.g. two named nested functions, or two arrows the oracle's OWN column
        # disambiguation resolves but this arm's join key does not carry a column at all — see
        # `_anon_join_name`), so this is a fact to CHECK, never assume. A collision is never
        # fuzz-matched to "pick one"; every raw key behind a colliding translated key is dropped and
        # counted so the edge never silently attributes to the wrong closure.
        self._closure_key_sources: dict[str, set[str]] = {}
        self.closure_collision_keys: set[str] = set()

    def _drop(self, reason: str) -> None:
        self.drops[reason] = self.drops.get(reason, 0) + 1

    def translate(self, raw_key: str) -> str | None:
        parsed = _parse_raw_key(raw_key)
        if parsed is None:
            self._drop("unparseable-raw-key")
            return None
        file_path, owner_fqn, name, params, closure_owner = parsed

        # Closure shape (`ownerMethod/name`, closure_owner set by _parse_raw_key): a NAMED nested
        # function/function-expression carries a real identifier and joins exactly like any other
        # member below (the oracle's labelFor never qualifies by enclosing function, only by class,
        # so `name` alone — the owner-function prefix already stripped — is the right thing to
        # qualify with `simple_name`). An ANONYMOUS/IIFE closure's `name` is TsAnalyzer's raw
        # `<anon:L..>`/`<iife:L..>` tag — rewritten to the oracle's `anonL<line>` spelling by
        # `_anon_join_name` (see that function's comment) so it falls through the SAME machinery
        # below as every other member, unchanged from here on.
        entry = self.methods.get(raw_key)
        if entry is None or entry.get("Line") is None:
            self._drop("no-methods-sidecar-line")
            return None
        line = entry["Line"]

        # NOT gated on `closure_owner`: an anonymous callable declared at MODULE level (or anywhere
        # else _parse_raw_key sees no owning method) carries the same `<anon:L..>`/`<iife:L..>` tag
        # and needs the same rewrite. Gating this on closure_owner was a real, measured defect —
        # class-validator never exposed it (its anonymous arrows all sit inside a function), zod did:
        # 3481 of 5989 arm edges left the arm still spelled `<anon:L37>`, and grade.py's C#-inherited
        # "a `<` in the key means compiler-generated garbage" rule then dropped every one of them as
        # unattributable. Third time that C# `<` rule has silently eaten a real TypeScript fact.
        anon_name = _anon_join_name(name)
        if anon_name is not None:
            # Line-contract self-check: the line embedded in TsAnalyzer's own tag must equal the
            # line `architecture.methods.json` recorded for this exact raw key — both are
            # ts-morph's `getStartLineNumber()` on the same node, so they MUST agree; if they ever
            # don't, something upstream is inconsistent and must be surfaced, not papered over by
            # silently trusting one of the two numbers.
            tag_line = int(_ANON_LABEL_RE.match(name).group(2))
            if tag_line != line:
                self._drop("anonymous-closure-tag-line-mismatch")
                return None
            name = anon_name

        info = self.types.kind_name.get(owner_fqn)
        if info is None:
            self._drop("no-owner-fqn-in-architecture-json")
            return None
        kind, simple_name = info
        simple_name = strip_type_param_suffix(simple_name)
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
        key = f"{rel}::{qualified}:{line}{suffix}"

        if closure_owner is not None:
            sources = self._closure_key_sources.setdefault(key, set())
            sources.add(raw_key)
            if len(sources) > 1:
                self.closure_collision_keys.add(key)

        self.ok += 1
        return key


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
    stale = _stale_build_reason(engine_dir)
    if stale is not None:
        raise RuntimeError(f"cheburnexus-ts: refusing to measure with a stale build — {stale}")

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

    # Same-line-collision guard (see Translator.__init__): drop, never fuzz-pick, any edge whose
    # caller or callee is a translated key that TWO OR MORE distinct named closures produced.
    collision_dropped = 0
    if translator.closure_collision_keys:
        kept = []
        for e in edges:
            if e.caller in translator.closure_collision_keys or e.callee in translator.closure_collision_keys:
                collision_dropped += 1
                continue
            kept.append(e)
        edges = kept

    total_calls = sum(len(e.get("Calls") or []) for e in calls_data.values())

    # Scope: a TS corpus entry declares its surface as `typescript_project_dir` — the same
    # directory runner/run.py hands the harness as the oracle's `--root`, so it is exactly the
    # slice the answer key can contain (armkit.scope_dirs_from_entry reads it). An entry without
    # that field (class-validator: whole repo IS the corpus) stays unscoped, as before, and
    # armkit's corpus_scope_dirs() prints its one stderr note. Nothing here invents a scope
    # corpus.json never declared. is_test_path (TEST_DIR_MARKERS) still applies for the
    # without-tests cell.
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
    collision_note = (f"; {len(translator.closure_collision_keys)} same-line named-closure key "
                       f"collision(s) found (2+ distinct closures translating to the identical key), "
                       f"{collision_dropped} edge(s) touching a colliding key dropped rather than "
                       f"guessed"
                       if translator.closure_collision_keys else
                       "; 0 same-line named-closure key collisions found")
    note = (f"TsAnalyzer resolved {total_calls} call sites; {translator.ok} key(s) translated "
            f"successfully, {dropped_calls} raw key lookups failed translation ({breakdown or 'none'})"
            f"{collision_note} "
            f"-- see this arm's run.py header for what each reason means. Named AND anonymous/IIFE "
            f"closures all get their own call-graph node now (engine commit 2a2014e1) and all "
            f"translate: named ones like any other member, anonymous ones via _anon_join_name against "
            f"the oracle's own positionally-stable anonLabel() (oracle/typescript/transformer.js, "
            f"2026-09-03 follow-up) -- residual drops are almost entirely no-methods-sidecar-line, "
            f"not a structural closure-identity gap anymore.")
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
