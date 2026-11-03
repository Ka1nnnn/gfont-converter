"""Shared fixtures and synthetic .gfont builders for the test suite.

The writer in ``gfont_converter`` only emits V3/V5, so V6/V7/V9 and bare-ZIP
containers are synthesized here byte-by-byte according to the format the
parser expects (see README "Container format").
"""
from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

import gfont_converter as conv

FIXTURES = Path(__file__).parent / "fixtures"

# A minimal but drawable glyph: pen-up at (0,0), lines to (10,0) and (10,20).
VALUES = (0.0, 0.0, 10.0, 0.0, 10.0, 20.0)
COMMANDS = bytes((0, 1, 1))
CODEPOINT = 0x41  # 'A'


def make_glyph(codepoint: int = CODEPOINT, values=VALUES, commands=COMMANDS) -> conv.Glyph:
    return conv.Glyph(codepoint, tuple(values), bytes(commands))


def make_metadata(**overrides) -> conv.Metadata:
    fields = {
        "e": "synthetic", "q": 2, "b": "1", "a": "1", "c": "test",
        "font_size": 270, "glyph_count": 1, "n": "", "o": "",
    }
    fields.update(overrides)
    return conv.Metadata(**fields)


def raw_blob(values=VALUES, commands=COMMANDS) -> bytes:
    """A V9 raw KDraw glyph blob (tag=2 => 2-byte coordinate count)."""
    count = len(values)
    return b"".join((
        struct.pack(">H", 2),
        b"\x00" * 8,  # reserved header the parser skips
        struct.pack(">H", count),
        struct.pack(f">{count}f", *values),
        struct.pack(">i", len(commands)),
        bytes(commands),
    ))


def zip_container(records: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(records.items()):
            archive.writestr(name, payload)
    return buffer.getvalue()


def build_v3_or_v6_or_v7(version: int, previews: int = 1) -> bytes:
    """V3 (plaintext metadata, no `o`) or V6/V7 (TEA-encrypted metadata)."""
    glyph = make_glyph()
    metadata = make_metadata()
    if version >= 5:
        encrypted = conv._tea_transform_encrypt(metadata.serialized(5))
        out = bytearray(struct.pack(">iI", version, len(encrypted)))
        out += encrypted
    else:
        out = bytearray(struct.pack(">i", version))
        out += metadata.serialized(3)
    out += struct.pack(">i", previews)
    for _ in range(previews):
        out += glyph.java_record()
    out += zip_container({str(glyph.codepoint): glyph.java_record()})
    return bytes(out)


def build_v9(previews: int = 1) -> bytes:
    """V9: TEA-encrypted metadata + raw KDraw blobs on disk and as previews."""
    glyph = make_glyph()
    plain = make_metadata().serialized(5)
    encrypted = conv._tea_transform_encrypt(plain)
    out = bytearray(struct.pack(">iI", 9, len(encrypted)))
    out += encrypted
    out += struct.pack(">i", previews)
    for _ in range(previews):
        out += raw_blob()
    out += zip_container({str(glyph.codepoint): raw_blob()})
    return bytes(out)


def build_bare_zip(codepoints=(0x41, 0x42)) -> bytes:
    """Bare-ZIP variant (reported as V0): raw blobs, no header/metadata."""
    return zip_container({str(cp): raw_blob() for cp in codepoints})


def build_v5_high_codepoint() -> bytes:
    """V5 container whose only glyph sits above the BMP (valid in, blocked out)."""
    glyph = make_glyph(codepoint=0x1F600)
    metadata = make_metadata()
    out = bytearray(struct.pack(">i", 5))
    plain = metadata.serialized(5)
    encrypted = conv._tea_transform_encrypt(plain)
    out += struct.pack(">I", len(encrypted))
    out += encrypted
    out += struct.pack(">i", 0)
    record = struct.pack(
        ">Hi", glyph.codepoint, len(glyph.values)
    ) + struct.pack(f">{len(glyph.values)}f", *glyph.values) + struct.pack(
        ">i", len(glyph.commands)
    ) + glyph.commands
    out += zip_container({str(glyph.codepoint): record})
    return bytes(out)


@pytest.fixture(scope="session")
def fixture_path() -> dict[str, Path]:
    paths = {
        "v3": FIXTURES / "gfont_versions" / "v3.gfont",
        "v5": FIXTURES / "gfont_versions" / "v5.gfont",
        "v6": FIXTURES / "gfont_versions" / "v6.gfont",
        "v7": FIXTURES / "gfont_versions" / "v7.gfont",
        "v9": FIXTURES / "gfont_versions" / "v9.gfont",
        "real_v9": FIXTURES / "real_v9_kvenjoy.gfont",
        "real_v0": FIXTURES / "real_v0_technical_latin.gfont",
    }
    missing = [str(p) for p in paths.values() if not p.is_file()]
    assert not missing, f"missing fixtures: {missing}"
    return paths


@pytest.fixture(scope="session")
def parsed_versions(fixture_path) -> dict[str, conv.ParsedGfont]:
    return {key: conv.load_gfont(path) for key, path in fixture_path.items()}


@pytest.fixture()
def tmp_gfont(tmp_path: Path) -> Path:
    """A valid synthetic V5 file on disk."""
    target = tmp_path / "sample.gfont"
    target.write_bytes(conv.build_gfont(
        conv.ParsedGfont(5, make_metadata(), (make_glyph(),), ()), 5,
    ))
    return target
