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

import hashlib
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

# ── which binary runs is NEVER guessed ──────────────────────────────────────────────────────────
# There used to be a hardcoded fallback here — this machine's packaged
# dist-all/cheburnexus-all-1.7.0-osx-x64/arch-computer.exe — used whenever $CHEBURNEXUS_ENGINE was
# unset. On 2026-08-29 that fallback silently spent a full four-cell run: the packaged binary was
# built 2026-08-09, three days BEFORE the passport signing key was rotated in the product repo, so
# it embeds the superseded public key, cannot verify a passport issued after the rotation, and
# fails closed to Free. Every cell came back BLOCKED with the call graph withheld, which reads
# exactly like "this machine has no licence" — while the passport on disk was healthy the whole
# time. Nothing in the run said which binary had been chosen or how old it was.
#
# A default that resolves to *some* binary is the whole defect: the arm cannot know whether the
# binary it picked is the one the experiment means, and a wrong-but-plausible engine produces
# wrong-but-plausible numbers. So there is no default. The engine is named explicitly or the arm
# refuses to run — the same rule the PRODUCT engine follows about facts it cannot establish
# (an exact fact or silence, never a guess). Cost of the rule: one env var per run. Cost of not
# having it, measured: one full run, published-looking and wrong.


def find_engine() -> Path | None:
    """The engine binary named by $CHEBURNEXUS_ENGINE, or None. There is deliberately no fallback."""
    env = os.environ.get(ENGINE_ENV)
    if not env:
        return None
    p = Path(env)
    if p.is_file():
        return p
    print(f"cheburnexus: ${ENGINE_ENV}={env!r} does not exist", file=sys.stderr)
    return None


def engine_missing_message() -> str:
    return (
        f"cheburnexus engine binary not found — set ${ENGINE_ENV} to the arch-computer.exe "
        f"(ArchitectureAnalyzer.CLI) of the build this experiment means. There is no default on "
        f"purpose: a silently-chosen binary can be older than the product change under test, or "
        f"older than the passport signing key, and either way it yields a plausible wrong number. "
        f"This arm is live only on a machine that has the product built; see arms/ARCHITECTURE.md's "
        f"live/replay split and NOTES.md."
    )


# The engine's analysis code lives in this assembly beside the launcher; the launcher itself is a
# generic apphost that two different builds can share byte for byte (observed: the e12 and e13 HEAD
# publishes had identical arch-computer.exe and different contents). Hashing the launcher would
# therefore certify two different engines as the same one.
_ENGINE_CODE_ASSEMBLY = "ArchitectureAnalyzer.Core.dll"


_IDENTITY_CACHE: dict[tuple[str, int, int], str] = {}


def engine_identity(engine: Path) -> str:
    """What binary actually ran, in terms that cannot go stale without saying so out loud.

    The old answer parsed a version number out of the distributable's FOLDER NAME — a label a build
    writes once and never revises. Both 2026-08-29 runs recorded one: the run that worked said
    "unknown" (its folder did not match the packaging regex) and the run that silently degraded to
    Free on a 20-day-old binary said "1.7.0". Both strings sat in the manifests and neither told the
    reader anything, least of all which one was old. A build date and a content hash cannot fail
    that way: a stale engine now states its own age in every result this arm produces, and two
    builds that differ are never described identically.

    Reading the file can fail — a locked or unreadable assembly, or a file that disappears between
    the `is_file()` test and the read. Provenance is not worth crashing a run over, and a crash here
    would leave the runner calling the cell "could not run here" instead of naming a cause, so an
    I/O failure is reported AS the identity: an unreadable engine is itself a fact worth publishing.
    """
    # Resolve first: a symlinked engine must be identified by the assembly beside its TARGET, not
    # beside the link, or a shared launcher would stand in for a real build's analysis assembly.
    real = engine.resolve()
    code = real.parent / _ENGINE_CODE_ASSEMBLY
    stamped = code if code.is_file() else real
    try:
        st = stamped.stat()
    except OSError as exc:
        return f"{stamped.name} UNREADABLE ({exc.strerror or exc}) at {engine}"
    # Keyed on identity-relevant stat fields, not just the path: a rebuilt binary at the same path
    # must be re-hashed, while the same binary is hashed once per run however many projects ask.
    # A self-contained single-file publish is ~42 MB and this is called once per withheld project.
    key = (str(stamped), st.st_mtime_ns, st.st_size)
    cached = _IDENTITY_CACHE.get(key)
    if cached is not None:
        return cached
    built = time.strftime("%Y-%m-%d", time.gmtime(st.st_mtime))
    try:
        digest = hashlib.sha256(stamped.read_bytes()).hexdigest()[:12]
    except OSError as exc:
        return f"{stamped.name} built {built} UNREADABLE ({exc.strerror or exc}) at {engine}"
    identity = f"{stamped.name} built {built} sha256:{digest} at {engine}"
    _IDENTITY_CACHE[key] = identity
    return identity


