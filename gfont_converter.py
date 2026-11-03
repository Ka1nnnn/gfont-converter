"""GFont Converter core: read/write/convert KDraw/Kenjoy .gfont containers.

Input versions: V3, V5, V6, V7 (Java DataOutputStream glyph records), V8
(multi-contour raw records with UTF-8 codepoints, written by the 奎享字体
editor), V9 (raw KDraw blobs) and the bare-ZIP variant (raw blobs without a
header, reported as version 0; e.g. plotterSOFT resources/technical*.gfont).
Output targets: V3, V5. Source files are only ever read; writing is
performed to explicit destinations chosen by the caller.

Based on the owner's original single-file converter script, extended with:
V6/V7/V8 input support, the bare-ZIP variant, hardened ZIP-directory
validation, container size caps and human-readable (Russian) error
descriptions. Recoverable inconsistencies (a stale glyph counter in the
metadata, an unknown metadata trailer, an unknown pen type, an out-of-range
command byte, a pen-down contour boundary in V8 records, extra bytes before
the ZIP payload) are reported as warnings instead of hard errors; the ZIP
directory is the source of truth for the glyph set.
"""
from __future__ import annotations

import argparse
import io
import math
import struct
import sys
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

MAGIC_KEY = bytes((1, 9, 8, 9, 0, 8, 2, 6, 1, 9, 9, 2, 0, 8, 2, 8))
DELTA = 0x9E3779B9
MASK = 0xFFFFFFFF
SUPPORTED_INPUT_VERSIONS = (3, 5, 6, 7, 8, 9)
SUPPORTED_TARGETS = (3, 5)
PREVIEW_LIMIT = 30
MAX_CONTAINER_BYTES = 32 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_CONTAINER_GLYPHS = 16_384
MAX_GLYPH_POINTS = 2_000_000


class GFontError(ValueError):
    """Raised when a .gfont container is malformed or unsupported."""


@dataclass(frozen=True)
class Glyph:
    codepoint: int
    values: tuple[float, ...]
    commands: bytes

    @property
    def point_count(self) -> int:
        return len(self.values) // 2

    @property
    def payload_size(self) -> int:
        return 4 * len(self.values) + len(self.commands)

    def java_record(self) -> bytes:
        if not 0 <= self.codepoint <= 0xFFFF:
            raise GFontError(f"codepoint U+{self.codepoint:X} does not fit Java char")
        if len(self.values) % 2:
            raise GFontError("glyph coordinate count must be even")
        if len(self.commands) != self.point_count:
            raise GFontError("command count must equal point count")
        if any(command not in (0, 1) for command in self.commands):
            raise GFontError("glyph commands must contain only 0/1")
        if not all(math.isfinite(value) for value in self.values):
            raise GFontError("glyph coordinates must be finite")
        return b"".join((
            struct.pack(">H", self.codepoint),
            struct.pack(">i", len(self.values)),
            struct.pack(f">{len(self.values)}f", *self.values),
            struct.pack(">i", len(self.commands)),
            self.commands,
        ))


@dataclass(frozen=True)
class Metadata:
    e: str
    q: int
    b: str
    a: str
    c: str
    font_size: int
    glyph_count: int
    n: str = ""
    o: str = ""

    def serialized(self, version: int) -> bytes:
        if version not in SUPPORTED_TARGETS:
            raise GFontError(f"unsupported target version: {version}")
        parts = [
            _write_utf(self.e),
            struct.pack(">i", self.q),
            _write_utf(self.b),
            _write_utf(self.a),
            _write_utf(self.c),
            struct.pack(">i", self.font_size),
            struct.pack(">i", self.glyph_count),
            _write_utf(self.n),
        ]
        if version >= 4:
            parts.append(_write_utf(self.o))
        return b"".join(parts)


@dataclass(frozen=True)
class ParsedGfont:
    version: int
    metadata: Metadata
    glyphs: tuple[Glyph, ...]
    previews: tuple[Glyph, ...]


# --- Java modified UTF-8 -------------------------------------------------

