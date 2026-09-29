using System.Management.Automation;
using System.Management.Automation.Runspaces;
using System.Security.Cryptography;
using System.Text;

namespace AnKuanHost;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        Console.InputEncoding = Encoding.UTF8;
        Console.Title = "安管自動化（原 STEP 1～21／無校正點）";

        string script;
        try
        {
            script = ScriptPayload.GetScript();
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"內嵌 PowerShell 載入失敗：{ex.Message}");
            return 90;
        }

        if (args.Any(a => a.Equals("--selftest", StringComparison.OrdinalIgnoreCase)))
            return SelfTest(script);

        var keyword = GetArg(args, "-Keyword", "--keyword");
        var recentCheckText = GetArg(args, "-RecentCheck", "--recent-check");
        var recentReportText = GetArg(args, "-RecentReport", "--recent-report");
        int recentCheck = int.TryParse(recentCheckText, out var rc) ? rc : 5;
        int recentReport = int.TryParse(recentReportText, out var rr) ? rr : 2;

        Console.WriteLine("============================================");
        Console.WriteLine("安管自動化 EXE Host");
        Console.WriteLine("執行核心：原 PowerShell STEP 1～21");
        Console.WriteLine("模式：同程序 PowerShell Runspace / STA");
        Console.WriteLine("校正點：不使用");
        Console.WriteLine("============================================");
        Console.WriteLine();

        var host = new ConsolePsHost();
        try
        {
            var iss = InitialSessionState.CreateDefault();
            using var runspace = RunspaceFactory.CreateRunspace(host, iss);
            runspace.ApartmentState = ApartmentState.STA;
            runspace.ThreadOptions = PSThreadOptions.UseCurrentThread;
            runspace.Open();

            using var ps = PowerShell.Create();
            ps.Runspace = runspace;
            ps.AddScript(script, useLocalScope: false);

            if (!string.IsNullOrWhiteSpace(keyword)) ps.AddParameter("Keyword", keyword);
            ps.AddParameter("RecentCheck", recentCheck);
            ps.AddParameter("RecentReport", recentReport);

            var results = ps.Invoke();
            foreach (var item in results)
            {
                var text = item?.ToString();
                if (!string.IsNullOrEmpty(text)) Console.WriteLine(text);
            }

            if (ps.HadErrors)
            {
                foreach (var err in ps.Streams.Error)
                    Console.Error.WriteLine(err.ToString());
                return host.ExitCode ?? 1;
            }

            return host.ExitCode ?? 0;
        }
        catch (RuntimeException ex)
        {
            Console.Error.WriteLine();
            Console.Error.WriteLine("PowerShell 執行失敗：");
            Console.Error.WriteLine(ex.ErrorRecord?.ToString() ?? ex.Message);
            return host.ExitCode ?? 1;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine();
            Console.Error.WriteLine("EXE Host 執行失敗：");
            Console.Error.WriteLine(ex);
            return host.ExitCode ?? 1;
        }
        finally
        {
            if (Environment.UserInteractive && !Console.IsInputRedirected)
            {
                Console.WriteLine();
                Console.Write("按 Enter 關閉視窗: ");
                Console.ReadLine();
            }
        }
    }

    private static string? GetArg(string[] args, params string[] names)
    {
        for (int i = 0; i < args.Length; i++)
        {
            if (!names.Any(n => args[i].Equals(n, StringComparison.OrdinalIgnoreCase))) continue;
            return i + 1 < args.Length ? args[i + 1] : null;
        }
        return null;
    }

    private static int SelfTest(string script)
    {
        var bytes = Encoding.UTF8.GetBytes(script);
        var runtimeSha = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        bool ok = true;

        ok &= script.Contains("STEP 1：進入場所建檔", StringComparison.Ordinal);
        ok &= script.Contains("STEP 21", StringComparison.Ordinal);
        ok &= script.Contains("TcxCustomInnerTextEdit", StringComparison.Ordinal);
        ok &= script.Contains("TB_GETITEMRECT", StringComparison.Ordinal);
        ok &= !script.Contains("ankuan_base_profile", StringComparison.OrdinalIgnoreCase);
        ok &= !script.Contains("calibration", StringComparison.OrdinalIgnoreCase);

        Console.WriteLine($"Script chars: {script.Length:N0}");
        Console.WriteLine($"Runtime UTF8 SHA256: {runtimeSha}");
        Console.WriteLine($"Source file SHA256: {ScriptPayload.SourceFileSha256}");
        Console.WriteLine($"STEP 1/21 markers: {(ok ? "OK" : "FAIL")}");
        Console.WriteLine("Persistent calibration markers: NONE");

        return ok ? 0 : 91;
    }
}
