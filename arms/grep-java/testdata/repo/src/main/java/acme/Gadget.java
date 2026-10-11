package acme;

interface Spec {
    void helper(int n);
}

public class Gadget implements Spec {
    @Override
    public void helper(int n) {
        throw new IllegalStateException("x");
    }
}