def version() -> str:
    """The tool's own version — cheap and fast, since --describe is called on every run. Read from
    the binary on disk rather than from a live analysis (which would be the only way to read the
    assembly-level EngineVersion field, and far too slow for a --describe call). See NOTES.md: the
    assembly-level EngineVersion observed in architecture.json output was "1.1.0" at time of
    writing, and no git sha is exposed anywhere in the CLI's own output for us to surface — which
    is exactly why identity is reported as build date + content hash instead of a version string.
    """
    engine = find_engine()
    if engine is None:
        return f"unavailable (engine binary not found — set ${ENGINE_ENV})"
    return f"{engine_identity(engine)} (ArchitectureAnalyzer.CLI --solution, Roslyn semantic front end)"


def withheld_graph_reason(engine: Path) -> str:
    """Why a project that exited 0 produced only the aggregate counts sidecar and no per-edge one.

    States the observation and both known causes, and asserts NEITHER. The previous wording said the
    withholding was "consistent with the documented entitlement wall (analyzer.semantic is a Pro
    feature and no valid passport is present on this machine)". That sentence names a fact about the
    machine that this arm cannot check and, on 2026-08-29, was simply false: a valid unexpired
    passport was sitting at the canonical path throughout, and the real cause was the engine binary
    being older than the passport signing key. Reading the message cost real time pointed at the
    wrong half of the system. An arm may report what it saw; it may not report why, when it does not
    know why — and naming the binary is what lets the reader settle it in one look.
    """
    return (
        "the engine analyzed this project (exit 0) but emitted only "
        "architecture.calls-counts.json (aggregate counts, no caller identity) — no "
        "architecture.calls.json. That is the shape the engine produces when it resolves this "
        "machine to the Free tier (the semantic call graph is Pro-gated — see NOTES.md); the "
        "engine's own output does not state a reason, so this arm does not claim one. Two causes "
        "produce it and only inspection can tell them apart: no valid passport on this machine, OR "
        "an engine binary older than the passport signing key it must verify against, which fails "
        f"closed to Free with a perfectly healthy passport on disk. Engine used: {engine_identity(engine)}."
    )

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



def _split_namespace(fqn: str) -> tuple[str, str]:
    """Split "Ns.Outer/Inner" into ("Ns", "Outer/Inner").

    The namespace boundary is the last dot BEFORE the nesting chain starts — a dot inside the chain
    would be part of a generic argument (`Box<A.B>/Entry`), never a namespace separator, which is the
    whole reason the engine spells nesting with a slash.
    """
    head = fqn.split("/", 1)[0]
    if "." not in head:
        return "", fqn
    namespace = head.rsplit(".", 1)[0]
    return namespace, fqn[len(namespace) + 1:]


def nested_type_path(type_chain: str) -> str:
    r"""Arity-convert every link of a nesting chain: `Box<T>/Entry` -> `` Box`1/Entry ``.

    Per SEGMENT, because each link carries its own type parameters and the oracle spells arity on
    every one of them (`HedgingExecutionContext\`1/ExecutionInfo\`1`). Running backtick_arity over the
    whole chain would see one trailing `<...>` and rewrite only the last link.
    """
    return "/".join(backtick_arity(seg) for seg in type_chain.split("/"))

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
                type_namespace, type_chain = _split_namespace(fqn)
            else:
                full_key = f"{namespace}.{name}" if namespace else name
                type_namespace = namespace
                type_chain = name
            # NOT backtick_arity(name): `name` is a nested type's OWN simple name, and using it drops
            # the enclosing type. Since 2026-08-29 the engine emits nested types (it never did before,
            # which is why this went unnoticed), and keying `ResilienceContextPool/SharedPool` as
            # `Polly.SharedPool` produced 21 junk edges naming a type that does not exist — the second
            # end of the same key contract the engine change fixed at the first.
            type_path = nested_type_path(type_chain)
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
            namespace, simple = _split_namespace(dotted_type)
            type_path = nested_type_path(simple)
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


