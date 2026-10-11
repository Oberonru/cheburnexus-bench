#!/usr/bin/env python3
"""The repowise-java arm: repowise's own call graph on a Java checkout, in the oracle's key spelling.

Sister of arms/repowise. repowise supports Java itself (tree-sitter Java spec + JVM resolver in the
pinned 0.45.0 venv), so this arm runs the same `repowise init --mode fast` (structural only: no LLM
key, no network call by this arm, no cost) through the C# arm's own helpers (venv, scratch copy,
init call) and reads `graph_nodes` / `graph_edges` from the same SQLite store. Only the key
building differs: the C# arm loads repowise's .NET namespace scanner, which does not apply here.

Key: `node_id` is `<file>::<Class>::<method>` (one level of parent only). The package and the full
nesting (`pkg.Outer/Inner`) come from scanning the file itself (arms/_lib/javascan.py); when the
bare class name is declared more than once in a file the edge is dropped and counted, never
guessed. A method named like its class is `.ctor`.

Scope: the corpus entry's `java_packages` (the oracle's own definition of a project class) on both
ends of an edge; the whole checkout is indexed, so the filter is what keeps sibling sources out.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_lib"))
import armkit  # noqa: E402
import javascan  # noqa: E402

_spec = importlib.util.spec_from_file_location("_repowise_arm", HERE.parent / "repowise" / "run.py")
rw = importlib.util.module_from_spec(_spec)
sys.modules["_repowise_arm"] = rw
_spec.loader.exec_module(rw)

_EXTRACTOR_VERSION = "java-1"
# Build output and tool state of JVM builds (Gradle `build/`, Maven `target/`): generated classes,
# not sources, and their deep paths break the copy on Windows. Sources never live there.
_JAVA_COPY_EXCLUDE = {"build", ".gradle", "target", ".git", ".idea", "bin", "obj", "node_modules"}
rw._COPY_EXCLUDE = _JAVA_COPY_EXCLUDE  # this process only; the C# arm runs in its own


def _packages(repo_root: Path):
    for entry in armkit._load_corpus_entries():
        if str(entry.get("name", "")).split("/")[-1] == repo_root.name:
            return tuple(entry.get("java_packages") or ()) or None
    return None


class KeyBuilder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.cache: dict[str, dict[str, list[str]] | None] = {}
        self.drops = {"unreadable_file": 0, "no_class_segment": 0, "class_not_declared_in_file": 0,
                      "ambiguous_class_name": 0}

    def _types(self, file_path: str):
        if file_path not in self.cache:
            try:
                text = (self.root / file_path).read_text(encoding="utf-8-sig", errors="replace")
                self.cache[file_path] = javascan.declared_types(javascan.strip_java(text))[1]
            except OSError:
                self.cache[file_path] = None
        return self.cache[file_path]

    def build(self, node_id: str, file_path: str, name: str) -> str | None:
        bare = rw._node_class_bare(node_id, file_path, name)
        if bare is None:
            self.drops["no_class_segment"] += 1
            return None
        types = self._types(file_path)
        if types is None:
            self.drops["unreadable_file"] += 1
            return None
        keys = types.get(bare, [])
        if not keys:
            self.drops["class_not_declared_in_file"] += 1
            return None
        if len(set(keys)) > 1:
            self.drops["ambiguous_class_name"] += 1
            return None
        return f"{keys[0]}::{'.ctor' if name == bare else name}"


def _collect_raw(repo_root: Path) -> list[dict]:
    index_dir = rw._index_dir_for(repo_root)
    cache_path = index_dir / "raw_edges.json"
    stamp_path = index_dir / "raw_edges.stamp.json"
    stamp = {"extractor": _EXTRACTOR_VERSION, "repowise": rw.version(), "head": rw._checkout_head(repo_root)}
    if cache_path.is_file() and stamp_path.is_file():
        try:
            if json.loads(stamp_path.read_text(encoding="utf-8")) == stamp:
                return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        print("repowise-java: cache does not match extractor/tool/checkout -- re-extracting", file=sys.stderr)

    copy = rw._ensure_scratch_copy(repo_root, index_dir)
    db_path = copy / ".repowise" / "wiki.db"
    if not db_path.is_file():
        rw._run_repowise_init(copy)
    if not db_path.is_file():
        raise RuntimeError(f"repowise init did not produce {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        repo_row = conn.execute("SELECT id FROM repositories LIMIT 1").fetchone()
        if repo_row is None:
            raise RuntimeError(f"no rows in repositories table of {db_path}")
        rid = repo_row["id"]
        nodes = {r["node_id"]: (r["file_path"], r["name"]) for r in conn.execute(
            "SELECT node_id, file_path, name FROM graph_nodes WHERE repository_id=? AND node_type='symbol' "
            "AND file_path IS NOT NULL AND name IS NOT NULL", (rid,))}
        edges = list(conn.execute(
            "SELECT source_node_id, target_node_id, call_lines_json FROM graph_edges "
            "WHERE repository_id=? AND edge_type='calls'", (rid,)))
    finally:
        conn.close()

    kb = KeyBuilder(copy)
    raw: list[dict] = []
    missing = 0
    for row in edges:
        src, dst = nodes.get(row["source_node_id"]), nodes.get(row["target_node_id"])
        if src is None or dst is None:
            missing += 1
            continue
        ck = kb.build(row["source_node_id"], *src)
        ek = kb.build(row["target_node_id"], *dst)
        if ck is None or ek is None:
            continue
        try:
            lines = json.loads(row["call_lines_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            lines = []
        raw.append({"caller": ck, "callee": ek, "caller_file": src[0].replace("\\", "/"),
                    "caller_line": min(lines) if lines else None})
    (index_dir / "extract_stats.json").write_text(json.dumps(
        {"edges_total": len(edges), "edges_emitted": len(raw), "dropped_missing_node": missing,
         **{f"dropped_{k}": v for k, v in kb.drops.items()}}, indent=2), encoding="utf-8")
    cache_path.write_text(json.dumps(raw), encoding="utf-8")
    stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    return raw


def collect(repo_root: Path, cell: str) -> Iterator[armkit.Edge]:
    packages = _packages(repo_root)
    for row in _collect_raw(repo_root):
        if packages and not (javascan.in_packages(row["caller"].split("::")[0], packages)
                             and javascan.in_packages(row["callee"].split("::")[0], packages)):
            continue
        if row["caller_file"]:
            path = repo_root / row["caller_file"]
            if cell == "without-tests" and armkit.is_test_path(path, repo_root):
                continue
        yield armkit.Edge(row["caller"], row["callee"], row["caller_file"] or None, row["caller_line"])


if __name__ == "__main__":
    sys.exit(armkit.main(name="repowise-java", version=rw.version, collect=collect))
