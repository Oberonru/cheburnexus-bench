#!/usr/bin/env python3
"""cheburnexus-java arm -- JavaEngineJdt's call graph, converted to the edge contract.

Drives the product's Java engine (`JavaEngineJdt`, a SEPARATE repository tree, never vendored in)
through its own CLI:

    java -jar <JavaEngineJdt>/build/libs/java-engine-jdt.jar --path <repo> --output-dir <scratch>
         --classpath <corpus compile classpath>

and reads `architecture.json` (types, to rebuild the type spelling) plus `architecture.calls.json`
(caller raw key -> [{Target, Line, Kind}]). Engine raw key: `{abs file}::{type fqn}::{name}({param
types})`, where an implicit constructor is `.ctor` and an explicit one is named after the type.

Oracle key spelling (oracle/java/README.md): `pkg.Type::method` (overloads collapse), nested
`pkg.Outer/Inner`, anonymous `Outer/1`, `.ctor`/`.cctor`.

Mapping, all read from the engine's own output, nothing project-specific:

* type: the engine spells a nested type `pkg.Outer.Inner` (dots all the way). The package is the
  file's `Namespace` in architecture.json, so `fqn[len(Namespace)+1:]` is the nesting chain and its
  dots become `/`. A type absent from architecture.json is a counted drop, never guessed.
* method: parameter list dropped (overloads collapse, as in the oracle). `.ctor` (compiler-made
  constructors, since the engine spells them so itself), `<init>` (older engines) or a method named
  like its type that architecture.json marks `IsConstructor` -> `.ctor`; `<clinit>`/`.cctor` -> `.cctor`.
* NOT converted, on purpose: the callee owner. The engine names the DECLARING type of the resolved
  method; the oracle names the owner javac wrote in the instruction (the receiver's static type).
  The two differ whenever a method is inherited or called through a subtype. There is no override
  or hierarchy map on the oracle side, and this arm does not invent one. The mismatch is a measured
  result, not tuned away.

Not behind any entitlement wall; engine missing or stale, or no classpath => RuntimeError (a run
without the classpath resolves far fewer calls and would be a plausible wrong number).

Environment:
  JAVA_ENGINE_JDT_HOME  JavaEngineJdt checkout (has src/ and build/libs/java-engine-jdt.jar). Required.
  AA_JAVA_CLASSPATH     the corpus compile classpath (runner sets it from the oracle's classpath.txt).
  CHEBURNEXUS_JAVA      java executable that runs the engine. Wins over everything when set.
                        Otherwise `<java_home>/bin/java` of the project's corpus.json entry (the same
                        JDK the oracle used, so JDT sees the same JDK API); otherwise `java` on PATH.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_lib"))
import armkit  # noqa: E402

WORK_ROOT = HERE / ".work"
ENGINE_ENV = "JAVA_ENGINE_JDT_HOME"
CLASSPATH_ENV = "AA_JAVA_CLASSPATH"
JAVA_ENV = "CHEBURNEXUS_JAVA"
_MTIME_SLOP_SECONDS = 2.0


def find_engine_dir() -> Path | None:
    env = os.environ.get(ENGINE_ENV)
    if not env:
        return None
    d = Path(env)
    if (d / "build" / "libs" / "java-engine-jdt.jar").is_file():
        return d
    print(f"cheburnexus-java: ${ENGINE_ENV}={env!r} has no build/libs/java-engine-jdt.jar "
          f"(run `gradle shadowJar` in it?)", file=sys.stderr)
    return None


def engine_missing_message() -> str:
    return (f"cheburnexus-java: JavaEngineJdt checkout not found -- set ${ENGINE_ENV} to the "
            f"JavaEngineJdt directory (with build/libs/java-engine-jdt.jar, built by `gradle "
            f"shadowJar`). No default on purpose: a silently chosen jar can be older than the code "
            f"under test.")


def _jar(engine_dir: Path) -> Path:
    return engine_dir / "build" / "libs" / "java-engine-jdt.jar"


def engine_identity(engine_dir: Path) -> str:
    jar = _jar(engine_dir)
    try:
        digest = hashlib.sha256(jar.read_bytes()).hexdigest()[:12]
        built = time.strftime("%Y-%m-%d", time.gmtime(jar.stat().st_mtime))
    except OSError as exc:
        return f"jar UNREADABLE ({exc.strerror or exc}) at {jar}"
    return f"java-engine-jdt.jar built {built} sha256:{digest} at {engine_dir}"


def version() -> str:
    engine_dir = find_engine_dir()
    if engine_dir is None:
        return f"unavailable (checkout not found -- set ${ENGINE_ENV})"
    return f"{engine_identity(engine_dir)} (JavaEngineJdt CLI, JDT bindings)"


def _stale_build_reason(engine_dir: Path) -> str | None:
    """None if the jar is at least as new as every file under src/ and build.gradle; else why not.
    Same rule as the TS arm: refuse, do not describe."""
    jar = _jar(engine_dir)
    sources = [p for p in (engine_dir / "src").rglob("*") if p.is_file()] if (engine_dir / "src").is_dir() else []
    sources += [p for p in (engine_dir / "build.gradle", engine_dir / "settings.gradle") if p.is_file()]
    if not sources:
        return None  # packaged jar-only build: nothing to compare against, do not guess
    try:
        newest = max(((p, p.stat().st_mtime) for p in sources), key=lambda t: t[1])
        jar_mtime = jar.stat().st_mtime
    except OSError as exc:
        return f"could not stat sources or jar: {exc.strerror or exc}"
    if newest[1] > jar_mtime:
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))  # noqa: E731
        return (f"jar is STALE relative to src: {newest[0].name} was edited {fmt(newest[1])} but the "
                f"jar was last built {fmt(jar_mtime)} -- run `gradle shadowJar` in {engine_dir} "
                f"before measuring. Refusing to run rather than produce plausible-looking numbers "
                f"from an old build.")
    return None


def _reset_work_dir(out_dir: Path) -> None:
    resolved, work_root = out_dir.resolve(), WORK_ROOT.resolve()
    if resolved == work_root or work_root not in resolved.parents:
        raise RuntimeError(f"refusing to clean {resolved} -- not strictly inside {work_root}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def corpus_entry(repo_root: Path) -> dict:
    for entry in armkit._load_corpus_entries():
        if str(entry.get("name", "")).split("/")[-1] == repo_root.name:
            return entry
    return {}


def java_executable(repo_root: Path) -> str:
    """CHEBURNEXUS_JAVA (explicit) > <java_home>/bin/java from corpus.json > `java`."""
    explicit = os.environ.get(JAVA_ENV)
    if explicit:
        return explicit
    home = corpus_entry(repo_root).get("java_home")
    return str(Path(home) / "bin" / "java") if home else "java"


def java_identity(java_exe: str) -> str:
    try:
        out = subprocess.run([java_exe, "-version"], capture_output=True, text=True, timeout=30).stderr
        first = out.splitlines()[0] if out else "no version output"
    except (OSError, subprocess.SubprocessError) as exc:
        first = f"unreadable ({exc})"
    return f"{java_exe} [{first}]"


def _run_engine(engine_dir: Path, repo_root: Path, out_dir: Path, classpath: str) -> dict:
    _reset_work_dir(out_dir)
    invoked_at = time.time()
    cmd = [java_executable(repo_root), "-jar", str(_jar(engine_dir)),
           "--path", str(repo_root), "--output-dir", str(out_dir), "--classpath", classpath]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "engine timed out after 900s"}
    (out_dir / "engine.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out_dir / "engine.stderr.txt").write_text(proc.stderr, encoding="utf-8")

    def tail() -> str:
        lines = [ln for ln in (proc.stdout + "\n" + proc.stderr).splitlines() if ln.strip()]
        return lines[-1] if lines else f"exit {proc.returncode}, no output"

    if proc.returncode != 0:
        return {"ok": False, "reason": f"exit {proc.returncode}: {tail()}"}
    paths = {n: out_dir / f"architecture{s}.json" for n, s in
             (("arch", ""), ("calls", ".calls"), ("methods", ".methods"))}
    for p in paths.values():
        if not (p.is_file() and p.stat().st_mtime >= invoked_at - _MTIME_SLOP_SECONDS):
            return {"ok": False, "reason": f"exit 0 but {p.name} is missing or predates this run"}
    return {"ok": True, **paths, "stderr": proc.stderr}


# ── key conversion ───────────────────────────────────────────────────────────────────────────────

class TypeIndex:
    """engine type fqn -> oracle type spelling (`pkg.Outer/Inner`), plus the constructors the engine
    declares. Built from this run's own architecture.json; never reads the oracle."""

    def __init__(self, arch: dict) -> None:
        self.spelling: dict[str, str] = {}
        self.simple_name: dict[str, str] = {}
        self.ctor_names: set[tuple[str, str]] = set()
        for file_obj in arch.get("Files", []):
            namespace = file_obj.get("Namespace") or ""
            for t in file_obj.get("Types", []):
                fqn = t.get("fqn")
                if not fqn:
                    continue
                if namespace and fqn.startswith(namespace + "."):
                    chain = fqn[len(namespace) + 1:].replace(".", "/")
                    spelled = f"{namespace}.{chain}"
                else:
                    spelled = fqn.replace(".", "/") if not namespace else fqn
                self.spelling[fqn] = spelled
                self.simple_name[fqn] = t.get("Name", "")
                for m in t.get("Methods", []):
                    if m.get("IsConstructor"):
                        self.ctor_names.add((fqn, m.get("Name", "")))


