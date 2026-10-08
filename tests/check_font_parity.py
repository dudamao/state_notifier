"""Verify main/font5x7.cpp matches host/vfd_display/font5x7.py.

The C table is a mechanical port of the Python one. The Python table only
contains uppercase glyphs, so this script expands it over the full 0x20..0x7E
range with the host TextRenderer semantics (uppercase fold, '?' fallback) and
compares every byte against the C table.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

ROW_PATTERN = re.compile(
    r"\{\s*((?:0x[0-9A-Fa-f]{2},\s*){4}0x[0-9A-Fa-f]{2})\s*\},"
)


def load_python_font() -> dict:
    path = PROJECT_DIR / "host" / "vfd_display" / "font5x7.py"
    spec = importlib.util.spec_from_file_location("host_font5x7", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FONT5X7


def load_c_font() -> list:
    path = PROJECT_DIR / "main" / "font5x7.cpp"
    rows = []
    inside_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if "kFont5x7" in line:
            inside_table = True
        if not inside_table:
            continue
        if line.strip().startswith("};"):
            break
        match = ROW_PATTERN.search(line)
        if match:
            rows.append([int(value.strip(), 16) for value in match.group(1).split(",")])
    return rows


def main() -> int:
    python_font = load_python_font()
    c_font = load_c_font()

    if len(c_font) != 95:
        print(f"FAIL: expected 95 glyph rows in font5x7.cpp, found {len(c_font)}")
        return 1

    failures = 0
    for code in range(0x20, 0x7F):
        expected = python_font.get(chr(code).upper(), python_font["?"])
        actual = tuple(c_font[code - 0x20])
        if actual != expected:
            print(f"FAIL: 0x{code:02X} {chr(code)!r}: c={actual} python={expected}")
            failures += 1

    if failures:
        return 1
    print("firmware font parity with host renderer: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