def _write_utf(value: str) -> bytes:
    encoded = bytearray()
    units = value.encode("utf-16-be")
    for index in range(0, len(units), 2):
        code_unit = (units[index] << 8) | units[index + 1]
        if code_unit == 0:
            encoded.extend(b"\xc0\x80")
        elif code_unit <= 0x7F:
            encoded.append(code_unit)
        elif code_unit <= 0x7FF:
            encoded.extend((0xC0 | (code_unit >> 6), 0x80 | (code_unit & 0x3F)))
        else:
            encoded.extend((
                0xE0 | (code_unit >> 12),
                0x80 | ((code_unit >> 6) & 0x3F),
                0x80 | (code_unit & 0x3F),
            ))
    if len(encoded) > 0xFFFF:
        raise GFontError("Java writeUTF string is longer than 65535 bytes")
    return struct.pack(">H", len(encoded)) + bytes(encoded)


def _read_utf(data: bytes, offset: int) -> tuple[str, int]:
    if offset + 2 > len(data):
        raise GFontError("truncated UTF length")
    length = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    end = offset + length
    if end > len(data):
        raise GFontError("truncated UTF payload")
    raw = data[offset:end]
    chars: list[str] = []
    index = 0
    while index < len(raw):
        first = raw[index]
        if first < 0x80:
            code_unit = first
            width = 1
        elif first & 0xE0 == 0xC0 and index + 1 < len(raw):
            code_unit = ((first & 0x1F) << 6) | (raw[index + 1] & 0x3F)
            width = 2
        elif first & 0xF0 == 0xE0 and index + 2 < len(raw):
            code_unit = (
                ((first & 0x0F) << 12)
                | ((raw[index + 1] & 0x3F) << 6)
                | (raw[index + 2] & 0x3F)
            )
            width = 3
        else:
            raise GFontError("invalid Java modified UTF-8 sequence")
        if code_unit >= 0xD800 and code_unit <= 0xDBFF:
            if index + 5 >= len(raw):
                raise GFontError("unpaired UTF-16 surrogate")
            low = raw[index + 3]
            if low & 0xF0 != 0xE0:
                raise GFontError("invalid UTF-16 surrogate pair")
            low_unit = ((low & 0x0F) << 12) | ((raw[index + 4] & 0x3F) << 6) | (raw[index + 5] & 0x3F)
            if not 0xDC00 <= low_unit <= 0xDFFF:
                raise GFontError("invalid UTF-16 low surrogate")
            chars.append(chr(0x10000 + ((code_unit - 0xD800) << 10) + (low_unit - 0xDC00)))
            index += 6
        else:
            chars.append(chr(code_unit))
            index += width
    return "".join(chars), end


# --- TEA-compatible V5 metadata transform -------------------------------

def _u32(value: int) -> int:
    return value & MASK


def _tea_encrypt_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack(">2I", block)
    k = struct.unpack(">4I", key)
    total = 0
    for _ in range(16):
        total = _u32(total + DELTA)
        v0 = _u32(v0 + (_u32((v1 << 4) + k[0]) ^ _u32(v1 + total) ^ _u32((v1 >> 5) + k[1])))
        v1 = _u32(v1 + (_u32((v0 << 4) + k[2]) ^ _u32(v0 + total) ^ _u32((v0 >> 5) + k[3])))
    raw = struct.pack("<2I", v1, v0)
    return raw[::-1]


def _tea_decrypt_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack(">2I", block)
    k = struct.unpack(">4I", key)
    total = _u32(DELTA * 16)
    for _ in range(16):
        v1 = _u32(v1 - (_u32((v0 << 4) + k[2]) ^ _u32(v0 + total) ^ _u32((v0 >> 5) + k[3])))
        v0 = _u32(v0 - (_u32((v1 << 4) + k[0]) ^ _u32(v1 + total) ^ _u32((v1 >> 5) + k[1])))
        total = _u32(total - DELTA)
    raw = struct.pack("<2I", v1, v0)
    return raw[::-1]


def _tea_transform_encrypt(data: bytes, key: bytes = MAGIC_KEY) -> bytes:
    if not data or len(key) != 16:
        raise GFontError("metadata and 16-byte key are required")
    remaining = len(data)
    padding = (remaining + 10) % 8
    if padding:
        padding = 8 - padding
    block = [0] * 8
    block[0] = 0x20 | padding
    index = 1
    for _ in range(padding):
        block[index] = 0
        index += 1
    previous_cipher = [0] * 8
    previous_xor = [0] * 8
    output: list[int] = []
    cursor = 0

    def process() -> None:
        nonlocal block, previous_cipher, previous_xor, index
        for i in range(8):
            block[i] ^= previous_cipher[i]
        cipher = _tea_encrypt_block(bytes(block), key)
        start = len(output)
        output.extend(cipher)
        for i in range(8):
            output[start + i] ^= previous_xor[i]
        previous_xor = block[:]
        previous_cipher = output[start:start + 8]
        index = 0

    # Java's b=2 loop only seeds the first block; zero bytes are deterministic.
    rounds = 0
    while rounds < 2:
        if index < 8:
            block[index] = 0
            index += 1
            rounds += 1
        if index == 8:
            process()
    while remaining:
        if index < 8:
            block[index] = data[cursor]
            cursor += 1
            remaining -= 1
            index += 1
        if index == 8:
            process()
    rounds = 0
    while rounds < 7:
        if index < 8:
            block[index] = 0
            index += 1
            rounds += 1
        if index == 8:
            process()
    return bytes(output)


