#!/usr/bin/env python3
"""Build the unitystation corpus entry: the Unity project's own scripts, compiled outside the Editor.

    python corpus-patches/unitystation/build.py corpus/unitystation

unitystation keeps its .csproj files out of git (Unity writes them on demand), so a plain checkout
has nothing `dotnet build` can open. This script puts a pinned set of them next to the project and
builds each one. The set lists source files only; no unitystation code lives in this repository.

Two things must exist on the machine, and the script stops with a clear message if they do not:

  UNITY_EDITOR_PATH  folder of the installed Unity 6000.2.10f1 (the one that contains `Editor/Data`),
                     e.g. C:\\Program Files\\Unity\\Hub\\Editor\\6000.2.10f1. UnityEngine.dll and the
                     other engine assemblies are referenced from there.
  UNITY_LIBRARY_DIR  the `Library` folder of an imported copy of this same commit (defaults to
                     <checkout>/UnityProject/Library). It holds PackageCache (Mirror and the other
                     git packages) and ScriptAssemblies. Open the project once in the Editor to
                     create it, or point this at another imported copy of the same sha.

`-p:DebugType=portable` is not optional: the csproj files say `full`, which writes a Windows PDB
that the oracle (Mono.Cecil, cross-platform reader) cannot read.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBJECT = "UnityProject"


def fail(message: str) -> "None":
    print(f"build.py: {message}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("checkout", type=Path, help="the unitystation checkout (repository root)")
    args = parser.parse_args()

    project = args.checkout.resolve() / SUBJECT
    if not (project / "Assets").is_dir():
        fail(f"{project} has no Assets folder; is this a unitystation checkout?")

    editor = os.environ.get("UNITY_EDITOR_PATH", "")
    if not editor or not (Path(editor) / "Editor" / "Data" / "Managed").is_dir():
        fail("set UNITY_EDITOR_PATH to the install folder of Unity 6000.2.10f1 "
             "(the folder that contains Editor/Data/Managed)")
    library = Path(os.environ.get("UNITY_LIBRARY_DIR") or project / "Library")
    if not (library / "ScriptAssemblies").is_dir() or not (library / "PackageCache").is_dir():
        fail(f"{library} has no ScriptAssemblies/PackageCache; open the project once in the Unity "
             f"Editor, or set UNITY_LIBRARY_DIR to an imported copy of the same commit")

    shutil.copy2(HERE / "Directory.Build.props", project / "Directory.Build.props")
    projects = sorted((HERE / "csproj").glob("*.csproj"))
    for csproj in projects:
        shutil.copy2(csproj, project / csproj.name)

    env = {**os.environ, "UNITY_EDITOR_PATH": editor, "UNITY_LIBRARY_DIR": str(library)}
    started = time.monotonic()
    failed: list[str] = []
    for csproj in projects:
        result = subprocess.run(
            ["dotnet", "build", str(project / csproj.name), "--no-restore",
             "-p:DebugType=portable", "-v:q", "-nologo"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        name = csproj.stem
        built = (project / "Temp" / "Bin" / "Debug" / name / f"{name}.dll").is_file()
        if result.returncode != 0 or not built:
            failed.append(name)
            print(f"FAILED {name}\n{result.stdout[-1500:]}", file=sys.stderr)
    print(f"built {len(projects) - len(failed)} of {len(projects)} projects "
          f"in {time.monotonic() - started:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