_DROP_REASONS = ("unparseable-raw-key", "type-not-in-architecture-json")


def _parse_raw_key(raw: str) -> tuple[str, str, str] | None:
    """`{file}::{fqn}::{name}({params})` or `...::<init>` -> (file, fqn, name)."""
    parts = raw.split("::", 2)
    if len(parts) != 3:
        return None
    file_path, fqn, member = parts
    return file_path, fqn, member.split("(", 1)[0]


class Translator:
    def __init__(self, types: TypeIndex) -> None:
        self.types = types
        self.drops = {r: 0 for r in _DROP_REASONS}
        self.ok = 0

    def translate(self, raw: str) -> str | None:
        parsed = _parse_raw_key(raw)
        if parsed is None:
            self.drops["unparseable-raw-key"] += 1
            return None
        _, fqn, name = parsed
        spelled = self.types.spelling.get(fqn)
        if spelled is None:
            self.drops["type-not-in-architecture-json"] += 1
            return None
        if name == "<init>" or (name == self.types.simple_name.get(fqn)
                                and (fqn, name) in self.types.ctor_names):
            name = ".ctor"
        elif name == "<clinit>":
            name = ".cctor"
        self.ok += 1
        return f"{spelled}::{name}"


def _to_repo_relative(abs_path: str, repo_root: Path) -> str:
    posix = abs_path.replace("\\", "/")
    root_posix = str(repo_root.resolve()).replace("\\", "/")
    if posix.startswith(root_posix + "/"):
        return posix[len(root_posix) + 1:]
    return posix  # never silently invent a path


