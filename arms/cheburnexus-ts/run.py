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
    name, paren, rest = member_sig.partition("(")
    if not paren:
        return None
    params = rest[:-1] if rest.endswith(")") else rest
    closure_owner = None
    if "/" in name:
        closure_owner, _, name = name.rpartition("/")
    return file_path, owner_fqn, name, params, closure_owner


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
