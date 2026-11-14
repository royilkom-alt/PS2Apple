using System.Diagnostics;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace PS2Apple;

internal sealed record ReleaseUpdate(Version Version, Uri Url, string Digest, long Size)
{
    public const string Page = "https://github.com/royilkom-alt/PS2Apple/releases";
    private const string Feed = "https://api.github.com/repos/royilkom-alt/PS2Apple/releases?per_page=30";
    private static readonly HttpClient Client = new();
    private static readonly string Downloads = Path.Combine(LocalBuilder.DataFolder, "Updates");
    public static readonly string InstallLog = Path.Combine(LocalBuilder.DataFolder, "Update.log");

    internal static ReleaseUpdate? Newest(JsonElement releases, Version installed)
    {
        ReleaseUpdate? update = null;
        foreach (JsonElement release in releases.EnumerateArray())
        {
            if (release.GetProperty("draft").GetBoolean() || release.GetProperty("prerelease").GetBoolean()) continue;
            string tag = release.GetProperty("tag_name").GetString() ?? "";
            if (!Regex.IsMatch(tag, @"\Av(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z") ||
                !Version.TryParse(tag.AsSpan(1), out var version) || version <= installed) continue;
            string name = $"PS2Apple-{version}-Windows-x64.exe";
            foreach (JsonElement asset in release.GetProperty("assets").EnumerateArray())
            {
                if (asset.GetProperty("name").GetString() != name || asset.GetProperty("state").GetString() != "uploaded" ||
                    !asset.GetProperty("size").TryGetInt64(out long size) || size <= 0 ||
                    !asset.TryGetProperty("digest", out var digest) || digest.GetString() is not { } hash ||
                    !Regex.IsMatch(hash, @"\Asha256:[a-fA-F0-9]{64}\z")) continue;
                update = new ReleaseUpdate(version, new Uri($"{Page}/download/{tag}/{name}"), hash[7..].ToUpperInvariant(), size);
                installed = version;
                break;
            }
        }
        return update;
    }

