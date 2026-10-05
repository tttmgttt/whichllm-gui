"""界面主题：深色配色、字体与 ttk 样式。

Windows 自带的 vista 主题会忽略大部分颜色设置，因此这里统一改用 clam 主题再
自行绘制，保证在 Windows / macOS / Linux 上观感一致。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

PALETTE = {
    "bg": "#14171c",
    "surface": "#1a1e25",
    "surface_alt": "#20252e",
    "surface_hi": "#262c37",
    "border": "#2c323c",
    "border_hi": "#3a424f",
    "fg": "#e8ecf3",
    "fg_dim": "#98a2b3",
    "fg_muted": "#6b7484",
    "accent": "#4c8dff",
    "accent_hi": "#6ba1ff",
    "accent_dim": "#2b4d8f",
    "accent_fg": "#ffffff",
    "ok": "#46c98b",
    "warn": "#e8b04b",
    "danger": "#f2707a",
    "info": "#57b8e8",
    "selection": "#2b3b57",
    "odd": "#1d2229",
    "even": "#191d24",
}


def _pick_font(candidates: list[str], fallback: str = "TkDefaultFont") -> str:
    try:
        available = {name.lower() for name in tkfont.families()}
    except Exception:  # noqa: BLE001 - 极少数环境下取不到字体列表
        return fallback
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback


class Fonts:
    """按平台挑选合适的中英文字体。"""

    def __init__(self) -> None:
        ui = _pick_font(
            [
                "Microsoft YaHei UI",
                "Microsoft YaHei",
                "PingFang SC",
                "Noto Sans CJK SC",
                "Source Han Sans SC",
                "Segoe UI",
            ]
        )
        mono = _pick_font(
            ["Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo", "DejaVu Sans Mono"],
            "TkFixedFont",
        )
        self.ui = ui
        self.mono = mono
        self.body = (ui, 10)
        self.small = (ui, 9)
        self.tiny = (ui, 8)
        self.bold = (ui, 10, "bold")
        self.title = (ui, 15, "bold")
        self.subtitle = (ui, 11, "bold")
        self.big_value = (ui, 13, "bold")
        self.mono_body = (mono, 9)
        self.mono_small = (mono, 8)


def apply_theme(root: tk.Misc) -> Fonts:
    """给整个应用套上深色主题，返回字体表。"""
    fonts = Fonts()
    palette = PALETTE
    style = ttk.Style(root)

    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=palette["bg"])
    root.option_add("*Font", fonts.body)
    root.option_add("*TearOff", False)

    # 容器
    style.configure("TFrame", background=palette["bg"])
    style.configure("Surface.TFrame", background=palette["surface"])
    style.configure("SurfaceAlt.TFrame", background=palette["surface_alt"])
    style.configure("Header.TFrame", background=palette["surface"])
    style.configure("Toolbar.TFrame", background=palette["surface"])
    style.configure("Status.TFrame", background=palette["surface"])
    style.configure("Divider.TFrame", background=palette["border"])

    # 文本
    style.configure("TLabel", background=palette["bg"], foreground=palette["fg"], font=fonts.body)
    style.configure("Surface.TLabel", background=palette["surface"], foreground=palette["fg"], font=fonts.body)
    style.configure("SurfaceDim.TLabel", background=palette["surface"], foreground=palette["fg_dim"], font=fonts.small)
    style.configure("Dim.TLabel", background=palette["bg"], foreground=palette["fg_dim"], font=fonts.small)
    style.configure("Title.TLabel", background=palette["surface"], foreground=palette["fg"], font=fonts.title)
    style.configure("Subtitle.TLabel", background=palette["bg"], foreground=palette["fg"], font=fonts.subtitle)
    style.configure("Section.TLabel", background=palette["bg"], foreground=palette["accent"], font=fonts.bold)
    style.configure("Value.TLabel", background=palette["surface"], foreground=palette["fg"], font=fonts.big_value)
    style.configure("Ok.TLabel", background=palette["bg"], foreground=palette["ok"], font=fonts.bold)
    style.configure("Warn.TLabel", background=palette["bg"], foreground=palette["warn"], font=fonts.bold)
    style.configure("Danger.TLabel", background=palette["bg"], foreground=palette["danger"], font=fonts.bold)
    style.configure("CardTitle.TLabel", background=palette["surface"], foreground=palette["accent"], font=fonts.bold)
    style.configure("CardKey.TLabel", background=palette["surface"], foreground=palette["fg_dim"], font=fonts.small)
    style.configure("CardDim.TLabel", background=palette["surface"], foreground=palette["fg_muted"], font=fonts.small)
    style.configure("CardValue.TLabel", background=palette["surface"], foreground=palette["fg"], font=fonts.body)
    style.configure("CardMono.TLabel", background=palette["surface"], foreground=palette["fg"], font=fonts.mono_small)

    # 按钮
    style.configure(
        "TButton",
        background=palette["surface_hi"],
        foreground=palette["fg"],
        bordercolor=palette["border_hi"],
        focuscolor=palette["accent"],
        lightcolor=palette["surface_hi"],
        darkcolor=palette["surface_hi"],
        relief="flat",
        padding=(12, 6),
        font=fonts.body,
    )
    style.map(
        "TButton",
        background=[("pressed", palette["accent_dim"]), ("active", palette["border_hi"]), ("disabled", palette["surface"])],
        foreground=[("disabled", palette["fg_muted"])],
    )
    style.configure(
        "Accent.TButton",
        background=palette["accent"],
        foreground=palette["accent_fg"],
        bordercolor=palette["accent"],
        lightcolor=palette["accent"],
        darkcolor=palette["accent"],
        relief="flat",
        padding=(14, 6),
        font=fonts.bold,
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", palette["accent_dim"]), ("active", palette["accent_hi"]), ("disabled", palette["surface_hi"])],
        foreground=[("disabled", palette["fg_muted"])],
    )
    style.configure(
        "Ghost.TButton",
        background=palette["surface"],
        foreground=palette["fg_dim"],
        bordercolor=palette["border"],
        relief="flat",
        padding=(10, 5),
        font=fonts.small,
    )
    style.map(
        "Ghost.TButton",
        background=[("active", palette["surface_hi"])],
        foreground=[("active", palette["fg"])],
    )
    style.configure("Small.TButton", padding=(8, 3), font=fonts.small)

    # 输入控件
    for name in ("TEntry", "TSpinbox"):
        style.configure(
            name,
            fieldbackground=palette["surface_alt"],
            background=palette["surface_alt"],
            foreground=palette["fg"],
            bordercolor=palette["border_hi"],
            lightcolor=palette["border_hi"],
            darkcolor=palette["border_hi"],
            insertcolor=palette["fg"],
            arrowcolor=palette["fg_dim"],
            padding=4,
        )
        style.map(
            name,
            fieldbackground=[("disabled", palette["surface"]), ("readonly", palette["surface_alt"])],
            foreground=[("disabled", palette["fg_muted"])],
        )
    style.configure(
        "TCombobox",
        fieldbackground=palette["surface_alt"],
        background=palette["surface_alt"],
        foreground=palette["fg"],
        bordercolor=palette["border_hi"],
        lightcolor=palette["border_hi"],
        darkcolor=palette["border_hi"],
        arrowcolor=palette["fg_dim"],
        padding=4,
        selectbackground=palette["selection"],
        selectforeground=palette["fg"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette["surface_alt"]), ("disabled", palette["surface"])],
        foreground=[("disabled", palette["fg_muted"])],
    )
    # 下拉列表（Tk 原生 listbox，需要单独配置）
    root.option_add("*TCombobox*Listbox.background", palette["surface_alt"])
    root.option_add("*TCombobox*Listbox.foreground", palette["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", palette["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", palette["accent_fg"])
    root.option_add("*TCombobox*Listbox.font", fonts.body)

    style.configure(
        "TCheckbutton",
        background=palette["bg"],
        foreground=palette["fg"],
        focuscolor=palette["accent"],
        indicatorcolor=palette["surface_alt"],
        padding=2,
    )
    style.map(
        "TCheckbutton",
        background=[("active", palette["bg"])],
        foreground=[("disabled", palette["fg_muted"])],
        indicatorcolor=[("selected", palette["accent"]), ("!selected", palette["surface_alt"])],
    )
    style.configure(
        "Surface.TCheckbutton",
        background=palette["surface"],
        foreground=palette["fg"],
        focuscolor=palette["accent"],
    )
    style.map(
        "Surface.TCheckbutton",
        background=[("active", palette["surface"])],
        indicatorcolor=[("selected", palette["accent"]), ("!selected", palette["surface_alt"])],
    )

    style.configure(
        "TLabelframe",
        background=palette["bg"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        relief="solid",
    )
    style.configure(
        "TLabelframe.Label",
        background=palette["bg"],
        foreground=palette["accent"],
        font=fonts.bold,
    )
    style.configure("Card.TLabelframe", background=palette["surface"], bordercolor=palette["border"])
    style.configure("Card.TLabelframe.Label", background=palette["surface"], foreground=palette["accent"], font=fonts.bold)

    # 表格
    style.configure(
        "Treeview",
        background=palette["even"],
        fieldbackground=palette["even"],
        foreground=palette["fg"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        rowheight=24,
        font=fonts.body,
    )
    style.map(
        "Treeview",
        background=[("selected", palette["selection"])],
        foreground=[("selected", palette["fg"])],
    )
    style.configure(
        "Treeview.Heading",
        background=palette["surface_hi"],
        foreground=palette["fg_dim"],
        bordercolor=palette["border"],
        lightcolor=palette["surface_hi"],
        darkcolor=palette["surface_hi"],
        relief="flat",
        padding=(6, 5),
        font=fonts.small,
    )
    style.map(
        "Treeview.Heading",
        background=[("active", palette["border_hi"])],
        foreground=[("active", palette["fg"])],
    )

    # 选项卡
    style.configure("TNotebook", background=palette["bg"], bordercolor=palette["border"], tabmargins=(6, 4, 6, 0))
    style.configure(
        "TNotebook.Tab",
        background=palette["surface"],
        foreground=palette["fg_dim"],
        padding=(16, 8),
        font=fonts.body,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", palette["bg"]), ("active", palette["surface_hi"])],
        foreground=[("selected", palette["accent"]), ("active", palette["fg"])],
        expand=[("selected", (0, 0, 0, 0))],
    )

    # 进度条 / 滚动条
    style.configure(
        "Horizontal.TProgressbar",
        background=palette["accent"],
        troughcolor=palette["surface_alt"],
        bordercolor=palette["surface_alt"],
        lightcolor=palette["accent"],
        darkcolor=palette["accent"],
        thickness=4,
    )
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=palette["surface_hi"],
            troughcolor=palette["surface"],
            bordercolor=palette["surface"],
            arrowcolor=palette["fg_dim"],
            lightcolor=palette["surface_hi"],
            darkcolor=palette["surface_hi"],
        )
        style.map(
            f"{orient}.TScrollbar",
            background=[("active", palette["border_hi"])],
        )

    style.configure("TSeparator", background=palette["border"])
    style.configure("TPanedwindow", background=palette["bg"])
    style.configure("Sash", sashthickness=6, gripcount=0, background=palette["bg"])

    return fonts


def style_text_widget(widget: tk.Text, fonts: Fonts, *, mono: bool = False) -> None:
    """给原生 Text 控件套用主题。"""
    widget.configure(
        background=PALETTE["surface"],
        foreground=PALETTE["fg"],
        insertbackground=PALETTE["fg"],
        selectbackground=PALETTE["selection"],
        selectforeground=PALETTE["fg"],
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=PALETTE["border"],
        highlightcolor=PALETTE["border_hi"],
        font=fonts.mono_body if mono else fonts.body,
        wrap="word" if not mono else "none",
        padx=10,
        pady=8,
    )
