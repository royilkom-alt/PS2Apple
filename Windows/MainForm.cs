using System.Diagnostics;
using System.Text.Json;
using static PS2Apple.DesktopTheme;

namespace PS2Apple;

internal sealed record Preferences(string? Output, string? Repo);

internal sealed class MainForm : Form
{
    private readonly Label iso = Label("No ISO selected", 10, true);
    private readonly Label output = Label("PS2Apple", 10, true);
    private readonly RichTextBox log = new()
    {
        ReadOnly = true, ScrollBars = RichTextBoxScrollBars.Vertical, Dock = DockStyle.Fill,
        BorderStyle = BorderStyle.None, BackColor = Color.FromArgb(13, 16, 21), ForeColor = Ink,
        Font = new Font("Consolas", 9), WordWrap = true
    };
    private readonly Label disc = Label("Choose a PlayStation 2 ISO.", 9, color: Muted);
    private readonly Label outputDetail = Label("", 9, color: Muted);
    private readonly Label status = Label("Ready", 9, color: Muted);
    private readonly Label percent = Label("", 9, color: Muted);
    private readonly Label savedIpa = Label("", 9);
    private readonly CheckBox follow = new() { Text = "Follow", Checked = true, ForeColor = Muted, AutoSize = true };
    private readonly ProgressBar progress = new() { Dock = DockStyle.Fill, Maximum = 100 };
    private readonly Button build = Button("Build IPA", true);
    private readonly Button checkTools = Button("Check Tools");
    private readonly Button chooseIso = Button("Choose ISO…");
    private readonly Button chooseOutput = Button("Choose Folder…");
    private readonly Button updates = Button("Updates");
    private readonly Button cancel = Button("Cancel");
    private readonly Button reveal = Button("Reveal IPA");
    private readonly Button revealLog = Button("Reveal Log");
    private readonly Button copyLog = Button("Copy");
    private readonly Label title = Label("", 14, true);
    private readonly Label edition = Label("", 9, color: Muted);
    private readonly System.ComponentModel.Container components = new();
    private readonly ToolTip paths;
    private readonly string settingsPath = Path.Combine(LocalBuilder.DataFolder, "preferences.json");
    private CancellationTokenSource? running;
    private string? selectedFingerprint;
    private int? probeStart;
    private string? isoPath;
    private string outputPath = "";
    private string? resultPath;
    private string repo = "";
    private bool closing;
    private bool hasLog;

    public MainForm()
    {
        SuspendLayout();
        paths = new ToolTip(components);
        Text = "PS2Apple";
        Icon = Icon.ExtractAssociatedIcon(Environment.ProcessPath!);
        Font = new Font("Segoe UI", 10);
        BackColor = Canvas;
        ForeColor = Ink;
        AutoScaleMode = AutoScaleMode.None;
        StartPosition = FormStartPosition.CenterScreen;
        double scale = Font.GetHeight() / Font.GetHeight(96);
        Rectangle desktop = Screen.FromPoint(Cursor.Position).WorkingArea;
        int panelWidth = Math.Min((int)(1100 * scale), desktop.Width - 36);
        int panelHeight = Math.Min((int)(720 * scale), desktop.Height - 70);
        ClientSize = new Size(panelWidth, panelHeight);
        MinimumSize = SizeFromClientSize(new Size(Math.Min((int)(980 * scale), panelWidth),
                                                   Math.Min((int)(610 * scale), panelHeight)));

        var split = new SplitContainer { Dock = DockStyle.Fill, BackColor = Line, SplitterWidth = 4 };
        Controls.Add(split);
        split.Size = ClientSize;
        split.Panel1MinSize = Math.Min((int)(600 * scale), (int)(ClientSize.Width * .57));
        split.Panel2MinSize = Math.Min((int)(340 * scale), (int)(ClientSize.Width * .32));
        split.SplitterDistance = (int)(ClientSize.Width * .60);
        split.Panel1.BackColor = Canvas;
        split.Panel2.BackColor = Canvas;
        int padding = (int)(22 * scale);
        var scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true, Padding = new Padding(padding), BackColor = Canvas };
        var workspace = Stack();
        workspace.Controls.Add(Header((int)(44 * scale)));
        workspace.Controls.Add(BuildCard());
        workspace.Controls.Add(EnvironmentCard());
        scroll.Controls.Add(workspace);
        split.Panel1.Controls.Add(scroll);
        split.Panel2.Controls.Add(Terminal());
        HandleCreated += (_, _) => DarkTitleBar(this);

