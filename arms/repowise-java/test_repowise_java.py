#!/usr/bin/env python3
"""Checks for the repowise-java key builder and the shared Java scanner (no repowise run needed).

    python3 arms/repowise-java/test_repowise_java.py
"""
import sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_lib"))
sys.path.insert(0, str(HERE))
import javascan  # noqa: E402
import run  # noqa: E402

SRC = '''package a.b;
/* class Fake { } */
public class Outer {
    String s = "class Nope {";
    public Outer() { }
    void m() { }
    static class Inner { void go() { } }
    interface Spec { void x(); }
}
class Other { void m() { } }
'''


def main():
    bad = []
    clean = javascan.strip_java(SRC)
    if "Fake" in clean or "Nope" in clean:
        bad.append("comment or string content survived strip_java")
    if clean.count("\n") != SRC.count("\n"):
        bad.append("strip_java moved line numbers")
    pkg, types = javascan.declared_types(clean)
    want = {"Outer": ["a.b.Outer"], "Inner": ["a.b.Outer/Inner"], "Spec": ["a.b.Outer/Spec"], "Other": ["a.b.Other"]}
    if pkg != "a.b" or types != want:
        bad.append(f"declared_types: {pkg} {types}")
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "Outer.java").write_text(SRC, encoding="utf-8")
        kb = run.KeyBuilder(Path(t))
        cases = [("Outer.java::Outer::Outer", "Outer.java", "Outer", "a.b.Outer::.ctor"),
                 ("Outer.java::Outer::m", "Outer.java", "m", "a.b.Outer::m"),
                 ("Outer.java::Inner::go", "Outer.java", "go", "a.b.Outer/Inner::go")]
        for nid, f, n, exp in cases:
            got = kb.build(nid, f, n)
            if got != exp:
                bad.append(f"{nid}: got {got}, want {exp}")
        if kb.build("Outer.java::Missing::m", "Outer.java", "m") is not None or \
                kb.drops["class_not_declared_in_file"] != 1:
            bad.append("an undeclared class must be a counted drop")
    if bad:
        print("FAILED:\n  - " + "\n  - ".join(bad)); return 1
    print("ok"); return 0


if __name__ == "__main__":
    sys.exit(main())