# ── conversion-operator caller translation ──────────────────────────────────────────────────────
# A user-defined conversion (`implicit operator T` / `explicit operator T`) is keyed by the
# engine's CallKeyBuilder.ConversionOperatorKey with the SOURCE spelling — the implicit/explicit
# keyword and the target type are part of the identity there, deliberately, because two
# conversions to different types on the same class are two distinct members and must not collapse
# onto one key. That is the right key for the engine's OWN space, but it is not the name IL gives
# the member: the CLR names every user-defined conversion `op_Implicit` or `op_Explicit`,
# regardless of its target type — overloads are told apart by signature in metadata, the same way
# `CallKeyBuilder.MethodSignature`'s own comment already argues for generic method arity. Measured
# on Polly/without-tests: 6 primary-cell oracle rows have a caller of exactly this shape, all
# `op_Implicit`, and the untranslated arm missed every one of them, both as precision (6 junk
# edges) and as recall (the same 6 oracle rows unmatched) — see
# PREREGISTRATION-e13-conversion-operator-spelling-2026-08-29.md. Same class of defect as
# `_accessor_caller_method` above, one level down: DECISION-accessor-caller-spelling-2026-08-28.md's
# argument applies here without change.
#
# The checked entry is listed FIRST and matched by trying each keyword in order: "explicit operator
# checked " is a superstring of "explicit operator ", so if the plain entry were tried first it would
# match the checked spelling too (it IS a prefix of it) and truncate `... checked int` down to just
# `op_Explicit`, silently dropping the fact that the conversion was checked and misnaming the member
# (IL calls it `op_CheckedExplicit`, not `op_Explicit` — a genuinely different member, the same
# reason CallKeyBuilder.OperatorKey/ConversionOperatorKey key checked and unchecked operators apart).
# There is no checked-implicit entry: the C# compiler rejects `implicit operator checked T` outright
# (CS9024, verified by compiling the probe) — an implicit conversion can never overflow, so only
# explicit conversions can be checked.
_CONVERSION_KEYWORDS = (
    ("explicit operator checked ", "op_CheckedExplicit"),
    ("implicit operator ", "op_Implicit"),
    ("explicit operator ", "op_Explicit"),
)


def _conversion_operator_caller_method(method_name: str) -> str:
    """Rewrite a conversion-operator caller's method-name segment to the name IL actually gives it.

    Finds the keyword rather than splitting on the last '.', unlike `_accessor_caller_method`: a
    property/event identifier never contains a dot, but a conversion's target TYPE can
    (`implicit operator System.Threading.Tasks.Task`), and splitting there would cut the qualifier
    in the wrong place. Everything before the keyword — nothing, or an explicit-interface
    qualifier plus its dot — is a fact from the source and is kept untouched; everything from the
    keyword to the end names only the kind of conversion, which IL never varies by target type, so
    it is replaced whole.
    """
    for keyword, il_name in _CONVERSION_KEYWORDS:
        pos = method_name.find(keyword)
        if pos != -1:
            return method_name[:pos] + il_name
    return method_name