    public static async Task<ReleaseUpdate?> CheckAsync(CancellationToken token)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(20));
        using var request = new HttpRequestMessage(HttpMethod.Get, Feed);
        request.Headers.UserAgent.ParseAdd($"PS2Apple/{LocalBuilder.Version}");
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        request.Headers.CacheControl = new CacheControlHeaderValue { NoCache = true };
        using HttpResponseMessage response = await Client.SendAsync(request, deadline.Token);
        response.EnsureSuccessStatusCode();
        await using var result = await response.Content.ReadAsStreamAsync(deadline.Token);
        using var releases = await JsonDocument.ParseAsync(result, cancellationToken: deadline.Token);
        return Newest(releases.RootElement, Version.Parse(LocalBuilder.Version));
    }

    public async Task<string> DownloadAsync(IProgress<int> progress, CancellationToken token)
    {
        string folder = Path.Combine(Downloads, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(folder);
        string file = Path.Combine(folder, $"PS2Apple-{Version}-Windows-x64.exe");
        try
        {
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
            deadline.CancelAfter(TimeSpan.FromMinutes(30));
            using HttpResponseMessage response = await Client.GetAsync(Url, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
            response.EnsureSuccessStatusCode();
            await using Stream source = await response.Content.ReadAsStreamAsync(deadline.Token);
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            await using (var output = File.Open(file, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                long bytes = 0;
                int previous = -1;
                byte[] buffer = new byte[128 * 1024];
                for (int read; (read = await source.ReadAsync(buffer, deadline.Token)) != 0;)
                {
                    bytes += read;
                    if (bytes > Size) throw new IOException("The update is larger than the GitHub release asset.");
                    hash.AppendData(buffer, 0, read);
                    await output.WriteAsync(buffer.AsMemory(0, read), deadline.Token);
                    int percent = (int)(bytes * 100.0 / Size);
                    if (percent != previous) progress.Report(previous = percent);
                }
                if (bytes != Size || Convert.ToHexString(hash.GetHashAndReset()) != Digest)
                    throw new IOException("The update checksum did not match GitHub.");
            }
            if (InstalledVersion(file) != Version) throw new IOException("The download contains a different version of PS2Apple.");
            return file;
        }
        catch
        {
            Discard(file);
            throw;
        }
    }

    public static void LaunchInstaller(string file, bool restart)
    {
        var start = new ProcessStartInfo(file) { UseShellExecute = false };
        start.ArgumentList.Add("--apply-update");
        start.ArgumentList.Add(Environment.ProcessId.ToString(System.Globalization.CultureInfo.InvariantCulture));
        start.ArgumentList.Add(Environment.ProcessPath!);
        start.ArgumentList.Add(restart ? "restart" : "quit");
        using Process installer = Process.Start(start) ?? throw new IOException("Could not start the PS2Apple update.");
    }

    public static int Apply(int parent, string target, bool restart)
    {
        string source = Environment.ProcessPath!;
        string? folder = Path.GetDirectoryName(source);
        if (parent <= 0 || parent == Environment.ProcessId || folder is null ||
            !string.Equals(Path.GetDirectoryName(folder), Downloads, StringComparison.OrdinalIgnoreCase) ||
            !Guid.TryParseExact(Path.GetFileName(folder), "N", out _) || !Path.IsPathFullyQualified(target) ||
            InstalledVersion(target) is not { } installed || Version.Parse(LocalBuilder.Version) <= installed) return 2;

        string incoming = target + "." + Guid.NewGuid().ToString("N") + ".tmp";
        string backup = incoming + ".previous";
        try
        {
            try
            {
                using var previous = Process.GetProcessById(parent);
                if (!previous.WaitForExit(60_000)) throw new IOException("Close the other PS2Apple window and try again.");
            }
            catch (ArgumentException) { }

            if (InstalledVersion(target) != installed) throw new IOException("The installed copy changed while updating.");
            File.Copy(source, incoming);
            File.Replace(incoming, target, backup, ignoreMetadataErrors: true);
            try
            {
                File.WriteAllText(Path.Combine(folder, "installed"), "");
                if (restart) StartApp(target);
            }
            catch
            {
                File.Replace(backup, target, null, ignoreMetadataErrors: true);
                throw;
            }
            TryDelete(backup);
            TryDelete(InstallLog);
            return 0;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.ComponentModel.Win32Exception)
        {
            try { File.WriteAllText(InstallLog, $"PS2Apple could not install the update. {error.Message}"); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            if (restart && File.Exists(target))
            {
                try { StartApp(target); }
                catch (System.ComponentModel.Win32Exception) { }
                catch (IOException) { }
            }
            return 1;
        }
        finally { TryDelete(incoming); }
    }

    public static void Discard(string file)
    {
        try { Directory.Delete(Path.GetDirectoryName(file)!, recursive: true); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    public static void CleanAppliedDownloads()
    {
        try
        {
            foreach (string folder in Directory.GetDirectories(Downloads))
            {
                if (Guid.TryParseExact(Path.GetFileName(folder), "N", out _) && File.Exists(Path.Combine(folder, "installed")))
                    Discard(Path.Combine(folder, "installed"));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static Version? InstalledVersion(string file)
    {
        if (!File.Exists(file)) return null;
        FileVersionInfo metadata = FileVersionInfo.GetVersionInfo(file);
        return metadata.ProductName == "PS2Apple" && Version.TryParse(metadata.ProductVersion, out var version)
            ? version : null;
    }

    private static void StartApp(string file)
    {
        using Process app = Process.Start(new ProcessStartInfo(file) { UseShellExecute = false })
            ?? throw new IOException("Could not reopen PS2Apple.");
    }

    private static void TryDelete(string file)
    {
        try { File.Delete(file); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
