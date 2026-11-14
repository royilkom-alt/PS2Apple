using System.Text.Json;

namespace PS2Apple;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args is ["--apply-update", var pid, var target, "restart" or "quit"] && int.TryParse(pid, out int parent))
            return ReleaseUpdate.Apply(parent, target, restart: args[3] == "restart");
        if (args.Length > 0) return DiagnoseAsync(args).GetAwaiter().GetResult();
        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
        return 0;
    }

    private static async Task<int> DiagnoseAsync(string[] args)
    {
        if (args is not (["--identify", _, _] or ["--build", _, _, _] or ["--catalog", _] or ["--check", _]))
            return 2;
        using var report = new StreamWriter(args[^1], append: false) { AutoFlush = true };
        try
        {
            switch (args)
            {
                case ["--identify", var iso, _]:
                    await LocalBuilder.IdentifyAsync(iso, report.WriteLine, CancellationToken.None);
                    break;
                case ["--build", var iso, var output, _]:
                    string artifact = await LocalBuilder.BuildAsync(iso, output, null, report.WriteLine, CancellationToken.None);
                    report.WriteLine($"Saved {artifact}");
                    break;
                case ["--check", _]:
                    await LocalBuilder.CheckAsync(report.WriteLine, CancellationToken.None);
                    break;
                case ["--catalog", _]:
                    report.WriteLine(JsonSerializer.Serialize(CatalogForm.ReadCatalog(), LocalBuilder.Json));
                    break;
            }
            return 0;
        }
        catch (Exception error)
        {
            report.WriteLine(error.Message);
            return 1;
        }
    }

}
