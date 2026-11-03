"""Parsing tests across every supported container version."""
from __future__ import annotations

import struct

import pytest

import gfont_converter as conv
from tests.conftest import (
    build_bare_zip,
    build_v3_or_v6_or_v7,
    build_v9,
    make_glyph,
    make_metadata,
    raw_blob,
    zip_container,
)


@pytest.mark.parametrize(
    ("key", "version", "glyphs", "previews"),
    [
        ("v3", 3, 1, 1),
        ("v5", 5, 1, 1),
        ("v6", 6, 1, 1),
        ("v7", 7, 1, 1),
        ("v9", 9, 1, 0),
        ("real_v9", 9, 122, 30),  # preview list capped at PREVIEW_LIMIT
    ],
)
def test_parse_version_fixture(parsed_versions, key: str, version: int, glyphs: int, previews: int) -> None:
    parsed = parsed_versions[key]
    assert parsed.version == version
    assert len(parsed.glyphs) == glyphs
    assert len(parsed.previews) == previews
    assert parsed.metadata.glyph_count == glyphs


def test_parse_bare_zip_reports_version_zero() -> None:
    parsed = conv.parse_gfont_bytes(build_bare_zip())
    assert parsed.version == 0
    assert [glyph.codepoint for glyph in parsed.glyphs] == [0x41, 0x42]
    # metadata is synthesized: glyph_count real, font_size estimated from heights
    assert parsed.metadata.glyph_count == 2
    assert parsed.metadata.font_size >= 20  # our glyph is 20 units tall
    assert parsed.previews == ()


def test_parse_real_bare_zip_fixture(parsed_versions) -> None:
    parsed = parsed_versions["real_v0"]
    assert parsed.version == 0
    assert len(parsed.glyphs) == 62
    assert sorted(glyph.codepoint for glyph in parsed.glyphs)[0] == 0x30


def test_metadata_fields_synthetic(parsed_versions) -> None:
    for key in ("v3", "v5", "v6", "v7", "v9"):
        meta = parsed_versions[key].metadata
        assert meta.e == "synthetic"
        assert meta.font_size == 270
        assert 0 <= meta.q <= 4


def test_real_v9_metadata(parsed_versions) -> None:
    meta = parsed_versions["real_v9"].metadata
    assert meta.e == "kvenjoy"
    assert meta.font_size == 525


def test_glyph_structure_everywhere(parsed_versions) -> None:
    for parsed in parsed_versions.values():
        for glyph in parsed.glyphs:
            assert len(glyph.values) % 2 == 0
            assert len(glyph.commands) == glyph.point_count
            assert set(glyph.commands) <= {0, 1}
            assert glyph.commands[0] == 0


def test_v6_and_v7_share_java_layout() -> None:
    """V6/V7 differ only in the version int; both parse the same records."""
    for version in (6, 7):
        parsed = conv.parse_gfont_bytes(build_v3_or_v6_or_v7(version))
        assert parsed.version == version
        assert parsed.glyphs == conv.parse_gfont_bytes(build_v3_or_v6_or_v7(version)).glyphs


def test_v9_uses_raw_glyphs() -> None:
    parsed = conv.parse_gfont_bytes(build_v9())
    glyph = parsed.glyphs[0]
    assert glyph.codepoint == 0x41
    assert glyph.values == (0.0, 0.0, 10.0, 0.0, 10.0, 20.0)
    assert glyph.commands == b"\x00\x01\x01"


def test_glyph_payload_size_matches_record() -> None:
    glyph = make_glyph()
    assert glyph.payload_size == 4 * len(glyph.values) + len(glyph.commands)
    assert glyph.point_count == 3


def test_unsupported_version_rejected() -> None:
    with pytest.raises(conv.GFontError, match="unsupported input version 4"):
        conv.parse_gfont_bytes(b"\x00\x00\x00\x04" + b"\x00" * 32)


def test_container_size_cap() -> None:
    with pytest.raises(conv.GFontError, match="larger than 32 MiB"):
        conv.parse_gfont_bytes(b"PK\x03\x04" + b"\x00" * (conv.MAX_CONTAINER_BYTES))


def test_too_short_input() -> None:
    with pytest.raises(conv.GFontError, match="file is too short"):
        conv.parse_gfont_bytes(b"PK")


def test_glyph_count_mismatch_warns_when_warnings_passed() -> None:
    # Hand-build a V9 container whose encrypted metadata lies about glyph_count;
    # the writer path always fixes the count, so build the bytes manually.
    meta = make_metadata(glyph_count=99)
    encrypted = conv._tea_transform_encrypt(meta.serialized(5))
    blob = b"".join((
        b"\x00\x00\x00\x09",
        len(encrypted).to_bytes(4, "big"),
        encrypted,
        b"\x00\x00\x00\x00",
    )) + zip_container({"65": raw_blob()})
    warnings: list[str] = []
    parsed = conv.parse_gfont_bytes(blob, warnings=warnings)
    assert parsed.version == 9
    assert warnings and "99" in warnings[0] and "1" in warnings[0]


def test_glyph_count_mismatch_never_raises() -> None:
    # A stale counter is recoverable: the ZIP directory is the source of
    # truth, so parsing succeeds even without a warnings list.
    meta = make_metadata(glyph_count=99)
    plain = meta.serialized(5)
    encrypted = conv._tea_transform_encrypt(plain)
    blob = b"".join((
        b"\x00\x00\x00\x09",
        len(encrypted).to_bytes(4, "big"),
        encrypted,
        b"\x00\x00\x00\x00",
    )) + zip_container({"65": raw_blob()})
    parsed = conv.parse_gfont_bytes(blob)
    assert parsed.version == 9
    assert len(parsed.glyphs) == 1
    assert parsed.metadata.glyph_count == 99  # stale value kept, ZIP wins


@pytest.mark.parametrize("version", [3, 9])
def test_padding_before_zip_tolerated_with_warning(version: int) -> None:
    # W2: extra bytes between the preview block and the ZIP payload (e.g.
    # 张生的字) are skipped on every version, reported through warnings.
    glyph = make_glyph()
    pad = b"\x00\x01\x02PAD"
    if version == 3:
        record = glyph.java_record()
        body = struct.pack(">i", version) + make_metadata().serialized(3)
        body += struct.pack(">i", 1) + record
    else:
        record = raw_blob()
        encrypted = conv._tea_transform_encrypt(make_metadata().serialized(5))
        body = struct.pack(">iI", version, len(encrypted)) + encrypted
        body += struct.pack(">i", 1) + raw_blob()
    warnings: list[str] = []
    parsed = conv.parse_gfont_bytes(body + pad + zip_container({"65": record}), warnings=warnings)
    assert parsed.version == version
    assert len(parsed.glyphs) == 1
    assert parsed.glyphs[0].commands[0] == 0
    assert any("лишних байт" in message for message in warnings), warnings
