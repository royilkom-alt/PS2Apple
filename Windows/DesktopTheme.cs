using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;

namespace PS2Apple;

internal static class DesktopTheme
{
    public static readonly Color Canvas = Color.FromArgb(18, 21, 27);
    public static readonly Color Rail = Color.FromArgb(24, 28, 35);
    public static readonly Color Surface = Color.FromArgb(29, 33, 41);
    public static readonly Color Line = Color.FromArgb(48, 53, 63);
    public static readonly Color Ink = Color.FromArgb(239, 243, 247);
    public static readonly Color Muted = Color.FromArgb(158, 171, 185);
    public static readonly Color Mint = Color.FromArgb(102, 227, 191);
    public static readonly Color Warning = Color.FromArgb(248, 171, 112);
    public static readonly Color Tile = Color.FromArgb(31, 55, 53);

    public static Label Label(string text, float size = 10, bool strong = false, Color? color = null) => new()
    {
        Text = text, AutoSize = true, ForeColor = color ?? Ink,
        Font = new Font("Segoe UI", size, strong ? FontStyle.Bold : FontStyle.Regular)
    };

    public static Button Button(string text, bool primary = false) => new ActionButton(primary)
    {
        Text = text, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
        Padding = new Padding(9, 4, 9, 4), FlatStyle = FlatStyle.Flat, UseVisualStyleBackColor = false,
        BackColor = primary ? Mint : Rail, ForeColor = primary ? Canvas : Ink,
        Font = new Font("Segoe UI", 9, primary ? FontStyle.Bold : FontStyle.Regular)
    };

    public static Image Image(string resource)
    {
        using Stream stream = LocalBuilder.OpenResource(resource);
        using var original = System.Drawing.Image.FromStream(stream);
        return new Bitmap(original);
    }

    public static void DarkTitleBar(Form form)
    {
        int enabled = 1;
        DwmSetWindowAttribute(form.Handle, 20, ref enabled, sizeof(int));
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);

    private sealed class ActionButton(bool primary) : Button
    {
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Parent?.BackColor ?? Canvas);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Color face = !Enabled ? Line : primary ? Mint : Rail;
            using var brush = new SolidBrush(face);
            using var edge = new Pen(primary && Enabled ? Mint : Line);
            Rectangle bounds = Rectangle.Inflate(ClientRectangle, -1, -1);
            using var path = Rounded(bounds, 6);
            e.Graphics.FillPath(brush, path);
            e.Graphics.DrawPath(edge, path);
            Color color = !Enabled ? Muted : primary ? Canvas : Ink;
            if (Image is { } image)
            {
                int side = Math.Min(bounds.Height - 8, 20);
                e.Graphics.DrawImage(image, (Width - side) / 2, (Height - side) / 2, side, side);
            }
            else
            {
                TextRenderer.DrawText(e.Graphics, Text, Font, ClientRectangle, color,
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);
            }
            if (Focused && ShowFocusCues)
                ControlPaint.DrawFocusRectangle(e.Graphics, Rectangle.Inflate(ClientRectangle, -4, -4), color, face);
        }
    }

    private static GraphicsPath Rounded(Rectangle bounds, int diameter)
    {
        var path = new GraphicsPath();
        diameter = Math.Min(diameter, Math.Min(bounds.Width, bounds.Height));
        if (diameter < 2)
        {
            path.AddRectangle(bounds);
            return path;
        }
        path.AddArc(bounds.Left, bounds.Top, diameter, diameter, 180, 90);
        path.AddArc(bounds.Right - diameter, bounds.Top, diameter, diameter, 270, 90);
        path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
        path.AddArc(bounds.Left, bounds.Bottom - diameter, diameter, diameter, 90, 90);
        path.CloseFigure();
        return path;
    }
}