def corpus_java_packages(repo_root: Path) -> tuple[str, ...] | None:
    """`java_packages` of the corpus entry named like this checkout (the oracle's own definition of
    "project class"), or None if corpus.json has no entry / no field."""
    for entry in armkit._load_corpus_entries():
        if str(entry.get("name", "")).split("/")[-1] == repo_root.name:
            pk = entry.get("java_packages")
            return tuple(pk) if pk else None
    return None


def _in_package(fqn_or_spelling: str, packages: tuple[str, ...]) -> bool:
    return any(fqn_or_spelling == p or fqn_or_spelling.startswith(p + ".") for p in packages)


def _edges_from_calls(calls_data: dict, translator: Translator, repo_root: Path):
    for raw_caller, entry in calls_data.items():
        caller = translator.translate(raw_caller)
        caller_rel = _to_repo_relative(raw_caller.split("::", 1)[0], repo_root)
        for call in entry.get("Calls", []) or []:
            target = call.get("Target")
            if not target:
                continue
            callee = translator.translate(target)
            if caller is None or callee is None:
                continue
            yield armkit.Edge(caller=caller, callee=callee, caller_file=caller_rel,
                              caller_line=call.get("Line"))


def collect(repo_root: Path, cell: str):
    engine_dir = find_engine_dir()
    if engine_dir is None:
        raise RuntimeError(engine_missing_message())
    stale = _stale_build_reason(engine_dir)
    if stale is not None:
        raise RuntimeError(f"cheburnexus-java: refusing to measure with a stale build -- {stale}")
    classpath = (os.environ.get(CLASSPATH_ENV) or "").strip()
    if not classpath:
        raise RuntimeError(f"cheburnexus-java: ${CLASSPATH_ENV} is not set (the runner takes it from "
                           f"the oracle's classpath.txt). Without it most library calls stay "
                           f"unresolved; refusing to publish that as a measurement.")

    scratch = WORK_ROOT / repo_root.name / cell
    result = _run_engine(engine_dir, repo_root, scratch, classpath)
    if not result["ok"]:
        raise RuntimeError(f"JavaEngineJdt failed on {repo_root}: {result['reason']}")

    arch = json.loads(result["arch"].read_text(encoding="utf-8"))
    calls_data = json.loads(result["calls"].read_text(encoding="utf-8")).get("Data") or {}
    translator = Translator(TypeIndex(arch))
    edges = list(_edges_from_calls(calls_data, translator, repo_root))
    total_calls = sum(len(v.get("Calls") or []) for v in calls_data.values())

    # Scope: the same definition of "project class" the oracle uses (java_packages), plus
    # .java files inside the cell's declared surface.
    packages = corpus_java_packages(repo_root)
    if packages is not None:
        edges = [e for e in edges if _in_package(e.caller, packages) and _in_package(e.callee, packages)]
    in_cell = {p for p in armkit.source_files(repo_root, cell, ".java")}
    edges = [e for e in edges
             if e.caller_file is None or (repo_root / e.caller_file) in in_cell]

    m = re.search(r"calls: (\d+) resolved internal edge\(s\), (\d+) external \(dropped\), "
                  r"(\d+) unresolved binding \(dropped\)", result["stderr"])
    engine_note = (f"engine: {m.group(1)} resolved internal, {m.group(2)} external, "
                   f"{m.group(3)} unresolved binding dropped" if m else "engine drop counts not found")
    unresolved = int(m.group(3)) if m else None
    dropped = sum(translator.drops.values())
    breakdown = ", ".join(f"{k}={v}" for k, v in translator.drops.items() if v) or "none"
    note = (f"engine run by {java_identity(java_executable(repo_root))}; JavaEngineJdt resolved {total_calls} project call sites ({engine_note}); "
            f"{dropped} key translation drops ({breakdown}). Callee is the DECLARING type, the "
            f"oracle's is the instruction owner (receiver static type): not converted, see header.")
    print(f"cheburnexus-java: {note}", file=sys.stderr)
    coverage = armkit.Coverage(is_exact=False, reason="declaring_type_vs_instruction_owner",
                               unresolved_call_sites=unresolved, note=note)
    return edges, coverage


if __name__ == "__main__":
    sys.exit(armkit.main(name="cheburnexus-java", version=version, collect=collect, mode="live"))
