"""Command byte 2 (Bezier) in raw KDraw glyph blobs: accepted as 1 + warning.

The writer only emits 0/1 commands, so the glyph blob is assembled by hand
and fed to the raw parser directly, plus a full synthetic V9 container for
the end-to-end path through ``parse_gfont_bytes``.
"""
from __future__ import annotations

from tests.conftest import CODEPOINT
from tests.gfont_helpers import (
    COMMAND2_BLOB,
    COMMAND2_MAPPED,
    build_v9_with_entry_blob,
    parse_lenient,
    parse_raw_glyph_with_warnings,
)


def test_raw_glyph_command2_mapped_to_line() -> None:
    glyph, consumed, warnings, accepts_warnings = parse_raw_glyph_with_warnings(
        COMMAND2_BLOB, CODEPOINT,
    )
    assert consumed == len(COMMAND2_BLOB)
    assert glyph.codepoint == CODEPOINT
    assert glyph.commands == COMMAND2_MAPPED  # 2 (Bezier) -> 1 (line)
    if accepts_warnings:
        assert warnings, "command 2 must produce a warning"


def test_v9_container_command2_warns_and_maps() -> None:
    parsed, warnings = parse_lenient(build_v9_with_entry_blob(COMMAND2_BLOB))
    assert parsed.version == 9
    assert len(parsed.glyphs) == 1
    assert parsed.glyphs[0].codepoint == CODEPOINT
    assert parsed.glyphs[0].commands == COMMAND2_MAPPED
    assert warnings, "command 2 must produce a warning"
