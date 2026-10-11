package oraclejava;

import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.List;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Handle;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

/**
 * Call-site recording javaagent.
 *
 * Agent arguments: {@code out=<dir>;prefix=<pkg.a>,<pkg.b>} (prefix = dotted package prefixes of
 * the project's own classes, main and test alike).
 *
 * Mechanism: for every project method with a body, each invoke instruction whose declared owner is
 * also a project class is preceded by {@code Recorder.hit(siteId)}. The edge is the static
 * (caller method, declared callee) pair of the instruction, like the IL oracle; it is only emitted
 * if the instruction was actually reached. Nothing is wrapped in try/finally, so no stack-map
 * frames change.
 */
public final class Agent {
    private Agent() {
    }

    public static void premain(String args, Instrumentation inst) {
        String out = null;
        List<String> prefixes = new ArrayList<>();
        if (args != null) {
            for (String part : args.split(";")) {
                int eq = part.indexOf('=');
                if (eq < 0) {
                    continue;
                }
                String k = part.substring(0, eq).trim();
                String v = part.substring(eq + 1).trim();
                if (k.equals("out")) {
                    out = v;
                } else if (k.equals("prefix")) {
                    for (String p : v.split(",")) {
                        if (!p.isBlank()) {
                            prefixes.add(p.trim().replace('.', '/') + "/");
                        }
                    }
                }
            }
        }
        if (out == null || prefixes.isEmpty()) {
            throw new IllegalArgumentException("java oracle agent needs out=<dir>;prefix=<pkg>[,<pkg>]");
        }
        Recorder.install(Recorder.pathOf(out));
        inst.addTransformer(new Transformer(prefixes), false);
    }

    static boolean isProject(String internalName, List<String> prefixes) {
        if (internalName == null || internalName.isEmpty() || internalName.charAt(0) == '['
                || internalName.contains("$$")) {
            return false;
        }
        for (String p : prefixes) {
            if (internalName.startsWith(p)) {
                return true;
            }
        }
        return false;
    }

    /** `org/x/Outer$Inner` becomes `org.x.Outer/Inner` (the C# `Ns.Outer/Inner` nesting spelling). */
    static String typeKey(String internalName) {
        int slash = internalName.lastIndexOf('/');
        String pkg = slash < 0 ? "" : internalName.substring(0, slash).replace('/', '.') + ".";
        String simple = internalName.substring(slash + 1).replace('$', '/');
        return pkg + simple;
    }

    /**
     * Method spelling. Constructors keep the C# `.ctor` / `.cctor` names (the grader treats them as
     * distinct methods and they contain no angle bracket). A javac lambda body `lambda$enclosing$N`
     * is mapped to the human-written enclosing method, the same remap the grader applies to
     * Roslyn's mangled lambda names.
     */
    static String methodKey(String name) {
        if (name.equals("<init>")) {
            return ".ctor";
        }
        if (name.equals("<clinit>")) {
            return ".cctor";
        }
        if (name.startsWith("lambda$")) {
            String rest = name.substring("lambda$".length());
            int last = rest.lastIndexOf('$');
            String enclosing = last > 0 ? rest.substring(0, last) : rest;
            if (enclosing.equals("new")) {
                return ".ctor";
            }
            if (enclosing.equals("static")) {
                return ".cctor";
            }
            return enclosing;
        }
        return name;
    }

    static final class Transformer implements ClassFileTransformer {
        private final List<String> prefixes;

        Transformer(List<String> prefixes) {
            this.prefixes = prefixes;
        }

        @Override
        public byte[] transform(ClassLoader loader, String className, Class<?> redefined,
                                ProtectionDomain pd, byte[] bytes) {
            if (className == null) {
                return null;
            }
            if (!isProject(className, prefixes)) {
                if (className.contains("$$")) {
                    for (String p : prefixes) {
                        if (className.startsWith(p)) {
                            Recorder.classesSkippedGenerated++;
                        }
                    }
                }
                return null;
            }
            try {
                ClassReader reader = new ClassReader(bytes);
                ClassWriter writer = new ClassWriter(reader, ClassWriter.COMPUTE_MAXS);
                reader.accept(new ClassVisitor(Opcodes.ASM9, writer) {
                    @Override
                    public MethodVisitor visitMethod(int access, String name, String desc, String sig, String[] ex) {
                        MethodVisitor mv = super.visitMethod(access, name, desc, sig, ex);
                        boolean lambda = name.startsWith("lambda$");
                        boolean synthetic = (access & (Opcodes.ACC_SYNTHETIC | Opcodes.ACC_BRIDGE)) != 0;
                        if (mv == null || (access & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) != 0
                                || (synthetic && !lambda)) {
                            return mv;
                        }
                        String caller = typeKey(className) + "::" + methodKey(name);
                        return new MethodVisitor(Opcodes.ASM9, mv) {
                            private void site(String callee, String op) {
                                int id = Recorder.register(caller, callee, op);
                                super.visitLdcInsn(id);
                                super.visitMethodInsn(Opcodes.INVOKESTATIC, "oraclejava/Recorder", "hit", "(I)V", false);
                            }

                            @Override
                            public void visitMethodInsn(int op, String owner, String n, String d, boolean itf) {
                                if (isProject(owner, prefixes)) {
                                    String kind = n.equals("<init>") ? "newobj"
                                            : (op == Opcodes.INVOKEVIRTUAL || op == Opcodes.INVOKEINTERFACE) ? "callvirt" : "call";
                                    site(typeKey(owner) + "::" + methodKey(n), kind);
                                }
                                super.visitMethodInsn(op, owner, n, d, itf);
                            }

                            @Override
                            public void visitInvokeDynamicInsn(String n, String d, Handle bsm, Object... args) {
                                if (bsm.getOwner().equals("java/lang/invoke/LambdaMetafactory") && args.length > 1
                                        && args[1] instanceof Handle) {
                                    Handle impl = (Handle) args[1];
                                    if (isProject(impl.getOwner(), prefixes) && !impl.getName().startsWith("lambda$")) {
                                        site(typeKey(impl.getOwner()) + "::" + methodKey(impl.getName()), "ldftn");
                                    }
                                }
                                super.visitInvokeDynamicInsn(n, d, bsm, args);
                            }
                        };
                    }
                }, 0);
                Recorder.classesInstrumented++;
                return writer.toByteArray();
            } catch (Throwable t) {
                Recorder.classesFailed++;
                System.err.println("JAVA-ORACLE: failed to instrument " + className + ": " + t);
                return null;
            }
        }
    }
}
