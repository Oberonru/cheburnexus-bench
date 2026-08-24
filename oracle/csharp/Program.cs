// The C# answer key for the polygon.
//
// It does not parse a single line of source. It reads what the compiler already produced — the IL in
// the built assemblies — and maps each call site back to a source line through the PDB. That is the
// whole point: the answer key must come from a level none of the measured arms operate on, and any
// reader with the .NET SDK must be able to regenerate it from the same pinned commit.
//
// Nothing is silently dropped. Every call edge found in IL is emitted, and the judgement calls
// (compiler-generated code, virtual dispatch, missing debug info) ride along as FLAGS on the row.
// The grader applies the documented filter; this tool only reports. A filter hidden inside the
// oracle would be a thumb on the scale nobody could see.

using System.Globalization;
using System.Text;
using System.Text.Json;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Cheburnexus.Bench.Oracle;

/// <summary>One call edge as the compiler emitted it, before any grading rule is applied.</summary>
internal sealed record EdgeRow
{
    /// <summary>Declaring type + method of the call site, in Cecil's full-name form.</summary>
    public required string Caller { get; init; }

    /// <summary>Source file of the call site, repo-relative when it could be made so.</summary>
    public string? CallerFile { get; init; }

    /// <summary>1-based line of the call instruction, from the nearest preceding sequence point.</summary>
    public int? CallerLine { get; init; }

    /// <summary>The method the IL names as the target. For callvirt this is the DECLARED method.</summary>
    public required string Callee { get; init; }

    /// <summary>Assembly the callee is declared in — lets the grader scope to first-party edges.</summary>
    public required string CalleeAssembly { get; init; }

    /// <summary>call / callvirt / newobj / ldftn / calli.</summary>
    public required string Op { get; init; }

    /// <summary>
    /// True when the IL target is virtual or an interface method, i.e. the runtime callee may differ.
    /// Arms that resolve to an implementation must be reduced to the declaration before comparison.
    /// </summary>
    public required bool VirtualDispatch { get; init; }

    /// <summary>Call site sits in compiler-generated code (async state machine, lambda, iterator, record member).</summary>
    public required bool CallerCompilerGenerated { get; init; }

    /// <summary>Target is compiler-generated.</summary>
    public required bool CalleeCompilerGenerated { get; init; }

    /// <summary>No sequence point covered the instruction — the edge exists but has no source anchor.</summary>
    public required bool NoDebugInfo { get; init; }
}

internal static class Program
{
    private const int HiddenLine = 0xfeefee; // Cecil's marker for a hidden sequence point.

