package acme;

import java.util.List;

/** Calls in comments like helper(1) must not count. */
public class Widget {
    private final String text = "helper(2) in a string";

    static {
        init();
    }

    public Widget(int n) {
        helper(n);
    }

    public <T> List<T> run(List<T> in,
                           int n) throws Exception {
        String block = """
            helper(3)
            """;
        Widget w = new Widget(1);
        Runnable r = () -> helper(4);
        return in;
    }

    static void init() {
    }

    int helper(int n) {
        return n;
    }

    public static class Inner {
        public void go() {
            new Gadget().helper(5);
        }
    }
}
