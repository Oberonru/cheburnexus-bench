package oraclejava;

import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
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
    private static final List<String> CALLEE = new ArrayList<>();
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

    static synchronized int register(String caller, String callee, String op) {
        if (CALLER.size() >= MAX_SITES) {
            throw new IllegalStateException("java oracle: more than " + MAX_SITES + " call sites");
        }
        CALLER.add(caller);
        CALLEE.add(callee);
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
                    edges.add(CALLER.get(i) + "\t" + CALLEE.get(i) + "\t" + OP.get(i));
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