    private static int Main(string[] args)
    {
        string? outPath = null;
        string? overridesPath = null;
        string? sourceRoot = null;
        var inputs = new List<string>();

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--out" when i + 1 < args.Length:
                    outPath = args[++i];
                    break;
                case "--overrides" when i + 1 < args.Length:
                    overridesPath = args[++i];
                    break;
                case "--source-root" when i + 1 < args.Length:
                    sourceRoot = Path.GetFullPath(args[++i]);
                    break;
                case "-h":
                case "--help":
                    Usage();
                    return 0;
                default:
                    inputs.Add(args[i]);
                    break;
            }
        }

        if (inputs.Count == 0)
        {
            Usage();
            return 2;
        }

        var assemblies = ResolveAssemblies(inputs);
        if (assemblies.Count == 0)
        {
            Console.Error.WriteLine("no assemblies matched the given paths");
            return 2;
        }

        // Only a file writer is owned here. Wrapping Console.Out in `using` would close the
        // process's own stdout on the way out, which breaks piping the rows into another tool.
        // Create the directory rather than requiring a caller to have made it. The runner happened
        // to create it first, which hid this until a test called the tool directly — a tool that
        // only works when driven by one particular caller is not one an outsider can rerun.
        EnsureDirectoryFor(outPath);
        EnsureDirectoryFor(overridesPath);

        var fileOutput = outPath is null
            ? null
            : new StreamWriter(File.Create(outPath), new UTF8Encoding(false));
        var output = fileOutput ?? Console.Out;

        var jsonOptions = new JsonSerializerOptions { WriteIndented = false };
        long emitted = 0, withoutDebug = 0, compilerGenerated = 0;
        var assembliesRead = 0;
        var skipped = new List<string>();
        // override full name -> the declarations it answers for. Raw Cecil names: the grader owns
        // canonicalisation, and duplicating that rule here would give the two sides two chances to
        // disagree about what a method is called.
        var overrides = new SortedDictionary<string, SortedSet<string>>(StringComparer.Ordinal);

        foreach (var path in assemblies)
        {
            var pdbPath = Path.ChangeExtension(path, ".pdb");
            var hasSymbols = File.Exists(pdbPath);

            AssemblyDefinition assembly;
            var symbolsLoaded = hasSymbols;
            try
            {
                assembly = AssemblyDefinition.ReadAssembly(
                    path,
                    new ReaderParameters { ReadSymbols = hasSymbols, ReadWrite = false });
            }
            catch (Exception ex) when (hasSymbols && IsSymbolFailure(ex))
            {
                // A stale or mismatched PDB is a build-hygiene problem, not a reason to drop an
                // assembly from the answer key: the IL is still exactly what the compiler produced.
                // Re-read without symbols so the edges survive, and let every row say plainly that
                // it has no source anchor. Dropping the assembly instead would silently shrink the
                // answer key — the one failure mode this oracle must never have.
                symbolsLoaded = false;
                skipped.Add($"{Path.GetFileName(path)}: PDB does not match the assembly ({ex.GetType().Name}) — read without symbols");
                assembly = AssemblyDefinition.ReadAssembly(
                    path,
                    new ReaderParameters { ReadSymbols = false, ReadWrite = false });
            }
            catch (Exception ex)
            {
                // A native or unmanaged file in the output folder is expected, not an error worth
                // aborting the run for — but it is recorded, because a silently skipped assembly
                // would quietly shrink the answer key.
                skipped.Add($"{Path.GetFileName(path)}: {ex.GetType().Name}");
                continue;
            }

            assembliesRead++;

            using (assembly)
            {
                if (!symbolsLoaded && hasSymbols is false)
                    skipped.Add($"{Path.GetFileName(path)}: no .pdb, edges carry no source anchor");

                foreach (var module in assembly.Modules)
                foreach (var type in AllTypes(module))
                foreach (var method in type.Methods)
                {
                    if (overridesPath is not null)
                    {
                        foreach (var declared in DeclarationsOf(method, type))
                        {
                            if (declared.FullName == method.FullName) continue;
                            if (!overrides.TryGetValue(method.FullName, out var targets))
                                overrides[method.FullName] = targets = new SortedSet<string>(StringComparer.Ordinal);
                            targets.Add(declared.FullName);
                        }
                    }

                    if (!method.HasBody) continue;

                    var callerGenerated = IsCompilerGenerated(method) || IsCompilerGenerated(type);
                    var body = method.Body;

                    foreach (var instruction in body.Instructions)
                    {
                        var op = OpName(instruction.OpCode.Code);
                        if (op is null) continue;
                        if (instruction.Operand is not MethodReference callee) continue;

                        var point = NearestSequencePoint(method, instruction);
                        var noDebug = point is null;

                        var row = new EdgeRow
                        {
                            Caller = method.FullName,
                            CallerFile = point is null ? null : Relativize(point.Document.Url, sourceRoot),
                            CallerLine = point?.StartLine,
                            Callee = callee.FullName,
                            CalleeAssembly = CalleeAssemblyName(callee),
                            Op = op,
                            VirtualDispatch = instruction.OpCode.Code == Code.Callvirt && IsVirtualTarget(callee),
                            CallerCompilerGenerated = callerGenerated,
                            CalleeCompilerGenerated = IsCompilerGeneratedName(callee.Name)
                                                      || IsCompilerGeneratedName(callee.DeclaringType?.Name),
                            NoDebugInfo = noDebug,
                        };

                        output.WriteLine(JsonSerializer.Serialize(row, jsonOptions));
                        emitted++;
                        if (noDebug) withoutDebug++;
                        if (callerGenerated) compilerGenerated++;
                    }
                }
            }
        }

        Console.Error.WriteLine($"assemblies read : {assembliesRead} of {assemblies.Count}");
        Console.Error.WriteLine($"edges emitted   : {emitted}");
        Console.Error.WriteLine($"  no debug info : {withoutDebug} ({Percent(withoutDebug, emitted)})");
        Console.Error.WriteLine($"  caller cgen   : {compilerGenerated} ({Percent(compilerGenerated, emitted)})");
        foreach (var s in skipped) Console.Error.WriteLine($"note: {s}");

        if (overridesPath is not null)
        {
            File.WriteAllText(
                overridesPath,
                JsonSerializer.Serialize(
                    overrides.ToDictionary(kv => kv.Key, kv => kv.Value.ToArray()),
                    new JsonSerializerOptions { WriteIndented = true }),
                new UTF8Encoding(false));
            Console.Error.WriteLine($"override map    : {overrides.Count} methods -> {overrides.Sum(kv => kv.Value.Count)} declarations");
        }

        fileOutput?.Dispose();
        return 0;
    }

    /// <summary>
    /// Everything <paramref name="method"/> overrides or implements: the base-class virtual it
    /// replaces, and every interface method it satisfies, explicitly or implicitly.
    ///
    /// IL names the DECLARED method at a call site, while a tool reading source frequently names an
    /// implementation instead. Neither is wrong, so the grader needs to know which implementations
    /// answer for which declaration. Without this map every virtual call an arm resolves to a
    /// concrete type is scored as a miss, and the penalty lands hardest on the arms that resolve
    /// best — the opposite of what the measurement is for.
    ///
    /// Signature matching is by name and parameter count. The grader's method key already drops
    /// parameter types (see EDGE_FORMAT.md), so a stricter match here would only invent
    /// disagreements the comparison cannot express.
    /// </summary>
    private static IEnumerable<MethodReference> DeclarationsOf(MethodDefinition method, TypeDefinition type)
    {
        if (!method.IsVirtual) yield break;

        // Explicit interface implementations name their target outright — no guessing needed.
        foreach (var explicitTarget in method.Overrides)
            yield return explicitTarget;

        // A base-class virtual this method replaces. IsNewSlot means it introduces a new one instead.
        if (!method.IsNewSlot)
        {
            for (var current = SafeResolve(type.BaseType); current is not null; current = SafeResolve(current.BaseType))
            {
                var match = current.Methods.FirstOrDefault(m =>
                    m.IsVirtual && m.Name == method.Name && m.Parameters.Count == method.Parameters.Count);
                if (match is not null)
                {
                    yield return match;
                    break;
                }
            }
        }

        // Implicit interface implementations: the method simply has the right name and shape. Walk
        // base types too — a type inherits its parents' interface obligations.
        for (var current = type; current is not null; current = SafeResolve(current.BaseType))
        {
            foreach (var implemented in current.Interfaces)
            {
                var contract = SafeResolve(implemented.InterfaceType);
                if (contract is null) continue;

                var match = contract.Methods.FirstOrDefault(m =>
                    m.Name == method.Name && m.Parameters.Count == method.Parameters.Count);
                if (match is not null)
                    yield return match;
            }
        }
    }

    /// <summary>
    /// Resolution reaches outside the assemblies we were handed and fails there routinely. A failure
    /// means one fewer entry in the map, never a wrong entry, so it is absorbed rather than raised.
    /// </summary>
    private static TypeDefinition? SafeResolve(TypeReference? reference)
    {
        if (reference is null) return null;
        try
        {
            return reference.Resolve();
        }
        catch (AssemblyResolutionException)
        {
            return null;
        }
    }

    /// <summary>
    /// Cecil signals symbol trouble through several unrelated exception types depending on the PDB
    /// format, so the fallback is keyed on the symbol-reading concern rather than one class name.
    /// </summary>
    private static bool IsSymbolFailure(Exception ex) =>
        ex is SymbolsNotMatchingException
        or SymbolsNotFoundException
        || ex.GetType().Name.Contains("Symbol", StringComparison.Ordinal);

    private static void EnsureDirectoryFor(string? path)
    {
        if (path is null) return;
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
    }

    private static void Usage()
    {
        Console.Error.WriteLine("""
            oracle-csharp — extract the compiler's own call edges from built assemblies.

              oracle-csharp <dll-or-directory>... [--source-root <path>] [--out <file.jsonl>]
                            [--overrides <file.json>]

            Reads IL via Mono.Cecil and anchors each call site to a source line via the PDB.
            Emits one JSON object per call edge; judgement calls ride as flags, never as filters.
            """);
    }

    /// <summary>Expand directories to the managed assemblies inside them; keep explicit files as given.</summary>
    private static List<string> ResolveAssemblies(IEnumerable<string> inputs)
    {
        var found = new List<string>();
        foreach (var input in inputs)
        {
            if (Directory.Exists(input))
            {
                found.AddRange(Directory
                    .EnumerateFiles(input, "*.dll", SearchOption.TopDirectoryOnly)
                    .Where(p => File.Exists(Path.ChangeExtension(p, ".pdb"))));
            }
            else if (File.Exists(input))
            {
                found.Add(input);
            }
            else
            {
                Console.Error.WriteLine($"note: path does not exist: {input}");
            }
        }
        return found.Distinct(StringComparer.Ordinal).OrderBy(p => p, StringComparer.Ordinal).ToList();
    }

    private static IEnumerable<TypeDefinition> AllTypes(ModuleDefinition module)
    {
        foreach (var type in module.Types)
        foreach (var nested in Flatten(type))
            yield return nested;

        static IEnumerable<TypeDefinition> Flatten(TypeDefinition type)
        {
            yield return type;
            foreach (var nested in type.NestedTypes)
            foreach (var inner in Flatten(nested))
                yield return inner;
        }
    }

    /// <summary>
    /// The sequence point covering this instruction, or the nearest one before it. A call inside a
    /// compiler-expanded construct often carries no point of its own; walking back gives the source
    /// line a reader would point at, which is what the arms are being compared on.
    /// </summary>
    private static SequencePoint? NearestSequencePoint(MethodDefinition method, Instruction instruction)
    {
        var debug = method.DebugInformation;
        if (debug is null || !debug.HasSequencePoints) return null;

        SequencePoint? best = null;
        foreach (var point in debug.SequencePoints)
        {
            if (point.StartLine == HiddenLine) continue;
            if (point.Offset > instruction.Offset) break;
            best = point;
        }
        return best;
    }

    private static bool IsVirtualTarget(MethodReference reference)
    {
        try
        {
            var resolved = reference.Resolve();
            return resolved is null || resolved.IsVirtual || resolved.DeclaringType.IsInterface;
        }
        catch (AssemblyResolutionException)
        {
            // Target lives in an assembly we were not given. callvirt on an unresolvable target is
            // treated as virtual: the conservative side, since assuming a static bind would let an
            // arm score a point the compiler never confirmed.
            return true;
        }
    }

    private static bool IsCompilerGenerated(ICustomAttributeProvider provider)
    {
        if (provider.HasCustomAttributes)
        {
            foreach (var attribute in provider.CustomAttributes)
            {
                if (attribute.AttributeType.FullName == "System.Runtime.CompilerServices.CompilerGeneratedAttribute")
                    return true;
            }
        }
        return provider switch
        {
            MemberReference member => IsCompilerGeneratedName(member.Name),
            _ => false,
        };
    }

    /// <summary>
    /// Roslyn names its synthesized members with characters C# forbids in identifiers, so a name
    /// carrying one cannot have been written by a human.
    /// </summary>
    private static bool IsCompilerGeneratedName(string? name) =>
        name is not null && (name.Contains('<') || name.Contains('>'));

    private static string CalleeAssemblyName(MethodReference reference)
    {
        var scope = reference.DeclaringType?.Scope;
        return scope switch
        {
            AssemblyNameReference assemblyName => assemblyName.Name,
            ModuleDefinition module => module.Assembly?.Name?.Name ?? module.Name,
            _ => scope?.Name ?? "<unknown>",
        };
    }

    private static string? OpName(Code code) => code switch
    {
        Code.Call => "call",
        Code.Callvirt => "callvirt",
        Code.Newobj => "newobj",
        Code.Ldftn => "ldftn",
        Code.Ldvirtftn => "ldvirtftn",
        Code.Calli => "calli",
        _ => null,
    };

    /// <summary>
    /// PDBs record absolute paths from the build machine. Rows must be comparable across machines,
    /// so a path under the pinned source root becomes repo-relative with forward slashes.
    /// </summary>
    private static string Relativize(string documentUrl, string? sourceRoot)
    {
        if (string.IsNullOrEmpty(documentUrl)) return documentUrl;
        if (sourceRoot is null) return documentUrl.Replace('\\', '/');

        var full = documentUrl.Replace('\\', Path.DirectorySeparatorChar);
        if (!full.StartsWith(sourceRoot, StringComparison.OrdinalIgnoreCase)) return documentUrl.Replace('\\', '/');

        return Path.GetRelativePath(sourceRoot, full).Replace('\\', '/');
    }

    private static string Percent(long part, long whole) =>
        whole == 0 ? "0.0%" : (100.0 * part / whole).ToString("F1", CultureInfo.InvariantCulture) + "%";
}
