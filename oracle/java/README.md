# oracle/java — call-site recording javaagent

Runtime answer key for Java repositories. Wired into `runner/run.py` as `build_oracle_java`
(`ORACLE_BUILDERS["java"]`). First subject: `spring-projects/spring-petclinic`.

## Mechanism

`-javaagent` (ASM 9.7.1, pinned by version and sha256 in `build.py`, shaded into the agent jar)
rewrites every *project* class as it loads. Before each invoke instruction whose **declared owner
is also a project class**, it inserts `Recorder.hit(siteId)`. At JVM shutdown the sites that were
reached are written as `caller<TAB>callee<TAB>op` lines (`edges.<pid>.tsv`) plus
`stats.<pid>.json` (classes instrumented / failed, sites, sites hit).

This is the same idea as the C# oracle (static caller, *declared* callee of the instruction),
gated by "the instruction actually ran", not a shadow stack like the TypeScript oracle. Consequences
worth knowing:

- An interface method that never gets a body in the project (Spring Data `OwnerRepository.save`)
  is still recorded: the call site names it. A shadow stack would have missed it.
- No try/finally, no stack-map changes, no thread-local state; one array store per executed site.
- Exceptions, threads and several test JVMs are fine (one file per JVM, merged by the runner).

Project classes = those whose name starts with a package in the entry's `java_packages` (main and
test classes alike). CGLIB/proxy classes (`$$` in the name) are never instrumented or counted.

## Key spelling (what `grader/grade.py` already parses; grade.py needed no change)

| Java | key |
|---|---|
| method `pkg.Type#m(...)` | `pkg.Type::m` (overloads collapse, as in C#) |
| nested class `pkg.Outer$Inner` | `pkg.Outer/Inner::m` |
| anonymous / local class `Outer$1` | `pkg.Outer/1::m` (kept as its own type; a source-level arm has to name it the same way) |
| constructor | `pkg.Type::.ctor` (includes the implicit `super()` call, like C# E7) |
| static initializer | `pkg.Type::.cctor` |
| lambda body `lambda$enclosing$N` | folded into the enclosing method: `pkg.Type::enclosing`; `lambda$new$N` -> `.ctor`, `lambda$static$N` -> `.cctor` |
| method reference `Foo::bar` | edge to `Foo::bar`, op `ldftn` |
| ops | `newobj` (`<init>`), `callvirt` (invokevirtual/interface), `call` (static/special) |

No key contains `<`, so nothing is dropped as an unattributable caller. `Op` values are all in
`COMPARABLE_OPS`. `CalleeAssembly` is the repo directory name (the single first-party module).
The callee is the owner javac wrote into the instruction, i.e. the receiver's *static* type, which
may be a subtype of where the method is declared (`OwnerRepository::save`, declared in
`CrudRepository`). There is no override map (`overrides.json` is `{}`).

Skipped on purpose: bridge and other synthetic methods (they would produce self-edges),
abstract/native methods, calls whose owner is a library class (those are the `external` cell
concept; nothing library-side is recorded, so an arm's library edges are simply not in the key).
Java getters are ordinary methods here; the grader's accessor cell is C# `get_`/`set_` only.

## Coverage, stated honestly

- **Only executed calls.** An edge exists only if a test reached that instruction. Recall is
  meaningful on the executed slice only; the runner therefore sets `oracle_coverage:
  executed-tests-only` and grades with `--executed-only` (arm edges whose caller never ran are
  withheld, not counted as false positives), exactly like TypeScript.
- **With-tests only.** A `without-tests` key does not exist (`CellNotMeasurable`).
- **Reflection and framework dispatch are not seen as calls.** Spring invoking a controller by
  reflection has no project call site, so there is no edge into the handler method from the
  framework (correctly so: no arm can see one either). A test calling it via MockMvc likewise
  produces no test->controller edge.
- A call guarded by a condition the tests never satisfy is absent. A call that threw before
  returning still counts (the hit is recorded before the instruction).
- Tests that need Docker (MySQL/Postgres Testcontainers) are skipped by the project itself; their
  code paths are not in the key.
- Generated proxies and AOT-generated sources (`$$`) are excluded.

## Failing loudly

The runner (`read_java_raw`) aborts the oracle build if: no stats file was written (agent never
ran), zero classes were instrumented (wrong `java_packages`), any class failed to instrument, or
zero edges were recorded. `oracle_identity.json` next to the key records the agent jar sha256, a
hash of all oracle sources + init scripts, subject HEAD vs. the pinned sha, a dirty-tracked-files
flag, Java version, test command and the agent's counters.

## corpus.json fields for a Java entry

- `language: "java"`, `sha`, `license`, `build_tool` (informational)
- `java_packages`: dotted package prefixes of the project's own code (required)
- `java_test_command`: run through the checkout's own wrapper; `{init_script}` expands to
  `test-agent.init.gradle` (adds the agent to every `Test` task via `-I`, forces rerun, ignores
  red tests). The checkout's tracked files are never edited.
- `java_classpath_command`: prints the main compile classpath on one line, using
  `classpath.init.gradle`. `run.java_classpath(entry, checkout)` runs it; the oracle also saves the
  result as `classpath.txt` (stage-2 arms pass it as `AA_JAVA_CLASSPATH`).

## Build / run by hand

    python oracle/java/build.py            # downloads ASM once into lib/ (gitignored), builds build/java-oracle-agent.jar
    python runner/run.py --checkouts corpus --only spring-petclinic --arms none --out D:/temp/out

The checkout lives in `corpus/spring-petclinic` (gitignored) at the pinned sha.
