package oraclejava;

import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Runtime side of the oracle. Instrumented call sites call {@link #hit(int)}; at JVM shutdown every
 * site that was reached is written out as one edge. A fixed-size flag table (no locking, no
 * boxing) keeps the per-call cost to one array store.
 */
public final class Recorder {
    /** Hard cap on call sites. Exceeding it fails the transform loudly instead of dropping sites. */
    static final int MAX_SITES = 4_000_000;

    private static final boolean[] HIT = new boolean[MAX_SITES];
    private static final List<String> CALLER = new ArrayList<>();
    private static final List<String> OWNER = new ArrayList<>();
    private static final List<String> NAME = new ArrayList<>();
    private static final List<String> DESC = new ArrayList<>();
    private static final List<String> OP = new ArrayList<>();

    static volatile int classesInstrumented;
    static volatile int classesFailed;
    static volatile int classesSkippedGenerated;
    static volatile Path outDir;

    private Recorder() {
    }

    /** Called from instrumented bytecode, immediately before the call instruction executes. */
    public static void hit(int site) {
        HIT[site] = true;
    }

    /** Per project class: declared non-synthetic methods (name + descriptor), superclass and interfaces. */
    private static final Map<String, Set<String>> DECLARED = new ConcurrentHashMap<>();
    private static final Map<String, List<String>> SUPERS = new ConcurrentHashMap<>();

    /** Remembers what a project class declares and extends, so callees can be moved to the declaring type. */
    static void noteClass(ClassReader reader) {
        final Set<String> declared = new HashSet<>();
        reader.accept(new ClassVisitor(Opcodes.ASM9) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String desc, String sig, String[] ex) {
                if ((access & Opcodes.ACC_SYNTHETIC) == 0) {
                    declared.add(name + desc);
                }
                return null;
            }
        }, ClassReader.SKIP_CODE | ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
        List<String> supers = new ArrayList<>();
        if (reader.getSuperName() != null) {
            supers.add(reader.getSuperName());
        }
        supers.addAll(Arrays.asList(reader.getInterfaces()));
        DECLARED.put(reader.getClassName(), declared);
        SUPERS.put(reader.getClassName(), supers);
    }

    /**
     * Declaring type, the C# convention: the first project type, walking the superclass chain and
     * then the superinterfaces (depth first, in declaration order), that declares name+descriptor.
     * Constructors are not inherited, so they keep their owner. If no project type declares the
     * method (Spring Data repository methods, Object::toString, library superclasses) the owner
     * written into the instruction is kept.
     */
    static String declaringOwner(String owner, String name, String desc) {
        if (name.equals("<init>")) {
            return owner;
        }
        String found = find(owner, name + desc, new HashSet<>());
        return found != null ? found : owner;
    }

    private static String find(String type, String key, Set<String> seen) {
        Set<String> decl = DECLARED.get(type);
        if (decl == null || !seen.add(type)) {
            return null;
        }
        if (decl.contains(key)) {
            return type;
        }
        for (String sup : SUPERS.get(type)) {
            String r = find(sup, key, seen);
            if (r != null) {
                return r;
            }
        }
        return null;
    }

    static synchronized int register(String caller, String owner, String name, String desc, String op) {
        if (CALLER.size() >= MAX_SITES) {
            throw new IllegalStateException("java oracle: more than " + MAX_SITES + " call sites");
        }
        CALLER.add(caller);
        OWNER.add(owner);
        NAME.add(name);
        DESC.add(desc);
        OP.add(op);
        return CALLER.size() - 1;
    }

    static void install(Path dir) {
        outDir = dir;
        Runtime.getRuntime().addShutdownHook(new Thread(Recorder::dump, "java-oracle-dump"));
    }

    static synchronized void dump() {
        try {
            Files.createDirectories(outDir);
            long pid = ProcessHandle.current().pid();
            Set<String> edges = new LinkedHashSet<>();
            int sitesHit = 0;
            for (int i = 0; i < CALLER.size(); i++) {
                if (HIT[i]) {
                    sitesHit++;
                    String callee = Agent.typeKey(declaringOwner(OWNER.get(i), NAME.get(i), DESC.get(i)))
                            + "::" + Agent.methodKey(NAME.get(i));
                    edges.add(CALLER.get(i) + "\t" + callee + "\t" + OP.get(i));
                }
            }
            Path edgeFile = outDir.resolve("edges." + pid + ".tsv");
            try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(edgeFile, StandardCharsets.UTF_8))) {
                for (String e : edges) {
                    w.println(e);
                }
            }
            Path stats = outDir.resolve("stats." + pid + ".json");
            Files.writeString(stats, "{\"classes_instrumented\":" + classesInstrumented
                    + ",\"classes_failed\":" + classesFailed
                    + ",\"classes_skipped_generated\":" + classesSkippedGenerated
                    + ",\"sites\":" + CALLER.size()
                    + ",\"sites_hit\":" + sitesHit
                    + ",\"unique_edges\":" + edges.size() + "}\n", StandardCharsets.UTF_8);
        } catch (IOException | RuntimeException e) {
            // A failed dump must be visible: the runner treats a missing stats file as fatal.
            System.err.println("JAVA-ORACLE: dump failed: " + e);
        }
    }

    static Path pathOf(String s) {
        return Paths.get(s).toAbsolutePath();
    }
}
