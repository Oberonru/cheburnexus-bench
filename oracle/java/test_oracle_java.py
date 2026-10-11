#!/usr/bin/env python3
"""Runs the Java oracle agent on a tiny project: a call to a method declared only in a library
type, made through a project type, must NOT be recorded (C# rule: declaring type is external);
a method declared in a project supertype resolves to that supertype.

    python3 oracle/java/test_oracle_java.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

SRC = {
    "t/Base.java": "package t; public class Base { public void own() {} }",
    "t/Bag.java": "package t; public class Bag extends java.util.ArrayList<String> {}",
    "t/Kid.java": "package t; public class Kid extends Base {}",
    "t/Main.java": """package t; public class Main { public static void main(String[] a) {
        Bag b = new Bag(); b.add("x"); b.size(); Kid k = new Kid(); k.own(); } }""",
}


def main() -> int:
    jar = build.build()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        files = []
        for name, text in SRC.items():
            p = td / "src" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            files.append(str(p))
        subprocess.run(["javac", "-d", str(td / "cls"), *files], check=True)
        out = td / "out"
        subprocess.run(["java", f"-javaagent:{jar}=out={out};prefix=t", "-cp", str(td / "cls"), "t.Main"], check=True)
        edges = set()
        for f in out.glob("edges.*.tsv"):
            edges |= {tuple(l.split("\t")[:2]) for l in f.read_text(encoding="utf-8").splitlines() if l}
    callees = {c for _, c in edges}
    failures = []
    for bad in ("t.Bag::add", "t.Bag::size"):
        if bad in callees:
            failures.append(f"library-declared method recorded on project type: {bad}")
    if "t.Base::own" not in callees:
        failures.append(f"project-declared inherited method not resolved to t.Base::own: {sorted(callees)}")
    for f in failures:
        print("FAIL", f)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
