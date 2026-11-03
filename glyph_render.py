"""Glyph geometry helpers and offscreen rendering (SVG + PNG, stdlib only).

Glyph draw model (identical for V3/V5/V6/V7 Java records and V9 raw KDraw
blobs): ``values`` is a flat sequence of float32 coordinates (X0, Y0, X1,
Y1, ...), ``commands`` holds one byte per point where 0 = pen up (start a
new stroke) and 1 = pen down (draw a line to this point). Coordinates are
Y-down, exactly like the KDraw app renders them.
"""
from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Sequence
from itertools import pairwise

from gfont_converter import GFontError, Glyph

Point = tuple[float, float]
Stroke = tuple[Point, ...]


def glyph_strokes(glyph: Glyph) -> tuple[Stroke, ...]:
    """Split glyph points/commands into pen strokes."""
    if len(glyph.commands) != glyph.point_count:
        raise GFontError("glyph command count does not match point count")
    strokes: list[list[Point]] = []
    current: list[Point] = []
    values = glyph.values
    for index, command in enumerate(glyph.commands):
        point = (values[2 * index], values[2 * index + 1])
        if command == 0:
            if current:
                strokes.append(current)
            current = [point]
        elif command == 1:
            if not current:
                raise GFontError("pen-down command before any pen-up command")
            current.append(point)
        else:
            raise GFontError(f"unsupported pen command {command}")
    if current:
        strokes.append(current)
    return tuple(tuple(stroke) for stroke in strokes)


