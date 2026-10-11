#!/usr/bin/env python3
"""Tests for the cheburnexus-java arm's key conversion and refusals. No engine run is needed.

    python3 arms/cheburnexus-java/test_cheburnexus_java.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "arms" / "_lib"))
sys.path.insert(0, str(HERE))

import run as arm  # noqa: E402

NS = "org.example.app"
ARCH = {"Files": [
    {"Namespace": NS, "Types": [
        {"fqn": f"{NS}.Owner", "Name": "Owner", "Methods": [
            {"Name": "Owner", "IsConstructor": True}, {"Name": "name", "IsConstructor": False}]},
        {"fqn": f"{NS}.Outer.Inner", "Name": "Inner", "Methods": []},
        {"fqn": f"{NS}.Outer.Inner.Deep", "Name": "Deep", "Methods": []},
        {"fqn": f"{NS}.Repo", "Name": "Repo", "Methods": []},
    ]},
]}


def _tr() -> arm.Translator:
    return arm.Translator(arm.TypeIndex(ARCH))


def check_type_spelling() -> list[str]:
    t = _tr()
    cases = [
        (f"/r/A.java::{NS}.Repo::find(Integer)", f"{NS}.Repo::find", "plain method, params dropped"),
        (f"/r/A.java::{NS}.Repo::find(Integer,String)", f"{NS}.Repo::find", "overloads collapse"),
        (f"/r/A.java::{NS}.Outer.Inner::m()", f"{NS}.Outer/Inner::m", "nested type uses '/'"),
        (f"/r/A.java::{NS}.Outer.Inner.Deep::m()", f"{NS}.Outer/Inner/Deep::m", "two levels of nesting"),
    ]
    out = []
    for raw, want, why in cases:
        got = t.translate(raw)
        if got != want:
            out.append(f"translate({raw!r}) = {got!r}, expected {want!r} -- {why}")
    return out


def check_constructors() -> list[str]:
    t = _tr()
    cases = [
        (f"/r/A.java::{NS}.Owner::<init>", f"{NS}.Owner::.ctor", "default constructor"),
        (f"/r/A.java::{NS}.Owner::Owner(String)", f"{NS}.Owner::.ctor", "explicit constructor"),
        (f"/r/A.java::{NS}.Outer.Inner::<clinit>", f"{NS}.Outer/Inner::.cctor", "static initializer"),
        (f"/r/A.java::{NS}.Repo::Repo()", f"{NS}.Repo::Repo",
         "a method named like its type that is NOT a declared constructor stays a method"),
    ]
    out = []
    for raw, want, why in cases:
        got = t.translate(raw)
        if got != want:
            out.append(f"translate({raw!r}) = {got!r}, expected {want!r} -- {why}")
    return out


def check_unknown_type_is_counted_not_guessed() -> list[str]:
    t = _tr()
    out = []
    if t.translate("/r/A.java::org.other.Ghost::m()") is not None:
        out.append("a type absent from architecture.json must be dropped, not guessed")
    if t.translate("garbage") is not None:
        out.append("an unparseable raw key must be dropped")
    if t.drops != {"unparseable-raw-key": 1, "type-not-in-architecture-json": 1}:
        out.append(f"drops not counted per reason: {t.drops}")
    return out


def check_declaring_owner_is_not_rewritten() -> list[str]:
    """The oracle names the instruction owner; the engine names the declaring type. The arm must
    keep what the engine said (see header), not 'repair' it."""
    root = Path(tempfile.gettempdir()).resolve() / "repo"
    f = (root / "A.java").as_posix()
    calls = {f"{f}::{NS}.Repo::run()": {"Calls": [
        {"Target": f"{f}::{NS}.Owner::name()", "Line": 7, "Kind": "instance"}]}}
    edges = list(arm._edges_from_calls(calls, _tr(), root))
    if [(e.caller, e.callee) for e in edges] != [(f"{NS}.Repo::run", f"{NS}.Owner::name")]:
        return [f"unexpected edges {edges}"]
    if edges[0].caller_file != "A.java" or edges[0].caller_line != 7:
        return [f"caller_file/line wrong: {edges[0]}"]
    return []


def check_package_filter() -> list[str]:
    ok = arm._in_package(f"{NS}.Repo::m", (NS,)) and not arm._in_package("org.example.apple.X::m", (NS,))
    return [] if ok else ["package prefix must match on a '.' boundary only"]


def check_stale_jar_refused() -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        eng = Path(d)
        (eng / "src").mkdir()
        (eng / "build" / "libs").mkdir(parents=True)
        jar = eng / "build" / "libs" / "java-engine-jdt.jar"
        jar.write_bytes(b"x")
        src = eng / "src" / "A.java"
        src.write_text("class A {}")
        now = time.time()
        os.utime(jar, (now - 100, now - 100))
        os.utime(src, (now - 200, now - 200))
        out = []
        if arm._stale_build_reason(eng) is not None:
            out.append("fresh jar (newer than every source) was reported stale")
        os.utime(src, (now, now))
        reason = arm._stale_build_reason(eng)
        if reason is None or "STALE" not in reason:
            out.append("a source newer than the jar must be refused as stale")
        return out


def check_missing_engine_and_classpath_refuse() -> list[str]:
    out = []
    saved = {k: os.environ.pop(k, None) for k in (arm.ENGINE_ENV, arm.CLASSPATH_ENV)}
    try:
        try:
            arm.collect(Path("."), "with-tests")
            out.append("no engine configured must raise, not return empty edges")
        except RuntimeError as exc:
            if arm.ENGINE_ENV not in str(exc):
                out.append(f"engine refusal does not name {arm.ENGINE_ENV}: {exc}")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "build" / "libs").mkdir(parents=True)
            (Path(d) / "build" / "libs" / "java-engine-jdt.jar").write_bytes(b"x")
            os.environ[arm.ENGINE_ENV] = d
            try:
                arm.collect(Path("."), "with-tests")
                out.append("missing classpath must raise")
            except RuntimeError as exc:
                if arm.CLASSPATH_ENV not in str(exc):
                    out.append(f"classpath refusal does not name {arm.CLASSPATH_ENV}: {exc}")
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return out


CHECKS = [check_type_spelling, check_constructors, check_unknown_type_is_counted_not_guessed,
          check_declaring_owner_is_not_rewritten, check_package_filter, check_stale_jar_refused,
          check_missing_engine_and_classpath_refuse]


def check_java_selection() -> list[str]:
    out = []
    saved = os.environ.pop(arm.JAVA_ENV, None)
    real = arm.corpus_entry
    try:
        fake_home = Path(tempfile.mkdtemp()) / "jdk"
        (fake_home / "bin").mkdir(parents=True)
        (fake_home / "bin" / "java").write_bytes(b"")
        (fake_home / "bin" / "java.exe").write_bytes(b"")
        arm.corpus_entry = lambda root: {"java_home": str(fake_home)}
        got = arm.java_executable(Path("/x/proj"))
        if Path(got).parent != fake_home / "bin":
            out.append(f"java_home not used: {got!r}")
        os.environ[arm.JAVA_ENV] = "/explicit/java"
        if arm.java_executable(Path("/x/proj")) != "/explicit/java":
            out.append("CHEBURNEXUS_JAVA must win over java_home")
        del os.environ[arm.JAVA_ENV]
        arm.corpus_entry = lambda root: {}
        if arm.java_executable(Path("/x/proj")) != "java":
            out.append("no java_home must give plain `java`")
    finally:
        arm.corpus_entry = real
        os.environ.pop(arm.JAVA_ENV, None)
        if saved is not None:
            os.environ[arm.JAVA_ENV] = saved
    return out


CHECKS.append(check_java_selection)


def check_java_home_validation_and_missing_entry() -> list[str]:
    import contextlib
    import io

    out = []
    saved = os.environ.pop(arm.JAVA_ENV, None)
    real = arm.corpus_entry
    try:
        arm.corpus_entry = lambda root: {"java_home": "/definitely/not/a/jdk"}
        try:
            arm.java_executable(Path("/x/proj"))
            out.append("nonexistent java_home must stop with SystemExit")
        except SystemExit as exc:
            if "java_home" not in str(exc):
                out.append(f"unclear message: {exc}")
        # the real corpus_entry: an unknown folder warns on stderr and falls back to `java`
        arm.corpus_entry = real
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = arm.java_executable(Path("/x/no-such-corpus-folder"))
        if got != "java" or "no-such-corpus-folder" not in err.getvalue():
            out.append(f"unknown folder: want `java` + stderr warning, got {got!r} / {err.getvalue()!r}")
    finally:
        arm.corpus_entry = real
        if saved is not None:
            os.environ[arm.JAVA_ENV] = saved
    return out


CHECKS.append(check_java_home_validation_and_missing_entry)


def main() -> int:
    failures = []
    for check in CHECKS:
        failures += [f"{check.__name__}: {f}" for f in check()]
    for f in failures:
        print("FAIL", f)
    print(f"{len(CHECKS)} checks, {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
