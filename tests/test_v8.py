"""V8 (Kuixiang) container support: synthetic multi-contour fonts.

Red until the reader fix lands: version 8 must be accepted, its raw glyph
records (UTF-8 codepoint header, u32 coordinate count, contour sequence)
must merge into single glyphs without losing points, and a full V5
conversion must keep every glyph byte-identical.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

import gfont_converter as conv
import glyph_render as render
from tests.gfont_helpers import (
    V8_CONTOUR_A_VALUES,
    V8_CONTOUR_B_VALUES,
    V8_GLYPH_COUNT,
    V8_MERGED_GLYPH,
    V8_PREVIEW_COUNT,
    build_v8_multicontour,
    read_v8,
    v8_contour,
    v8_glyph_blob,
)


def test_v8_file_loads() -> None:
    parsed = read_v8()
    assert parsed.version == 8
    assert len(parsed.glyphs) == V8_GLYPH_COUNT
    assert parsed.metadata.font_size > 0
    assert parsed.metadata.glyph_count == V8_GLYPH_COUNT  # consistent counter


def test_v8_glyphs_are_valid() -> None:
    parsed = read_v8()
    codepoints = [glyph.codepoint for glyph in parsed.glyphs]
    assert len(set(codepoints)) == len(codepoints)
    for glyph in parsed.glyphs:
        assert len(glyph.values) % 2 == 0
        assert len(glyph.commands) == glyph.point_count
        assert set(glyph.commands) <= {0, 1}


def test_v8_previews_present() -> None:
    assert len(read_v8().previews) == V8_PREVIEW_COUNT


def test_v8_renders_svg_and_png() -> None:
    parsed = read_v8()
    drawable = next(
        glyph
        for glyph in sorted(parsed.glyphs, key=lambda item: -item.point_count)
        if glyph.commands and 1 in glyph.commands
    )
    svg = render.svg_string(drawable)
    assert "<polyline" in svg or "<circle" in svg
    assert ET.fromstring(svg).tag == "{http://www.w3.org/2000/svg}svg"

    png = render.png_bytes(drawable, size=192)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IEND" in png
    assert len(png) >= 200


def test_v8_roundtrip_to_v5_preserves_glyphs() -> None:
    parsed = read_v8()
    rebuilt = conv.parse_gfont_bytes(conv.build_gfont(parsed, 5))
    assert rebuilt.version == 5
    assert len(rebuilt.glyphs) == len(parsed.glyphs)
    original = {glyph.codepoint: glyph for glyph in parsed.glyphs}
    for glyph in rebuilt.glyphs:
        assert glyph == original[glyph.codepoint], f"glyph U+{glyph.codepoint:04X} changed"
    assert rebuilt.metadata.glyph_count == len(rebuilt.glyphs)


def test_v8_multicontour_glyph_keeps_all_points() -> None:
    parsed = conv.parse_gfont_bytes(build_v8_multicontour())
    assert parsed.version == 8
    assert len(parsed.previews) == 2
    assert len(parsed.glyphs) == 1

    glyph = parsed.glyphs[0]
    assert glyph == V8_MERGED_GLYPH
    assert glyph.point_count == 5  # contour A (3 points) + contour B (2 points)

    # the merged multi-contour glyph survives a full V5 conversion
    rebuilt = conv.parse_gfont_bytes(conv.build_gfont(parsed, 5))
    assert rebuilt.glyphs[0] == V8_MERGED_GLYPH


def test_v8_contour_boundary_pen_down_normalized() -> None:
    # H1: contour #1 starting pen-down (1) is normalized to pen-up so the
    # merged contours stay visually separate; a warning is reported.
    warnings: list[str] = []
    parsed = conv.parse_gfont_bytes(
        build_v8_multicontour(entry_contour_b_commands=bytes((1, 1))),
        warnings=warnings,
    )
    glyph = parsed.glyphs[0]
    assert glyph.point_count == 5
    assert glyph.commands == V8_MERGED_GLYPH.commands  # boundary byte is 0
    assert any(
        "граница контура нормализована" in message for message in warnings
    ), warnings

    # the normalized glyph survives a full V5 conversion
    rebuilt = conv.parse_gfont_bytes(conv.build_gfont(parsed, 5))
    assert rebuilt.glyphs[0] == glyph


def test_v8_first_contour_must_start_with_pen_up() -> None:
    # H1: contour #0 must still start with pen-up (strict, as in every
    # other version) — a pen-down first contour is a hard error.
    blob = v8_glyph_blob(0x410, (
        v8_contour(V8_CONTOUR_A_VALUES, bytes((1, 1, 1))),
        v8_contour(V8_CONTOUR_B_VALUES, bytes((0, 1))),
    ))
    with pytest.raises(conv.GFontError, match="start with 0"):
        conv._parse_raw_glyph_v8(blob, 0x410)