def _tea_transform_decrypt(data: bytes, key: bytes = MAGIC_KEY) -> bytes:
    if len(data) < 8 or len(data) % 8:
        raise GFontError("encrypted metadata must be 8-byte aligned")
    first = _tea_decrypt_block(data[:8], key)
    padding = first[0] & 7
    payload_length = len(data) - 1 - padding - 2 - 7
    if payload_length < 0:
        raise GFontError("invalid metadata padding")
    current = first
    previous_xor = bytearray(8)
    previous_cipher = bytearray(data)
    cursor = 8
    position = padding + 1
    rounds = 1
    while rounds <= 2:
        if position < 8:
            position += 1
            rounds += 1
            continue
        cipher = data[cursor:cursor + 8]
        if len(cipher) != 8:
            raise GFontError("truncated metadata cipher block")
        current = _tea_decrypt_block(bytes(a ^ b for a, b in zip(current, cipher, strict=True)), key)
        previous_xor = previous_cipher
        previous_cipher = bytearray(cipher)
        cursor += 8
        position = 0
    output = bytearray()
    remaining = payload_length
    while remaining:
        if position < 8:
            output.append(current[position] ^ previous_xor[position])
            position += 1
            remaining -= 1
        else:
            cipher = data[cursor:cursor + 8]
            if len(cipher) != 8:
                raise GFontError("truncated metadata payload block")
            current = _tea_decrypt_block(bytes(a ^ b for a, b in zip(current, cipher, strict=True)), key)
            previous_xor = previous_cipher
            previous_cipher = bytearray(cipher)
            cursor += 8
            position = 0
    rounds = 1
    while rounds <= 7:
        if position < 8:
            position += 1
            rounds += 1
            continue
        if position != 8:
            continue
        cipher = data[cursor:cursor + 8]
        if len(cipher) != 8:
            raise GFontError("truncated metadata trailer")
        current = _tea_decrypt_block(bytes(a ^ b for a, b in zip(current, cipher, strict=True)), key)
        previous_xor = previous_cipher
        previous_cipher = bytearray(cipher)
        cursor += 8
        position = 0
    return bytes(output)


# --- Readers -------------------------------------------------------------

def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise GFontError("truncated int32")
    return struct.unpack_from(">i", data, offset)[0], offset + 4


def _normalize_commands(
    commands: bytes, codepoint: int, warnings: list[str] | None
) -> bytes:
    """Approximate Bezier command 2 with a pen-down line, reported as a warning."""
    if 2 not in commands:
        return commands
    if warnings is not None:
        glyph = f"глиф U+{codepoint:04X}" if codepoint >= 0 else "превью-глиф"
        warnings.append(f"{glyph}: команда 2 (Безье) приближена линией")
    return bytes(1 if command == 2 else command for command in commands)


def _parse_raw_glyph(
    blob: bytes, codepoint: int = -1, offset: int = 0, *, warnings: list[str] | None = None
) -> tuple[Glyph, int]:
    if len(blob) < offset + 12:
        raise GFontError("raw glyph is too short")
    tag = struct.unpack_from(">H", blob, offset)[0]
    if tag not in (1, 2, 3):
        raise GFontError(f"unsupported raw header tag {tag}")
    cursor = offset + 10
    coordinate_count = int.from_bytes(blob[cursor:cursor + tag], "big")
    cursor += tag
    if coordinate_count % 2 or coordinate_count > 400_000:
        raise GFontError("raw coordinate count is odd")
    end_values = cursor + 4 * coordinate_count
    if end_values + 4 > len(blob):
        raise GFontError("raw coordinate payload is truncated")
    values = struct.unpack_from(f">{coordinate_count}f", blob, cursor)
    if not all(math.isfinite(value) for value in values):
        raise GFontError("raw coordinates contain NaN or infinity")
    point_count, cursor = _read_i32(blob, end_values)
    if point_count != coordinate_count // 2:
        raise GFontError("raw coordinate count does not match point count")
    end = cursor + point_count
    if end > len(blob):
        raise GFontError("raw command payload is truncated")
    commands = _normalize_commands(blob[cursor:end], codepoint, warnings)
    if not commands or commands[0] != 0:
        raise GFontError("raw commands must start with 0 and contain only 0/1")
    if any(command not in (0, 1) for command in commands):
        raise GFontError("raw commands must contain only 0/1")
    return Glyph(codepoint, tuple(values), commands), end


