"""Draw the raster images the link previews need, from the same marks and palette.

iMessage, Slack and everything else that unfurls a link wants a bitmap. The app
itself only has SVGs, which Apple's link presentation will not render, so this
draws PNGs to match rather than leaving the preview to fall back to a blank card.

    uv run --with pillow python scripts/make_share_images.py

Pillow is not a dependency of the app. It is needed to draw these two files and
never at runtime, so it is borrowed for the length of one command instead of
being installed into the project.

The mark is drawn from the same geometry as favicon-light.svg rather than
converted from it, so there is one description of the shape per format and no
tracing step in between. If the palette in base.html moves, change PAPER and INK
here and run this again; a test pins the two together.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "mini_league" / "web" / "static"

# base.html, :root
PAPER = "#f4f1e8"
INK = "#14130f"
FAINT = "#5f5b4e"

# The wordmark's own face, the first one base.html asks for.
FONT_PATH = "/System/Library/Fonts/HelveticaNeue.ttc"
FONT_CONDENSED_BOLD = 4

# favicon-light.svg, in its 32-unit viewBox: three columns and three rows of
# 8-unit squares, the middle row outlined rather than filled.
CELLS = (3, 12, 21)
CELL = 8
STROKE = 1

CARD = (1200, 630)  # what an unfurled link expects: roughly 1.91:1
TOUCH_ICON = 180  # apple-touch-icon, the small icon Apple falls back to


def draw_mark(draw: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    """The mark, its 32-unit grid scaled to `size` and its top left at (x, y)."""
    unit = size / 32
    for row_index, row in enumerate(CELLS):
        for column in CELLS:
            left, top = x + column * unit, y + row * unit
            box = (left, top, left + CELL * unit, top + CELL * unit)
            if row_index == 1:  # the middle row is an outline
                draw.rectangle(box, outline=INK, width=max(1, round(STROKE * unit)))
            else:
                draw.rectangle(box, fill=INK)


def text_width(font: ImageFont.FreeTypeFont, text: str, tracking: float) -> float:
    return sum(font.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)


def draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    tracking: float,
) -> None:
    """Draw text a character at a time, so it can carry the CSS letter-spacing."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + tracking


def build_card() -> Image.Image:
    image = Image.new("RGB", CARD, PAPER)
    draw = ImageDraw.Draw(image)

    size = 150
    font = ImageFont.truetype(FONT_PATH, size, index=FONT_CONDENSED_BOLD)
    sub = ImageFont.truetype(FONT_PATH, 40, index=FONT_CONDENSED_BOLD)

    word = "MINI LEAGUE"
    tracking = 0.02 * size  # letter-spacing: .02em
    # The masthead's own proportions: a .9em mark, then a 7/16 em gap.
    mark_size = 0.9 * size
    gap = (7 / 16) * size * 0.6

    width = mark_size + gap + text_width(font, word, tracking)
    left = (CARD[0] - width) / 2
    baseline = CARD[1] / 2 + 10

    # Cap height, so the mark's squares line up with the capitals beside them.
    cap = draw.textbbox((0, 0), "M", font=font, anchor="ls")[1] * -1
    draw_mark(draw, left, baseline - cap - (mark_size * 3 / 32), mark_size)
    draw_tracked(draw, (left + mark_size + gap, baseline), word, font, INK, tracking)

    tagline = "ULTIMATE FRISBEE RATINGS, TEAMS AND STANDINGS"
    sub_tracking = 0.06 * 40
    sub_width = text_width(sub, tagline, sub_tracking)
    draw_tracked(
        draw,
        ((CARD[0] - sub_width) / 2, baseline + 110),
        tagline,
        sub,
        FAINT,
        sub_tracking,
    )

    draw.rectangle((0, CARD[1] - 12, CARD[0], CARD[1]), fill=INK)
    return image


def build_touch_icon() -> Image.Image:
    image = Image.new("RGB", (TOUCH_ICON, TOUCH_ICON), PAPER)
    draw_mark(ImageDraw.Draw(image), 0, 0, TOUCH_ICON)
    return image


def main() -> int:
    if not Path(FONT_PATH).exists():
        raise SystemExit(f"{FONT_PATH} is not here; this script needs macOS system fonts")

    for name, image in (
        ("og-image.png", build_card()),
        ("apple-touch-icon.png", build_touch_icon()),
    ):
        path = STATIC / name
        image.save(path, "PNG", optimize=True)
        print(f"{path.relative_to(ROOT)}  {image.size[0]}x{image.size[1]}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
