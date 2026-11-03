"""ZIP general-purpose flag bits 0x2/0x4 (deflate compression options).

Some writers mark deflate entries with option bits (0x2 = maximum
compression, 0x4 = fast); the reader must not reject glyph entries that
carry them. A reference .gfont is rebuilt via ``build_gfont`` from the
repo's synthetic V9 fixture, then hex-patched in both the local and the
central directory headers of its ZIP payload.
"""
from __future__ import annotations

import io
import zipfile

import pytest

import gfont_converter as conv
from tests.conftest import FIXTURES
from tests.gfont_helpers import patch_zip_flag_bits

REPO_V9 = FIXTURES / "gfont_versions" / "v9.gfont"


def build_reference() -> bytes:
    """A fresh V5 .gfont built (via build_gfont) from the repo V9 fixture."""
    parsed = conv.parse_gfont_bytes(REPO_V9.read_bytes())
    assert parsed.version == 9
    return conv.build_gfont(parsed, 5)


@pytest.mark.parametrize("flag", [0x0002, 0x0004], ids=["flag-0x2", "flag-0x4"])
def test_deflate_option_flag_bits_accepted(flag: int) -> None:
    reference = build_reference()
    patched = patch_zip_flag_bits(reference, flag)

    # self-check: the flag really landed in the glyph entry and data is intact
    zip_start = patched.find(b"PK\x03\x04")
    with zipfile.ZipFile(io.BytesIO(patched[zip_start:])) as archive:
        info = archive.infolist()[0]
        assert info.flag_bits & flag == flag
        assert archive.read(info)

    parsed = conv.parse_gfont_bytes(patched)
    assert parsed.version == 5
    assert len(parsed.glyphs) == 1
    assert parsed.glyphs[0] == conv.parse_gfont_bytes(reference).glyphs[0]


def test_unpatched_reference_still_parses() -> None:
    parsed = conv.parse_gfont_bytes(build_reference())
    assert parsed.version == 5
    assert len(parsed.glyphs) == 1
