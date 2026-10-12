#!/usr/bin/env python3
"""The repowise-ts arm: the competitor's own indexer over a TypeScript checkout, call graph read
from its SQLite store (same route as arms/repowise, see its NOTES.md), keys spelled the way the
TypeScript oracle spells them: `{file}::{Class.}{name}:{declaration line}`.

Everything here is a join on columns repowise itself persisted (`graph_nodes.kind/name/file_path/
start_line/parent_symbol_id`, `graph_edges` of type `calls`); nothing is guessed:
  * method          -> `file::Parent.name:start_line`
  * function/variable (arrow or function expression bound to a const) -> `file::name:start_line`
  * `__module__` caller (top-level code) -> `file::module-scope:0`
  * a class as CALLEE (a `new`/decorator call) -> the class's own `constructor` method node when
    repowise indexed one, otherwise dropped (no line to name it by)
  * any other node kind (type_alias, interface, enum, constant, module as callee) -> dropped
Anonymous closures, accessors' (get)/(set) tags and overload-head lines are not in repowise's graph,
so those oracle keys can never be matched by this arm; that is a limit of the tool, not a rule here.
Reuses arms/repowise's index cache (`arms/repowise/.index/<repo>`), `repowise init --mode fast`: no
LLM, no cost.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import armkit  # noqa: E402

_spec = importlib.util.spec_from_file_location("rw_cs", Path(__file__).resolve().parent.parent / "repowise" / "run.py")
rw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rw)


def _db(repo_root: Path) -> Path:
    idx = rw._index_dir_for(repo_root)
    cc = rw._ensure_scratch_copy(repo_root, idx)
    db = cc / ".repowise" / "wiki.db"
    if not db.is_file():
        rw._run_repowise_init(cc)
    if not db.is_file():
        raise RuntimeError(f"repowise init did not produce {db}")
    return db


def collect(repo_root: Path, cell: str) -> Iterator[armkit.Edge]:
    conn = sqlite3.connect(str(_db(repo_root)))
    conn.row_factory = sqlite3.Row
    try:
        nodes = {r["node_id"]: r for r in conn.execute(
            "SELECT node_id, kind, name, file_path, start_line, parent_symbol_id FROM graph_nodes "
            "WHERE node_type='symbol' AND file_path IS NOT NULL")}
        edges = list(conn.execute(
            "SELECT source_node_id, target_node_id, call_lines_json FROM graph_edges WHERE edge_type='calls'"))
    finally:
        conn.close()
    ctor = {}
    for r in nodes.values():
        if r["kind"] == "method" and r["name"] == "constructor":
            ctor[(r["file_path"], r["parent_symbol_id"])] = r

    def key(n, as_callee: bool):
        f, k = n["file_path"].replace("\\", "/"), n["kind"]
        if k == "module" and not as_callee:
            return f"{f}::module-scope:0"
        if k == "class" and as_callee:
            n = ctor.get((n["file_path"], n["name"]))
            return None if n is None else key(n, True)
        if k == "method":
            return f"{f}::{n['parent_symbol_id']}.{n['name']}:{n['start_line']}"
        if k in ("function", "variable"):
            return f"{f}::{n['name']}:{n['start_line']}"
        return None

    for e in edges:
        s, t = nodes.get(e["source_node_id"]), nodes.get(e["target_node_id"])
        if s is None or t is None:
            continue
        ck, tk = key(s, False), key(t, True)
        if ck is None or tk is None:
            continue
        f = s["file_path"].replace("\\", "/")
        if not armkit.in_scope(repo_root / f, repo_root, cell):
            continue
        try:
            lines = json.loads(e["call_lines_json"] or "[]")
        except (ValueError, TypeError):
            lines = []
        yield armkit.Edge(ck, tk, f, min(lines) if lines else None)


def version() -> str:
    return f"repowise-ts-1 / repowise {rw.version()}"


if __name__ == "__main__":
    sys.exit(armkit.main(name="repowise-ts", version=version, collect=collect))
