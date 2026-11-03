"""Java modified UTF-8 codec tests (_write_utf / _read_utf)."""
from __future__ import annotations

import struct

import pytest

from gfont_converter import GFontError, _read_utf, _write_utf


@pytest.mark.parametrize(
    ("text", "expected_len"),
    [
        ("", 0),
        ("abc", 3),
        ("\x00", 2),  # NUL is encoded as C0 80, not 00
        ("Ж", 2),  # Cyrillic fits two modified-UTF8 bytes
        ("ÿ", 2),  # U+00FF, two-byte form
        ("\u4e2d\u6587", 6),  # CJK, three bytes each
        ("\U0001F600", 6),  # non-BMP -> surrogate pair, 3 + 3 bytes
    ],
)
def test_write_utf_lengths(text: str, expected_len: int) -> None:
    encoded = _write_utf(text)
    assert len(encoded) == expected_len + 2  # + big-endian uint16 length prefix
    assert struct.unpack_from(">H", encoded)[0] == expected_len


@pytest.mark.parametrize(
    "text",
    ["", "ascii", "Привет мир", "\x00embedded\x00null", "\u4e2d\u6587\u5b57", "\U0001F600\U0001F601", "mix\x00Ж\u4e2d"],
)
def test_read_write_roundtrip(text: str) -> None:
    decoded, end = _read_utf(_write_utf(text), 0)
    assert decoded == text
    assert end == len(_write_utf(text))


def test_nul_encoded_as_c0_80() -> None:
    encoded = _write_utf("\x00")
    assert encoded[2:] == b"\xc0\x80"


def test_read_utf_offset_handling() -> None:
    payload = b"\xde\xad" + _write_utf("ok")
    decoded, end = _read_utf(payload, 2)
    assert decoded == "ok"
    assert end == len(payload)


def test_read_utf_truncated_length() -> None:
    with pytest.raises(GFontError, match="truncated UTF length"):
        _read_utf(b"\x00", 0)


def test_read_utf_truncated_payload() -> None:
    with pytest.raises(GFontError, match="truncated UTF payload"):
        _read_utf(struct.pack(">H", 10) + b"abc", 0)


def test_read_utf_invalid_sequence() -> None:
    with pytest.raises(GFontError, match="invalid Java modified UTF-8"):
        _read_utf(struct.pack(">H", 1) + b"\xff", 0)


def test_read_utf_unpaired_surrogate() -> None:
    # High surrogate C3... wait: surrogate U+D83D encodes as ED A0 BD.
    encoded = struct.pack(">H", 3) + b"\xed\xa0\xbd"
    with pytest.raises(GFontError, match="unpaired UTF-16 surrogate"):
        _read_utf(encoded, 0)


def test_read_utf_invalid_low_surrogate() -> None:
    encoded = struct.pack(">H", 6) + b"\xed\xa0\xbd" + b"\xed\x00\x00"
    with pytest.raises(GFontError, match="low surrogate"):
        _read_utf(encoded, 0)


def test_write_utf_too_long() -> None:
    with pytest.raises(GFontError, match="longer than 65535"):
        _write_utf("a" * 65536)
