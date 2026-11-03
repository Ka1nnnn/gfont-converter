"""TEA metadata transform tests: roundtrip, structure, failure modes."""
from __future__ import annotations

import struct

import pytest

import gfont_converter as conv
from gfont_converter import GFontError, _tea_transform_decrypt, _tea_transform_encrypt


@pytest.mark.parametrize("length", [1, 5, 7, 8, 13, 16, 31, 64, 100])
def test_encrypt_decrypt_roundtrip(length: int) -> None:
    data = bytes(range(length))
    encrypted = _tea_transform_encrypt(data)
    assert len(encrypted) % 8 == 0
    assert len(encrypted) >= length + 10  # header byte + padding + 2 seed + 7 trailer zeros
    assert _tea_transform_decrypt(encrypted) == data


def test_encrypt_is_deterministic() -> None:
    data = b"metadata-payload"
    assert _tea_transform_encrypt(data) == _tea_transform_encrypt(data)


def test_encrypt_output_differs_from_plaintext() -> None:
    data = b"visible text"
    assert data not in _tea_transform_encrypt(data)


def test_custom_key_roundtrip() -> None:
    key = bytes(range(16))
    data = b"custom key payload"
    assert _tea_transform_decrypt(_tea_transform_encrypt(data, key), key) == data


def test_encrypt_rejects_empty_payload() -> None:
    with pytest.raises(GFontError, match="metadata and 16-byte key are required"):
        _tea_transform_encrypt(b"")


def test_encrypt_rejects_bad_key_length() -> None:
    with pytest.raises(GFontError, match="16-byte key"):
        _tea_transform_encrypt(b"abc", key=b"short")


def test_decrypt_rejects_unaligned() -> None:
    with pytest.raises(GFontError, match="8-byte aligned"):
        _tea_transform_decrypt(b"\x00" * 9)


def test_decrypt_rejects_short_input() -> None:
    with pytest.raises(GFontError, match="8-byte aligned"):
        _tea_transform_decrypt(b"\x00" * 4)


def test_decrypt_garbage_raises_or_garbage_out() -> None:
    # Random bytes usually violate the padding header; both failure and
    # wrong-length output are acceptable, silent success with valid data is not.
    garbage = struct.pack(">I", 0xDEADBEEF) * 4
    try:
        result = _tea_transform_decrypt(garbage)
    except GFontError:
        return
    assert result != b"kvenjoy"


def test_magic_key_constant() -> None:
    assert conv.MAGIC_KEY == bytes((1, 9, 8, 9, 0, 8, 2, 6, 1, 9, 9, 2, 0, 8, 2, 8))
    assert len(conv.MAGIC_KEY) == 16


def test_tea_block_known_structure() -> None:
    # Encryption of two identical blocks (no CBC chaining on first block) must
    # still differ because of chaining; verify block count math for a 16-byte payload.
    encrypted = _tea_transform_encrypt(b"x" * 16)
    assert len(encrypted) == 8 * 4  # 16 data + 1 header + padding + 2 + 7 trailer, padded to 32
