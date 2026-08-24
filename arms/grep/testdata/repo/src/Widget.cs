namespace Acme.Widgets
{
    public class Widget
    {
        public Widget(int x)
        {
        }

        public void Run()
        {
            Helper();
            var w = new Widget(1);
        }

        public void Helper()
        {
        }
    }

    public class Gadget
    {
        // Same method name as Widget.Helper — grep cannot tell them apart, so a call to Helper()
        // must produce a candidate edge to BOTH declarations.
        public void Helper()
        {
        }
    }
}
