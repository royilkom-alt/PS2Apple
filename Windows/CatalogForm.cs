using System.Text.Json;
using static PS2Apple.DesktopTheme;

namespace PS2Apple;

internal sealed record Catalog(int Schema, CatalogGame[] Games);
internal sealed record CatalogGame(string Title, string Series, CatalogEdition[] Editions);
internal sealed record CatalogEdition(string Serial, string Region, int? TestedFPS, int? TestedMinFPS,
    string? TestedDevice, bool? TestedUnstable, string? ObservedGameplay)
{
    public bool Tested => TestedFPS is not null;
}

internal sealed class CatalogForm : Form
{
    private readonly TextBox search = new() { PlaceholderText = "Search title, series or disc serial", Width = 305 };
    private readonly CheckBox testedOnly = new() { Text = "Tested playable only", AutoSize = true };
    private readonly ListView results = new()
    {
        Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, HideSelection = false,
        BorderStyle = BorderStyle.None, BackColor = Surface, ForeColor = Ink, OwnerDraw = true
    };
    private readonly Catalog catalog;

    internal static Catalog ReadCatalog()
    {
        using Stream input = LocalBuilder.OpenResource("game-catalog.json");
        return JsonSerializer.Deserialize<Catalog>(input, LocalBuilder.Json) is { Schema: 1 } result
            ? result : throw new InvalidDataException("Could not open the PS2Apple game catalog.");
    }

    public CatalogForm()
    {
        SuspendLayout();
        catalog = ReadCatalog();
        Text = "Game Catalog";
        StartPosition = FormStartPosition.CenterParent;
        Font = new Font("Segoe UI", 10);
        AutoScaleMode = AutoScaleMode.None;
        BackColor = Canvas;
        ForeColor = Ink;
        Rectangle desktop = Screen.FromPoint(Cursor.Position).WorkingArea;
        double scale = Font.GetHeight() / Font.GetHeight(96);
        ClientSize = new Size(Math.Min((int)(1020 * scale), desktop.Width - 36),
                              Math.Min((int)(600 * scale), desktop.Height - 70));
        MinimumSize = new Size(650, 320);
        HandleCreated += (_, _) => DarkTitleBar(this);
        foreach (string heading in new[] { "Title", "Disc serial", "Region", "Recorded iPhone test" })
            results.Columns.Add(heading);
        results.SizeChanged += (_, _) => SizeColumns();
        results.DrawColumnHeader += (_, e) => DrawCell(e.Graphics, e.Bounds, e.Header?.Text ?? "", Muted, Rail);
        results.DrawSubItem += (_, e) =>
        {
            string text = e.SubItem?.Text ?? "";
            Color color = e.ColumnIndex == 3 && text.StartsWith("Tested by iSolution (Playable)") ? Mint :
                e.ColumnIndex == 3 && text.StartsWith("Tested by iSolution (Unstable)") ? Warning : Ink;
            Color background = e.Item?.Selected == true ? Tile : e.ItemIndex % 2 == 0 ? Surface : Rail;
            DrawCell(e.Graphics, e.Bounds, text, color, background);
        };
        var headingRow = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 2 };
        headingRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        headingRow.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        headingRow.Controls.Add(Label("Game Catalog", 17, true), 0, 0);
        var done = Button("Done");
        done.DialogResult = DialogResult.Cancel;
        CancelButton = done;
        headingRow.Controls.Add(done, 1, 0);
        search.BackColor = Rail;
        search.ForeColor = Ink;
        search.Dock = DockStyle.Fill;
        testedOnly.ForeColor = Muted;
        var filter = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 2 };
        filter.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        filter.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        filter.Controls.Add(search, 0, 0);
        filter.Controls.Add(testedOnly, 1, 0);
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding((int)(18 * scale)), ColumnCount = 1 };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.Controls.Add(headingRow);
        layout.Controls.Add(Label("Choose an ISO in the builder. PS2Apple matches the disc and its boot revision automatically.",
            9, color: Muted));
        layout.Controls.Add(filter);
        layout.Controls.Add(results);
        Controls.Add(layout);
        search.TextChanged += (_, _) => Render();
        testedOnly.CheckedChanged += (_, _) => Render();
        Render();
        ResumeLayout();
    }

    private void SizeColumns()
    {
        int available = Math.Max(0, results.ClientSize.Width - SystemInformation.VerticalScrollBarWidth);
        double[] proportions = [.29, .11, .085, .515];
        for (int i = 0; i < proportions.Length; i++) results.Columns[i].Width = (int)(available * proportions[i]);
    }

    private void DrawCell(Graphics graphics, Rectangle bounds, string text, Color textColor, Color background)
    {
        using var brush = new SolidBrush(background);
        graphics.FillRectangle(brush, bounds);
        bounds.Inflate(-5, 0);
        if (bounds.Width > 0)
            TextRenderer.DrawText(graphics, text, results.Font, bounds, textColor,
                TextFormatFlags.EndEllipsis | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);
    }

    private void Render()
    {
        string query = search.Text.Trim();
        results.BeginUpdate();
        results.Items.Clear();
        foreach (CatalogGame game in catalog.Games.OrderByDescending(game => game.Editions.Any(edition => edition.Tested)))
        foreach (CatalogEdition edition in game.Editions)
        {
            if (testedOnly.Checked && !edition.Tested) continue;
            if (!new[] { game.Title, game.Series, edition.Serial }
                    .Any(text => text.Contains(query, StringComparison.OrdinalIgnoreCase))) continue;
            string test = "Not Tested";
            if (edition.TestedFPS is { } fps)
            {
                string rate = edition.TestedMinFPS is { } low ? $"{low}–{fps} FPS" : $"{fps} FPS";
                test = $"Tested by iSolution (Playable) · {edition.TestedDevice} · {rate}";
            }
            else if (edition.TestedUnstable == true)
                test = $"Tested by iSolution (Unstable) · {edition.TestedDevice} · {edition.ObservedGameplay}";
            results.Items.Add(new ListViewItem([game.Title, edition.Serial, edition.Region, test]));
        }
        results.EndUpdate();
    }
}
