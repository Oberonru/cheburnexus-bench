namespace Acme.Widgets
{
    // Generic declaring type: the key must carry arity (`Cache`1`), computed from this
    // declaration's own `<T>` list — never from a use site like `new Cache<int>()`.
    public class Cache<T>
    {
        public T Get(int key)
        {
            return default;
        }

        public T GetTwice(int key)
        {
            var value = Get(key);
            return value;
        }
    }
}
