using System.IO.Compression;
using System.Reflection;
using System.Text;

namespace AnKuanHost;

internal static class ScriptPayload
{
    internal const string SourceFileSha256 = "31922a9d91d93290c22be472d010b8f83a86b4f40adfbcedc3efbc6b623e6223";

    internal static string GetScript()
    {
        var assembly = Assembly.GetExecutingAssembly();
        var names = assembly.GetManifestResourceNames()
            .Where(n => n.Contains(".script.part", StringComparison.OrdinalIgnoreCase) && n.EndsWith(".b64", StringComparison.OrdinalIgnoreCase))
            .OrderBy(n => n, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (names.Length == 0)
            throw new InvalidOperationException("找不到內嵌 PowerShell payload。\n");

        var sb = new StringBuilder();
        foreach (var name in names)
        {
            using var stream = assembly.GetManifestResourceStream(name)
                ?? throw new InvalidOperationException($"無法開啟資源：{name}");
            using var reader = new StreamReader(stream, Encoding.ASCII, detectEncodingFromByteOrderMarks: false);
            sb.Append(reader.ReadToEnd().Trim());
        }

        byte[] compressed = Convert.FromBase64String(sb.ToString());
        using var input = new MemoryStream(compressed);
        using var gzip = new GZipStream(input, CompressionMode.Decompress);
        using var output = new MemoryStream();
        gzip.CopyTo(output);
        return Encoding.UTF8.GetString(output.ToArray());
    }
}
