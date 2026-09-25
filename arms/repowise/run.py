#!/usr/bin/env python3
"""The repowise arm: run the competitor's own indexer over a C# checkout and convert its call
graph into our edge contract.

See NOTES.md for the full discovery writeup (why SQLite over their export/MCP surfaces, exactly
which table/columns are read, and precisely how a namespace-qualified key is rebuilt from data
repowise itself does not persist in namespace-qualified form).

Shape of the pipeline, per repository:

  1. Copy the checkout into a scratch working directory under ``.index/<repo>/checkout`` — repowise
     writes ``.repowise/`` (state, config, CLAUDE.md, editor integration files) straight into the
     directory it indexes, and the runner's checkouts must stay read-only. The copy is disposable.
  2. Run ``repowise init --mode fast`` (structural only: no LLM key, no prose, no cost) from the
     pinned venv against the scratch copy. This is the one step that needs the venv's Python — every
     dependency repowise pulls in (tree-sitter grammars, SQLAlchemy async drivers, ...) lives there.
  3. Read ``graph_nodes`` / ``graph_edges`` straight out of the SQLite file it wrote, with the
     stdlib ``sqlite3`` module — no ORM needed, and this step does not need repowise importable.
  4. Rebuild a namespace-qualified ``Namespace.Type::Method`` key for each edge endpoint, reusing
     repowise's own regex-based namespace/type scanner (``namespace_map.py``, loaded straight out of
     the installed venv by file path — the exact pinned code, not a transcription of it).
  5. Cache the raw resolved edges once per repository so both cells reuse the same index; filter to
     ``without-tests`` on the way out with ``armkit.is_test_path``, exactly like every other arm.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import armkit  # noqa: E402

ARM_DIR = Path(__file__).resolve().parent
VENV_DIR = ARM_DIR / ".venv"
INDEX_ROOT = ARM_DIR / ".index"


def _venv_executable(name: str) -> Path:
    """The pinned venv's ``name`` executable, whichever layout this platform's ``venv`` used to
    build it: POSIX puts executables in ``bin/``; Windows puts them in ``Scripts/`` with a ``.exe``
    suffix. Checked in that order; when neither exists yet (venv not installed) the POSIX shape is
    returned so the resulting "not found" error still names a real, inspectable path.
    """
    posix = VENV_DIR / "bin" / name
    if posix.is_file():
        return posix
    windows = VENV_DIR / "Scripts" / f"{name}.exe"
    if windows.is_file():
        return windows
    return posix


VENV_PYTHON = _venv_executable("python")

# Directories that never carry hand-written source and are excluded from the scratch copy purely
# to save time/disk on the copy — repowise's own ``blocked_dirs`` for C# already skips bin/obj/.vs
# during parsing, so leaving them out of the copy changes nothing about what gets indexed.
_COPY_EXCLUDE = {"bin", "obj", ".vs", "node_modules"}


# ── Locating the pinned install ──────────────────────────────────────────────────────────────

def _site_packages() -> Path | None:
    """The pinned venv's site-packages directory, on whichever layout built it.

    Windows ``venv`` lays this out directly as ``Lib/site-packages``; POSIX nests it one level
    deeper, under a python-version directory (``lib/python3.12/site-packages``). Both are checked
    so this resolves under whichever platform actually created ``.venv``.
    """
    direct = VENV_DIR / "Lib" / "site-packages"
    if direct.is_dir():
        return direct
    lib = VENV_DIR / "lib"
    if not lib.is_dir():
        return None
    for py_dir in lib.iterdir():
        candidate = py_dir / "site-packages"
        if candidate.is_dir():
            return candidate
    return None


def installed_version() -> str | None:
    """The installed repowise version, read from its dist-info directory name.

    Avoids importing the package (which needs the venv's own interpreter) just to answer
    ``--describe`` under whatever Python the runner happens to invoke this script with.
    """
    sp = _site_packages()
    if sp is None:
        return None
    for entry in sp.glob("repowise-*.dist-info"):
        # "repowise-0.45.0.dist-info" -> "0.45.0"
        name = entry.name[len("repowise-"):-len(".dist-info")]
        return name
    return None


def version() -> str:
    return installed_version() or "not-installed"


def _namespace_map_module():
    """Load repowise's own ``namespace_map.py`` straight out of the pinned venv, by file path.

    Not a transcription: this is the exact pinned-version file, loaded with ``importlib`` so it
    never has to go through ``repowise/core/ingestion/__init__.py`` (which pulls in every
    third-party dependency the package has, most of it irrelevant to two regex functions). The
    module itself imports only ``re`` and ``pathlib``, so this works under any Python, including
    the one the runner uses to invoke this script — not just the venv's own interpreter.
    """
    sp = _site_packages()
    if sp is None:
        raise RuntimeError("repowise venv not found; run pip install first")
    path = sp / "repowise" / "core" / "ingestion" / "resolvers" / "dotnet" / "namespace_map.py"
    if not path.is_file():
        raise RuntimeError(f"namespace_map.py not found at {path}")
    spec = importlib.util.spec_from_file_location("_repowise_namespace_map", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── Step 1: scratch copy ─────────────────────────────────────────────────────────────────────

def _index_dir_for(repo_root: Path) -> Path:
    return INDEX_ROOT / repo_root.name


def _ensure_scratch_copy(repo_root: Path, index_dir: Path) -> Path:
    checkout_copy = index_dir / "checkout"
    if checkout_copy.is_dir():
        return checkout_copy
    index_dir.mkdir(parents=True, exist_ok=True)

    def _ignore(dirpath: str, names: list[str]) -> set[str]:
        return {n for n in names if n in _COPY_EXCLUDE}

    tmp = index_dir / "checkout.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(repo_root, tmp, ignore=_ignore)
    tmp.rename(checkout_copy)
    return checkout_copy


# ── Step 2: run their indexer ────────────────────────────────────────────────────────────────

def _run_repowise_init(checkout_copy: Path) -> None:
    repowise_bin = _venv_executable("repowise")
    cmd = [
        str(repowise_bin), "init", str(checkout_copy),
        "--mode", "fast",          # structural graph only: no LLM key, no prose, no spend
        "--no-editor-setup",       # do not write .vscode/ or MCP registrations
        "--no-claude-md",          # do not write CLAUDE.md
        "--yes",                   # never wait on a confirmation prompt
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    log_path = checkout_copy.parent / "init.stderr.txt"
    log_path.write_text((result.stdout or "") + "\n---stderr---\n" + (result.stderr or ""),
                         encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"repowise init failed (exit {result.returncode}); see {log_path}"
        )


# ── Step 3+4: read the SQLite store, rebuild qualified keys ─────────────────────────────────

class _RawEdge:
    __slots__ = ("caller", "callee", "caller_file", "caller_line")

    def __init__(self, caller: str, callee: str, caller_file: str, caller_line: int | None):
        self.caller = caller
        self.callee = callee
        self.caller_file = caller_file
        self.caller_line = caller_line


def _node_class_bare(node_id: str, file_path: str, name: str) -> str | None:
    """Recover the class/type segment ``repowise`` folded into ``node_id``.

    ``node_id`` is built by repowise as ``f"{file_path}::{parent_name}::{name}"`` when a parent
    type was found, or ``f"{file_path}::{name}"`` when it was not (see
    ``packages/core/src/repowise/core/ingestion/parser.py``, ~L858 in the pinned reference clone).
    ``file_path`` and ``name`` are both trusted columns already read off the row, so the only
    unknown left is whatever sits between them — which is exactly the immediate enclosing type's
    *bare* name repowise itself resolved (one level only: a doubly-nested class collapses onto its
    immediate parent, a known repowise limitation carried over here — see NOTES.md).
    """
    two_part = f"{file_path}::{name}"
    if node_id == two_part:
        return None
    prefix = f"{file_path}::"
    suffix = f"::{name}"
    if node_id.startswith(prefix) and node_id.endswith(suffix) and len(node_id) > len(prefix) + len(suffix) - 2:
        middle = node_id[len(prefix):-len(suffix)]
        return middle or None
    return None  # node_id shape did not match either pattern repowise is known to emit


class _KeyBuilder:
    """Turns a repowise (file_path, class_bare, method_name) triple into ``Namespace.Type::Method``.

    Reuses repowise's own ``scan_type_declarations``/``declared_namespaces`` (see NOTES.md for why:
    repowise's own persisted ``qualified_name`` column is derived from the *file path*, not from the
    C# ``namespace`` block, so it is unusable for this contract's key shape). Caches per-file parses
    since many edges share a caller/callee file.
    """

    def __init__(self, namespace_map_module, checkout_copy: Path):
        self._mod = namespace_map_module
        self._root = checkout_copy
        self._decls_cache: dict[str, list] = {}
        self._ns_cache: dict[str, list[str]] = {}
        self.dropped_no_class = 0
        self.dropped_unreadable = 0

    def _decls_for(self, file_path: str) -> list:
        if file_path in self._decls_cache:
            return self._decls_cache[file_path]
        try:
            text = (self._root / file_path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            self._decls_cache[file_path] = []
            self._ns_cache[file_path] = []
            return []
        decls = self._mod.scan_type_declarations(text)
        self._decls_cache[file_path] = decls
        self._ns_cache[file_path] = self._mod.declared_namespaces(text)
        return decls

    def build(self, node_id: str, file_path: str, name: str) -> str | None:
        class_bare = _node_class_bare(node_id, file_path, name)
        if class_bare is None:
            self.dropped_no_class += 1
            return None

        decls = self._decls_for(file_path)
        if not decls and file_path not in self._ns_cache:
            self.dropped_unreadable += 1
            return None

        # Tier 1: an exact type declaration in this file names this class — use repowise's own
        # namespace + one-level-nesting resolution (``TypeDecl.fqn``).
        for decl in decls:
            if decl.name == class_bare:
                type_part = decl.fqn
                return f"{type_part}::{name}"

        # Tier 2: no matching declaration (e.g. the class is a partial fragment whose *this* file
        # only contributes a member, or ``class_bare`` was actually a namespace segment repowise's
        # own resolver picked up — see NOTES.md). Fall back to the file's own declared namespace(s).
        namespaces = self._ns_cache.get(file_path, [])
        if len(namespaces) == 1:
            return f"{namespaces[0]}.{class_bare}::{name}"
        # Zero namespaces (global namespace) or an ambiguous multi-namespace file with no exact
        # type match: emit the bare type, which is still a valid (if less qualified) key. Recorded,
        # never guessed further than what repowise's own scanner actually found.
        return f"{class_bare}::{name}"


# ── Orchestration ────────────────────────────────────────────────────────────────────────────

# Bump when the extraction below changes shape, so cached rows from an older rule are not reused.
_EXTRACTOR_VERSION = "1"


def _checkout_head(repo_root: Path) -> str:
    """The checkout's commit, so a cache cannot outlive the code it describes. Unknown is honest."""
    try:
        proc = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _collect_raw(repo_root: Path) -> list[dict]:
    """Index (if needed) and return the cached raw edge rows for this repository, unfiltered by
    cell. Cached to ``.index/<repo>/raw_edges.json`` so ``with-tests`` and ``without-tests`` share
    one indexing run — repowise's own graph does not distinguish the cells at all; the split is
    applied afterwards, on ``caller_file``, exactly like the task brief allows.
    """
    index_dir = _index_dir_for(repo_root)
    cache_path = index_dir / "raw_edges.json"
    stamp_path = index_dir / "raw_edges.stamp.json"

    # A cache that never expires is a reproducibility hazard, not a speed-up: edit the extraction
    # here, or install a different repowise, and a stale file keeps answering with rows nobody can
    # trace to the code that supposedly produced them. The stamp ties the cache to the three things
    # that can change its contents; any drift and it is rebuilt.
    stamp = {
        "extractor": _EXTRACTOR_VERSION,
        "repowise": version(),
        "head": _checkout_head(repo_root),
    }
    if cache_path.is_file() and stamp_path.is_file():
        try:
            if json.loads(stamp_path.read_text(encoding="utf-8")) == stamp:
                return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # unreadable stamp means re-extract, never means trust the cache
        print("repowise: cached rows do not match the current extractor/tool/checkout — re-extracting",
              file=sys.stderr)

    checkout_copy = _ensure_scratch_copy(repo_root, index_dir)
    db_path = checkout_copy / ".repowise" / "wiki.db"
    if not db_path.is_file():
        _run_repowise_init(checkout_copy)
    if not db_path.is_file():
        raise RuntimeError(f"repowise init did not produce {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        repo_row = conn.execute(
            "SELECT id FROM repositories WHERE local_path = ?", (str(checkout_copy),)
        ).fetchone()
        if repo_row is None:
            repo_row = conn.execute("SELECT id FROM repositories LIMIT 1").fetchone()
        if repo_row is None:
            raise RuntimeError(f"no rows in repositories table of {db_path}")
        repository_id = repo_row["id"]

        nodes: dict[str, tuple[str, str]] = {}
        for row in conn.execute(
            "SELECT node_id, file_path, name FROM graph_nodes "
            "WHERE repository_id = ? AND node_type = 'symbol' "
            "AND file_path IS NOT NULL AND name IS NOT NULL",
            (repository_id,),
        ):
            nodes[row["node_id"]] = (row["file_path"], row["name"])

        edge_rows = list(conn.execute(
            "SELECT source_node_id, target_node_id, call_lines_json FROM graph_edges "
            "WHERE repository_id = ? AND edge_type = 'calls'",
            (repository_id,),
        ))
    finally:
        conn.close()

    ns_mod = _namespace_map_module()
    kb = _KeyBuilder(ns_mod, checkout_copy)

    raw: list[dict] = []
    dropped_missing_node = 0
    for row in edge_rows:
        src_id, dst_id = row["source_node_id"], row["target_node_id"]
        src = nodes.get(src_id)
        dst = nodes.get(dst_id)
        if src is None or dst is None:
            dropped_missing_node += 1
            continue
        src_file, src_name = src
        dst_file, dst_name = dst

        caller_key = kb.build(src_id, src_file, src_name)
        callee_key = kb.build(dst_id, dst_file, dst_name)
        if caller_key is None or callee_key is None:
            continue

        lines = []
        try:
            lines = json.loads(row["call_lines_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            lines = []
        caller_line = min(lines) if lines else None

        raw.append({
            "caller": caller_key,
            "callee": callee_key,
            "caller_file": src_file.replace("\\", "/"),
            "caller_line": caller_line,
        })

    stats = {
        "edges_total": len(edge_rows),
        "edges_emitted": len(raw),
        "dropped_missing_node": dropped_missing_node,
        "dropped_no_class_segment": kb.dropped_no_class,
        "dropped_unreadable_file": kb.dropped_unreadable,
    }
    (index_dir / "extract_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    cache_path.write_text(json.dumps(raw), encoding="utf-8")
    stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    return raw


def collect(repo_root: Path, cell: str) -> Iterator[armkit.Edge]:
    raw = _collect_raw(repo_root)
    for row in raw:
        caller_file = row["caller_file"]
        if caller_file:
            abs_path = repo_root / caller_file
            # Defect #10: repowise indexes the whole scratch copy (see module docstring, step 1),
            # not just the repository's declared product/test projects — a sibling, non-first-party
            # project (Polly's legacy `src/Polly/`) got indexed and scored right alongside
            # `Polly.Core`. Scope first, cell split (test vs. product) second — same order as
            # `armkit.source_files` uses for every other arm.
            if not armkit.in_scope(abs_path, repo_root, cell):
                continue
            if cell == "without-tests" and armkit.is_test_path(abs_path, repo_root):
                continue
        yield armkit.Edge(
            caller=row["caller"],
            callee=row["callee"],
            caller_file=caller_file or None,
            caller_line=row["caller_line"],
        )


if __name__ == "__main__":
    sys.exit(armkit.main(name="repowise", version=version, collect=collect))