def _parse_raw_glyph_v8(
    blob: bytes,
    codepoint: int = -1,
    offset: int = 0,
    *,
    chained: bool = False,
    warnings: list[str] | None = None,
) -> tuple[Glyph, int]:
    """Parse a V8 (奎享字体) multi-contour raw glyph record.

    Contour #0: [u16 utf8 length][utf-8 codepoint][u32 coordinate count BE]
    [f32 coordinates BE][i32 point count][command bytes]; contours #1+ repeat
    without the u16/utf-8 prefix, and the coordinates/commands of every
    contour are concatenated. With ``chained`` (preview stream) the record
    ends where the next glyph header or the ZIP payload begins instead of
    at the end of the blob.
    """
    if len(blob) < offset + 8:
        raise GFontError("raw glyph is too short")
    tag = struct.unpack_from(">H", blob, offset)[0]
    if tag not in (1, 2, 3, 4):
        raise GFontError(f"unsupported raw header tag {tag}")
    try:
        char = blob[offset + 2:offset + 2 + tag].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GFontError("invalid raw glyph codepoint") from exc
    if len(char) != 1 or ord(char) > 0x10FFFF:
        raise GFontError("invalid raw glyph codepoint")
    glyph_codepoint = ord(char)
    values: list[float] = []
    commands = bytearray()
    cursor = offset + 2 + tag
    contour = 0
    while True:
        if cursor + 4 > len(blob):
            raise GFontError("raw coordinate payload is truncated")
        if contour and chained and blob[cursor:cursor + 2] != b"\x00\x00":
            break  # next glyph header (00 0T) or the ZIP magic (PK)
        coordinate_count = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if coordinate_count == 0 or coordinate_count % 2 or coordinate_count > 400_000:
            raise GFontError("raw coordinate count is odd")
        if cursor + 4 * coordinate_count + 4 > len(blob):
            raise GFontError("raw coordinate payload is truncated")
        contour_values = struct.unpack_from(f">{coordinate_count}f", blob, cursor)
        if not all(math.isfinite(value) for value in contour_values):
            raise GFontError("raw coordinates contain NaN or infinity")
        cursor += 4 * coordinate_count
        point_count, cursor = _read_i32(blob, cursor)
        if point_count != coordinate_count // 2:
            raise GFontError("raw coordinate count does not match point count")
        end = cursor + point_count
        if end > len(blob):
            raise GFontError("raw command payload is truncated")
        contour_commands = _normalize_commands(blob[cursor:end], glyph_codepoint, warnings)
        if contour_commands[0] != 0:
            if not contour:
                raise GFontError("raw commands must start with 0")
            # H1: a contour that starts pen-down would weld itself to the
            # previous contour once the contours are merged; normalize the
            # boundary to pen-up so the outline stays visually separate.
            contour_commands = b"\x00" + contour_commands[1:]
            if warnings is not None:
                warnings.append(
                    f"глиф U+{glyph_codepoint:04X}: граница контура нормализована в pen-up"
                )
        commands.extend(contour_commands)
        values.extend(contour_values)
        cursor = end
        contour += 1
        if cursor == len(blob):
            break
    if codepoint >= 0 and glyph_codepoint != codepoint:
        raise GFontError("glyph identity mismatch")
    return Glyph(glyph_codepoint, tuple(values), bytes(commands)), cursor


