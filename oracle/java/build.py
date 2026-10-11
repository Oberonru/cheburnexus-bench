#!/usr/bin/env python3
"""Build oracle/java/build/java-oracle-agent.jar (agent + shaded ASM) with plain javac + jar.

ASM is pinned by version AND sha256; a download that does not match is refused. Prints the sha256
of the finished jar on the last line so the runner can put it in the manifest.

    python oracle/java/build.py
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASM_VERSION = "9.7.1"
ASM_SHA256 = "8cadd43ac5eb6d09de05faecca38b917a040bb9139c7edeb4cc81c740b713281"
ASM_URL = f"https://repo1.maven.org/maven2/org/ow2/asm/asm/{ASM_VERSION}/asm-{ASM_VERSION}.jar"
LIB = HERE / "lib" / f"asm-{ASM_VERSION}.jar"
BUILD = HERE / "build"
JAR = BUILD / "java-oracle-agent.jar"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_asm() -> None:
    if not LIB.is_file():
        LIB.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(ASM_URL, LIB)
    if sha256(LIB) != ASM_SHA256:
        raise SystemExit(f"{LIB}: sha256 {sha256(LIB)} != pinned {ASM_SHA256}; refusing to build")


def build() -> Path:
    ensure_asm()
    sources = sorted((HERE / "src").rglob("*.java"))
    stamp = hashlib.sha256()
    for s in sources:
        stamp.update(s.read_bytes())
    stamp.update(ASM_SHA256.encode())
    marker = BUILD / "inputs.sha256"
    if JAR.is_file() and marker.is_file() and marker.read_text() == stamp.hexdigest():
        return JAR

    if BUILD.exists():
        shutil.rmtree(BUILD)
    classes = BUILD / "classes"
    classes.mkdir(parents=True)
    subprocess.run(["javac", "--release", "17", "-encoding", "UTF-8", "-cp", str(LIB), "-d", str(classes),
                    *map(str, sources)], check=True)
    with zipfile.ZipFile(LIB) as z:
        for name in z.namelist():
            if name.startswith("org/objectweb/asm/") and name.endswith(".class"):
                z.extract(name, classes)
    manifest = BUILD / "MANIFEST.MF"
    manifest.write_text("Premain-Class: oraclejava.Agent\nCan-Retransform-Classes: false\n", encoding="utf-8")
    subprocess.run(["jar", "--create", "--file", str(JAR), "--manifest", str(manifest), "-C", str(classes), "."],
                   check=True)
    marker.write_text(stamp.hexdigest())
    return JAR


if __name__ == "__main__":
    jar = build()
    print(f"built {jar}")
    print(sha256(jar))
