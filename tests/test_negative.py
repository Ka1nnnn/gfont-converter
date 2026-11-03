"""Negative tests: malformed containers must be rejected with human-readable errors."""
from __future__ import annotations

import io
import struct
import zipfile

import pytest

import gfont_converter as conv
from gfont_converter import GFontError, describe_error
from tests.conftest import (
    FIXTURES,
    build_v9,
    make_metadata,
    raw_blob,
    zip_container,
)


def _is_russian_hint(exc: BaseException) -> bool:
    """describe_error must map known failures away from the raw parser message."""
    hint = describe_error(exc)
    return bool(hint) and hint != str(exc)


def test_describe_error_covers_every_hint() -> None:
    # Every hint needle must be non-empty and every mapping Russian text distinct enough.
    needles = [needle for needle, _hint in conv._ERROR_HINTS]
    assert len(needles) == len(set(needles))
    assert all(needle for needle in needles)


def test_describe_error_passthrough_unknown() -> None:
    assert describe_error(RuntimeError("boom")) == "boom"


def test_all_fixture_files_parse() -> None:
    # Real-world fixtures may carry a stale glyph_count (a warning now, not
    # an error): parsing itself must succeed for every fixture.
    for path in FIXTURES.rglob("*.gfont"):
        parsed = conv.load_gfont(path)
        assert parsed.glyphs, f"fixture produced no glyphs: {path}"


@pytest.mark.parametrize("bad_version", [0, 1, 2, 4, 11, -1, 10, 0x7FFFFFFF])
def test_unsupported_versions(bad_version: int) -> None:
    data = struct.pack(">i", bad_version) + b"\x00" * 64
    with pytest.raises(GFontError, match="unsupported input version"):
        conv.parse_gfont_bytes(data)


def test_truncated_int32_version() -> None:
    with pytest.raises(GFontError, match="file is too short"):
        conv.parse_gfont_bytes(b"\x00\x00\x00")


def test_invalid_metadata_length() -> None:
    data = struct.pack(">iI", 5, 4) + b"\x00" * 64
    with pytest.raises(GFontError, match="invalid encrypted metadata length"):
        conv.parse_gfont_bytes(data)


def test_truncated_encrypted_metadata() -> None:
    encrypted = conv._tea_transform_encrypt(make_metadata().serialized(5))
    data = struct.pack(">iI", 5, len(encrypted)) + encrypted[:-8]
    with pytest.raises(GFontError):
        conv.parse_gfont_bytes(data)


def test_corrupted_encrypted_metadata_rejected() -> None:
    encrypted = bytearray(conv._tea_transform_encrypt(make_metadata().serialized(5)))
    encrypted[4] ^= 0xFF
    data = struct.pack(">iI", 5, len(encrypted)) + bytes(encrypted) + build_v9()[-64:]
    with pytest.raises(GFontError):
        conv.parse_gfont_bytes(data)


def test_metadata_enum_ordinal_out_of_range_warns() -> None:
    # W1: an unknown pen type (玉秋繁体-class files) is recoverable — the
    # value is kept as is and reported through the warnings channel.
    plain = make_metadata(q=99).serialized(5)
    encrypted = conv._tea_transform_encrypt(plain)
    data = (
        struct.pack(">iI", 5, len(encrypted))
        + encrypted
        + struct.pack(">i", 0)
        + zip_container({"65": make_java_record()})
    )
    warnings: list[str] = []
    parsed = conv.parse_gfont_bytes(data, warnings=warnings)
    assert parsed.version == 5
    assert parsed.metadata.q == 99  # kept as is
    assert any("неизвестный тип пера q=99" in message for message in warnings), warnings


def test_metadata_enum_ordinal_strict_without_warnings_channel() -> None:
    # The private parser stays strict when no warnings list is threaded in.
    plain = make_metadata(q=99).serialized(5)
    with pytest.raises(GFontError, match="ordinal out of range"):
        conv._parse_metadata(plain, 5, 0)


def test_missing_zip_payload() -> None:
    plain = make_metadata().serialized(5)
    encrypted = conv._tea_transform_encrypt(plain)
    data = struct.pack(">iI", 5, len(encrypted)) + encrypted + b"\x00\x00\x00\x00" + b"NOZIP!"
    with pytest.raises(GFontError, match="ZIP payload expected"):
        conv.parse_gfont_bytes(data)


def test_zip_without_directory() -> None:
    data = b"PK\x03\x04" + b"\x00" * 100
    with pytest.raises(GFontError, match="missing ZIP directory"):
        conv.parse_gfont_bytes(data)


def test_zip_entry_non_decimal_name() -> None:
    blob = zip_container({"glyph65": raw_blob()})
    with pytest.raises(GFontError, match="invalid or duplicate glyph entry"):
        conv.parse_gfont_bytes(blob)


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_zip_entry_duplicate_name() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("65", raw_blob())
        archive.writestr("65", raw_blob())
    with pytest.raises(GFontError, match="invalid or duplicate glyph entry"):
        conv.parse_gfont_bytes(buffer.getvalue())


