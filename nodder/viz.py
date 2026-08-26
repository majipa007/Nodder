"""Small plotting primitives for the dashboard.

Braille gives eight addressable dots per character cell, so a chart drawn in
it has four times the vertical resolution of one drawn in block characters,
and twice the horizontal. That is the difference between a chart you can read
and a row of blobs.

Everything here is pure: numbers in, list of strings out. No curses, no state.

The dot numbering in a braille cell is historical rather than sensible:

    1 4        0x01 0x08
    2 5   ->   0x02 0x10
    3 6        0x04 0x20
    7 8        0x40 0x80
"""

from __future__ import annotations

BRAILLE_BASE = 0x2800

#: DOTS[row][column] -> the bit for that dot. Rows run top to bottom.
DOTS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)

#: Partial blocks, for single-line sparklines. Index 0 is empty.
BLOCKS = " ▁▂▃▄▅▆▇█"


def resample(values: list[float], count: int) -> list[float]:
    """Squeeze or stretch a series to exactly `count` points.

    Down-sampling takes the maximum of each bucket rather than the mean: on an
    activity chart a brief burst is the interesting part, and averaging is
    exactly what would hide it.
    """
    if count <= 0:
        return []
    if not values:
        return [0.0] * count
    if len(values) == count:
        return [float(v) for v in values]

    if len(values) > count:
        out = []
        for index in range(count):
            start = index * len(values) // count
            end = max(start + 1, (index + 1) * len(values) // count)
            out.append(float(max(values[start:end])))
        return out

    # Stretching: hold each value for as long as it is the nearest one.
    return [float(values[index * len(values) // count]) for index in range(count)]


def area(values: list[float], width: int, height: int,
         peak: float | None = None) -> list[str]:
    """A filled area chart in braille, `height` lines of `width` characters.

    `peak` fixes the top of the scale; by default the largest value sets it.
    Returns blank lines rather than raising when there is nothing to draw, so
    a caller can render an empty chart without a special case.
    """
    if width <= 0 or height <= 0:
        return []

    columns = width * 2
    rows = height * 4
    points = resample(values, columns)
    ceiling = peak if peak is not None else max(points, default=0.0)

    grid = [[0] * width for _ in range(height)]
    if ceiling > 0:
        for x, value in enumerate(points):
            filled = round(max(0.0, value) / ceiling * rows)
            # A non-zero value should always show something, or a quiet
            # period and a dead one look identical.
            if value > 0:
                filled = max(1, filled)
            for step in range(min(filled, rows)):
                y = rows - 1 - step
                grid[y // 4][x // 2] |= DOTS[y % 4][x % 2]

    return ["".join(chr(BRAILLE_BASE + cell) for cell in row) for row in grid]


def sparkline(values: list[float], width: int,
              peak: float | None = None) -> str:
    """A one-line block sparkline, for a table cell."""
    if width <= 0:
        return ""
    points = resample(values, width)
    ceiling = peak if peak is not None else max(points, default=0.0)
    if ceiling <= 0:
        return "·" * width

    out = []
    for value in points:
        if value <= 0:
            out.append("·")
            continue
        step = round(value / ceiling * (len(BLOCKS) - 1))
        out.append(BLOCKS[max(1, min(step, len(BLOCKS) - 1))])
    return "".join(out)


def bar(value: float, ceiling: float, width: int) -> str:
    """A horizontal bar, eighth-of-a-cell accurate."""
    if width <= 0:
        return ""
    if ceiling <= 0 or value <= 0:
        return " " * width
    filled = min(1.0, value / ceiling) * width
    whole = int(filled)
    remainder = filled - whole
    out = "█" * whole
    if whole < width and remainder > 0:
        out += BLOCKS[max(1, round(remainder * 8))]
    return out.ljust(width)
