# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.1] — 2026-11-03

### Improved

- Refreshed the dark palette, section contrast and keyboard focus states.
- The selected filename is shown in the toolbar without exposing its full local path.
- Reworked the Russian README and added a concise format reference.
- Updated the repository screenshot with a synthetic demo font.

## [1.1.0] — 2026-11-01

Reader fix release, validated against a corpus of 30 real-world .gfont files
(the AyuGram Desktop set).

### Fixed

- A `glyph_count` stored in the metadata that does not match the number of ZIP
  entries is no longer a validation error: the reader emits a warning and uses
  the actual ZIP entry count. Root cause: the 奎享字体 (Kuixiang Font) editor
  appends glyphs to a container without updating the stored counter, and subset
  exports inherit the donor file's metadata — the glyph data itself is intact,
  only the counter is stale.
- Added reader support for format version **V8** (produced by 奎享字体-2026 /
  Kuixiang Font 2026): multi-contour glyph records
  `[u16 utf8_len][utf-8 codepoint][u32 coord_count][f32 × coord_count][i32
  cmd_count][cmds 0/1]`, with contours 2..N appended without the codepoint
  header. The container layout and the TEA metadata cipher (key
  `1989082619920828`, 16 rounds, CBC-like chaining) are shared with
  V5/V6/V7/V9.

### Hardening

- ZIP entries with general-purpose flag bits `0x2`/`0x4` (deflate options) are
  accepted instead of rejected.
- Trailing bytes after the decrypted metadata block produce a warning instead
  of an error; the metadata is parsed anyway.
- An unknown pen/brush type `q` outside `0..4` (e.g. the 玉秋繁体 plot2 file)
  is kept as is and reported as a warning instead of failing the file.
- Unexpected bytes between the preview block and the ZIP payload are tolerated
  on every version (e.g. the 张生的字 file): the reader scans for the ZIP
  local-header magic and reports the gap as a warning.
- In V8 glyph records a contour that starts pen-down is normalized to pen-up:
  once contours are merged into one flat point/command list the boundary
  would otherwise weld two outlines together (reported as a warning).
- Command byte `2` (Bezier) in glyph records is approximated by a
  straight-line segment and reported as a warning instead of failing
  validation.

### Added

- Regression tests for the reader fixes and V8 support: real-world AyuGram
  fixtures (a V8 font, a stale-`glyph_count` V5 font and a valid V9 control),
  a synthetic V8 multi-contour builder, command-2 mapping, stale-counter
  roundtrips and ZIP flag-bit patching. Total: 197 tests.

## [1.0.1] — 2026-10-31

Visual-bug fix release (found via an offscreen vision pass: alpha-0 window +
`PrintWindow` captures of every screen with real fonts, then pixel/geometry
analysis of the shots).

### Fixed

- Main window did not stretch its content to the full window width: the root
  grid column had no weight, leaving a ~184 px dead strip on the right at the
  default 1240x800 size and clipping the toolbar off-screen at the minimum
  980x640 size. Added `root.columnconfigure(0, weight=1)`.
- The left info column (Шрифт/Метаданные/Поиск/Зум) is ~786 px tall but the
  main area is only ~538 px, so «Поиск глифа» and «Зум превью» rendered
  *underneath* the log panel (the zoom slider was invisible at the default
  window size and cut off at the minimum size). The column now lives in a
  scrollable canvas with its own scrollbar and mouse-wheel support.
- The toolbar path entry grew with the loaded file path and pushed the action
  buttons off-screen at small widths; it is now a fixed-request, stretchable
  field (width=14, weight column).
- Tile captions (`c U+XXXX`) were wider than the tile pitch at zoom levels
  below ~56 px and collided with neighbouring captions; captions now degrade
  (`U+XXXX`, then bare hex) to always fit `cell + gap - 2`.
- The native window title bar stayed light (`#f3f3f3`) on the dark UI; it is
  now painted dark via DWM immersive dark mode (attribute 20/19, applied
  after the window is mapped).
- Minimum window size raised to 980x700 so the right detail panel no longer
  spills under the log at the smallest legal size.

### Added

- `tests/test_gui_layout.py`: 11 regression tests for the layout fixes
  (column weight, full-width log, scrollable left column, toolbar fit at
  minsize, tile-caption pitch invariants), run against an invisible
  (`-alpha 0`) window. Total: 182 tests.

## [1.0.0] — 2026-10-31

First open-source release.

### Added

- Core library (`gfont_converter.py`):
  - Parser for KDraw/Kenjoy .gfont containers: V3, V5, V6, V7, V9 and the
    bare-ZIP variant (reported as V0).
  - TEA-compatible metadata cipher (16 rounds, CBC-like chaining) with the
    container's 128-bit `MAGIC_KEY`.
  - Java modified UTF-8 reader/writer (CESU-8 style, surrogate pairs, NUL as
    `C0 80`).
  - Writer for V3/V5 targets; source files are always treated as read-only.
  - Hardened validation: container size caps (32 MiB on disk / 64 MiB unpacked),
    ZIP-directory checks, decompression-bomb and entry-count guards, NaN/Inf
    coordinate rejection.
  - Human-readable (Russian) error descriptions via `describe_error`.
- Renderer (`glyph_render.py`): stroke extraction, bounding boxes, standalone
  SVG export and an anti-aliased pure-Python PNG rasterizer.
- GUI (`gfont_gui.py`): dark tkinter interface with glyph grid, zoom, codepoint
  search, drag & drop, per-glyph SVG/PNG export, bulk SVG export, V3↔V5
  conversion and folder batch mode.
- Headless modes: `--smoke` self-check (parse + render + roundtrip +
  corruption-detection per file) and `--convert`.
- CLI: single-file and directory conversion, `--target V3|V5`.
- Test suite: 171 pytest tests covering all input versions, roundtrip identity,
  TEA vectors, negative/corruption cases, rendering and the CLI.
- Packaging: PEP 621 `pyproject.toml` (stdlib-only runtime deps), pinned dev
  requirements, PyInstaller spec for a windowed onefile Windows build.
- CI: GitHub Actions — ruff + pytest on windows-latest (Python 3.12), PyInstaller
  artifact job on `v*` tags.

[1.1.0]: https://github.com/claudessh/gfont-converter/releases/tag/v1.1.0
[1.0.1]: https://github.com/claudessh/gfont-converter/releases/tag/v1.0.1
[1.0.0]: https://github.com/claudessh/gfont-converter/releases/tag/v1.0.0
