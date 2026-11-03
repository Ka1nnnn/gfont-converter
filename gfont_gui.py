"""GFont Converter — dark tkinter GUI for KDraw/Kenjoy .gfont fonts.

Modes:
  gfont_gui.py                     -> GUI (default)
  gfont_gui.py --smoke [files...]  -> headless self-check, no windows, exit 0/1
  gfont_gui.py --convert IN -o OUT [--target V5]  -> headless conversion

GUI features: file open / drag&drop, glyph grid preview with zoom, codepoint
search, per-glyph detail with SVG/PNG export, V3<->V5 conversion, batch mode
on a folder, export of all glyphs to SVG, human-readable validation errors.
Inputs V3/V5/V6/V7/V8/V9 and bare ZIP are supported; recoverable issues
(stale glyph counter in the metadata, unknown V8 metadata trailer, command 2
approximated by a line) are reported as warnings, not hard errors.
"""
from __future__ import annotations

import argparse
import ctypes
import re
import sys
import threading
import time
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import gfont_converter as converter
import glyph_render as render

# --- Theme ----------------------------------------------------------------

BG = "#10151d"
PANEL = "#181f2a"
PANEL2 = "#222c39"
BORDER = "#2e3b4d"
FG = "#edf2f8"
MUTED = "#a1afc2"
ACCENT = "#5b9cf2"
OK = "#48c89a"
WARN = "#e9c16f"
ERR = "#ef7880"
GRID_BG = "#0c121a"
TILE_BG = "#151d27"
TILE_BORDER = "#293747"

CELL_DEFAULT = 72
TILE_LABEL_H = 20
GRID_GAP = 10

# --- Helpers ---------------------------------------------------------------


def _apply_dark_titlebar(root: tk.Misc) -> None:
    """Paint the native title bar dark (DWM immersive dark mode), best effort."""
    try:
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20, older 19)
            if dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value),
            ) == 0:
                break
    except Exception:
        pass


def _fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _fmt_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("Б", "КиБ", "МиБ", "ГиБ"):
        if value < 1024 or unit == "ГиБ":
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} {unit}"
        value /= 1024
    return f"{num_bytes} Б"


def _truncate(text: str, limit: int = 28) -> str:
    text = text.replace("\n", "\\n").replace("\r", "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plural_ru(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


_GLYPH_COUNT_WARNING_RE = re.compile(r"glyph_count\D+(\d+)\D+ZIP\D+(\d+)", re.IGNORECASE)


def _humanize_warning(warning: str) -> str:
    """Core parser warning -> human-readable Russian text (pass-through otherwise)."""
    match = _GLYPH_COUNT_WARNING_RE.search(warning)
    if match:
        declared, actual = match.groups()
        return (
            f"Счётчик глифов в файле протух ({declared}≠{actual}) — "
            f"использовано фактическое {actual} (по ZIP); файл цел, "
            f"это баг редактора 奎享字体"
        )
    return warning


def _format_warnings(warnings: list[str]) -> list[str]:
    """Humanized warnings, capped at 10 entries plus an "…and K more" tail."""
    shown = [_humanize_warning(warning) for warning in warnings[:10]]
    if len(warnings) > 10:
        rest = len(warnings) - 10
        shown.append(
            f"…и ещё {rest} {_plural_ru(rest, 'предупреждение', 'предупреждения', 'предупреждений')}"
        )
    return shown


def _safe_char(codepoint: int) -> str:
    if 0x20 <= codepoint <= 0x10FFFF and not (0xD800 <= codepoint <= 0xDFFF):
        try:
            return chr(codepoint)
        except (ValueError, OverflowError):
            return ""
    return ""


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


# --- Drag & drop (WM_DROPFILES, no external deps) --------------------------

WM_DROPFILES = 0x0233
GWL_WNDPROC = -4


def enable_file_drop(widget: tk.Misc, callback) -> object | None:
    """Accept Explorer file drops; returns the WNDPROC guard or None."""
    try:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
        )
        user32.CallWindowProcW.argtypes = [
            WNDPROC, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, ctypes.c_int]
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p]
        shell32.DragQueryFileW.restype = ctypes.c_uint
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        set_proc = getattr(user32, "SetWindowLongPtrW", None)
        if set_proc is not None:
            set_proc.argtypes = [ctypes.c_void_p, ctypes.c_int, WNDPROC]
            set_proc.restype = WNDPROC
            get_proc = user32.GetWindowLongPtrW
        else:  # 32-bit Python fallback
            set_proc = user32.SetWindowLongW
            set_proc.argtypes = [ctypes.c_void_p, ctypes.c_int, WNDPROC]
            set_proc.restype = WNDPROC
            get_proc = user32.GetWindowLongW
        get_proc.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_proc.restype = WNDPROC

        hwnd = widget.winfo_id()
        target = user32.GetParent(hwnd) or hwnd
        shell32.DragAcceptFiles(target, True)
        old = get_proc(target, GWL_WNDPROC)

        def wndproc(handle, message, wparam, lparam):
            if message == WM_DROPFILES:
                files: list[str] = []
                try:
                    count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None)
                    buffer = ctypes.create_unicode_buffer(1024)
                    for index in range(min(count, 64)):
                        shell32.DragQueryFileW(wparam, index, buffer)
                        files.append(buffer.value)
                finally:
                    shell32.DragFinish(wparam)
                try:
                    if files:
                        callback(files)
                except Exception:
                    pass
                return 0
            return user32.CallWindowProcW(old, handle, message, wparam, lparam)

        guard = WNDPROC(wndproc)
        set_proc(target, GWL_WNDPROC, guard)
        return guard
    except Exception:
        return None


