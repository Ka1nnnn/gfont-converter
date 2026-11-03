"""Rendering tests: strokes, bbox, SVG output, PNG raster, fit_points."""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zlib

import pytest

from gfont_converter import GFontError, Glyph
from glyph_render import (
    fit_points,
    glyph_bbox,
    glyph_strokes,
    png_bytes,
    stroke_stats,
    svg_string,
)
from tests.conftest import CODEPOINT, COMMANDS, VALUES, make_glyph


def test_glyph_strokes_split_on_pen_up() -> None:
    glyph = Glyph(0x41, (0, 0, 5, 0, 5, 5, 0, 5), b"\x00\x01\x01\x00")
    strokes = glyph_strokes(glyph)
    assert len(strokes) == 2
    assert strokes[0] == ((0.0, 0.0), (5.0, 0.0), (5.0, 5.0))
    assert strokes[1] == ((0.0, 5.0),)


def test_glyph_strokes_single_point_stroke() -> None:
    glyph = Glyph(0x41, (1.0, 2.0), b"\x00")
    assert glyph_strokes(glyph) == (((1.0, 2.0),),)


def test_glyph_strokes_pen_down_before_up() -> None:
    glyph = Glyph(0x41, (1.0, 2.0, 3.0, 4.0), b"\x01\x01")
    with pytest.raises(GFontError, match="pen-down"):
        glyph_strokes(glyph)


def test_glyph_strokes_unknown_command() -> None:
    glyph = Glyph(0x41, (1.0, 2.0), b"\x07")
    with pytest.raises(GFontError, match="unsupported pen command"):
        glyph_strokes(glyph)


def test_glyph_strokes_command_count_mismatch() -> None:
    glyph = Glyph(0x41, (1.0, 2.0), b"\x00\x01")
    with pytest.raises(GFontError, match="does not match"):
        glyph_strokes(glyph)


def test_glyph_bbox() -> None:
    glyph = Glyph(0x41, (3.0, -2.0, 9.0, 7.0, 1.0, 4.0), b"\x00\x01\x01")
    assert glyph_bbox(glyph) == (1.0, -2.0, 9.0, 7.0)


def test_glyph_bbox_empty_raises() -> None:
    with pytest.raises(GFontError, match="no points"):
        glyph_bbox(Glyph(0x41, (), b""))


def test_stroke_stats() -> None:
    a = make_glyph(0x41)  # 3 points, 1 stroke
    b = Glyph(0x42, (0, 0, 1, 0, 1, 1, 2, 2), b"\x00\x01\x00\x01")  # 4 points, 2 strokes
    assert stroke_stats([a, b]) == (7, 3)


def test_svg_is_valid_xml_with_polyline() -> None:
    glyph = make_glyph()
    svg = svg_string(glyph)
    root = ET.fromstring(svg)  # raises if malformed
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    polylines = list(root.iter("{http://www.w3.org/2000/svg}polyline"))
    assert len(polylines) == 1
    points = polylines[0].attrib["points"].split()
    assert len(points) == 3
    assert root.find("{http://www.w3.org/2000/svg}title").text == f"U+{CODEPOINT:04X}"


def test_svg_single_point_becomes_circle() -> None:
    # A lone pen-up dot inside an otherwise drawable glyph renders as a circle.
    glyph = Glyph(0x41, (0.0, 0.0, 5.0, 0.0, 5.0, 5.0), b"\x00\x01\x00")
    svg = svg_string(glyph)
    root = ET.fromstring(svg)
    assert len(list(root.iter("{http://www.w3.org/2000/svg}circle"))) == 1
    assert len(list(root.iter("{http://www.w3.org/2000/svg}polyline"))) == 1


def test_svg_rejects_nonpositive_padding() -> None:
    with pytest.raises(GFontError, match="padding"):
        svg_string(make_glyph(), padding=0)
    with pytest.raises(GFontError, match="padding"):
        svg_string(make_glyph(), padding=float("inf"))


def test_svg_rejects_undrawable_glyph() -> None:
    # All pen-up points only: no stroke longer than one point.
    glyph = Glyph(0x41, (0.0, 0.0, 5.0, 5.0), b"\x00\x00")
    with pytest.raises(GFontError, match="no drawable strokes"):
        svg_string(glyph)


def test_svg_dimensions_cover_bbox_and_padding() -> None:
    svg = svg_string(make_glyph(), padding=4.0)
    # viewBox="0 0 W H": bbox is 10x20, plus 2*4 padding.
    assert 'viewBox="0 0 18.0000 28.0000"' in svg


def test_png_signature_and_dimensions() -> None:
    data = png_bytes(make_glyph(), size=64, supersample=2)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (64, 64)
    assert data.endswith(b"IEND\xae\x42\x60\x82")


def test_png_crc_integrity() -> None:
    data = png_bytes(make_glyph(), size=48)
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack_from(">I", data, offset)
        tag = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        (crc,) = struct.unpack_from(">I", data, offset + 8 + length)
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, tag
        offset += 12 + length
        if tag == b"IEND":
            break
    else:
        pytest.fail("IEND chunk not found")


def test_png_background_and_foreground_colors() -> None:
    size = 32
    data = png_bytes(
        make_glyph(), size=size, supersample=2,
        background=(255, 255, 255), foreground=(0, 0, 0),
    )
    # Find IDAT and decompress to inspect corner pixels.
    offset = 8
    idat = b""
    while offset < len(data):
        (length,) = struct.unpack_from(">I", data, offset)
        tag = data[offset + 4:offset + 8]
        if tag == b"IDAT":
            idat += data[offset + 8:offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(idat)
    corner = raw[1:4]  # first row, first pixel (after filter byte)
    assert corner == b"\xff\xff\xff"  # pure background at the corner
    assert raw != b"\x00" * len(raw)  # something was drawn


def test_png_size_clamped_to_minimum() -> None:
    data = png_bytes(make_glyph(), size=4)
    width, _height = struct.unpack(">II", data[16:24])
    assert width == 16


def test_png_rejects_undrawable_glyph() -> None:
    glyph = Glyph(0x41, (0.0, 0.0, 5.0, 5.0), b"\x00\x00")
    with pytest.raises(GFontError, match="no drawable strokes"):
        png_bytes(glyph)


def test_fit_points_centers_and_scales() -> None:
    glyph = make_glyph()  # bbox 10x20
    strokes = fit_points(glyph, 0.0, 0.0, 100.0, 100.0, padding=10.0)
    flat = [point for stroke in strokes for point in stroke]
    xs = [p[0] for p in flat]
    ys = [p[1] for p in flat]
    assert min(xs) >= 10.0 and max(xs) <= 90.0
    assert min(ys) >= 10.0 and max(ys) <= 90.0
    # Y-down preserved: top of the glyph (y=0) maps to the smallest y.
    first_point = strokes[0][0]
    assert abs(first_point[1] - min(ys)) < 1e-9


def test_constants_are_flattened_tuples() -> None:
    assert VALUES == (0.0, 0.0, 10.0, 0.0, 10.0, 20.0)
    assert COMMANDS == b"\x00\x01\x01"
