#!/usr/bin/env python3
"""The "Unity calls it" cell: what it takes in, what it must leave alone.

The Unity engine calls a MonoBehaviour's Awake/Update/OnTriggerEnter from outside the compiled game,
so the answer key has no call site for them. An arm that reports such an edge is describing real
behaviour the key cannot express. This cell takes those edges out of the primary precision
denominator, and it must do nothing else:

  * an edge the key DOES hold (a real `base.Awake()` in IL) stays in primary and still matches;
  * a method with a Unity message name on a class that is not a MonoBehaviour stays in primary;
  * a MonoBehaviour method that is not a Unity message stays in primary;
  * recall never moves: no key edge is added, removed or reclassified;
  * without --unity-components (every non-Unity repository) the grader behaves exactly as before,
    and no new cell appears in the report.

    python3 test_unity.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent


def row(caller: str, callee: str, assembly: str = "App") -> dict:
    return dict(Caller=f"System.Void {caller}()", Callee=f"System.Void {callee}()", Op="call",
                CalleeAssembly=assembly, VirtualDispatch=False, CallerCompilerGenerated=False,
                CalleeCompilerGenerated=False, NoDebugInfo=False)


ORACLE = [
    row("App.Player::Update", "App.Player::Move"),
    # a genuine call to a message in IL: the key holds it
    row("App.Boss::Awake", "App.Player::Awake"),
    row("App.Plain::Update", "App.Helper::Run"),
]

ARM = [
    ("App.Player::Update", "App.Player::Move"),        # true positive
    ("UnityEngine.Engine::Tick", "App.Player::Update"),  # engine call: Unity cell
    ("UnityEngine.Engine::Tick", "App.Boss::OnTriggerEnter"),  # engine call: Unity cell
    ("App.Boss::Awake", "App.Player::Awake"),          # in the key: stays primary, matches
    ("App.Boss::Fire", "App.Plain::Update"),           # Plain is not a MonoBehaviour: junk
    ("App.Boss::Fire", "App.Player::Shoot"),           # not a Unity message: junk
]


def write(path: Path, rows: list) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def grade(tmp: Path, with_unity: bool) -> dict:
    out = tmp / ("with.json" if with_unity else "without.json")
    cmd = [sys.executable, str(HERE / "grade.py"), "--oracle", str(tmp / "oracle.jsonl"),
           "--arm", str(tmp / "arm.jsonl"), "--first-party", "App", "--json", str(out)]
    if with_unity:
        cmd += ["--unity-components", str(tmp / "unity-components.json")]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.read_text())


def cell(report: dict, prefix: str) -> dict | None:
    for entry in report["cells"]:
        if entry["cell"].startswith(prefix):
            return entry
    return None


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        write(tmp / "oracle.jsonl", ORACLE)
        write(tmp / "arm.jsonl", [dict(caller=c, callee=e) for c, e in ARM])
        # Nested and generic names go through the same key function as everything else.
        (tmp / "unity-components.json").write_text(
            json.dumps(["App.Player", "App.Boss", "App.Gen`1/Inner"]), encoding="utf-8")

        before = grade(tmp, with_unity=False)
        after = grade(tmp, with_unity=True)

    if cell(before, "excluded — Unity") is not None:
        failures.append("a Unity cell appeared without --unity-components")
    unity = cell(after, "excluded — Unity")
    if unity is None:
        failures.append("no Unity cell with --unity-components")
        unity = {"arm_edges_in_cell": -1, "oracle_edges": -1}

    p_before, p_after = cell(before, "primary"), cell(after, "primary")
    if unity["arm_edges_in_cell"] != 2:
        failures.append(f"Unity cell took {unity['arm_edges_in_cell']} edges, expected 2 "
                        f"(Player::Update and Boss::OnTriggerEnter)")
    if unity["oracle_edges"] != 0:
        failures.append("the Unity cell must have an empty answer key; it is a place for arm edges")
    # 6 arm edges in primary before; 2 move out; the 3 matched stay.
    if (p_before["arm_edges_in_cell"], p_after["arm_edges_in_cell"]) != (6, 4):
        failures.append(f"primary arm edges {p_before['arm_edges_in_cell']} -> "
                        f"{p_after['arm_edges_in_cell']}, expected 6 -> 4")
    if p_before["matched"] != p_after["matched"]:
        failures.append("matched edges changed")
    if p_before["recall"] != p_after["recall"] or p_before["oracle_edges"] != p_after["oracle_edges"]:
        failures.append("recall or the key moved; the cell may only move arm edges")
    if p_after["matched"] != 2:
        failures.append(f"expected 2 matched (Player::Move and the real base Awake), "
                        f"got {p_after['matched']}")
    if p_after["precision"] is None or abs(p_after["precision"] - 0.5) > 1e-9:
        failures.append(f"primary precision {p_after['precision']}, expected 0.5 (2 of 4)")

    # The cell must be able to fail: the junk edges must still be junk.
    if p_after["arm_edges_in_cell"] - p_after["matched"] != 2:
        failures.append("the two junk edges (non-MonoBehaviour Update, non-message Shoot) must stay "
                        "in primary as false positives")

    if failures:
        print("FAILED test_unity:")
        for f in failures:
            print("  -", f)
        return 1
    print("test_unity: ok (Unity-invoked edges leave primary; key, recall and non-Unity runs untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
