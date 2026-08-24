// A C# file whose call graph is known by construction, so the answer key can be checked against
// something other than itself. Every construct here exists because it produces IL that differs from
// what the source looks like — which is the whole reason the oracle reads IL rather than text.
//
// Lines are located in the test by the `// MARK:` comments, never by number, so editing this file
// cannot silently break the assertions.

using System.Collections;

namespace OracleFixture;

public interface ISink
{
    void Emit(string message);
    string Name { get; }
}

public abstract class SinkBase : ISink
{
    public abstract void Emit(string message);
    public virtual string Name => "base";           // MARK: base-name-property
}

public sealed class FileSink : SinkBase
{
    public override void Emit(string message)       // MARK: filesink-emit-decl
    {
        Store(message);                             // MARK: plain-call
    }

    public override string Name => "file";

    private void Store(string message)
    {
    }
}

/// <summary>Explicit interface implementation: IL names it `OracleFixture.ISink.Emit`.</summary>
public sealed class NullSink : ISink
{
    void ISink.Emit(string message)                 // MARK: explicit-impl-decl
    {
    }

    string ISink.Name => "null";
}

public sealed class Pipeline
{
    private static readonly string Prefix = Build(); // MARK: static-field-init

    private readonly List<ISink> _sinks = new();

    public Pipeline()
    {
        _sinks.Add(new FileSink());                 // MARK: ctor-call
    }

    private static string Build() => "p";

    /// <summary>Interface dispatch: IL emits callvirt against the DECLARED ISink.Emit.</summary>
    public void Broadcast(string message)
    {
        foreach (var sink in _sinks)                // MARK: foreach-loop
        {
            sink.Emit(message);                     // MARK: interface-dispatch
        }
    }

    /// <summary>Property read: IL emits a call to get_Name, which no source reader calls a call.</summary>
    public string FirstName()
    {
        return _sinks[0].Name;                      // MARK: property-read
    }

    /// <summary>A call written inside a lambda: IL attributes it to a generated closure type.</summary>
    public void EachLambda()
    {
        _sinks.ForEach(sink => sink.Emit("x"));     // MARK: lambda-call
    }

    /// <summary>A call inside an async method: IL attributes it to a state machine's MoveNext.</summary>
    public async Task DrainAsync()
    {
        await Task.Yield();
        Broadcast("async");                         // MARK: async-call
    }

    /// <summary>Generic method instantiation: IL names the closed form, source names one method.</summary>
    public void UseGeneric()
    {
        Wrap<FileSink>(new FileSink());             // MARK: generic-call
    }

    private static void Wrap<T>(T value) where T : ISink
    {
    }

    /// <summary>A local function — a caller the compiler moves out of the method a human wrote.</summary>
    public void WithLocal()
    {
        Helper();                                   // MARK: local-fn-call

        void Helper() => Broadcast("local");        // MARK: local-fn-body
    }
}

/// <summary>Iterator: the whole body moves into a generated state machine type.</summary>
public sealed class Numbers : IEnumerable
{
    public IEnumerator GetEnumerator()
    {
        yield return 1;
    }
}