        LoadPreferences();
        chooseIso.Click += async (_, _) => await SelectDiscAsync();
        chooseOutput.Click += (_, _) => ChooseFolder();
        build.Click += async (_, _) => await BuildAsync();
        checkTools.Click += async (_, _) => await RunAsync("Checking the local compiler…", async (token, lines) =>
        {
            await Task.Run(() => LocalBuilder.CheckAsync(lines, token));
            status.Text = "Local compiler ready";
        });
        updates.Click += async (_, _) => await RunUpdatesAsync();
        cancel.Click += (_, _) => { running?.Cancel(); cancel.Enabled = false; };
        reveal.Click += (_, _) => { if (resultPath is { } file) RevealFile(file); };
        revealLog.Click += (_, _) => ShowLog();
        copyLog.Click += (_, _) => { if (log.TextLength > 0) Clipboard.SetText(log.Text); };
        follow.CheckedChanged += (_, _) => { if (follow.Checked) log.ScrollToCaret(); };
        FormClosing += (_, e) =>
        {
            if (running is not null)
            {
                e.Cancel = true;
                closing = true;
                running.Cancel();
                status.Text = "Cancelling…";
            }
        };
        RefreshButtons();
        ResumeLayout();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) components.Dispose();
        base.Dispose(disposing);
    }

    private static TableLayoutPanel Stack()
    {
        var panel = new TableLayoutPanel
        {
            Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            ColumnCount = 1, Margin = Padding.Empty
        };
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        return panel;
    }

    private static FlowLayoutPanel Row(params Control[] controls)
    {
        var row = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, WrapContents = false, Margin = Padding.Empty };
        row.Controls.AddRange(controls);
        return row;
    }

    private Control Header(int iconSize)
    {
        var row = new TableLayoutPanel { AutoSize = true, Dock = DockStyle.Top, ColumnCount = 3,
            Margin = new Padding(0, 0, 0, 16) };
        row.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        row.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var icon = new PictureBox { Image = DesktopTheme.Image("AppIcon.png"), Size = new Size(iconSize, iconSize),
            SizeMode = PictureBoxSizeMode.Zoom, Anchor = AnchorStyles.Left, Margin = new Padding(0, 0, 6, 0) };
        row.Controls.Add(icon, 0, 0);
        var brand = Stack();
        brand.Anchor = AnchorStyles.Left;
        brand.Controls.Add(Label("PS2Apple", 20, true));
        brand.Controls.Add(Label("Created by iSolution", 8.5f, color: Muted));
        row.Controls.Add(brand, 1, 0);
        var catalog = Button("Game Catalog");
        catalog.Click += (_, _) => { using var dialog = new CatalogForm(); dialog.ShowDialog(this); };
        row.Controls.Add(Row(Social("YouTube", "https://www.youtube.com/@iSolutional", iconSize),
            Social("Discord", "https://discord.gg/e58k5bkja3", iconSize), catalog, updates), 2, 0);
        return row;
    }

    private Button Social(string name, string address, int iconSize)
    {
        var button = Button("");
        button.AutoSize = false;
        button.Size = new Size((int)(iconSize * .72), (int)(iconSize * .72));
        button.Image = DesktopTheme.Image($"{name}.png");
        button.AccessibleName = $"iSolution on {name}";
        paths.SetToolTip(button, button.AccessibleName);
        button.Click += (_, _) => Process.Start(new ProcessStartInfo(address) { UseShellExecute = true });
        return button;
    }

    private Control BuildCard()
    {
        var card = Card();
        title.Visible = edition.Visible = false;
        card.Controls.Add(title);
        card.Controls.Add(edition);
        var titleRule = Divider();
        titleRule.Visible = false;
        edition.VisibleChanged += (_, _) => titleRule.Visible = edition.Visible;
        card.Controls.Add(titleRule);
        card.Controls.Add(FileRow("PS2 Disc Image", "◉", iso, disc, chooseIso));
        card.Controls.Add(FileRow("IPA Output", "▱", output, outputDetail, chooseOutput));
        var result = Row(savedIpa, reveal);
        result.BackColor = Tile;
        result.Visible = false;
        card.Controls.Add(result);
        card.Controls.Add(Divider());
        card.Controls.Add(Row(build, cancel));
        return card;
    }

    private static Control Divider() => new Panel
    {
        BackColor = Line, Dock = DockStyle.Top, Height = 1, Margin = new Padding(0, 6, 0, 9)
    };

    private static TableLayoutPanel Card()
    {
        var card = Stack();
        card.BackColor = Surface;
        card.Padding = new Padding(14);
        card.Margin = new Padding(0, 0, 0, 14);
        card.Paint += (_, e) =>
        {
            using var pen = new Pen(Line);
            e.Graphics.DrawRectangle(pen, 0, 0, card.Width - 1, card.Height - 1);
        };
        return card;
    }

    private static Control FileRow(string name, string icon, Label value, Label detail, Button browse)
    {
        var row = new TableLayoutPanel { ColumnCount = 3, AutoSize = true, Dock = DockStyle.Top,
            Margin = new Padding(0, 4, 0, 8) };
        row.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        row.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var badge = Label(icon, 13, true, Mint);
        badge.Padding = new Padding(7, 4, 7, 4);
        badge.BackColor = Tile;
        badge.Anchor = AnchorStyles.Left;
        var text = Stack();
        text.Controls.Add(Label(name, 8.5f, color: Muted));
        foreach (Label label in new[] { value, detail })
        {
            label.AutoSize = false;
            label.Height = TextRenderer.MeasureText("X", label.Font).Height + 3;
            label.Dock = DockStyle.Top;
            label.AutoEllipsis = true;
            text.Controls.Add(label);
        }
        row.Controls.Add(badge, 0, 0);
        row.Controls.Add(text, 1, 0);
        browse.Anchor = AnchorStyles.Right;
        row.Controls.Add(browse, 2, 0);
        return row;
    }

    private Control EnvironmentCard()
    {
        var card = Card();
        card.Controls.Add(Label("Build Environment", 12, true));
        card.Controls.Add(Label("The compiler and iPhoneOS runtime are included. Builds run on this PC.", 9, color: Muted));
        var checks = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 3 };
        foreach ((string name, string detail) in new[] { ("ISO Reader", "Serial and boot ELF"),
            ("AOT Compiler", "EE and VU code"), ("iPhoneOS Linker", "ARM64 device builds") })
        {
            checks.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f / 3));
            var check = Stack();
            check.BackColor = Rail;
            check.Padding = new Padding(6);
            check.Margin = new Padding(2, 2, 3, 2);
            check.Controls.Add(Label(name, 9, true, Mint));
            check.Controls.Add(Label(detail, 8, color: Muted));
            checks.Controls.Add(check);
        }
        card.Controls.Add(checks);
        card.Controls.Add(Row(checkTools));
        return card;
    }

    private Control Terminal()
    {
        var panel = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Rail, ColumnCount = 1, RowCount = 4 };
        panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        panel.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        var header = Row(Label("›_  Build Log", 9, true, Mint), follow, copyLog, revealLog);
        header.Padding = new Padding(10, 5, 6, 5);
        panel.Controls.Add(header, 0, 0);
        progress.Height = 5;
        progress.Margin = new Padding(12, 0, 12, 6);
        panel.Controls.Add(progress, 0, 1);
        var buffer = new Panel { Dock = DockStyle.Fill, BackColor = log.BackColor, Padding = new Padding(11) };
        log.Text = "Build output and diagnostics appear here.";
        buffer.Controls.Add(log);
        panel.Controls.Add(buffer, 0, 2);
        var footer = Row(Label("●", 8, color: Mint), status, percent);
        footer.WrapContents = true;
        footer.Padding = new Padding(10, 5, 10, 5);
        panel.Controls.Add(footer, 0, 3);
        return panel;
    }

    private void LoadPreferences()
    {
        outputPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "PS2Apple");
        try
        {
            if (File.Exists(settingsPath) &&
                JsonSerializer.Deserialize<Preferences>(File.ReadAllText(settingsPath), LocalBuilder.Json) is { } saved)
            {
                if (!string.IsNullOrWhiteSpace(saved.Output)) outputPath = saved.Output;
                repo = saved.Repo ?? "";
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        {
            Append($"Could not load preferences. {error.Message}");
        }
        UpdateOutputLabel();
    }

    private void SavePreferences()
    {
        try
        {
            Directory.CreateDirectory(LocalBuilder.DataFolder);
            File.WriteAllText(settingsPath, JsonSerializer.Serialize(new Preferences(outputPath, repo)));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            Append($"Could not save preferences. {error.Message}");
        }
    }

    private void ChooseFolder()
    {
        using var dialog = new FolderBrowserDialog { Description = "Choose the Windows IPA output folder",
            UseDescriptionForTitle = true };
        if (Directory.Exists(outputPath)) dialog.InitialDirectory = outputPath;
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        outputPath = dialog.SelectedPath;
        UpdateOutputLabel();
        SavePreferences();
        RefreshButtons();
    }

    private void UpdateOutputLabel()
    {
        output.Text = new DirectoryInfo(outputPath).Name;
        outputDetail.Text = outputPath;
        paths.SetToolTip(outputDetail, outputPath);
    }

    private static void RevealFile(string file)
    {
        var explorer = new ProcessStartInfo("explorer.exe") { UseShellExecute = false };
        explorer.ArgumentList.Add($"/select,{file}");
        Process.Start(explorer);
    }

    private void ShowLog()
    {
        try
        {
            string folder = Path.Combine(LocalBuilder.DataFolder, "Logs");
            Directory.CreateDirectory(folder);
            string file = Path.Combine(folder, "Build.log");
            File.WriteAllText(file, log.Text);
            RevealFile(file);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.ComponentModel.Win32Exception)
        {
            Append(error.Message);
        }
    }

    private void RefreshButtons()
    {
        bool idle = running is null;
        chooseIso.Enabled = chooseOutput.Enabled = idle;
        build.Enabled = idle && selectedFingerprint is not null;
        updates.Enabled = checkTools.Enabled = idle;
        cancel.Visible = cancel.Enabled = !idle;
        reveal.Enabled = idle && resultPath is { } file && File.Exists(file);
        copyLog.Enabled = revealLog.Enabled = hasLog;
        progress.Visible = !idle;
    }

    private async Task SelectDiscAsync()
    {
        using var dialog = new OpenFileDialog { Filter = "PlayStation 2 ISO (*.iso)|*.iso", Title = "Choose PS2 Disc Image" };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        isoPath = dialog.FileName;
        iso.Text = Path.GetFileName(isoPath);
        paths.SetToolTip(iso, isoPath);
        selectedFingerprint = null;
        resultPath = null;
        savedIpa.Parent!.Visible = false;
        title.Visible = edition.Visible = false;
        disc.ForeColor = Muted;
        disc.Text = "Reading SYSTEM.CNF and the boot ELF…";
        await RunAsync("Reading PS2 disc…", async (token, lines) =>
        {
            BuilderEvent verified = await Task.Run(() => LocalBuilder.IdentifyAsync(dialog.FileName, lines, token));
            selectedFingerprint = verified.BootSHA256;
            title.Text = verified.Title ?? verified.Serial ?? "PlayStation 2";
            edition.Text = string.Join(" · ", new[] { verified.Region, verified.Serial }
                .Where(text => !string.IsNullOrWhiteSpace(text)));
            title.Visible = edition.Visible = true;
            disc.ForeColor = Mint;
            disc.Text = "Boot ELF verified · Ready for local AOT build";
            status.Text = "Ready to build IPA";
        }, onFailure: () => disc.Text = "Disc verification failed. See the log.");
    }

    private async Task RunUpdatesAsync()
    {
        using var prompt = new Form
        {
            Text = "Check for Updates", StartPosition = FormStartPosition.CenterParent,
            FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false,
            ClientSize = new Size(410, 118), Font = Font, BackColor = Surface, ForeColor = Ink
        };
        prompt.HandleCreated += (_, _) => DarkTitleBar(prompt);
        var name = new TextBox { Text = repo, Dock = DockStyle.Top, BackColor = Rail, ForeColor = Ink };
        var confirm = Button("Check");
        confirm.DialogResult = DialogResult.OK;
        confirm.Dock = DockStyle.Bottom;
        prompt.Controls.Add(name);
        prompt.Controls.Add(new Label
        {
            Text = "PS2Apple release repository (optional OWNER/REPO)", Dock = DockStyle.Top, Height = 28
        });
        prompt.Controls.Add(confirm);
        prompt.AcceptButton = confirm;
        if (prompt.ShowDialog(this) != DialogResult.OK) return;
        repo = name.Text.Trim();
        SavePreferences();
        await RunAsync("Checking release information…", async (token, lines) =>
        {
            string result = await LocalBuilder.CheckUpdateAsync(repo, token);
            lines(result);
            status.Text = result;
        });
    }

    private async Task BuildAsync()
    {
        if (selectedFingerprint is not { } expected || isoPath is not { } source) return;
        string destination = outputPath;
        resultPath = null;
        savedIpa.Parent!.Visible = false;
        await RunAsync("Preparing the local build environment…", async (token, lines) =>
        {
            string result = await Task.Run(() => LocalBuilder.BuildAsync(source, destination, expected, lines, token));
            resultPath = result;
            savedIpa.Text = Path.GetFileName(result);
            paths.SetToolTip(savedIpa, result);
            savedIpa.Parent!.Visible = true;
            status.Text = "IPA built locally";
            Append($"IPA saved to {result}");
        });
    }

    private async Task RunAsync(string title,
        Func<CancellationToken, Action<string>, Task> task, Action? onFailure = null)
    {
        using var job = new CancellationTokenSource();
        running = job;
        status.Text = title;
        status.ForeColor = Muted;
        percent.Text = "";
        probeStart = null;
        progress.Value = 0;
        log.Clear();
        hasLog = false;
        RefreshButtons();
        try
        {
            await task(job.Token, line => OnUi(() => Consume(line)));
            progress.Value = 100;
        }
        catch (OperationCanceledException) { status.Text = "Cancelled"; onFailure?.Invoke(); }
        catch (Exception error)
        {
            status.Text = error.Message;
            status.ForeColor = Warning;
            Append(error.Message);
            onFailure?.Invoke();
        }
        finally
        {
            running = null;
            RefreshButtons();
            if (closing) Close();
        }
    }

    private void OnUi(Action action)
    {
        if (IsDisposed) return;
        if (InvokeRequired) Invoke(action);
        else action();
    }

    private void Consume(string line)
    {
        try
        {
            if (LocalBuilder.Parse(line) is { } message)
            {
                if (message.Kind == "progress" && message.Value is { } fraction)
                {
                    progress.Value = Math.Max(progress.Value, Math.Clamp((int)(fraction * 100), 0, 100));
                    percent.Text = $"{progress.Value}%";
                    status.Text = message.Detail ?? status.Text;
                }
                if (message.Kind == "boot-progress" && message.Current is { } current && message.Total is > 0)
                {
                    int total = message.Total.Value;
                    if (current == 0) probeStart = progress.Value;
                    int phasePercent = Math.Clamp((int)(100.0 * current / total), 0, 100);
                    status.Text = $"Checking SYSTEM.CNF boot · {current:N0}/{total:N0} frames ({phasePercent}%)";
                    progress.Value = Math.Clamp((probeStart ?? progress.Value) + phasePercent / 50, 0, 100);
                }
                if (message.Kind == "boot-progress-ended") probeStart = null;
                if (message.Kind is "check" or "update" or "error" && message.Detail is { } detail)
                    Append(message.Key is null ? detail : $"{message.Key} · {detail}");
                return;
            }
        }
        catch (JsonException) { }
        Append(line);
    }

    private void Append(string line)
    {
        if (!hasLog) log.Clear();
        hasLog = copyLog.Enabled = revealLog.Enabled = true;
        int selection = log.SelectionStart, length = log.SelectionLength;
        log.AppendText(line + Environment.NewLine);
        if (log.TextLength > 300000)
        {
            log.Select(0, 50000);
            log.SelectedText = "";
            selection = Math.Max(0, selection - 50000);
        }
        if (follow.Checked) log.ScrollToCaret();
        else log.Select(selection, Math.Min(length, log.TextLength - selection));
    }
}