def _parse_java_glyph(
    blob: bytes, codepoint: int = -1, offset: int = 0, *, warnings: list[str] | None = None
) -> tuple[Glyph, int]:
    if offset + 6 > len(blob):
        raise GFontError("Java glyph record is too short")
    char_value = struct.unpack_from(">H", blob, offset)[0]
    count, cursor = _read_i32(blob, offset + 2)
    if count < 0 or count > 400_000 or count % 2 or cursor + 4 * count + 4 > len(blob):
        raise GFontError("invalid Java coordinate count")
    values = struct.unpack_from(f">{count}f", blob, cursor)
    if not all(math.isfinite(value) for value in values):
        raise GFontError("Java coordinates contain NaN or infinity")
    cursor += 4 * count
    command_count, cursor = _read_i32(blob, cursor)
    if command_count < 0 or cursor + command_count > len(blob):
        raise GFontError("truncated Java command payload")
    commands = blob[cursor:cursor + command_count]
    if len(commands) != count // 2 or not commands or commands[0] != 0:
        raise GFontError("invalid Java glyph command payload")
    effective = char_value if codepoint < 0 else codepoint
    commands = _normalize_commands(commands, effective, warnings)
    if any(command not in (0, 1) for command in commands):
        raise GFontError("Java commands must contain only 0/1")
    return Glyph(effective, tuple(values), commands), cursor + command_count


def _parse_metadata(
    data: bytes, version: int, offset: int, *, warnings: list[str] | None = None
) -> tuple[Metadata, int]:
    e, offset = _read_utf(data, offset)
    q, offset = _read_i32(data, offset)
    if not 0 <= q <= 4:
        # W1: an unknown pen type (e.g. 玉秋繁体 plot2) is recoverable: the
        # value is kept as is and reported through the warnings channel.
        if warnings is None:
            raise GFontError(f"metadata enum ordinal out of range: {q}")
        warnings.append(f"неизвестный тип пера q={q} — оставлен как есть")
    b, offset = _read_utf(data, offset)
    a, offset = _read_utf(data, offset)
    c, offset = _read_utf(data, offset)
    font_size, offset = _read_i32(data, offset)
    glyph_count, offset = _read_i32(data, offset)
    if font_size <= 0 or glyph_count < 0:
        raise GFontError("invalid metadata counts")
    n = ""
    o = ""
    if version >= 2:
        n, offset = _read_utf(data, offset)
    if version >= 4:
        o, offset = _read_utf(data, offset)
    return Metadata(e, q, b, a, c, font_size, glyph_count, n, o), offset


def _locate_zip_offset(data: bytes, offset: int, warnings: list[str]) -> int:
    """ZIP payload offset: right after the previews or the first local header.

    Some writers leave extra bytes between the preview block and the ZIP
    payload (e.g. 张生的字); the exact offset is checked first and the gap
    is reported as a warning once the first local header is found.
    """
    if offset + 4 <= len(data) and data[offset:offset + 4] == b"PK\x03\x04":
        return offset
    found = data.find(b"PK\x03\x04", offset)
    if found < 0:
        raise GFontError(f"ZIP payload expected at offset {offset}")
    warnings.append(f"между превью-блоком и ZIP найдено {found - offset} лишних байт")
    return found


def _check_zip_directory(data: bytes) -> None:
    """Reject oversized/multidisk/ZIP64 directories before ZipFile allocates entries."""
    start = max(0, len(data) - 65_557)
    end = data.rfind(b'PK\x05\x06', start)
    if end < 0 or end + 22 > len(data):
        raise GFontError('missing ZIP directory')
    fields = struct.unpack_from('<HHHHIIH', data, end + 4)
    disk, directory_disk, on_disk, total, size, offset, comment = fields
    if (disk or directory_disk or on_disk != total or total > MAX_CONTAINER_GLYPHS
            or total == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF
            or end + 22 + comment != len(data) or offset + size > end):
        raise GFontError('invalid or oversized ZIP directory')


