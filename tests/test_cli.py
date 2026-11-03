"""CLI tests for gfont_converter.main and the headless gfont_gui helpers."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import gfont_converter as conv
import gfont_gui as gui
from tests.conftest import build_v9, make_glyph, make_metadata

# --- gfont_converter CLI -----------------------------------------------------

def test_parse_target_accepts_v3_v5() -> None:
    assert conv._parse_target("V3") == 3
    assert conv._parse_target("v5") == 5


@pytest.mark.parametrize("bad", ["V9", "V4", "", "5", "V55"])
def test_parse_target_rejects_others(bad: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        conv._parse_target(bad)


def test_cli_missing_input_exits_2(tmp_path, capsys) -> None:
    assert conv.main([str(tmp_path / "missing.gfont"), "-o", "out.gfont"]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_single_file_requires_output(tmp_gfont: Path, capsys) -> None:
    assert conv.main([str(tmp_gfont)]) == 1
    assert "output" in capsys.readouterr().err.lower()


def test_cli_output_must_differ_from_input(tmp_gfont: Path, capsys) -> None:
    assert conv.main([str(tmp_gfont), "-o", str(tmp_gfont)]) == 1
    assert "read-only" in capsys.readouterr().err


def test_cli_single_file_conversion(tmp_path, tmp_gfont: Path, capsys) -> None:
    output = tmp_path / "converted.gfont"
    assert conv.main([str(tmp_gfont), "-o", str(output)]) == 0
    assert output.is_file()
    assert conv.load_gfont(output).version == 5
    assert "wrote" in capsys.readouterr().out


def test_cli_single_file_target_v3(tmp_path, tmp_gfont: Path) -> None:
    output = tmp_path / "converted_v3.gfont"
    assert conv.main([str(tmp_gfont), "-o", str(output), "--target", "V3"]) == 0
    assert conv.load_gfont(output).version == 3


def test_cli_directory_batch(tmp_path) -> None:
    sources = tmp_path / "fonts"
    sources.mkdir()
    for index in range(3):
        font = conv.ParsedGfont(9, make_metadata(), (make_glyph(),), ())
        (sources / f"f{index}.gfont").write_bytes(conv.build_gfont(font, 5))
    assert conv.main([str(sources), "-o", str(tmp_path / "out")]) == 0
    written = sorted((tmp_path / "out").glob("*.gfont"))
    assert len(written) == 3
    assert all(path.stem.endswith("_v5") for path in written)


def test_cli_directory_default_output(tmp_path) -> None:
    sources = tmp_path / "fonts"
    sources.mkdir()
    font = conv.ParsedGfont(9, make_metadata(), (make_glyph(),), ())
    (sources / "only.gfont").write_bytes(conv.build_gfont(font, 5))
    assert conv.main([str(sources)]) == 0
    assert (sources / "converted_fonts" / "only_v5.gfont").is_file()


def test_cli_directory_without_gfonts(tmp_path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert conv.main([str(empty)]) == 1
    assert "no .gfont" in capsys.readouterr().err


def test_cli_invalid_font_human_readable_error(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.gfont"
    bad.write_bytes(b"\x00\x00\x00\x2a\x00")  # version 42: unsupported
    assert conv.main([str(bad), "-o", str(tmp_path / "out.gfont")]) == 1
    assert "error: Неподдерживаемая версия" in capsys.readouterr().err


# --- gfont_gui headless helpers ----------------------------------------------

def test_gui_parse_query() -> None:
    assert gui.GFontApp._parse_query("65") == 65
    assert gui.GFontApp._parse_query("0x41") == 0x41
    assert gui.GFontApp._parse_query("U+0410") == 0x410
    assert gui.GFontApp._parse_query("#41") == 0x41
    assert gui.GFontApp._parse_query("A") == ord("A")
    assert gui.GFontApp._parse_query("") is None
    assert gui.GFontApp._parse_query("   ") is None
    assert gui.GFontApp._parse_query("zzz") == -1


def test_gui_format_helpers() -> None:
    assert gui._fmt_int(12345) == "12 345"
    assert gui._fmt_size(512) == "512 Б"
    assert gui._fmt_size(2048) == "2.0 КиБ"
    assert gui._fmt_size(3 * 1024 * 1024) == "3.0 МиБ"
    assert gui._truncate("a" * 40) == "a" * 27 + "…"
    assert gui._truncate("short") == "short"
    assert gui._truncate("line\nbreak") == "line\\nbreak"


def test_gui_safe_char() -> None:
    assert gui._safe_char(0x41) == "A"
    assert gui._safe_char(0x410) == "А"
    assert gui._safe_char(0) == ""
    assert gui._safe_char(0xD800) == ""  # surrogate
    assert gui._safe_char(0x1F600) == "\U0001F600"


def test_gui_collect_smoke_files_dedup_and_filter(tmp_path: Path) -> None:
    (tmp_path / "a.gfont").write_bytes(build_v9())
    (tmp_path / "b.txt").write_text("not a font")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.gfont").write_bytes(build_v9())
    collected = gui._collect_smoke_files(
        [str(tmp_path / "a.gfont"), str(tmp_path), str(tmp_path), str(nested)],
    )
    names = [path.name for path in collected]
    assert names == ["a.gfont", "c.gfont"]  # sorted, deduped, .txt dropped


def test_gui_smoke_one_passes_on_valid_font(tmp_gfont: Path) -> None:
    ok, line = gui._smoke_one(tmp_gfont)
    assert ok, line
    assert line.startswith("OK ")


def test_gui_run_smoke_ok_and_report(tmp_path: Path) -> None:
    for index in range(2):
        font = conv.ParsedGfont(9, make_metadata(), (make_glyph(),), ())
        (tmp_path / f"s{index}.gfont").write_bytes(conv.build_gfont(font, 5))
    report = tmp_path / "report.txt"
    assert gui.run_smoke([str(tmp_path)], str(report)) == 0
    text = report.read_text(encoding="utf-8")
    assert "SMOKE OK: 2/2" in text


def test_gui_run_smoke_rejects_garbage(tmp_path: Path) -> None:
    (tmp_path / "bad.gfont").write_bytes(b"not a gfont at all")
    assert gui.run_smoke([str(tmp_path)], None) == 1


def test_gui_run_smoke_no_files(tmp_path: Path) -> None:
    assert gui.run_smoke([str(tmp_path)], None) == 1


def test_gui_run_convert(tmp_path: Path) -> None:
    source = tmp_path / "in.gfont"
    font = conv.ParsedGfont(9, make_metadata(), (make_glyph(),), ())
    source.write_bytes(conv.build_gfont(font, 5))
    output = tmp_path / "out.gfont"
    assert gui.run_convert(str(source), str(output), 3) == 0
    assert conv.load_gfont(output).version == 3
    assert gui.run_convert(str(tmp_path / "missing.gfont"), None, 3) == 2


def test_gui_arg_parser_defaults() -> None:
    parser = gui.build_arg_parser()
    args = parser.parse_args([])
    assert args.smoke is False
    assert args.convert is False
    assert args.target == "V5"
    assert args.paths == []
    args = parser.parse_args(["--smoke", "x.gfont", "--smoke-report", "r.txt"])
    assert args.smoke is True and args.paths == ["x.gfont"] and args.smoke_report == "r.txt"