def glyph_bbox(glyph: Glyph) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y); degenerate for empty glyphs."""
    values = glyph.values
    if not values:
        raise GFontError("glyph has no points")
    xs = values[0::2]
    ys = values[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def stroke_stats(glyphs: Sequence[Glyph]) -> tuple[int, int]:
    """Return (total points, total strokes) for a sequence of glyphs."""
    points = sum(len(glyph.commands) for glyph in glyphs)
    strokes = sum(glyph.commands.count(0) for glyph in glyphs)
    return points, strokes


def svg_string(
    glyph: Glyph,
    *,
    padding: float = 8.0,
    stroke_width: float | None = None,
    background: str = "white",
    foreground: str = "black",
) -> str:
    """Render one glyph as a standalone SVG document (Y-down, upright)."""
    if not math.isfinite(padding) or padding <= 0:
        raise GFontError("SVG padding must be a positive finite number")
    strokes = glyph_strokes(glyph)
    if not any(len(stroke) > 1 for stroke in strokes):
        raise GFontError("glyph has no drawable strokes")
    min_x, min_y, max_x, max_y = glyph_bbox(glyph)
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    view_width = width + 2 * padding
    view_height = height + 2 * padding
    if stroke_width is None:
        stroke_width = max(2.0, min(width, height) / 40.0)
    paths = []
    for stroke in strokes:
        if len(stroke) < 2:
            single = stroke[0]
            paths.append(
                f'<circle cx="{single[0] - min_x + padding:.4f}" '
                f'cy="{single[1] - min_y + padding:.4f}" r="{stroke_width / 2:.4f}" />'
            )
            continue
        point_text = " ".join(
            f"{x - min_x + padding:.4f},{y - min_y + padding:.4f}" for x, y in stroke
        )
        paths.append(f'<polyline points="{point_text}" />')
    body = "\n    ".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {view_width:.4f} {view_height:.4f}" '
        f'width="{view_width:.4f}mm" height="{view_height:.4f}mm">\n'
        f'  <title>U+{glyph.codepoint:04X}</title>\n'
        f'  <rect width="100%" height="100%" fill="{background}" />\n'
        f'  <g fill="{foreground}" stroke="{foreground}" stroke-width="{stroke_width:.4f}" '
        f'stroke-linecap="round" stroke-linejoin="round">\n'
        f"    {body}\n"
        f"  </g>\n"
        f"</svg>\n"
    )


# --- Pure-Python PNG rasterizer -------------------------------------------

def _stamp_disk(pixels: bytearray, grid_w: int, grid_h: int, cx: float, cy: float, radius: float) -> None:
    r2 = radius * radius
    x0 = max(0, int(cx - radius))
    x1 = min(grid_w - 1, int(cx + radius) + 1)
    y0 = max(0, int(cy - radius))
    y1 = min(grid_h - 1, int(cy + radius) + 1)
    for y in range(y0, y1 + 1):
        base = y * grid_w
        dy2 = (y - cy) * (y - cy)
        for x in range(x0, x1 + 1):
            dx = x - cx
            if dx * dx + dy2 <= r2:
                pixels[base + x] = 255


def _draw_thick_line(pixels: bytearray, grid_w: int, grid_h: int,
                     x0: float, y0: float, x1: float, y1: float, radius: float) -> None:
    distance = math.hypot(x1 - x0, y1 - y0)
    steps = max(1, int(distance * 2))
    for step in range(steps + 1):
        t = step / steps
        _stamp_disk(pixels, grid_w, grid_h, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, radius)


def _png_bytes(rgb: bytes, width: int, height: int) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride:(y + 1) * stride])
    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
        chunk(b"IDAT", zlib.compress(bytes(raw), 6)),
        chunk(b"IEND", b""),
    ))


def png_bytes(
    glyph: Glyph,
    *,
    size: int = 256,
    padding_ratio: float = 0.08,
    background: tuple[int, int, int] = (255, 255, 255),
    foreground: tuple[int, int, int] = (17, 17, 17),
    stroke_width: float = 2.0,
    supersample: int = 3,
) -> bytes:
    """Rasterize one glyph into an anti-aliased PNG (pure stdlib)."""
    size = max(16, min(4096, int(size)))
    supersample = max(1, min(6, int(supersample)))
    if size > 384:
        supersample = min(supersample, 2)  # keep pure-Python downscale fast
    strokes = glyph_strokes(glyph)
    if not any(len(stroke) > 1 for stroke in strokes):
        raise GFontError("glyph has no drawable strokes")
    min_x, min_y, max_x, max_y = glyph_bbox(glyph)
    glyph_w = max(max_x - min_x, 1.0)
    glyph_h = max(max_y - min_y, 1.0)
    max_dim = max(glyph_w, glyph_h)
    pad = max_dim * padding_ratio
    scale = size / (max_dim * (1.0 + 2.0 * padding_ratio))
    grid_w = size * supersample
    grid_h = size * supersample
    pixels = bytearray(grid_w * grid_h)

    def to_grid(x: float, y: float) -> tuple[float, float]:
        gx = (x - min_x + pad) * scale * supersample
        gy = (y - min_y + pad) * scale * supersample
        return gx, gy

    radius = stroke_width * supersample / 2.0
    for stroke in strokes:
        if len(stroke) == 1:
            gx, gy = to_grid(*stroke[0])
            _stamp_disk(pixels, grid_w, grid_h, gx, gy, radius)
            continue
        for (ax, ay), (bx, by) in pairwise(stroke):
            x0, y0 = to_grid(ax, ay)
            x1, y1 = to_grid(bx, by)
            _draw_thick_line(pixels, grid_w, grid_h, x0, y0, x1, y1, radius)

    cell = supersample * supersample
    divisor = 255.0 * cell
    br, bg_, bb = background
    fr, fg_, fb = foreground
    rgb = bytearray(size * size * 3)
    ss = supersample
    for py in range(size):
        out_row = py * size * 3
        grid_base = py * ss * grid_w
        for px in range(size):
            column_base = grid_base + px * ss
            acc = 0
            for oy in range(ss):
                row = column_base + oy * grid_w
                for ox in range(ss):
                    acc += pixels[row + ox]
            alpha = acc / divisor
            index = out_row + px * 3
            rgb[index] = int(br + (fr - br) * alpha)
            rgb[index + 1] = int(bg_ + (fg_ - bg_) * alpha)
            rgb[index + 2] = int(bb + (fb - bb) * alpha)
    return _png_bytes(bytes(rgb), size, size)


def fit_points(
    glyph: Glyph,
    box_x: float,
    box_y: float,
    box_w: float,
    box_h: float,
    padding: float = 6.0,
) -> list[list[tuple[float, float]]]:
    """Map glyph strokes into a canvas box (Y-down preserved), centered."""
    strokes = glyph_strokes(glyph)
    min_x, min_y, max_x, max_y = glyph_bbox(glyph)
    glyph_w = max(max_x - min_x, 1e-6)
    glyph_h = max(max_y - min_y, 1e-6)
    usable_w = max(box_w - 2 * padding, 1.0)
    usable_h = max(box_h - 2 * padding, 1.0)
    scale = min(usable_w / glyph_w, usable_h / glyph_h)
    offset_x = box_x + (box_w - glyph_w * scale) / 2.0
    offset_y = box_y + (box_h - glyph_h * scale) / 2.0
    return [
        [
            (offset_x + (x - min_x) * scale, offset_y + (y - min_y) * scale)
            for x, y in stroke
        ]
        for stroke in strokes
    ]
