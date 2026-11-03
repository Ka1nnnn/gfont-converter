"""GUI layout regression tests.

The window is created invisible (``-alpha 0``) so nothing ever flashes on
screen while pytest runs; geometry is still fully computed and queryable.
Covers the visual bugs fixed after the vision pass:
  - root column weight (content stretched to full window width),
  - left info column moved into a scrollable canvas (no overlap with the log),
  - toolbar fits at the minimum window size (squeezable path entry),
  - tile captions never wider than the tile pitch (no label collisions).
"""
from __future__ import annotations

import time
import tkinter as tk

import pytest

import gfont_gui as gui

CODEPOINTS = (0x21, 0x41, 0x410, 0x1F600, 0x10FFFF)


def _make_root(retries: int = 2, backoff: float = 0.75) -> tk.Tk:
    """Create a Tk root, retrying on TclError.

    Sourcing tcl/tk library scripts can transiently fail ("couldn't read
    file ... no such file or directory" for a file that exists) when another
    process on the same interpreter initializes Tk concurrently. A short
    retry rides out the contention; a genuinely headless host still raises.
    """
    for attempt in range(retries + 1):
        try:
            return tk.Tk()
        except tk.TclError:
            if attempt == retries:
                raise
            time.sleep(backoff)


@pytest.fixture()
def app_window():
    try:
        root = _make_root()
    except tk.TclError as exc:  # pragma: no cover - headless CI without Tk
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        root.attributes("-alpha", 0.0)  # invisible window, no focus steal
    except tk.TclError:  # pragma: no cover - platforms without alpha
        root.withdraw()
    app = gui.GFontApp(root)
    root.geometry("1240x800+40+40")
    root.update()
    root.update()
    yield root, app
    root.destroy()


def _toolbar_buttons(app) -> list:
    bar = app.btn_open.master
    return [child for child in bar.winfo_children() if isinstance(child, gui.ttk.Button)]


def test_root_column_has_weight(app_window) -> None:
    root, _app = app_window
    assert root.grid_columnconfigure(0, "weight") == 1


def test_log_spans_full_window_width(app_window) -> None:
    root, app = app_window
    log = app.log_text.master
    assert abs(log.winfo_width() - (root.winfo_width() - 24)) <= 2


def test_glyph_canvas_gets_remaining_width(app_window) -> None:
    root, app = app_window
    assert app.canvas.winfo_width() > 500  # 1240 - 296 left - 250 right - paddings
    assert root.winfo_width() == 1240
    assert (
        app.canvas.winfo_rootx() + app.canvas.winfo_width()
        <= app.detail_canvas.winfo_rootx()
    )


def test_left_panels_live_in_scroll_canvas(app_window) -> None:
    _root, app = app_window
    panels = app._left_panel.winfo_children()
    assert len(panels) == 4  # Шрифт, Метаданные, Поиск глифа, Зум превью
    assert app._left_panel.master is app.left_canvas
    # canvas row ends above the log row: clipping keeps panels off the log
    assert (
        app.left_canvas.winfo_rooty() + app.left_canvas.winfo_height()
        <= app.log_text.winfo_rooty() + 1
    )


def test_left_canvas_content_is_taller_than_view(app_window) -> None:
    _root, app = app_window
    region = app.left_canvas.cget("scrollregion").split()
    assert len(region) == 4
    assert float(region[3]) > app.left_canvas.winfo_height()
    assert app.left_vsb.winfo_manager() == "grid"  # scrollbar visible


def test_minsize_window_keeps_toolbar_inside(app_window) -> None:
    root, app = app_window
    root.geometry("980x700+40+40")
    root.update()
    root.update()
    right_edge = root.winfo_rootx() + root.winfo_width()
    for button in _toolbar_buttons(app):
        assert button.winfo_rootx() + button.winfo_width() <= right_edge, button.winfo_class()
    assert app.path_entry.cget("width") <= 14  # entry shrinks, buttons stay put


def test_minsize_log_and_status_do_not_overlap(app_window) -> None:
    root, app = app_window
    root.geometry("980x700+40+40")
    root.update()
    root.update()
    log_bottom = app.log_text.winfo_rooty() + app.log_text.winfo_height()
    status_top = app.progress.winfo_rooty()
    assert log_bottom <= status_top + 1


def test_tile_label_never_exceeds_pitch(app_window) -> None:
    _root, app = app_window
    for cell in (40, 52, 72, 110, 150):
        app.cell = cell
        budget = cell + gui.GRID_GAP - 2
        for codepoint in CODEPOINTS:
            label = app._tile_label(codepoint, gui._safe_char(codepoint))
            assert app._tile_font.measure(label) <= budget, (cell, hex(codepoint), label)


def test_tile_label_uses_full_form_when_roomy(app_window) -> None:
    _root, app = app_window
    app.cell = 150
    label = app._tile_label(0x41, "A")
    assert label == "A  U+0041"
    app.cell = 72
    assert app._tile_label(0x41, "A") == "A  U+0041"


def test_tile_label_degrades_gracefully(app_window) -> None:
    _root, app = app_window
    app.cell = 40
    label = app._tile_label(0x41, "A")
    assert label in ("U+0041", "0041")  # char dropped, no overlap with neighbours
    label = app._tile_label(0x1F600, "\U0001F600")
    assert label in ("U+1F600", "1F600")


def test_dark_titlebar_helper_is_safe_without_window() -> None:
    # smoke: helper must not raise when the wrapper window is not mapped yet
    root = _make_root()
    try:
        gui._apply_dark_titlebar(root)
    finally:
        root.destroy()
