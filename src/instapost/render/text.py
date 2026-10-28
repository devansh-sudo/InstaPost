"""Devanagari-safe text rendering.

Pillow's basic layout engine breaks conjuncts and matras (e.g. "त्यक्त्वा"), so every font is
loaded with the RAQM engine (HarfBuzz shaping). `require_raqm()` fails loudly if it is missing.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont, features


class ShapingUnavailable(RuntimeError):
    pass


def require_raqm() -> None:
    if not features.check("raqm"):
        raise ShapingUnavailable(
            "Pillow was built without libraqm; Hindi text would render incorrectly. "
            "Install libraqm (apt install libraqm0) and reinstall Pillow."
        )


@lru_cache(maxsize=64)
def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    require_raqm()
    return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)


def text_width(f: ImageFont.FreeTypeFont, text: str) -> float:
    return f.getlength(text, language="hi")


# Emoji and pictographs: our Devanagari fonts have no glyphs for them (they would render as
# boxes), so they are kept in captions but removed from text drawn on images.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def strip_emoji(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text)).strip()


def wrap(f: ImageFont.FreeTypeFont, text: str, max_width: float) -> list[str]:
    """Greedy word wrap; explicit newlines are kept, emoji are dropped."""
    lines: list[str] = []
    for para in strip_emoji(text).split("\n"):
        current = ""
        for word in para.split():
            trial = f"{current} {word}".strip()
            if current and text_width(f, trial) > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        lines.append(current)
    return [line for line in lines if line]


def wrap_balanced(f: ImageFont.FreeTypeFont, text: str, max_width: float) -> list[str]:
    """Same number of lines as `wrap`, but evenly filled (no single word left on a line)."""
    lines = wrap(f, text, max_width)
    if len(lines) < 2:
        return lines
    lo, hi = max_width * 0.4, max_width
    best = lines
    for _ in range(14):  # binary search the narrowest width that keeps the line count
        mid = (lo + hi) / 2
        trial = wrap(f, text, mid)
        if len(trial) <= len(lines):
            best, hi = trial, mid
        else:
            lo = mid
    return best


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float


def draw_centered(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    f: ImageFont.FreeTypeFont,
    center_x: float,
    top: float,
    fill,
    line_gap: float = 1.35,
) -> tuple[float, list[Box]]:
    """Draw lines centered on `center_x`; return the y after the block and each line's box."""
    boxes = []
    step = f.size * line_gap
    y = top
    for line in lines:
        w = text_width(f, line)
        x = center_x - w / 2
        draw.text((x, y), line, font=f, fill=fill, language="hi")
        bx = draw.textbbox((x, y), line, font=f, language="hi")
        boxes.append(Box(*bx))
        y += step
    return y, boxes