def _read_zip(
    data: bytes, offset: int, *, version: int, warnings: list[str] | None = None
) -> tuple[Glyph, ...]:
    """Read the glyph entries of the ZIP payload; ``version`` selects the record layout."""
    zip_data = data[offset:]
    _check_zip_directory(zip_data)
    with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_CONTAINER_GLYPHS:
            raise GFontError("glyph count mismatch")
        result: list[Glyph] = []
        seen: set[str] = set()
        unpacked = 0
        points_total = 0
        for info in entries:
            name = info.filename
            if (name in seen or len(name) > 7 or not name.isascii() or not name.isdecimal()
                    or str(int(name)) != name or not 0 <= int(name) <= 0x10FFFF
                    or info.is_dir()
                    or info.flag_bits & ~(0x808 | 0x6)
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise GFontError("invalid or duplicate glyph entry")
            seen.add(name)
            unpacked += info.file_size
            if unpacked > MAX_UNPACKED_BYTES:
                raise GFontError("unpacked archive too large")
            try:
                codepoint = int(name)
            except ValueError as exc:  # pragma: no cover - guarded above
                raise GFontError(f"non-decimal glyph entry: {name}") from exc
            with archive.open(info) as stream:
                blob = stream.read(info.file_size + 1)
            if len(blob) != info.file_size:
                raise GFontError("invalid glyph size")
            # Raw layouts carry no Java char header; V8 records carry the
            # codepoint in the contour header (checked inside the parser).
            if version not in (0, 8, 9) and (
                len(blob) < 2 or struct.unpack_from(">H", blob)[0] != codepoint
            ):
                raise GFontError("glyph identity mismatch")
            if version == 8:
                glyph, consumed = _parse_raw_glyph_v8(blob, codepoint, warnings=warnings)
            elif version in (0, 9):
                glyph, consumed = _parse_raw_glyph(blob, codepoint, warnings=warnings)
            else:
                glyph, consumed = _parse_java_glyph(blob, codepoint, warnings=warnings)
            if consumed != len(blob):
                raise GFontError(f"trailing bytes in glyph entry {name}")
            points_total += len(glyph.commands)
            if points_total > MAX_GLYPH_POINTS:
                raise GFontError("too many glyph points")
            result.append(glyph)
        return tuple(result)


def _bare_zip_font_size(glyphs: tuple[Glyph, ...]) -> int:
    """Approximate font size from glyph heights for bare-ZIP containers."""
    size = 0
    for glyph in glyphs:
        values = glyph.values
        if values:
            height = max(values[1::2]) - min(values[1::2])
            size = max(size, height)
    return max(round(size), 1)


def parse_gfont_bytes(data: bytes, *, warnings: list[str] | None = None) -> ParsedGfont:
    """Parse a container; recoverable issues are reported via ``warnings``.

    The ZIP directory is the source of truth for the glyph set, so a stale
    ``glyph_count`` in the metadata never blocks reading. Warnings are also
    collected for an unknown metadata trailer, an unknown pen type ``q``,
    Bezier command bytes, V8 contour boundaries normalized to pen-up and
    extra bytes before the ZIP payload; when ``warnings`` is not passed they
    are dropped (nothing raises).
    """
    collected: list[str] = [] if warnings is None else warnings
    if len(data) < 4:
        raise GFontError("file is too short")
    if len(data) > MAX_CONTAINER_BYTES:
        raise GFontError("container is larger than 32 MiB")
    if data[:4] == b"PK\x03\x04":
        # Bare-ZIP variant (plotterSOFT technical fonts): raw KDraw glyph
        # blobs, no version header and no encrypted metadata.
        glyphs = _read_zip(data, 0, version=0, warnings=collected)
        metadata = Metadata("", 0, "", "", "", _bare_zip_font_size(glyphs), len(glyphs))
        return ParsedGfont(0, metadata, glyphs, ())
    version, offset = _read_i32(data, 0)
    if version not in SUPPORTED_INPUT_VERSIONS:
        raise GFontError(f"unsupported input version {version}")
    metadata_length = 0
    if version >= 5:
        metadata_length, offset = _read_i32(data, offset)
        if metadata_length < 8 or offset + metadata_length > len(data):
            raise GFontError("invalid encrypted metadata length")
        metadata_plain = _tea_transform_decrypt(data[offset:offset + metadata_length])
        offset += metadata_length
    else:
        metadata_plain = data
    metadata, metadata_cursor = _parse_metadata(
        metadata_plain, version, 0 if version >= 5 else offset, warnings=collected
    )
    if version >= 5 and metadata_cursor != len(metadata_plain):
        collected.append(
            "метаданные содержат неизвестный хвост "
            f"({len(metadata_plain) - metadata_cursor} байт)"
        )
    if version >= 5:
        offset = 8 + metadata_length
    else:
        offset = metadata_cursor
    preview_count, offset = _read_i32(data, offset)
    if preview_count < 0 or preview_count > PREVIEW_LIMIT:
        raise GFontError(f"invalid preview count: {preview_count}")
    previews: list[Glyph] = []
    for _ in range(preview_count):
        if version == 8:
            glyph, offset = _parse_raw_glyph_v8(
                data, -1, offset, chained=True, warnings=collected
            )
        elif version == 9:
            glyph, offset = _parse_raw_glyph(data, -1, offset, warnings=collected)
        else:
            glyph, offset = _parse_java_glyph(data, -1, offset, warnings=collected)
        previews.append(glyph)
    # V8 preview records stop at the next header, and some writers leave
    # padding between the previews and the ZIP payload: tolerate extra bytes
    # on every version (exact offset first, first local header as fallback).
    glyphs = _read_zip(
        data, _locate_zip_offset(data, offset, collected), version=version, warnings=collected
    )
    if metadata.glyph_count != len(glyphs):
        collected.append(
            f"metadata glyph_count={metadata.glyph_count} but ZIP has {len(glyphs)} glyphs"
        )
    return ParsedGfont(version, metadata, glyphs, tuple(previews))


def load_gfont(path: Path | str) -> ParsedGfont:
    """Read and validate a .gfont file from disk."""
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise GFontError(f"cannot read file: {exc}") from exc
    return parse_gfont_bytes(data)


# --- Writer --------------------------------------------------------------

def _select_previews(glyphs: Sequence[Glyph], limit: int = PREVIEW_LIMIT) -> tuple[Glyph, ...]:
    ordered = sorted(glyphs, key=lambda glyph: (-glyph.payload_size, glyph.codepoint))
    return tuple(ordered[: min(limit, len(ordered))])


def build_gfont(parsed: ParsedGfont, target: int) -> bytes:
    if target not in SUPPORTED_TARGETS:
        raise GFontError(f"unsupported target version: {target}")
    if not parsed.glyphs:
        raise GFontError("cannot write a font without glyphs")
    metadata = replace(parsed.metadata, glyph_count=len(parsed.glyphs))
    plain_metadata = metadata.serialized(target)
    if target == 5:
        encrypted = _tea_transform_encrypt(plain_metadata)
        output = bytearray(struct.pack(">iI", 5, len(encrypted)) + encrypted)
    else:
        output = bytearray(struct.pack(">i", 3) + plain_metadata)
    previews = _select_previews(parsed.glyphs)
    output.extend(struct.pack(">i", len(previews)))
    for glyph in previews:
        output.extend(glyph.java_record())
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for glyph in sorted(parsed.glyphs, key=lambda item: item.codepoint):
            archive.writestr(str(glyph.codepoint), glyph.java_record())
    output.extend(archive_buffer.getvalue())
    return bytes(output)


def convert_bytes(data: bytes, target: int) -> bytes:
    return build_gfont(parse_gfont_bytes(data), target)


def convert_file(source: Path, destination: Path, target: int) -> ParsedGfont:
    parsed = parse_gfont_bytes(source.read_bytes())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build_gfont(parsed, target))
    return parsed


