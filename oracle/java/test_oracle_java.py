#!/usr/bin/env python3
"""Runs the Java oracle agent on a tiny project: a call to a method declared only in a library
type, made through a project type, must NOT be recorded (C# rule: declaring type is external);
a method declared in a project supertype resolves to that supertype.

    python3 oracle/java/test_oracle_java.py
"""
from __future__ import annotations

import json
import os
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
    # first_failure goes into stats JSON by hand-written escaping: quote, backslash and control
    # characters must still give valid JSON, and null must stay null.
    nasty = "a/B: " + chr(34) + "q" + chr(34) + " b" + chr(92) + "s t" + chr(9) + "h" + chr(1) + "e"
    with tempfile.TemporaryDirectory() as pd:
        pd = Path(pd)
        probe = pd / "src" / "oraclejava" / "Probe.java"
        probe.parent.mkdir(parents=True)
        probe.write_text(
            "package oraclejava; public class Probe { public static void main(String[] a) {"
            " System.out.println(Recorder.jsonString(a.length == 0 ? null : a[0])); } }", encoding="utf-8")
        subprocess.run(["javac", "-cp", str(jar), "-d", str(pd / "cls"), str(probe)], check=True)
        for text in (None, nasty):
            cmd = ["java", "-cp", f"{pd / 'cls'}{os.pathsep}{jar}", "oraclejava.Probe"] + ([text] if text is not None else [])
            got = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
            try:
                back = json.loads(got)
            except ValueError:
                failures.append(f"jsonString({text!r}) is not valid JSON: {got!r}")
                continue
            want = None if text is None else "".join(" " if ord(c) < 32 else c for c in text)
            if back != want:
                failures.append(f"jsonString round trip: {back!r} != {want!r}")
    for f in failures:
        print("FAIL", f)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
