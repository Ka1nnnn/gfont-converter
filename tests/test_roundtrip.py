"""Write -> read roundtrip identity tests for V3/V5 targets."""
from __future__ import annotations

import pytest

import gfont_converter as conv
from tests.conftest import build_v9, make_glyph, make_metadata


@pytest.mark.parametrize("target", [3, 5])
@pytest.mark.parametrize("key", ["v3", "v5", "v6", "v7", "v9", "real_v9", "real_v0"])
def test_convert_roundtrip_preserves_glyphs(parsed_versions, key: str, target: int) -> None:
    parsed = parsed_versions[key]
    if any(glyph.codepoint > 0xFFFF for glyph in parsed.glyphs):
        pytest.skip("glyphs above U+FFFF cannot be represented in V3/V5")
    rebuilt = conv.parse_gfont_bytes(conv.build_gfont(parsed, target))
    assert rebuilt.version == target
    assert len(rebuilt.glyphs) == len(parsed.glyphs)
    original = {glyph.codepoint: glyph for glyph in parsed.glyphs}
    for glyph in rebuilt.glyphs:
        assert glyph == original[glyph.codepoint], f"glyph U+{glyph.codepoint:04X} changed"


def test_convert_file_writes_destination(tmp_path) -> None:
    source = tmp_path / "src.gfont"
    source.write_bytes(build_v9())
    destination = tmp_path / "out" / "src_v5.gfont"
    parsed = conv.convert_file(source, destination, 5)
    assert parsed.version == 9
    assert destination.is_file()
    reread = conv.load_gfont(destination)
    assert reread.version == 5
    assert len(reread.glyphs) == len(parsed.glyphs)


def test_writer_recomputes_glyph_count() -> None:
    glyphs = (make_glyph(0x41), make_glyph(0x42))
    parsed = conv.ParsedGfont(5, make_metadata(glyph_count=999), glyphs, ())
    rebuilt = conv.parse_gfont_bytes(conv.build_gfont(parsed, 5))
    assert rebuilt.metadata.glyph_count == 2


def test_previews_capped_at_30() -> None:
    glyphs = tuple(make_glyph(cp) for cp in range(0x41, 0x41 + 40))
    parsed = conv.ParsedGfont(5, make_metadata(), glyphs, ())
    data = conv.build_gfont(parsed, 5)
    rebuilt = conv.parse_gfont_bytes(data)
    assert len(rebuilt.previews) == conv.PREVIEW_LIMIT


def test_previews_are_largest_glyphs() -> None:
    small = make_glyph(0x41, values=(0.0, 0.0, 1.0, 1.0), commands=b"\x00\x01")
    large = make_glyph(0x42, values=(0.0, 0.0, 100.0, 0.0, 100.0, 100.0), commands=b"\x00\x01\x01")
    parsed = conv.ParsedGfont(5, make_metadata(), (small, large), ())
    rebuilt = conv.parse_gfont_bytes(conv.build_gfont(parsed, 5))
    assert [glyph.codepoint for glyph in rebuilt.previews] == [0x42, 0x41]


def test_zip_entries_sorted_by_codepoint_and_named_decimal() -> None:
    glyphs = (make_glyph(0x42), make_glyph(0x41), make_glyph(0x10A))
    parsed = conv.ParsedGfont(5, make_metadata(), glyphs, ())
    rebuilt = conv.parse_gfont_bytes(conv.build_gfont(parsed, 5))
    assert [glyph.codepoint for glyph in rebuilt.glyphs] == [0x41, 0x42, 0x10A]


def test_build_rejects_empty_font() -> None:
    parsed = conv.ParsedGfont(5, make_metadata(), (), ())
    with pytest.raises(conv.GFontError, match="without glyphs"):
        conv.build_gfont(parsed, 5)


@pytest.mark.parametrize("target", [4, 6, 7, 9, 0])
def test_build_rejects_unsupported_target(target: int) -> None:
    parsed = conv.ParsedGfont(5, make_metadata(), (make_glyph(),), ())
    with pytest.raises(conv.GFontError, match="unsupported target version"):
        conv.build_gfont(parsed, target)


def test_build_rejects_above_bmp() -> None:
    data = build_v9()  # synthetic V9 with only U+0041, replace codepoint via parser
    parsed = conv.parse_gfont_bytes(data)
    high = conv.Glyph(0x1F600, parsed.glyphs[0].values, parsed.glyphs[0].commands)
    tampered = conv.ParsedGfont(parsed.version, parsed.metadata, (high,), ())
    with pytest.raises(conv.GFontError, match="does not fit Java char"):
        conv.build_gfont(tampered, 5)


def test_glyph_record_validation() -> None:
    with pytest.raises(conv.GFontError, match="even"):
        conv.Glyph(0x41, (1.0, 2.0, 3.0), b"\x00\x01").java_record()
    with pytest.raises(conv.GFontError, match="command count"):
        conv.Glyph(0x41, (1.0, 2.0, 3.0, 4.0), b"\x00\x01\x00").java_record()
    with pytest.raises(conv.GFontError, match="only 0/1"):
        conv.Glyph(0x41, (1.0, 2.0, 3.0, 4.0), b"\x00\x02").java_record()
    with pytest.raises(conv.GFontError, match="finite"):
        conv.Glyph(0x41, (1.0, float("nan")), b"\x00").java_record()
    with pytest.raises(conv.GFontError, match="Java char"):
        conv.Glyph(0x11000, (1.0, 2.0), b"\x00\x01").java_record()


def test_metadata_serialization_rejects_bad_target() -> None:
    with pytest.raises(conv.GFontError, match="unsupported target version"):
        make_metadata().serialized(9)


def test_parse_from_bytes_matches_load(tmp_path) -> None:
    data = build_v9()
    source = tmp_path / "x.gfont"
    source.write_bytes(data)
    assert conv.load_gfont(source) == conv.parse_gfont_bytes(data)
