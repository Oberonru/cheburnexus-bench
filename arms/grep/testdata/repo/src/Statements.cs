namespace Acme.Widgets
{
    public class Router
    {
        public string Dispatch(string key)
        {
            return Resolve(key);
        }

        public string Resolve(string key)
        {
            return key;
        }
    }
}
