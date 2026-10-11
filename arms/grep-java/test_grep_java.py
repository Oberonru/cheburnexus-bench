#!/usr/bin/env python3
"""Checks for the grep-java arm on testdata/repo (python3 arms/grep-java/test_grep_java.py)."""
import json, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO, RUN = HERE / "testdata" / "repo", HERE / "run.py"


def run_arm(cell):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "e.jsonl"
        r = subprocess.run([sys.executable, str(RUN), "--repo", str(REPO), "--cell", cell, "--out", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    bad = []
    w = run_arm("with-tests")
    wo = run_arm("without-tests")
    pairs = {(e["caller"], e["callee"]) for e in w}

    def need(c, d):
        if (c, d) not in pairs:
            bad.append(f"missing {c} -> {d}")

    need("acme.Widget::.ctor", "acme.Widget::helper")          # ctor declared, call attributed
    need("acme.Widget::run", "acme.Widget::.ctor")            # new Widget(1) in a multi-line decl
    need("acme.Widget::run", "acme.Widget::helper")           # lambda body folds into run
    need("acme.Widget::.cctor", "acme.Widget::init")          # static block
    need("acme.Widget/Inner::go", "acme.Gadget::.ctor")       # nested type spelling
    need("acme.Widget/Inner::go", "acme.Gadget::helper")
    need("acme.Widget/Inner::go", "acme.Spec::helper")        # fan-out to every same-named decl
    if any(e["caller"].endswith("::helper") and e["caller_line"] in (5, 7, 21) for e in w):
        bad.append("a comment/string/text block was read as a call")
    if any(e["callee"] == "acme.Gadget::IllegalStateException" for e in w) or \
            any("IllegalStateException" in e["callee"] for e in w):
        bad.append("library constructor became an edge")
    if not any(e["caller_file"].startswith("src/test/") for e in w):
        bad.append("with-tests has no test-file edge")
    if any(e["caller_file"].startswith("src/test/") for e in wo):
        bad.append("without-tests leaked the test file")
    if bad:
        print("FAILED:\n  - " + "\n  - ".join(bad)); return 1
    print(f"ok - {len(wo)} edges without-tests, {len(w)} with-tests"); return 0


if __name__ == "__main__":
    sys.exit(main())
