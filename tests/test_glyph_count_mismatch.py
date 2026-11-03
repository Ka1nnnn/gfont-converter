"""Stale metadata glyph_count warns, and a valid V9 control stays quiet."""
from __future__ import annotations

import gfont_converter as conv
from tests.gfont_helpers import (
    V5_STALE_METADATA_COUNT,
    V5_STALE_ZIP_COUNT,
    V9_VALID_GLYPH_COUNT,
    build_stale_v5,
    build_v9_valid,
    parse_lenient,
    zip_entry_count,
)


def test_stale_v5_loads_without_exception() -> None:
    parsed = conv.parse_gfont_bytes(build_stale_v5())
    assert parsed.version == 5
    assert len(parsed.glyphs) == V5_STALE_ZIP_COUNT


def test_stale_v5_counter_mismatch_warns() -> None:
    data = build_stale_v5()
    parsed, warnings = parse_lenient(data)
    assert parsed.metadata.glyph_count == V5_STALE_METADATA_COUNT  # donor value
    assert len(parsed.glyphs) == V5_STALE_ZIP_COUNT  # ZIP is the source of truth
    assert zip_entry_count(data) == V5_STALE_ZIP_COUNT
    assert any("4377" in message and "66" in message for message in warnings), warnings


def test_stale_v5_convert_roundtrip_writes_correct_counter(tmp_path) -> None:
    source_path = tmp_path / "synthetic_stale_v5.gfont"
    destination = tmp_path / "stale_v5_converted.gfont"
    data = build_stale_v5()
    source_path.write_bytes(data)
    conv.convert_file(source_path, destination, 5)
    reread = conv.load_gfont(destination)
    assert reread.version == 5
    assert reread.metadata.glyph_count == V5_STALE_ZIP_COUNT  # counter recomputed
    assert len(reread.glyphs) == V5_STALE_ZIP_COUNT

    source = conv.parse_gfont_bytes(data)
    original = {glyph.codepoint: glyph for glyph in source.glyphs}
    for glyph in reread.glyphs:
        assert glyph == original[glyph.codepoint], f"glyph U+{glyph.codepoint:04X} changed"


def test_valid_v9_control_reports_no_counter_warning() -> None:
    data = build_v9_valid()
    parsed, warnings = parse_lenient(data)
    assert len(parsed.glyphs) == V9_VALID_GLYPH_COUNT == zip_entry_count(data)
    assert not any("glyph_count" in message for message in warnings)
    assert conv.parse_gfont_bytes(data).version == 9  # strict path still works