# ── operator spelling ───────────────────────────────────────────────────────────────────────────
# The engine keys an operator by its C# token (`operator==`), deliberately — see
# CallKeyBuilder.OperatorKey, whose comment argues that the token is what tells `+` from `-` and no
# real member name can begin with "operator". The CLR names the same member `op_Equality`. Same
# situation as the conversion operators e13 translated, one step along: the engine's spelling is
# right for the engine's own key space and is not what the answer key calls the member.
#
# BOTH ends, unlike the conversion translation, which measured zero conversion-operator CALLEES in
# all four corpora. A record's synthesized `operator!=` calls its `operator==`, so this shape appears
# as caller AND as callee in the same edge — reading only one end would have converted a miss into a
# junk edge instead of a match.
#
# Every key here must be the literal `operator` + `OperatorToken.Text` the engine actually emits —
# checked, not assumed. `operator true`/`operator false` used to carry a SPACE, copied from how the
# member reads in C# source; compiling a probe and reading `OperatorToken.Text` back shows Roslyn
# gives the `true`/`false` operator the same bare-token spelling as every other operator
# (`"true"`/`"false"`, no leading space), so the engine key is `operatortrue`/`operatorfalse` and the
# space-bearing entries never matched anything — `op_True`/`op_False` were dead code, silently
# leaving every `operator true`/`operator false` callee's method name untranslated (junk on the
# callee end, since IL never spells it that way) and, symmetrically, every such CALLER unmatched
# against the oracle (a miss) — same failure shape `_conversion_operator_caller_method`'s own comment
# already measured for conversion operators, one operator family over.
#
# `op_UnsignedRightShift` (`>>>`, C# 11) was simply missing — verified the same way: a probe with
# `operator >>>` parses to `OperatorToken.Text == ">>>"`, so the untranslated key `operator>>>` fell
# through `_il_operator_method`'s `None` branch unchanged, same dead-entry effect as the space bug
# above, just via absence instead of a wrong spelling.
#
# `operatorchecked` + token (no space, no keyword-first "checked "): CallKeyBuilder.OperatorKey folds
# C# 11's checked-operator keyword in exactly this position — see that method's own comment — so a
# checked operator's engine key is `operatorchecked+`, not `operator checked+` or `operator+
# checked`. Checked-ness is only ever legal on the operators the CLR itself names a checked overload
# for (+, -, *, /, unary -, ++, --); the rest of the table has no `operatorchecked...` counterpart on
# purpose, not by omission — there is no `op_Checked...` name for `==` or `<<` because C# does not
# allow `operator checked ==`.
_IL_OPERATOR_NAMES = {
    "operator==": "op_Equality",   "operator!=": "op_Inequality",
    "operator<":  "op_LessThan",   "operator>":  "op_GreaterThan",
    "operator<=": "op_LessThanOrEqual", "operator>=": "op_GreaterThanOrEqual",
    "operator+":  "op_Addition",   "operator-":  "op_Subtraction",
    "operator*":  "op_Multiply",   "operator/":  "op_Division",
    "operator%":  "op_Modulus",    "operator&":  "op_BitwiseAnd",
    "operator|":  "op_BitwiseOr",  "operator^":  "op_ExclusiveOr",
    "operator<<": "op_LeftShift",  "operator>>": "op_RightShift",
    "operator>>>": "op_UnsignedRightShift",
    "operator!":  "op_LogicalNot", "operator~":  "op_OnesComplement",
    "operator++": "op_Increment",  "operator--": "op_Decrement",
    "operatortrue": "op_True",     "operatorfalse": "op_False",
    "operatorchecked+":  "op_CheckedAddition",       "operatorchecked-":  "op_CheckedSubtraction",
    "operatorchecked*":  "op_CheckedMultiply",        "operatorchecked/":  "op_CheckedDivision",
    "operatorchecked++": "op_CheckedIncrement",       "operatorchecked--": "op_CheckedDecrement",
}


# The three tokens C# spells the SAME for one operand and for two. `-x` and `a - b` are different
# members with different IL names, and the operator token alone cannot tell them apart — only the
# parameter count can. Nothing else in the table is ambiguous: `*`, `/`, `%`, the comparisons and the
# shifts are binary-only, `!`, `~`, `++`, `--`, `true`, `false` are unary-only, and each already has
# exactly one IL name. There is no `op_CheckedUnaryPlus`, because C# does not allow `operator
# checked +` on one operand.
_IL_UNARY_OPERATOR_NAMES = {
    "operator+": "op_UnaryPlus",
    "operator-": "op_UnaryNegation",
    "operatorchecked-": "op_CheckedUnaryNegation",
}