# --- Human-readable errors (Russian) --------------------------------------

_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("file is too short", "Файл слишком мал — это не .gfont контейнер."),
    ("larger than 32 MiB", "Файл слишком большой (> 32 МиБ) — отклонён."),
    ("unsupported input version", "Неподдерживаемая версия .gfont (ожидаются V3/V5/V6/V7/V8/V9)."),
    ("unsupported target version", "Неподдерживаемая целевая версия (доступны V3 и V5)."),
    ("invalid encrypted metadata length", "Повреждён блок зашифрованных метаданных."),
    (
        "encrypted metadata must be 8-byte aligned",
        "Метаданные повреждены (неверное выравнивание шифроблока).",
    ),
    ("invalid metadata padding", "Метаданные повреждены (некорректное дополнение)."),
    ("metadata cipher block", "Метаданные повреждены (обрезан шифроблок)."),
    ("metadata payload block", "Метаданные повреждены (обрезан блок данных)."),
    ("metadata trailer", "Метаданные повреждены (обрезан хвост шифра)."),
    ("truncated UTF", "Метаданные повреждены (обрезана строка)."),
    ("invalid Java modified UTF", "Метаданные повреждены (некорректная строка UTF)."),
    ("surrogate", "Метаданные повреждены (битая UTF-16 пара)."),
    ("metadata enum ordinal", "Метаданные повреждены (неверный тип пера)."),
    ("invalid metadata counts", "Метаданные повреждены (нулевой размер шрифта или счётчик глифов)."),
    ("ZIP payload expected", "Внутри файла не найден ZIP-контейнер глифов — файл обрезан или это не .gfont."),
    ("missing ZIP directory", "ZIP-каталог внутри файла не найден — файл обрезан."),
    ("invalid or oversized ZIP directory", "ZIP-каталог внутри файла повреждён (недопустимые поля)."),
    ("invalid or duplicate glyph entry", "В ZIP-контейнере некорректная или дублирующаяся запись глифа."),
    ("glyph identity mismatch", "Имя записи глифа не совпадает с кодом символа внутри записи."),
    ("trailing bytes in glyph entry", "Лишние байты в записи глифа — файл повреждён."),
    ("invalid glyph size", "Размер записи глифа не совпадает с каталогом ZIP."),
    ("unpacked archive too large", "Распакованный контейнер слишком велик — файл отклонён."),
    ("too many glyph points", "Слишком много точек в глифах — файл отклонён."),
    ("raw glyph is too short", "Запись глифа обрезана (V8/V9)."),
    ("Java glyph record is too short", "Запись глифа обрезана (V3/V5/V6/V7)."),
    ("unsupported raw header tag", "Неизвестный тип записи глифа (V8/V9)."),
    ("invalid raw glyph codepoint", "Код символа в записи глифа повреждён (V8)."),
    ("coordinate count is odd", "Нечётное число координат в глифе — файл повреждён."),
    ("coordinate payload is truncated", "Координаты глифа обрезаны — файл повреждён."),
    ("NaN or infinity", "Координаты глифа содержат NaN/бесконечность — файл повреждён."),
    ("coordinate count does not match", "Число координат не совпадает со счётчиком точек — файл повреждён."),
    ("command payload is truncated", "Таблица команд глифа обрезана — файл повреждён."),
    ("commands must start with 0", "Таблица команд глифа повреждена (должна начинаться с 0)."),
    ("contain only 0/1", "Таблица команд глифа повреждена (ожидается 0/1)."),
    ("invalid Java coordinate count", "Повреждено число координат в записи глифа."),
    ("invalid Java glyph command payload", "Повреждена таблица команд глифа."),
    ("does not fit Java char", "Глиф с кодом > U+FFFF нельзя записать в V3/V5 (требуется Java char)."),
    ("cannot write a font without glyphs", "В шрифте нет ни одного глифа — записывать нечего."),
    ("cannot read file", "Не удалось прочитать файл."),
    ("is a directory", "Указан путь к папке, а не к файлу."),
)