# --- Headless smoke ---------------------------------------------------------

# Smoke checks default to the fixtures shipped with the repository. Pass
# explicit paths to --smoke to check a custom corpus on the current machine.
_DEFAULT_FIXTURES = (
    Path(__file__).resolve().parent / "tests" / "fixtures" / "gfont_versions" / "*.gfont"
)

DEFAULT_SMOKE_PATTERNS = (
    str(_DEFAULT_FIXTURES),
)


def _collect_smoke_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    if paths:
        for item in paths:
            candidate = Path(item)
            if candidate.is_dir():
                files.extend(sorted(candidate.glob("*.gfont")))
            else:
                files.append(candidate)
    else:
        for pattern in DEFAULT_SMOKE_PATTERNS:
            base = Path(pattern)
            files.extend(sorted(base.parent.glob(base.name)))
    unique: dict[str, Path] = {}
    for file_path in files:
        if file_path.is_file():
            unique[str(file_path).lower()] = file_path
    return [unique[key] for key in sorted(unique)]


def _smoke_one(path: Path) -> tuple[bool, str]:
    """Returns (ok, line) for one .gfont file."""
    data = path.read_bytes()
    warnings: list[str] = []
    parsed = converter.parse_gfont_bytes(data, warnings=warnings)
    points, strokes = render.stroke_stats(parsed.glyphs)
    png_ok = svg_ok = True
    for glyph in parsed.glyphs[:3]:
        png = render.png_bytes(glyph, size=192)
        svg = render.svg_string(glyph)
        if len(png) < 200 or b"IEND" not in png:
            png_ok = False
        if "<polyline" not in svg and "<circle" not in svg:
            svg_ok = False

    def roundtrip(target: int) -> str:
        try:
            rebuilt = converter.parse_gfont_bytes(converter.build_gfont(parsed, target))
            if len(rebuilt.glyphs) != len(parsed.glyphs):
                return "FAIL(count)"
            return "ok"
        except converter.GFontError as exc:
            if "does not fit Java char" in str(exc):
                return "skip(>BMP)"
            return f"FAIL({exc})"
        except (ValueError, zipfile.BadZipFile, OSError) as exc:
            return f"FAIL({exc})"

    rt5 = roundtrip(5)
    rt3 = roundtrip(3)

    corrupted_positions = sorted({4, len(data) // 4, len(data) // 2, (3 * len(data)) // 4, len(data) - 12})
    negative = "FAIL(no-detect)"
    for position in corrupted_positions:
        if not 0 <= position < len(data):
            continue
        corrupted = bytearray(data)
        corrupted[position] ^= 0xFF
        try:
            converter.parse_gfont_bytes(bytes(corrupted))
        except Exception:  # any parse rejection counts as detection
            negative = "ok"
            break

    roundtrips_ok = all(rt.startswith(("ok", "skip")) for rt in (rt5, rt3))
    ok = png_ok and svg_ok and roundtrips_ok and negative == "ok"
    line = (
        f"{'OK ' if ok else 'BAD'} {path.name:<22} v{parsed.version} "
        f"glyphs={len(parsed.glyphs):<5} points={points:<7} strokes={strokes:<6} "
        f"size={_fmt_size(len(data)):<12} png={'ok' if png_ok else 'FAIL'} "
        f"svg={'ok' if svg_ok else 'FAIL'} rt5={rt5} rt3={rt3} neg={negative} "
        f"warn={len(warnings)}"
    )
    return ok, line


def _print(message: str) -> None:
    """Print without failing in windowed exe (stdout may be a stub)."""
    try:
        print(message)
    except (OSError, ValueError, AttributeError):
        pass


def run_smoke(paths: list[str], report_path: str | None) -> int:
    _configure_console()
    files = _collect_smoke_files(paths)
    lines: list[str] = []
    if not files:
        lines.append("SMOKE: no .gfont files found to test")
        _print(lines[0])
        if report_path:
            Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1
    passed = 0
    for path in files:
        try:
            ok, line = _smoke_one(path)
        except Exception as exc:
            ok = False
            line = f"BAD {path.name:<22} exception: {converter.describe_error(exc)}"
        passed += 1 if ok else 0
        lines.append(line)
        _print(line)
    summary = f"SMOKE {'OK' if passed == len(files) else 'FAILED'}: {passed}/{len(files)} files passed"
    lines.append(summary)
    _print(summary)
    if report_path:
        Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if passed == len(files) else 1


def _convert_lenient(
    source: Path, destination: Path, target: int
) -> tuple[converter.ParsedGfont, list[str]]:
    """Read + convert with the lenient warnings path (stale counters do not fail)."""
    warnings: list[str] = []
    parsed = converter.parse_gfont_bytes(source.read_bytes(), warnings=warnings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(converter.build_gfont(parsed, target))
    return parsed, warnings


def run_convert(input_path: str, output_path: str | None, target: int) -> int:
    _configure_console()
    source = Path(input_path)
    if not source.is_file():
        print(f"error: not a file: {source}", file=sys.stderr)
        return 2
    destination = Path(output_path) if output_path else source.with_name(f"{source.stem}_v{target}.gfont")
    try:
        parsed, warnings = _convert_lenient(source, destination, target)
        verified = converter.parse_gfont_bytes(destination.read_bytes())
    except (converter.GFontError, OSError, ValueError) as exc:
        print(f"error: {converter.describe_error(exc)}", file=sys.stderr)
        return 1
    for warning in _format_warnings(warnings):
        print(f"warning: {warning}")
    print(
        f"ok: {source.name} v{parsed.version} ({len(parsed.glyphs)} glyphs) -> "
        f"{destination} v{verified.version} ({len(verified.glyphs)} glyphs)"
    )
    return 0


# --- GUI -------------------------------------------------------------------


class GFontApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.parsed: converter.ParsedGfont | None = None
        self.path: Path | None = None
        self.file_size = 0
        self.view: list[converter.Glyph] = []
        self.selected: int | None = None
        self.cell = CELL_DEFAULT
        self.cols = 1
        self._drawn: set[int] = set()
        self._draw_job: str | None = None
        self._filter_job: str | None = None
        self._busy = False
        self._drop_guard = None
        self._tile_font = tkfont.Font(family="Segoe UI", size=8)

        root.title("GFont Converter")
        root.geometry("1240x800")
        root.minsize(980, 700)
        root.configure(bg=BG)
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)
        self._apply_theme()

        self._build_toolbar()
        self._build_main()
        self._build_log()
        self._build_status()

        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.after(60, lambda: _apply_dark_titlebar(root))
        root.after(80, self._attach_drop)
        root.after(100, self._layout_grid)
        self._set_status("Откройте .gfont файл или перетащите его в окно.")

    # -- theme ---------------------------------------------------------------

    def _apply_theme(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Segoe UI", 10)
        style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=base)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("Card.TLabel", background=PANEL, foreground=FG)
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("TLabelframe", background=BG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TLabelframe.Label", background=BG, foreground=FG, font=("Segoe UI", 9, "bold"))
        style.configure(
            "TButton", background=PANEL2, foreground=FG, bordercolor=BORDER,
            lightcolor=PANEL2, darkcolor=PANEL2, focuscolor=ACCENT, focusthickness=1, padding=(12, 6),
        )
        style.map(
            "TButton",
            background=[("disabled", PANEL), ("pressed", "#333d4e"), ("active", "#2a3342")],
            foreground=[("disabled", MUTED)],
            bordercolor=[("focus", ACCENT), ("active", ACCENT)],
        )
        style.configure(
            "Accent.TButton", background=ACCENT, foreground="#ffffff",
            lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=ACCENT,
            font=("Segoe UI", 10, "bold"), focuscolor="#b8d5ff", focusthickness=1,
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", PANEL), ("pressed", "#3f7ed1"), ("active", "#70a9f6")],
            foreground=[("disabled", MUTED)],
        )
        style.configure(
            "TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG,
            bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=4,
        )
        style.map(
            "TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
        )
        style.configure(
            "Path.TEntry", fieldbackground=PANEL2, foreground=FG, insertcolor=FG,
            bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=5,
        )
        style.map("Path.TEntry", bordercolor=[("focus", ACCENT)])
        style.configure(
            "TCombobox", fieldbackground=PANEL, foreground=FG, arrowcolor=FG,
            bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=3,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL)],
            foreground=[("readonly", FG)],
            bordercolor=[("focus", ACCENT)],
        )
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure(
            "Vertical.TScrollbar", background=PANEL2, troughcolor=BG,
            bordercolor=BG, arrowcolor=MUTED, lightcolor=PANEL2, darkcolor=PANEL2,
        )
        style.map("Vertical.TScrollbar", background=[("active", "#2a3342")])
        style.configure(
            "Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL,
            bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT,
        )
        style.configure(
            "Horizontal.TScale", background=BG, troughcolor=PANEL,
            bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT,
        )

    # -- layout ---------------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(12, 12, 12, 8))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)

        self.btn_open = ttk.Button(bar, text="Открыть .gfont", command=self._browse_open)
        self.btn_open.grid(row=0, column=0, sticky="w")

        self.path_var = tk.StringVar(value="")
        self.path_entry = ttk.Entry(
            bar, textvariable=self.path_var, state="readonly", width=14,
            font=("Segoe UI", 9), style="Path.TEntry",
        )
        self.path_entry.grid(row=0, column=1, sticky="ew", padx=(10, 10))

        actions = ttk.Frame(bar)
        actions.grid(row=0, column=2, sticky="e")
        ttk.Label(actions, text="Конвертировать в:", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.target_var = tk.StringVar(value="V5")
        self.target_box = ttk.Combobox(
            actions, textvariable=self.target_var, values=("V3", "V5"),
            state="readonly", width=4,
        )
        self.target_box.grid(row=0, column=1, padx=(0, 8))
        self.btn_convert = ttk.Button(
            actions, text="Конвертировать", style="Accent.TButton", command=self._convert,
        )
        self.btn_convert.grid(row=0, column=2, padx=(0, 8))
        self.btn_batch = ttk.Button(actions, text="Папка (batch)", command=self._batch)
        self.btn_batch.grid(row=0, column=3, padx=(0, 8))
        self.btn_export_all = ttk.Button(actions, text="Все глифы → SVG", command=self._export_all_svg)
        self.btn_export_all.grid(row=0, column=4)

    def _build_main(self) -> None:
        main = ttk.Frame(self.root)
        main.grid(row=1, column=0, sticky="nsew", padx=12)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        # left: scrollable info column (panels stack taller than the window)
        left = ttk.Frame(main, width=286)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.left_canvas = tk.Canvas(left, width=270, bg=BG, highlightthickness=0)
        self.left_canvas.grid(row=0, column=0, sticky="nsw")
        self.left_vsb = ttk.Scrollbar(left, orient="vertical", command=self.left_canvas.yview)
        self.left_vsb.grid(row=0, column=1, sticky="ns")

        panel = ttk.Frame(self.left_canvas)
        self._left_panel = panel
        panel.columnconfigure(0, weight=1)
        self._left_win = self.left_canvas.create_window((0, 0), window=panel, anchor="nw")
        self.left_canvas.configure(yscrollcommand=self._on_left_yscroll)
        panel.bind("<Configure>", self._on_left_panel_configure)
        self.left_canvas.bind("<Configure>", self._on_left_canvas_configure)

        info = ttk.Labelframe(panel, text="Шрифт", padding=10)
        info.grid(row=0, column=0, sticky="ew")
        self.info_labels: dict[str, ttk.Label] = {}
        info_keys = ("имя", "версия", "размер", "глифов", "точек", "штрихов", "font_size", "перья (q)")
        for row, key in enumerate(info_keys):
            ttk.Label(info, text=key, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=1)
            value = ttk.Label(info, text="—", style="Card.TLabel", width=12)
            value.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=1)
            self.info_labels[key] = value
        self.glyph_note = ttk.Label(info, text="", style="Muted.TLabel")
        self.glyph_note.grid(row=info_keys.index("глифов"), column=2, sticky="w", padx=(6, 0), pady=1)
        info.columnconfigure(1, weight=1)

        meta = ttk.Labelframe(panel, text="Метаданные", padding=10)
        meta.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.meta_labels: dict[str, ttk.Label] = {}
        for row, key in enumerate(("e", "b", "a", "c", "n", "o")):
            ttk.Label(meta, text=key, style="Muted.TLabel", width=3).grid(
                row=row, column=0, sticky="w", pady=1,
            )
            value = ttk.Label(meta, text="—", style="Card.TLabel")
            value.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=1)
            self.meta_labels[key] = value
        meta.columnconfigure(1, weight=1)

        search = ttk.Labelframe(panel, text="Поиск глифа", padding=10)
        search.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        search.columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.search_var)
        entry.grid(row=0, column=0, sticky="ew")
        entry.configure(justify="left")
        self.search_var.trace_add("write", self._on_search_changed)
        ttk.Label(
            search,
            text="код в 10/16-ричном виде (65, 0x41, U+0410) или сам символ",
            style="Muted.TLabel", wraplength=228, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        zoom = ttk.Labelframe(panel, text="Зум превью", padding=10)
        zoom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        zoom.columnconfigure(0, weight=1)
        self.zoom_var = tk.IntVar(value=CELL_DEFAULT)
        scale = ttk.Scale(zoom, from_=40, to=150, variable=self.zoom_var, command=self._on_zoom)
        scale.grid(row=0, column=0, sticky="ew")
        self.zoom_label = ttk.Label(zoom, text=f"клетка {CELL_DEFAULT} px", style="Muted.TLabel")
        self.zoom_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._bind_left_wheel(self.left_canvas)
        self._bind_left_wheel(panel)

        # center: glyph grid
        center = ttk.Frame(main)
        center.grid(row=0, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            center, bg=GRID_BG, highlightthickness=1, highlightbackground=BORDER,
            xscrollincrement=20, yscrollincrement=20,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb = ttk.Scrollbar(center, orient="vertical", command=self._on_yview)
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.canvas.bind("<Button-1>", self._on_grid_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", lambda _event: self._layout_grid())
        self.canvas.bind("<Double-Button-1>", lambda _event: self._export_selected_png())

        # right: detail
        right = ttk.Frame(main, width=250)
        right.grid(row=0, column=2, sticky="nse", padx=(10, 0))
        right.grid_propagate(False)
        right.columnconfigure(0, weight=1)

        detail = ttk.Labelframe(right, text="Выбранный глиф", padding=10)
        detail.grid(row=0, column=0, sticky="ew")
        detail.columnconfigure(0, weight=1)
        self.detail_canvas = tk.Canvas(
            detail, width=210, height=210, bg=TILE_BG,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.detail_canvas.grid(row=0, column=0, pady=(0, 8))
        self.detail_info = tk.StringVar(value="—")
        ttk.Label(
            detail, textvariable=self.detail_info, style="CardMuted.TLabel", justify="left",
        ).grid(row=1, column=0, sticky="w")
        buttons = ttk.Frame(detail)
        buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="SVG", command=self._export_selected_svg).grid(
            row=0, column=0, sticky="ew", padx=(0, 4),
        )
        ttk.Button(buttons, text="PNG", command=self._export_selected_png).grid(row=0, column=1, sticky="ew")
        ttk.Button(buttons, text="Копировать символ", command=self._copy_symbol).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0),
        )

    # -- left panel scrolling ---------------------------------------------------

    def _on_left_panel_configure(self, _event) -> None:
        self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))

    def _on_left_canvas_configure(self, event) -> None:
        self.left_canvas.itemconfigure(self._left_win, width=event.width)

    def _on_left_yscroll(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.left_vsb.grid_remove()
        else:
            self.left_vsb.grid()
        self.left_vsb.set(first, last)

    def _bind_left_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_left_wheel)
        for child in widget.winfo_children():
            self._bind_left_wheel(child)

    def _on_left_wheel(self, event) -> None:
        self.left_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _build_log(self) -> None:
        log = ttk.Labelframe(self.root, text="Журнал", padding=8)
        log.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 0))
        self.log_text = tk.Text(
            log, height=6, wrap="word", state="disabled", bg=PANEL, fg=FG,
            relief="flat", padx=6, pady=4, font=("Consolas", 9),
        )
        self.log_text.tag_configure("warn", foreground=WARN)
        self.log_text.pack(fill="both", expand=True)

    def _build_status(self) -> None:
        status = ttk.Frame(self.root, padding=(12, 8, 12, 12))
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w",
        )
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=140)
        self.progress.grid(row=0, column=1, sticky="e")

    # -- logging / status -------------------------------------------------------

    def _log(self, message: str, *, tag: str | None = None) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        if tag:
            self.log_text.insert("end", f"[{stamp}] {message}\n", (tag,))
        else:
            self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _bg(self, work, on_done=None, on_error=None) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                self.root.after(0, lambda e=exc: on_error(e) if on_error else None)
            else:
                self.root.after(0, lambda r=result: on_done(r) if on_done else None)

        threading.Thread(target=runner, daemon=True).start()
        self.progress.start(12)

    def _done(self) -> None:
        self.progress.stop()
        self._busy = False

    # -- file loading -----------------------------------------------------------

    def _attach_drop(self) -> None:
        self._drop_guard = enable_file_drop(self.root, self._on_drop)
        if self._drop_guard is None:
            self._log("Drag&drop недоступен в этой системе (используйте «Открыть»).")

    def _on_drop(self, files: list[str]) -> None:
        gfonts = [f for f in files if f.lower().endswith(".gfont")]
        if not gfonts:
            self._set_status("Перетащите файл .gfont.")
            return
        if len(gfonts) == 1:
            self._open_path(gfonts[0])
            return
        folder = str(Path(gfonts[0]).parent)
        self._log(f"Перетащено {len(gfonts)} файлов — батч-конвертация из {folder}")
        self._run_batch_files([Path(f) for f in gfonts])

    def _browse_open(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Открыть .gfont",
            filetypes=(("GFont шрифты", "*.gfont"), ("Все файлы", "*.*")),
        )
        if path:
            self._open_path(path)

    def _open_path(self, raw_path: str) -> None:
        if self._busy:
            return
        path = Path(raw_path)
        if not path.is_file():
            self._set_status(f"Файл не найден: {path}")
            return
        self._busy = True
        self._set_status(f"Читаю {path.name}…")

        def work() -> tuple[converter.ParsedGfont, int, list[str]]:
            data = path.read_bytes()
            warnings: list[str] = []
            parsed = converter.parse_gfont_bytes(data, warnings=warnings)
            return parsed, len(data), warnings

        def done(result: tuple[converter.ParsedGfont, int, list[str]]) -> None:
            self._done()
            parsed, size, warnings = result
            self.parsed = parsed
            self.path = path
            self.file_size = size
            self.selected = None
            self.path_var.set(path.name)
            points, strokes = render.stroke_stats(parsed.glyphs)
            meta = parsed.metadata
            values = {
                "имя": _truncate(path.name, 30),
                "версия": f"V{parsed.version}" if parsed.version else "ZIP (без заголовка)",
                "размер": _fmt_size(size),
                "глифов": _fmt_int(len(parsed.glyphs)),
                "точек": _fmt_int(points),
                "штрихов": _fmt_int(strokes),
                "font_size": str(meta.font_size),
                "перья (q)": str(meta.q),
            }
            for key, text in values.items():
                self.info_labels[key].configure(text=text)
            for key, text in (
                ("e", meta.e), ("b", meta.b), ("a", meta.a),
                ("c", meta.c), ("n", meta.n), ("o", meta.o),
            ):
                self.meta_labels[key].configure(text=_truncate(text) if text else "—")
            declared = meta.glyph_count
            if declared != len(parsed.glyphs):
                self.glyph_note.configure(text=f"в файле: {_fmt_int(declared)}")
            else:
                self.glyph_note.configure(text="")
            self._log(
                f"Открыт {path.name}: V{parsed.version}, {_fmt_int(len(parsed.glyphs))} глифов, "
                f"{_fmt_int(points)} точек, {_fmt_size(size)}"
            )
            for warning in _format_warnings(warnings):
                self._log(warning, tag="warn")
            if warnings:
                word = _plural_ru(
                    len(warnings), "предупреждение", "предупреждения", "предупреждений",
                )
                self._set_status(
                    f"Прочитано · {_fmt_int(len(parsed.glyphs))} глифов · "
                    f"⚠ {len(warnings)} {word} (V{parsed.version}, {_fmt_size(size)})"
                )
            else:
                self._set_status(
                    f"V{parsed.version} • {_fmt_int(len(parsed.glyphs))} глифов • {_fmt_size(size)}"
                )
            self._apply_filter()

        def fail(exc: Exception) -> None:
            self._done()
            message = converter.describe_error(exc)
            self._set_status("Ошибка: " + message)
            self._log("Ошибка чтения: " + message)
            messagebox.showerror("GFont Converter", message, parent=self.root)

        self._bg(work, done, fail)

    # -- glyph grid --------------------------------------------------------------

    def _tile_height(self) -> int:
        return self.cell + TILE_LABEL_H

    def _layout_grid(self) -> None:
        if not self.canvas.winfo_exists():
            return
        width = max(self.canvas.winfo_width(), 1)
        self.cols = max(1, (width - GRID_GAP) // (self.cell + GRID_GAP))
        rows = max(1, (len(self.view) + self.cols - 1) // self.cols)
        total_w = self.cols * (self.cell + GRID_GAP) + GRID_GAP
        total_h = rows * (self._tile_height() + GRID_GAP) + GRID_GAP
        self.canvas.configure(scrollregion=(0, 0, total_w, max(total_h, width)))
        self._reset_tiles()

    def _reset_tiles(self) -> None:
        self._drawn.clear()
        self.canvas.delete("all")
        if not self.view:
            self.canvas.create_text(
                self.canvas.winfo_width() // 2 or 300, 120,
                text="Откройте .gfont файл или перетащите его в окно",
                fill=MUTED, font=("Segoe UI", 12), tags="hint",
            )
        self._draw_visible()

    def _on_yscroll(self, first: float, last: float) -> None:
        self.vsb.set(first, last)
        self._schedule_draw()

    def _on_yview(self, *args) -> None:
        self.canvas.yview(*args)
        self._schedule_draw()

    def _schedule_draw(self) -> None:
        if self._draw_job is not None:
            try:
                self.root.after_cancel(self._draw_job)
            except tk.TclError:
                pass
        self._draw_job = self.root.after(16, self._draw_visible)

    def _draw_visible(self) -> None:
        self._draw_job = None
        if not self.view:
            return
        canvas = self.canvas
        top = canvas.canvasy(0)
        bottom = top + max(canvas.winfo_height(), 1)
        left = canvas.canvasx(0)
        right = left + max(canvas.winfo_width(), 1)
        for index, glyph in enumerate(self.view):
            col = index % self.cols
            row = index // self.cols
            tx = GRID_GAP + col * (self.cell + GRID_GAP)
            ty = GRID_GAP + row * (self._tile_height() + GRID_GAP)
            if tx + self.cell < left or tx > right or ty + self._tile_height() < top or ty > bottom:
                continue
            if index not in self._drawn:
                self._draw_tile(index, glyph, tx, ty)

    def _draw_tile(self, index: int, glyph: converter.Glyph, tx: int, ty: int) -> None:
        self._drawn.add(index)
        tags = ("tile", f"idx{index}")
        selected = index == self.selected
        self.canvas.create_rectangle(
            tx, ty, tx + self.cell, ty + self.cell,
            fill=TILE_BG,
            outline=ACCENT if selected else TILE_BORDER,
            width=2 if selected else 1,
            tags=tags,
        )
        pad = max(6, self.cell // 12)
        try:
            strokes = render.fit_points(
                glyph, tx + pad, ty + pad, self.cell - 2 * pad, self.cell - 2 * pad, padding=2,
            )
        except converter.GFontError:
            strokes = []
        line_width = max(1, round(self.cell / 44))
        for stroke in strokes:
            if len(stroke) == 1:
                x, y = stroke[0]
                self.canvas.create_oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5, fill=FG, outline="", tags=tags)
            else:
                flat = [coord for point in stroke for coord in point]
                self.canvas.create_line(
                    *flat, fill=FG, width=line_width, capstyle="round",
                    joinstyle="round", tags=tags,
                )
        char = _safe_char(glyph.codepoint)
        label = self._tile_label(glyph.codepoint, char)
        self.canvas.create_text(
            tx + self.cell // 2, ty + self.cell + TILE_LABEL_H // 2,
            text=label, fill=MUTED if not selected else ACCENT,
            font=("Segoe UI", 8), tags=tags,
        )

    def _tile_label(self, codepoint: int, char: str) -> str:
        """Glyph caption that never exceeds the tile pitch (cell + gap)."""
        full = f"{char}  U+{codepoint:04X}" if char.strip() else f"U+{codepoint:04X}"
        medium = f"U+{codepoint:04X}"
        short = f"{codepoint:04X}"
        budget = self.cell + GRID_GAP - 2
        if self._tile_font.measure(full) <= budget:
            return full
        if self._tile_font.measure(medium) <= budget:
            return medium
        return short

    def _on_grid_click(self, event) -> None:
        items = self.canvas.find_withtag("current")
        if not items:
            return
        tags = self.canvas.gettags(items[0])
        index = None
        for tag in tags:
            if tag.startswith("idx"):
                index = int(tag[3:])
                break
        if index is None:
            return
        old = self.selected
        self.selected = index
        for changed in (old, index):
            if changed is not None and changed in self._drawn:
                self._redraw_tile(changed)
        self._update_detail()

    def _redraw_tile(self, index: int) -> None:
        self.canvas.delete(f"idx{index}")
        self._drawn.discard(index)
        col = index % self.cols
        row = index // self.cols
        tx = GRID_GAP + col * (self.cell + GRID_GAP)
        ty = GRID_GAP + row * (self._tile_height() + GRID_GAP)
        self._draw_tile(index, self.view[index], tx, ty)

    def _on_wheel(self, event) -> None:
        if event.state & 0x0004:  # Ctrl held -> zoom
            step = 6 if event.delta > 0 else -6
            new_value = max(40, min(150, self.zoom_var.get() + step))
            self.zoom_var.set(new_value)
            self._on_zoom(new_value)
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            self._schedule_draw()

    def _on_zoom(self, value) -> None:
        try:
            cell = int(float(value))
        except (TypeError, ValueError):
            return
        if cell == self.cell:
            return
        self.cell = cell
        self.zoom_label.configure(text=f"клетка {cell} px")
        self._layout_grid()

    # -- search -------------------------------------------------------------------

    def _on_search_changed(self, *_args) -> None:
        if self._filter_job is not None:
            try:
                self.root.after_cancel(self._filter_job)
            except tk.TclError:
                pass
        self._filter_job = self.root.after(120, self._apply_filter)

    @staticmethod
    def _parse_query(query: str) -> int | None:
        query = query.strip()
        if not query:
            return None
        if len(query) == 1 and not query.isdigit():
            return ord(query)
        lowered = query.lower()
        try:
            if lowered.startswith("0x") or lowered.startswith("u+"):
                return int(query[2:], 16)
            if lowered.startswith("#"):
                return int(query[1:], 16)
            if query.isdigit():
                return int(query, 10)
        except ValueError:
            return -1
        return -1

    def _apply_filter(self) -> None:
        self._filter_job = None
        if self.parsed is None:
            self.view = []
            self._layout_grid()
            return
        glyphs = sorted(self.parsed.glyphs, key=lambda glyph: glyph.codepoint)
        query = self._parse_query(self.search_var.get())
        if query is None:
            self.view = glyphs
        elif query == -1:
            self.view = []
            self._set_status("Поиск: введите код (65, 0x41, U+0410) или один символ.")
        elif any(glyph.codepoint == query for glyph in glyphs):
            self.view = [glyph for glyph in glyphs if glyph.codepoint == query]
            self._set_status(f"Найден глиф U+{query:04X}.")
        else:
            self.view = [g for g in glyphs if str(g.codepoint).startswith(self.search_var.get().strip())]
            if not self.view:
                self._set_status(f"Глиф U+{query:04X} не найден в шрифте.")
        self.selected = None
        self._update_detail()
        self._layout_grid()
        self.canvas.yview_moveto(0.0)

    # -- detail panel ----------------------------------------------------------------

    def _selected_glyph(self) -> converter.Glyph | None:
        if self.selected is None or not (0 <= self.selected < len(self.view)):
            return None
        return self.view[self.selected]

    def _update_detail(self) -> None:
        canvas = self.detail_canvas
        canvas.delete("all")
        glyph = self._selected_glyph()
        if glyph is None:
            self.detail_info.set("—")
            return
        canvas.create_rectangle(0, 0, 211, 211, fill=TILE_BG, outline="")
        try:
            strokes = render.fit_points(glyph, 12, 12, 186, 186, padding=4)
            for stroke in strokes:
                if len(stroke) == 1:
                    x, y = stroke[0]
                    canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=FG, outline="")
                else:
                    flat = [coord for point in stroke for coord in point]
                    canvas.create_line(*flat, fill=FG, width=2, capstyle="round", joinstyle="round")
        except converter.GFontError as exc:
            self.detail_info.set(f"Глиф не отрисовывается: {exc}")
            return
        points = glyph.point_count
        strokes_count = glyph.commands.count(0)
        char = _safe_char(glyph.codepoint)
        head = f"символ «{char}»" if char.strip() else "символ непечатный"
        self.detail_info.set(
            f"{head}\nU+{glyph.codepoint:04X}  (dec {glyph.codepoint})\n"
            f"точек: {points}\nштрихов: {strokes_count}\n"
            f"запись: {_fmt_size(glyph.payload_size)}"
        )

    def _copy_symbol(self) -> None:
        glyph = self._selected_glyph()
        if glyph is None:
            return
        char = _safe_char(glyph.codepoint)
        if not char:
            self._set_status("Этот код не является печатным символом.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(char)
        self._set_status(f"Скопировано: {char}")

    # -- export --------------------------------------------------------------------

    def _export_selected_svg(self) -> None:
        glyph = self._selected_glyph()
        if glyph is None:
            self._set_status("Выберите глиф в таблице.")
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Экспорт глифа в SVG",
            defaultextension=".svg", initialfile=f"U+{glyph.codepoint:04X}.svg",
            filetypes=(("SVG", "*.svg"),),
        )
        if not path:
            return
        try:
            Path(path).write_text(render.svg_string(glyph), encoding="utf-8")
        except (converter.GFontError, OSError) as exc:
            messagebox.showerror("Экспорт SVG", converter.describe_error(exc), parent=self.root)
            return
        self._log(f"SVG сохранён: {path}")
        self._set_status(f"SVG сохранён: {path}")

    def _export_selected_png(self) -> None:
        glyph = self._selected_glyph()
        if glyph is None:
            self._set_status("Выберите глиф в таблице.")
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Экспорт глифа в PNG",
            defaultextension=".png", initialfile=f"U+{glyph.codepoint:04X}.png",
            filetypes=(("PNG", "*.png"),),
        )
        if not path:
            return
        try:
            Path(path).write_bytes(render.png_bytes(glyph, size=512))
        except (converter.GFontError, OSError) as exc:
            messagebox.showerror("Экспорт PNG", converter.describe_error(exc), parent=self.root)
            return
        self._log(f"PNG сохранён: {path}")
        self._set_status(f"PNG сохранён: {path}")

    def _export_all_svg(self) -> None:
        if self.parsed is None:
            self._set_status("Сначала откройте .gfont файл.")
            return
        folder = filedialog.askdirectory(parent=self.root, title="Куда экспортировать все глифы (SVG)")
        if not folder:
            return
        glyphs = sorted(self.parsed.glyphs, key=lambda glyph: glyph.codepoint)
        target = Path(folder)
        self._busy = True
        self._set_status(f"Экспортирую {len(glyphs)} глифов…")

        def work() -> int:
            for index, glyph in enumerate(glyphs):
                name = f"{index:04d}_U+{glyph.codepoint:04X}.svg"
                (target / name).write_text(render.svg_string(glyph), encoding="utf-8")
            return len(glyphs)

        def done(count: int) -> None:
            self._done()
            self._log(f"Экспортировано {count} SVG в {target}")
            self._set_status(f"Экспортировано {count} SVG в {target}")

        def fail(exc: Exception) -> None:
            self._done()
            message = converter.describe_error(exc)
            self._log("Ошибка экспорта: " + message)
            messagebox.showerror("Экспорт SVG", message, parent=self.root)

        self._bg(work, done, fail)

    # -- conversion -------------------------------------------------------------------

    def _convert(self) -> None:
        if self._busy:
            return
        if self.parsed is None or self.path is None:
            self._set_status("Сначала откройте .gfont файл.")
            return
        target = int(self.target_var.get()[1:])
        destination = self.path.with_name(f"{self.path.stem}_v{target}.gfont")
        if destination.exists():
            if not messagebox.askyesno(
                "Перезаписать?", f"Файл {destination.name} уже существует.\nПерезаписать?",
                parent=self.root,
            ):
                return
        self._busy = True
        self._set_status(f"Конвертирую в {self.target_var.get()}…")

        def work() -> Path:
            data = self.path.read_bytes()
            rebuilt = converter.build_gfont(
                converter.parse_gfont_bytes(data, warnings=[]), target
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(rebuilt)
            verified = converter.parse_gfont_bytes(destination.read_bytes())
            if len(verified.glyphs) != len(self.parsed.glyphs):
                raise converter.GFontError("verification failed after write")
            return destination

        def done(result: Path) -> None:
            self._done()
            self._log(f"Конвертировано: {result}")
            self._set_status(f"Готово: {result.name} ({self.target_var.get()})")

        def fail(exc: Exception) -> None:
            self._done()
            message = converter.describe_error(exc)
            self._log("Ошибка конвертации: " + message)
            messagebox.showerror("Конвертация", message, parent=self.root)

        self._bg(work, done, fail)

    def _batch(self) -> None:
        if self._busy:
            return
        initial = str(self.path.parent) if self.path else ""
        folder = filedialog.askdirectory(parent=self.root, title="Папка с .gfont файлами", initialdir=initial)
        if not folder:
            return
        files = sorted(Path(folder).glob("*.gfont"))
        if not files:
            self._set_status(f"В папке нет .gfont файлов: {folder}")
            return
        target = self.target_var.get()
        if not messagebox.askyesno(
            "Batch-конвертация",
            f"Найдено файлов: {len(files)}.\n"
            f"Конвертировать все в {target} в папку converted_{target.lower()}?",
            parent=self.root,
        ):
            return
        self._run_batch_files(files)

    def _run_batch_files(self, files: list[Path]) -> None:
        if self._busy:
            return
        target = int(self.target_var.get()[1:])
        out_dir = files[0].parent / f"converted_v{target}"
        self._busy = True
        self._set_status(f"Batch: {len(files)} файлов → V{target}…")

        def work() -> tuple[int, int, int]:
            ok_count = 0
            warn_files = 0
            for source in files:
                destination = out_dir / f"{source.stem}_v{target}.gfont"
                try:
                    _parsed, warnings = _convert_lenient(source, destination, target)
                    ok_count += 1
                    self.root.after(0, lambda s=source: self._log(f"OK   {s.name}"))
                    if warnings:
                        warn_files += 1
                        for warning in _format_warnings(warnings):
                            self.root.after(
                                0, lambda w=warning: self._log(w, tag="warn"),
                            )
                except Exception as exc:
                    self.root.after(
                        0, lambda s=source, e=exc: self._log(f"FAIL {s.name}: {converter.describe_error(e)}"),
                    )
            return ok_count, warn_files, len(files)

        def done(result: tuple[int, int, int]) -> None:
            self._done()
            ok_count, warn_files, total = result
            self._log(f"Batch завершён: {ok_count}/{total} в {out_dir}")
            status = f"Batch: {ok_count}/{total} файлов → {out_dir}"
            if warn_files:
                status += f" · ⚠ {warn_files} с предупреждениями"
            self._set_status(status)

        def fail(exc: Exception) -> None:
            self._done()
            self._log("Batch error: " + converter.describe_error(exc))

        self._bg(work, done, fail)


def run_gui() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    root = tk.Tk()
    app = GFontApp(root)
    app._log("GFont Converter готов. Перетащите .gfont файл в окно.")
    root.mainloop()
    return 0


# --- CLI --------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KDraw/Kenjoy .gfont converter (GUI/headless): "
        "reads V3/V5/V6/V7/V8/V9 and bare ZIP, writes V3/V5"
    )
    parser.add_argument(
        "paths", nargs="*", help="input .gfont files (for --smoke) or input file (for --convert)",
    )
    parser.add_argument("--smoke", action="store_true", help="headless self-check on real files, no windows")
    parser.add_argument("--smoke-report", metavar="FILE", help="write smoke report to FILE")
    parser.add_argument("--convert", action="store_true", help="headless conversion of paths[0]")
    parser.add_argument("-o", "--output", help="output file for --convert")
    parser.add_argument("--target", choices=("V3", "V5"), default="V5", help="target version for --convert")
    parser.add_argument("--gui", action="store_true", help="force GUI mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.smoke:
        return run_smoke(args.paths, args.smoke_report)
    if args.convert:
        if not args.paths:
            print("error: --convert requires an input file", file=sys.stderr)
            return 2
        target = int(args.target[1:])
        return run_convert(args.paths[0], args.output, target)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
