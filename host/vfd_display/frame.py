"""40x7 frame primitives and exact fixed-width 5x7 text rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - reported when image rendering is requested
    Image = None
    ImageOps = None

from .font5x7 import FONT5X7


WIDTH = 40
HEIGHT = 7


def _validate_columns(columns: Iterable[int], expected: Optional[int] = None) -> Tuple[int, ...]:
    values = tuple(columns)
    if expected is not None and len(values) != expected:
        raise ValueError("expected {} columns, got {}".format(expected, len(values)))
    if any(not isinstance(value, int) or not 0 <= value <= 0x7F for value in values):
        raise ValueError("each column must be an integer in 0..127")
    return values


@dataclass(frozen=True)
class Bitmap7:
    columns: Tuple[int, ...]

    def __init__(self, columns: Iterable[int]) -> None:
        object.__setattr__(self, "columns", _validate_columns(columns))

    @property
    def width(self) -> int:
        return len(self.columns)

    def window(self, offset: int, width: int) -> "Bitmap7":
        if width < 0:
            raise ValueError("width cannot be negative")
        return Bitmap7(
            self.columns[index] if 0 <= index < self.width else 0
            for index in range(offset, offset + width)
        )

    def padded(self, left: int = 0, right: int = 0) -> "Bitmap7":
        return Bitmap7((0,) * left + self.columns + (0,) * right)


@dataclass(frozen=True)
class Frame40x7:
    columns: Tuple[int, ...]

    def __init__(self, columns: Iterable[int] = (0,) * WIDTH) -> None:
        object.__setattr__(self, "columns", _validate_columns(columns, WIDTH))

    @classmethod
    def blank(cls) -> "Frame40x7":
        return cls()

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[Any]]) -> "Frame40x7":
        if len(rows) != HEIGHT or any(len(row) != WIDTH for row in rows):
            raise ValueError("rows must be exactly 7x40")
        columns = []
        for x in range(WIDTH):
            value = 0
            for y in range(HEIGHT):
                if bool(rows[y][x]):
                    value |= 1 << y
            columns.append(value)
        return cls(columns)

    @classmethod
    def from_image(cls, image: Any, threshold: int = 128,
                   invert: bool = False) -> "Frame40x7":
        if Image is None:
            raise RuntimeError("Pillow is required for image rendering")
        if not 0 <= threshold <= 255:
            raise ValueError("threshold must be in 0..255")
        source = image if hasattr(image, "convert") else Image.open(image)
        source = source.convert("L")
        if invert:
            source = ImageOps.invert(source)
        source.thumbnail((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        canvas = Image.new("L", (WIDTH, HEIGHT), 0)
        x = (WIDTH - source.width) // 2
        y = (HEIGHT - source.height) // 2
        canvas.paste(source, (x, y))
        rows = []
        for row in range(HEIGHT):
            pixels = []
            for column in range(WIDTH):
                lit = canvas.getpixel((column, row)) >= threshold
                pixels.append(lit)
            rows.append(pixels)
        return cls.from_rows(rows)

    def translated(self, dx: int, dy: int) -> "Frame40x7":
        output = [0] * WIDTH
        for x, column in enumerate(self.columns):
            target = x + dx
            if not 0 <= target < WIDTH:
                continue
            output[target] = ((column << dy) & 0x7F if dy >= 0 else
                              column >> -dy)
        return Frame40x7(output)

    def crop(self, x: int, y: int, width: int, height: int) -> "Frame40x7":
        if width < 0 or height < 0:
            raise ValueError("crop dimensions cannot be negative")
        output = [0] * WIDTH
        for target, source in enumerate(range(x, x + min(width, WIDTH))):
            if 0 <= source < WIDTH and target < WIDTH:
                value = 0
                for target_y in range(min(height, HEIGHT)):
                    source_y = y + target_y
                    if (0 <= source_y < HEIGHT and
                            self.columns[source] & (1 << source_y)):
                        value |= 1 << target_y
                output[target] = value
        return Frame40x7(output)

    def overlay(self, other: "Frame40x7", x: int = 0, y: int = 0) -> "Frame40x7":
        shifted = other.translated(x, y)
        return Frame40x7(a | b for a, b in zip(self.columns, shifted.columns))

    def with_bitmap(self, bitmap: Bitmap7, x: int = 0) -> "Frame40x7":
        output = list(self.columns)
        for index, column in enumerate(bitmap.columns):
            target = x + index
            if 0 <= target < WIDTH:
                output[target] |= column
        return Frame40x7(output)


class TextRenderer:
    """Render uppercase ASCII using exact, pre-defined 5x7 glyphs.

    ``font_path``, ``font_size`` and ``threshold`` remain accepted for source
    compatibility with SN2 2.0 callers, but bitmap text never loads or scales a
    system font. Lowercase ASCII is converted to uppercase and every unsupported
    character is rendered as ``?``.
    """

    def __init__(self, font_path: Optional[str] = None, font_size: int = 9,
                 threshold: int = 96) -> None:
        del font_path, font_size, threshold
        self.font_path = "builtin:ascii-5x7"

    def render(self, text: str) -> Bitmap7:
        columns = []
        for character in (text or " ").upper():
            columns.extend(FONT5X7.get(character, FONT5X7["?"]))
        return Bitmap7(columns)
