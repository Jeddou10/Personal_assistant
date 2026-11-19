"""Shared colors, typography and vector soccer-ball artwork."""

from tkinter import ttk

BG = "#f5f4ef"
PANEL = "#ffffff"
TEXT = "#253b34"
MUTED = "#687b72"
ACCENT = "#32785f"
BORDER = "#e1e6df"
SOFT = "#eaf2eb"


def draw_soccer_ball(canvas, size=60):
    """Scalable black-and-white football, drawn without font or image assets."""
    from math import cos, radians, sin

    scale = size / 60
    ink = "#253039"

    def point(angle, radius):
        angle = radians(angle)
        return (30 + radius * cos(angle)) * scale, (30 + radius * sin(angle)) * scale

    def polygon(points, **options):
        canvas.create_polygon(*[coordinate for point in points for coordinate in point], **options)

    canvas.create_oval(3 * scale, 4 * scale, 57 * scale, 58 * scale, fill="#c8d1cc", outline="")
    canvas.create_oval(3 * scale, 2 * scale, 57 * scale, 56 * scale, fill="#fafcfb", outline=ink, width=1.5 * scale)
    # The five seams connect a central pentagon to foreshortened edge panels.
    angles = [-90 + index * 72 for index in range(5)]
    for angle in angles:
        canvas.create_line(*point(angle, 10.5), *point(angle, 19), fill=ink, width=1.2 * scale)
        polygon(
            [
                point(angle, 19),
                point(angle - 17, 23),
                point(angle - 12, 26),
                point(angle + 12, 26),
                point(angle + 17, 23),
            ],
            fill=ink,
            outline=ink,
            width=0.7 * scale,
        )
    polygon([point(angle, 10.5) for angle in angles], fill=ink, outline=ink)


# Retain the previous public drawing entry point for existing callers.
draw_cat = draw_soccer_ball


def configure_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    root.option_add("*Font", ("Segoe UI", 10))
    style.configure(
        "TButton",
        background=PANEL,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=PANEL,
        darkcolor=PANEL,
        padding=(12, 8),
        relief="flat",
        font=("Segoe UI", 10),
    )
    style.map("TButton", background=[("active", SOFT), ("pressed", "#dce9de")], foreground=[("disabled", "#99a79f")])
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="white",
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", "#a4baae"), ("active", "#28634e")],
        foreground=[("disabled", "#f4f6f2"), ("active", "white")],
    )
    style.configure("TEntry", fieldbackground=PANEL, foreground=TEXT, padding=7, bordercolor=BORDER, insertcolor=TEXT)
    style.configure("TCombobox", fieldbackground=PANEL, background=SOFT, foreground=TEXT, padding=6)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
    style.configure("Vertical.TScrollbar", background=BORDER, troughcolor=PANEL, arrowcolor=MUTED, bordercolor=PANEL)