def _key_arity(raw_key: str) -> int | None:
    """Parameter count of an engine key's signature, or None when the key carries no signature.

    Counted at DEPTH ZERO only: a signature holds full type spellings, so `Box<A,B>` and `int[,]`
    each contain a comma that belongs to a type, not to the parameter list. Splitting naively would
    report `operator +(Box<A,B> x)` as binary and hand it the wrong IL name.
    """
    _, sep, sig = raw_key.partition("(")
    if not sep:
        return None
    sig = sig.rpartition(")")[0]
    if not sig.strip():
        return 0
    depth = 0
    count = 1
    for ch in sig:
        if ch in "<[(":
            depth += 1
        elif ch in ">])":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


def _il_operator_method(method_name: str, arity: int | None = None) -> str:
    """Rewrite an operator's method-name segment to the name IL gives it.

    Split at the last '.' the way `_accessor_caller_method` does, so an explicit-interface qualifier
    (`IAdd<W>.operator+` — legal since C# 11) keeps its prefix. A qualifier can itself contain dots,
    but the operator token never can, so the last dot is always the boundary.

    `arity` is the declared parameter count, and it is what separates `operator -(A a)` from
    `operator -(A a, A b)`: the same source token, two different IL names. Passing None keeps the
    binary reading — the only safe default, since every other entry in the table is unambiguous.
    """
    head, sep, tail = method_name.rpartition(".")
    token = tail if sep else method_name
    il = None
    if arity == 1:
        il = _IL_UNARY_OPERATOR_NAMES.get(token)
    if il is None:
        il = _IL_OPERATOR_NAMES.get(token)
    if il is None:
        return method_name
    return head + sep + il

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
            callee_key = index.contract_key(
                callee_type, _il_operator_method(callee_method, _key_arity(target)))

            # Accessor translation happens per CALL, not once per raw_caller entry: a property's
            # getter and setter share this SAME entry (one member, per CallKeyBuilder.MemberKey),
            # so only each call's own Accessor tag says which body it came from. Conversion-operator
            # translation runs AFTER metadata and BEFORE accessor: an explicit-interface conversion's
            # MetadataName already arrives correctly spelled (`FluentValidation.IFoo.op_Implicit`,
            # the compiler's own answer) and so never matches `_CONVERSION_KEYWORDS` — only a
            # non-explicit-interface conversion's untouched source spelling does — and a conversion
            # operator body is never itself an accessor, so `_accessor_caller_method` is a no-op on
            # its output either way.
            effective_caller_method = _accessor_caller_method(
                _il_operator_method(
                    _conversion_operator_caller_method(_metadata_caller_method(caller_method, entry)),
                    _key_arity(raw_caller),
                ),
                call.get("Accessor"),
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


# ── combined-solution mode ───────────────────────────────────────────────────────────────────────
# ESTABLISHED CAUSE (2026-08-30): the per-project loop above runs the engine on ONE .csproj at a
# time, so a sibling in-scope <ProjectReference> enters that run only as a compiled DLL — the
# engine's declaration index is built from parsed SOURCE only, so every call into a sibling
# first-party project is counted DroppedExternal ("lives in another assembly") about a member that
# IS in the corpus. Measured ceiling on serilog/with-tests: 1100 raw test->src edges recovered when
# the same engine binary is pointed at Serilog.sln instead of at one project.
#
# The fix is not to teach the conversion logic about sibling projects — it is to stop asking the
# engine about one project at a time. A synthetic solution listing every in-scope candidate together
# lets Roslyn resolve a ProjectReference to the sibling's own SOURCE, the same way `dotnet build`
# would. The solution file is written into THIS arm's own scratch dir, never into the corpus
# checkout — `armkit.in_scope` already computed exactly which candidates belong in this cell, and
# this mode must see precisely that list, not one project more or fewer (see
# `_write_synthetic_solution` and its test below).
DOTNET_ENV = "CHEBURNEXUS_DOTNET"


def _dotnet_bin() -> str:
    """Which `dotnet` builds the synthetic solution — overridable so a test can hand this a fake
    shim instead of needing the real SDK's `dotnet new`/`dotnet sln add` to be hermetic. Unlike the
    engine binary lookup above, an unpinned `dotnet` is not a correctness risk: this arm's own
    numbers do not depend on which SDK patch assembled a project list, only on that list being
    exactly right — which is what the tests below check, against a fake shim, not the real tool."""
    return os.environ.get(DOTNET_ENV) or "dotnet"


def _combined_out_dir(scratch: Path) -> Path:
    return scratch / "_combined"


def _run_new_sln(
    dotnet: str, out_dir: Path, sln_name: str, extra: list[str]
) -> tuple[subprocess.CompletedProcess | None, str | None]:
    """One `dotnet new sln` invocation. Returns (proc, None) when the process ran (whatever its exit
    code), or (None, reason) when it could not even start — the caller inspects a returned proc's own
    exit code, but a failure to launch dotnet at all is terminal for this whole path."""
    try:
        proc = subprocess.run(
            [dotnet, "new", "sln", "-n", sln_name, *extra],
            cwd=str(out_dir), capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{dotnet} new sln failed to start: {exc}"
    return proc, None


def _format_flag_unrecognized(proc: subprocess.CompletedProcess) -> bool:
    """Did `dotnet new sln --format sln` fail specifically because THIS SDK does not know `--format`?

    `--format` landed in the SDK 9 templating engine; an older SDK (8/7/3.1, the pins CORPUS_NOTES.md
    documents) rejects it as an unrecognised argument while still defaulting to the classic `.sln`, so
    that single case — and only that case — is worth retrying without the flag. Any OTHER failure is a
    real one and must surface, not be masked by a second attempt. Matched on the CLI's own wording
    ("Unrecognized command or argument '--format'"), case-folded and tolerant of the phrasing varying
    across SDK versions."""
    text = f"{proc.stdout}\n{proc.stderr}".lower()
    if "format" not in text:
        return False
    return any(w in text for w in ("unrecognized", "unrecognised", "unknown", "invalid", "unexpected"))


def _write_synthetic_solution(
    out_dir: Path, sln_name: str, csprojs: list[Path]
) -> tuple[Path | None, str]:
    """`dotnet new sln` + `dotnet sln add`, listing EXACTLY `csprojs` — sorted, so the file (and any
    log naming it) is the same on a re-run. Returns (path, "") on success, or (None, reason) when
    either step fails; the caller decides whether that means falling back to the per-project path.
    `out_dir` is always inside this arm's own WORK_ROOT (enforced by `_reset_work_dir` on the
    caller's side) — this function never writes into the corpus checkout.
    """
    dotnet = _dotnet_bin()
    sln_path = out_dir / f"{sln_name}.sln"

    # SDK 9+ `dotnet new sln` defaults to the NEW `.slnx` XML solution format. This arm looks up
    # `<name>.sln` (just below) and hands the file to the engine's `--solution` parser, whose ability
    # to read `.slnx` is unknown — so a `.slnx` here silently fails the `.sln` lookup and drops the
    # whole cell to the per-project fallback (the e18 regression, measured as Polly recall
    # 0.8734 → 0.8193). Force the classic format with `--format sln`. That flag only exists on SDK 9+;
    # an older SDK rejects it as an unknown argument but already defaults to `.sln`, so when the flag
    # itself is what it choked on, retry once without it.
    new_proc, start_err = _run_new_sln(dotnet, out_dir, sln_name, ["--format", "sln"])
    if start_err is not None:
        return None, start_err
    if (new_proc.returncode != 0 or not sln_path.is_file()) and _format_flag_unrecognized(new_proc):
        new_proc, start_err = _run_new_sln(dotnet, out_dir, sln_name, [])
        if start_err is not None:
            return None, start_err

    if new_proc.returncode != 0 or not sln_path.is_file():
        tail = (new_proc.stderr or new_proc.stdout).strip()
        return None, f"{dotnet} new sln failed: exit {new_proc.returncode}: {tail[-500:]}"

    try:
        add_proc = subprocess.run(
            [dotnet, "sln", str(sln_path), "add", *[str(c) for c in sorted(csprojs)]],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{dotnet} sln add failed to start: {exc}"
    if add_proc.returncode != 0:
        tail = (add_proc.stderr or add_proc.stdout).strip()
        return None, f"{dotnet} sln add failed: exit {add_proc.returncode}: {tail[-500:]}"

    return sln_path, ""


def _run_combined(
    engine: Path, repo_root: Path, cell: str, candidates: list[Path], scratch: Path
) -> dict:
    """One engine invocation over every in-scope candidate at once. Never raises: a synthetic-
    solution failure or an engine refusal both come back as `{"ok": False, "reason": ...}` so
    `collect()` can fall back to the per-project path instead of dying.

    `--project <repo_root>` alongside `--solution` is the engine's own documented monorepo-scoping
    combination (ArchitectureAnalyzer.CLI/Program.cs: "combine with --project to scope a monorepo to
    one subtree") — without it the analysis root defaults to the SOLUTION FILE's own directory,
    which is empty (the .sln lives in our scratch dir, never in the checkout), and the engine
    refuses with "No in-scope C# sources found" before it ever reads a candidate. Pinning
    `--project` to `repo_root` also makes every `Files[].Path` in the resulting architecture.json
    repo-relative — the anchor `_combined_project_coverage` below relies on.
    """
    out_dir = _combined_out_dir(scratch)
    _reset_work_dir(out_dir)  # defect #9 applies here too: never read a previous run's leftovers
    sln_name = f"{repo_root.name}-{cell}"
    sln_path, reason = _write_synthetic_solution(out_dir, sln_name, candidates)
    if sln_path is None:
        return {"ok": False, "reason": reason, "sln_path": None}

    result = _invoke(engine, ["--solution", str(sln_path), "--project", str(repo_root)],
                      out_dir, "combined-solution")
    result["sln_path"] = sln_path
    return result


def _combined_project_coverage(
    files: list[dict], candidates: list[Path], repo_root: Path
) -> tuple[list[str], list[tuple[str, str]]]:
    """Which in-scope candidates contributed at least one file to a combined run's
    architecture.json — the same productive/unproductive split the old per-project loop got for
    free from running the engine once per project and reading each invocation's own exit code. One
    combined invocation's exit code can only say "the whole cell" succeeded or not; a project whose
    own sources produced nothing (still possible — a project entirely under `#if` guards for this
    TFM, say) must not be allowed to hide inside an overall-successful run, or a partial result
    would be graded as a complete one again — the exact defect #9 masking bug the per-project loop
    was built to catch.

    Matched by path COMPONENT prefix, like `armkit.in_declared_scope` — a string-prefix test would
    wrongly credit `src/Polly` for files that are actually under a sibling `src/Polly.Core`.
    """
    contributed_dirs = {
        tuple(Path(f.get("Path", "").replace("\\", "/")).parts[:-1]) for f in files
    }
    productive: list[str] = []
    unproductive: list[tuple[str, str]] = []
    for csproj in sorted(candidates):
        rel = str(csproj.relative_to(repo_root))
        project_parts = csproj.parent.relative_to(repo_root).parts
        hit = any(dirparts[: len(project_parts)] == project_parts for dirparts in contributed_dirs)
        if hit:
            productive.append(rel)
        else:
            unproductive.append((
                rel,
                "combined solution analysis contributed no files under this project's directory",
            ))
    return productive, unproductive


def _edges_from_combined(
    combined: dict, repo_root: Path, candidates: list[Path]
) -> tuple[list[armkit.Edge], list[str], list[tuple[str, str]]]:
    """Build ONE ClassIndex from the combined run's architecture.json and call `_edges_from_calls`
    ONCE on its calls.json — the whole point of combining: a sibling ProjectReference is now parsed
    SOURCE sitting in the SAME Files[] list, not a compiled DLL read by a separate invocation."""
    arch_model = json.loads(combined["arch"].read_text(encoding="utf-8"))
    files = arch_model.get("Files", [])
    index = ClassIndex()
    for file_obj in files:
        index.add_file(file_obj)
    edges = list(_edges_from_calls(combined["calls"], index, repo_root))
    productive, unproductive = _combined_project_coverage(files, candidates, repo_root)
    return edges, productive, unproductive


def _collect_per_project(
    engine: Path, repo_root: Path, cell: str, candidates: list[Path], scratch: Path
) -> list[armkit.Edge]:
    """The original one-project-at-a-time path — now the FALLBACK for when the combined solution run
    cannot be produced at all (no `dotnet`, a broken synthetic solution, or the engine refusing the
    combined invocation outright), never for a real per-project coverage gap, which the combined
    path reports itself via `_combined_project_coverage`. Unchanged from before this fix: same
    engine invocation per candidate, same defect-#9 masking guard, same without-tests filter.
    """
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
            reason = withheld_graph_reason(engine)
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


def collect(repo_root: Path, cell: str) -> tuple[list[armkit.Edge], armkit.Coverage]:
    engine = find_engine()
    if engine is None:
        raise RuntimeError(engine_missing_message())

    product, test = _discover_csproj(repo_root)
    candidates = list(product) + (test if cell == "with-tests" else [])
    # Defect #10: a sibling, non-first-party project (same repo, different assembly, e.g. Polly's
    # legacy src/Polly/) is not a test directory, so it survived _discover_csproj unfiltered. Scope
    # it out here against corpus.json's own product_projects/test_assemblies_projects — the same
    # check armkit.source_files applies to raw source files for grep, and armkit.in_scope applies
    # per-edge for repowise. This is also exactly the candidate list the synthetic solution below
    # must list — not one project more (a sibling would leak into every callee) or fewer (the whole
    # point of combining is lost).
    candidates = [c for c in candidates if armkit.in_scope(c, repo_root, cell)]
    if not candidates:
        raise RuntimeError(f"no .csproj found under {repo_root}")

    scratch = WORK_ROOT / repo_root.name / cell
    combined = _run_combined(engine, repo_root, cell, candidates, scratch)

    if combined["ok"] and combined.get("calls") is not None:
        edges, productive, unproductive = _edges_from_combined(combined, repo_root, candidates)

        if not productive:
            detail = "; ".join(f"{rel}: {why}" for rel, why in unproductive)
            raise armkit.Blocked(
                f"combined solution run analyzed {len(candidates)} candidate project(s) for cell "
                f"{cell!r} but none contributed a file to it — {detail}. "
                f"Solution: {combined['sln_path']}"
            )
        if unproductive:
            # Same masking guard as the per-project path, derived from file contribution instead of
            # a per-invocation exit code — see `_combined_project_coverage`.
            detail = "; ".join(f"{rel}: {why}" for rel, why in unproductive)
            raise armkit.Blocked(
                f"combined solution run: {len(unproductive)} of {len(candidates)} in-scope "
                f"project(s) contributed no files even though {len(productive)} did "
                f"({', '.join(productive)}) — refusing to grade a partial result as complete. "
                f"{detail}. Solution: {combined['sln_path']}"
            )

        if cell == "without-tests":
            edges = [
                e for e in edges
                if e.caller_file is None
                or not armkit.is_test_path(repo_root / e.caller_file, repo_root)
            ]

        note = (f"combined solution mode: one engine invocation over {len(candidates)} in-scope "
                f"project(s) via synthetic solution {combined['sln_path']}")
        return edges, armkit.Coverage(note=note)

    if combined["ok"] and combined.get("counts") is not None:
        # The entitlement wall is a whole-cell fact, not a per-project one — a fallback per-project
        # run would hit the identical wall on every project, so there is nothing to gain by retrying.
        raise armkit.Blocked(
            f"combined solution run analyzed {len(candidates)} candidate project(s) for cell "
            f"{cell!r} but the call graph was withheld — {withheld_graph_reason(engine)}. "
            f"Solution: {combined['sln_path']}"
        )

    # The combined run itself could not be produced (no dotnet, a broken synthetic solution, the
    # engine refusing the combined invocation, or — belt and braces — an unexpected shape with
    # neither sidecar) — fall back to the per-project path rather than emitting an empty graph.
    fallback_reason = (
        combined.get("reason") or "combined run produced neither calls.json nor calls-counts.json"
    )
    print(f"cheburnexus: combined solution mode unavailable for cell {cell!r} ({fallback_reason}) "
          f"— falling back to per-project analysis", file=sys.stderr)
    edges = _collect_per_project(engine, repo_root, cell, candidates, scratch)
    note = (f"per-project fallback mode: combined solution analysis was unavailable "
            f"({fallback_reason}) — analyzed {len(candidates)} project(s) individually")
    return edges, armkit.Coverage(note=note)


if __name__ == "__main__":
    sys.exit(armkit.main(name="cheburnexus", version=version, collect=collect, mode="live"))
