"""Synthetic V5/V8/V9 containers for reader regression tests.

The builders follow the V8 record layout: contour 0 = u16 UTF-8 byte length +
codepoint + u32 coordinate count + f32 coordinates + i32 point count +
0/1 command bytes; contours 1+ repeat without the header and merge into one
glyph. No personal font files are needed by these tests.
"""
from __future__ import annotations

import functools
import inspect
import io
import struct
import zipfile

import gfont_converter as conv
from tests.conftest import CODEPOINT, make_metadata, raw_blob, zip_container

V8_GLYPH_COUNT = 66
V8_PREVIEW_COUNT = 30
V5_STALE_METADATA_COUNT = 4377  # donor font counter; the ZIP truth is 66
V5_STALE_ZIP_COUNT = 66
V9_VALID_GLYPH_COUNT = 96


def parse_lenient(data: bytes) -> tuple[conv.ParsedGfont, list[str]]:
    """Parse with the warnings list the reader threads through parsing."""
    warnings: list[str] = []
    parsed = conv.parse_gfont_bytes(data, warnings=warnings)
    return parsed, warnings


def zip_entry_count(data: bytes) -> int:
    """Number of glyph entries in the container's ZIP payload."""
    zip_start = data.find(b"PK\x03\x04")
    with zipfile.ZipFile(io.BytesIO(data[zip_start:])) as archive:
        return len(archive.infolist())


@functools.lru_cache(maxsize=1)
def read_v8() -> conv.ParsedGfont:
    """Session-cached parse of a synthetic 66-glyph V8 font."""
    return conv.parse_gfont_bytes(build_v8_font())


def build_v8_font(glyph_count: int = V8_GLYPH_COUNT) -> bytes:
    """Build a V8 font with up to 30 previews and multi-contour ZIP glyphs."""
    records: dict[str, bytes] = {}
    previews: list[bytes] = []
    for index in range(glyph_count):
        codepoint = 0x410 + index
        offset = float(index % 7)
        values_a = (0.0, 0.0, 10.0 + offset, 0.0, 10.0 + offset, 20.0)
        commands_a = V8_CONTOUR_A_COMMANDS
        values_b = (5.0, 5.0, 15.0, 5.0)
        commands_b = V8_CONTOUR_B_COMMANDS
        contour_a = v8_contour(values_a, commands_a)
        contour_b = v8_contour(values_b, commands_b)
        records[str(codepoint)] = v8_glyph_blob(codepoint, (contour_a, contour_b))
        if len(previews) < V8_PREVIEW_COUNT:
            previews.append(v8_glyph_blob(codepoint, (contour_a,)))

    metadata = make_metadata(glyph_count=glyph_count).serialized(5)
    metadata += conv._write_utf("52a7c477-25f3-4fb6-ab1d-4f15bbbfc483")
    encrypted = conv._tea_transform_encrypt(metadata)
    out = bytearray(struct.pack(">iI", 8, len(encrypted)) + encrypted)
    out += struct.pack(">i", len(previews))
    out += b"".join(previews)
    out += zip_container(records)
    return bytes(out)


def build_stale_v5() -> bytes:
    """Build a V5 font whose metadata says 4377 glyphs but ZIP has 66."""
    glyphs = [
        conv.Glyph(0x30 + index, V8_CONTOUR_A_VALUES, V8_CONTOUR_A_COMMANDS)
        for index in range(V5_STALE_ZIP_COUNT)
    ]
    metadata = make_metadata(glyph_count=V5_STALE_METADATA_COUNT).serialized(5)
    encrypted = conv._tea_transform_encrypt(metadata)
    out = bytearray(struct.pack(">iI", 5, len(encrypted)) + encrypted)
    out += struct.pack(">i", 0)
    out += zip_container({str(glyph.codepoint): glyph.java_record() for glyph in glyphs})
    return bytes(out)


def build_v9_valid() -> bytes:
    """Build a valid 96-glyph V9 control font."""
    metadata = make_metadata(glyph_count=V9_VALID_GLYPH_COUNT).serialized(5)
    encrypted = conv._tea_transform_encrypt(metadata)
    out = bytearray(struct.pack(">iI", 9, len(encrypted)) + encrypted)
    out += struct.pack(">i", 0)
    out += zip_container({
        str(codepoint): raw_blob()
        for codepoint in range(0x30, 0x30 + V9_VALID_GLYPH_COUNT)
    })
    return bytes(out)


# --- synthetic V8 (multi-contour) --------------------------------------------

V8_CONTOUR_A_VALUES = (0.0, 0.0, 10.0, 0.0, 10.0, 20.0)
V8_CONTOUR_A_COMMANDS = bytes((0, 1, 1))
V8_CONTOUR_B_VALUES = (5.0, 5.0, 15.0, 5.0)
V8_CONTOUR_B_COMMANDS = bytes((0, 1))
V8_MERGED_GLYPH = conv.Glyph(
    0x410,
    V8_CONTOUR_A_VALUES + V8_CONTOUR_B_VALUES,
    V8_CONTOUR_A_COMMANDS + V8_CONTOUR_B_COMMANDS,
)