def describe_error(exc: BaseException) -> str:
    """Map internal parser messages to human-readable Russian text."""
    text = str(exc)
    for needle, hint in _ERROR_HINTS:
        if needle in text:
            return hint
    if isinstance(exc, zipfile.BadZipFile):
        return f"ZIP-контейнер глифов повреждён: {text}"
    if isinstance(exc, struct.error):
        return f"Файл повреждён (обрезанная структура): {text}"
    if isinstance(exc, OSError):
        return f"Ошибка доступа к файлу: {text}"
    if isinstance(exc, GFontError):
        return f"Некорректный .gfont: {text}"
    return text


# --- CLI ------------------------------------------------------------------

def _parse_target(value: str) -> int:
    normalized = value.upper()
    if normalized not in {"V3", "V5"}:
        raise argparse.ArgumentTypeError("target must be V3 or V5")
    return int(normalized[1:])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert KDraw/Kenjoy .gfont fonts (V3/V5/V6/V7/V8/V9) to V3/V5"
    )
    parser.add_argument("input", type=Path, help="input .gfont file or directory")
    parser.add_argument("-o", "--output", type=Path, help="output file, or directory in directory mode")
    parser.add_argument("--target", type=_parse_target, default=5, help="target V3 or V5 (default: V5)")
    return parser


def _convert_many(sources: Iterable[Path], output_dir: Path, target: int) -> list[Path]:
    written: list[Path] = []
    for source in sources:
        destination = output_dir / f"{source.stem}_v{target}.gfont"
        convert_file(source, destination, target)
        written.append(destination)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.exists():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2
    try:
        if args.input.is_dir():
            sources = sorted(args.input.glob("*.gfont"))
            if not sources:
                raise GFontError(f"no .gfont files in {args.input}")
            output_dir = args.output or (args.input / "converted_fonts")
            for destination in _convert_many(sources, output_dir, args.target):
                print(f"wrote {destination}")
        else:
            if args.output is None:
                raise GFontError("-o/--output is required for a single input file")
            if args.output.resolve() == args.input.resolve():
                raise GFontError("output must differ from input; source files are read-only")
            convert_file(args.input, args.output, args.target)
            print(f"wrote {args.output}")
    except (GFontError, OSError, zipfile.BadZipFile, ValueError) as exc:
        print(f"error: {describe_error(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