def test_zip_empty_archive() -> None:
    # A local-file header followed by an EOCD with zero entries: valid ZIP,
    # zero glyphs -> rejected by the glyph-count guard.
    local = b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(GFontError, match="glyph count mismatch"):
        conv.parse_gfont_bytes(local + eocd)


def test_raw_glyph_identity_not_checked_for_v9() -> None:
    # Bare ZIP: entry named 66 holding a blob is fine (raw blobs carry no codepoint).
    parsed = conv.parse_gfont_bytes(zip_container({"66": raw_blob()}))
    assert parsed.glyphs[0].codepoint == 66


def test_raw_glyph_bad_tag() -> None:
    blob = struct.pack(">H", 9) + b"\x00" * 32
    with pytest.raises(GFontError, match="unsupported raw header tag"):
        conv.parse_gfont_bytes(zip_container({"65": blob}))


def test_raw_glyph_odd_coordinates() -> None:
    values = struct.pack(">3f", 1.0, 2.0, 3.0)
    blob = struct.pack(">H", 2) + b"\x00" * 8 + struct.pack(">H", 3) + values
    blob += struct.pack(">i", 1) + b"\x00"
    with pytest.raises(GFontError, match="odd"):
        conv.parse_gfont_bytes(zip_container({"65": blob}))


def test_raw_glyph_nan_coordinates() -> None:
    values = struct.pack(">2f", 1.0, float("nan"))
    blob = struct.pack(">H", 2) + b"\x00" * 8 + struct.pack(">H", 2) + values
    blob += struct.pack(">i", 1) + b"\x00"
    with pytest.raises(GFontError, match="NaN"):
        conv.parse_gfont_bytes(zip_container({"65": blob}))


def test_raw_glyph_commands_not_starting_with_zero() -> None:
    blob = raw_blob(commands=b"\x01\x01\x01")
    with pytest.raises(GFontError, match="start with 0"):
        conv.parse_gfont_bytes(zip_container({"65": blob}))


def test_raw_glyph_truncated_payload() -> None:
    blob = raw_blob()[:10]
    with pytest.raises(GFontError):
        conv.parse_gfont_bytes(zip_container({"65": blob}))


def test_java_glyph_trailing_bytes() -> None:
    record = make_java_record() + b"\x00\x00"
    with pytest.raises(GFontError, match="trailing bytes"):
        conv.parse_gfont_bytes(_v5_with_entry("65", record))


def make_java_record() -> bytes:
    return make_java_glyph().java_record()


def make_java_glyph():
    return conv.Glyph(0x41, (0.0, 0.0, 10.0, 0.0, 10.0, 20.0), b"\x00\x01\x01")


def _v5_with_entry(name: str, record: bytes) -> bytes:
    plain = make_metadata().serialized(5)
    encrypted = conv._tea_transform_encrypt(plain)
    return (
        struct.pack(">iI", 5, len(encrypted))
        + encrypted
        + struct.pack(">i", 0)
        + zip_container({name: record})
    )


def test_java_glyph_identity_mismatch() -> None:
    # ZIP entry says 65 but the record carries 0x42.
    record = conv.Glyph(0x42, (0.0, 0.0, 10.0, 0.0, 10.0, 20.0), b"\x00\x01\x01").java_record()
    with pytest.raises(GFontError, match="identity mismatch"):
        conv.parse_gfont_bytes(_v5_with_entry("65", record))


def test_load_gfont_missing_file(tmp_path) -> None:
    with pytest.raises(GFontError, match="cannot read file"):
        conv.load_gfont(tmp_path / "nope.gfont")


def test_load_gfont_directory(tmp_path) -> None:
    with pytest.raises(GFontError, match="cannot read file"):
        conv.load_gfont(tmp_path)


@pytest.mark.parametrize(
    "corrupt_offset",
    [0, 4, 8, 12],
)
def test_byte_flips_in_header_rejected(corrupt_offset: int) -> None:
    data = bytearray(build_v9())
    data[corrupt_offset] ^= 0xFF
    with pytest.raises((GFontError, zipfile.BadZipFile, ValueError, struct.error)):
        conv.parse_gfont_bytes(bytes(data))


def test_human_readable_hints_on_negative_paths(tmp_path) -> None:
    cases = [
        b"",  # too short
        struct.pack(">i", 42) + b"\x00" * 32,  # bad version
        struct.pack(">iI", 5, 3) + b"\x00" * 64,  # metadata length
        b"PK\x03\x04garbage",  # broken zip
    ]
    for data in cases:
        with pytest.raises((GFontError, zipfile.BadZipFile, struct.error, ValueError)) as exc_info:
            conv.parse_gfont_bytes(data)
        assert _is_russian_hint(exc_info.value), str(exc_info.value)


def test_empty_path_hint_is_human_readable(tmp_path) -> None:
    with pytest.raises(GFontError) as exc_info:
        conv.load_gfont(tmp_path / "absent.gfont")
    assert describe_error(exc_info.value).startswith("Не удалось прочитать файл")