def v8_contour(values: tuple[float, ...], commands: bytes) -> bytes:
    """One contour body: u32 coordinate count + f32s + i32 point count + cmds."""
    return b"".join((
        struct.pack(">I", len(values)),
        struct.pack(f">{len(values)}f", *values),
        struct.pack(">i", len(commands)),
        commands,
    ))


def v8_glyph_blob(codepoint: int, contours: tuple[bytes, ...]) -> bytes:
    """A V8 raw glyph record: headed contour 0 + bare continuation contours."""
    char = chr(codepoint).encode("utf-8")
    headed = struct.pack(">H", len(char)) + char + contours[0]
    return headed + b"".join(contours[1:])


def build_v8_multicontour(entry_contour_b_commands: bytes | None = None) -> bytes:
    """A complete synthetic V8 font with one two-contour glyph (U+0410 'А').

    The metadata matches the real layout (fields + 36-char UUID tail), the
    two previews are headed single-contour records, and the single ZIP entry
    carries both contours of the glyph. ``entry_contour_b_commands``
    overrides the commands of the ZIP entry's contour #1 only (the previews
    stay clean), building the H1 pen-down contour-boundary case.
    """
    contour_b = V8_CONTOUR_B_COMMANDS if entry_contour_b_commands is None else entry_contour_b_commands
    plain = make_metadata(glyph_count=1).serialized(5)
    plain += conv._write_utf("52a7c477-25f3-4fb6-ab1d-4f15bbbfc483")
    encrypted = conv._tea_transform_encrypt(plain)
    out = bytearray(struct.pack(">iI", 8, len(encrypted)))
    out += encrypted
    out += struct.pack(">i", 2)
    out += v8_glyph_blob(0x41, (v8_contour(V8_CONTOUR_A_VALUES, V8_CONTOUR_A_COMMANDS),))
    out += v8_glyph_blob(0x42, (v8_contour(V8_CONTOUR_B_VALUES, V8_CONTOUR_B_COMMANDS),))
    entry = v8_glyph_blob(0x410, (
        v8_contour(V8_CONTOUR_A_VALUES, V8_CONTOUR_A_COMMANDS),
        v8_contour(V8_CONTOUR_B_VALUES, contour_b),
    ))
    out += zip_container({str(0x410): entry})
    return bytes(out)


# --- command 2 (Bezier) -------------------------------------------------------

COMMAND2_BLOB = raw_blob(
    values=(0.0, 0.0, 10.0, 0.0, 10.0, 20.0),
    commands=bytes((0, 1, 2)),
)
COMMAND2_MAPPED = bytes((0, 1, 1))


def build_v9_with_entry_blob(entry_blob: bytes) -> bytes:
    """A synthetic V9 container whose ZIP entry blob is caller-provided.

    The preview stays clean (0/1 commands) so only the ZIP glyph exercises
    the command-2 path.
    """
    encrypted = conv._tea_transform_encrypt(make_metadata().serialized(5))
    out = bytearray(struct.pack(">iI", 9, len(encrypted)))
    out += encrypted
    out += struct.pack(">i", 1)
    out += raw_blob()
    out += zip_container({str(CODEPOINT): entry_blob})
    return bytes(out)


def parse_raw_glyph_with_warnings(
    blob: bytes, codepoint: int,
) -> tuple[conv.Glyph, int, list[str], bool]:
    """Call the raw-glyph parser, threading a warnings list when it has one.

    Returns ``(glyph, consumed, warnings, accepts_warnings)``. Pre-fix the
    call raises on command bytes outside 0/1.
    """
    parameters = inspect.signature(conv._parse_raw_glyph).parameters
    if "warnings" in parameters:
        warnings: list[str] = []
        glyph, consumed = conv._parse_raw_glyph(blob, codepoint, 0, warnings=warnings)
        return glyph, consumed, warnings, True
    glyph, consumed = conv._parse_raw_glyph(blob, codepoint, 0)
    return glyph, consumed, [], False


# --- ZIP flag bit patching -----------------------------------------------------

def patch_zip_flag_bits(data: bytes, flag: int) -> bytes:
    """Set extra general-purpose bits in every local and central ZIP header."""
    out = bytearray(data)
    zip_start = out.find(b"PK\x03\x04")
    if zip_start < 0:
        raise AssertionError("no ZIP payload found")
    patched = 0
    position = zip_start
    while (found := out.find(b"PK\x03\x04", position)) >= 0:
        current = struct.unpack_from("<H", out, found + 6)[0]
        struct.pack_into("<H", out, found + 6, current | flag)
        patched += 1
        position = found + 4
    position = zip_start
    while (found := out.find(b"PK\x01\x02", position)) >= 0:
        current = struct.unpack_from("<H", out, found + 8)[0]
        struct.pack_into("<H", out, found + 8, current | flag)
        patched += 1
        position = found + 4
    if patched < 2:
        raise AssertionError("expected at least one local and one central header")
    return bytes(out)
