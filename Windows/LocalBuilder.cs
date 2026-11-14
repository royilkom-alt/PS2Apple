using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;

namespace PS2Apple;

internal sealed record BuilderEvent(
    string Kind, string? Key, string? Detail, bool? Ok, double? Value,
    string? Title, string? Serial, string? Region, string? Channel,
    string? Note, string? BootSHA256, string? Profile, string? Path, int? Current, int? Total);

internal static class LocalBuilder
{
    public static readonly string DataFolder = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PS2Apple");
    public static readonly string Version = Assembly.GetExecutingAssembly().GetName().Version!.ToString(3);
    public static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public static Stream OpenResource(string name) =>
        Assembly.GetExecutingAssembly().GetManifestResourceStream(name) ??
        throw new InvalidOperationException($"Missing PS2Apple resource {name}.");

    public static BuilderEvent? Parse(string line) =>
        line.StartsWith("@@PS2 ", StringComparison.Ordinal)
            ? JsonSerializer.Deserialize<BuilderEvent>(line[6..], Json)
            : null;

    public static async Task<BuilderEvent> IdentifyAsync(string iso, Action<string> log, CancellationToken token)
    {
        BuilderEvent? disc = null;
        await RunAsync("identify", ["--iso", iso], log, message =>
        {
            if (message.Kind == "disc") disc = message;
        }, token);
        return disc is { Ok: true } ? disc : throw new IOException("The selected disc could not be verified.");
    }

    public static async Task<string> BuildAsync(string iso, string destination, string? expected,
        Action<string> log, CancellationToken token)
    {
        var args = new List<string> { "--iso", iso, "--output", destination };
        if (expected is not null) args.AddRange(["--expected", expected]);
        string? artifact = null;
        await RunAsync("build", args, log, message =>
        {
            if (message.Kind == "artifact") artifact = message.Path;
        }, token);
        return artifact is not null && File.Exists(artifact)
            ? artifact : throw new IOException("The builder did not produce a verified IPA.");
    }

    public static Task CheckAsync(Action<string> log, CancellationToken token)
        => RunAsync("check", [], log, _ => { }, token);

    private static async Task RunAsync(string action, IEnumerable<string> args, Action<string> log,
        Action<BuilderEvent> handle, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        string root = InstallComponents();
        token.ThrowIfCancellationRequested();
        var start = new ProcessStartInfo(Path.Combine(root, "python.exe"))
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8
        };
        foreach (string arg in new[] {
            "-I", "-X", "utf8", "-u", Path.Combine(root, "Backend", "windows_build.py"),
            action, "--workspace", DataFolder
        })
            start.ArgumentList.Add(arg);
        foreach (string arg in args) start.ArgumentList.Add(arg);

        using var process = Process.Start(start) ?? throw new IOException("Could not start the PS2Apple compiler.");
        using var cancel = token.Register(() =>
        {
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
            catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception) { }
        });
        Task<string> errors = process.StandardError.ReadToEndAsync();
        string? failure = null;
        while (await process.StandardOutput.ReadLineAsync() is { } line)
        {
            log(line);
            BuilderEvent? message = Parse(line);
            if (message is not null)
            {
                handle(message);
                if (message.Kind == "error") failure = message.Detail;
            }
        }
        string stderr = await errors;
        if (!string.IsNullOrWhiteSpace(stderr)) log(stderr.TrimEnd());
        await process.WaitForExitAsync();
        token.ThrowIfCancellationRequested();
        if (process.ExitCode != 0)
            throw new IOException(failure ?? "PS2Apple stopped before completing this step. See the log.");
    }

    private static string InstallComponents()
    {
        using Stream content = OpenResource("components.zip");
        string digest = Convert.ToHexString(SHA256.HashData(content));
        string parent = Path.Combine(DataFolder, "Components");
        string target = Path.Combine(parent, digest);
        if (Directory.Exists(target)) return target;
        Directory.CreateDirectory(parent);
        string stage = Path.Combine(parent, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        try
        {
            content.Position = 0;
            using var zip = new ZipArchive(content, ZipArchiveMode.Read);
            zip.ExtractToDirectory(stage);
            try { Directory.Move(stage, target); }
            catch (IOException) when (Directory.Exists(target)) { }
            return target;
        }
        finally
        {
            if (Directory.Exists(stage)) Directory.Delete(stage, recursive: true);
        }
    }
}
