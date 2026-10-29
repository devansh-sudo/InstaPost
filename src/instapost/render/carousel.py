"""Carousel slides (1080x1350) and story frames (1080x1920) in the same paper style, on any
of the looks in render.templates (paper, mandala, night, dusk; stories also "card")."""

import random

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from instapost.brand import Brand
from instapost.models import Draft
from instapost.render.backdrops import backdrop, look_brand
from instapost.render.page import _highlight, _place_illustration, hex_rgb, paper_texture
from instapost.render.text import Box, draw_centered, font, text_width, wrap_balanced

SLIDE_W, SLIDE_H = 1080, 1350
STORY_W, STORY_H = 1080, 1920


def _fit(brand: Brand, role: str, text: str, max_w: int, max_h: int, start: int,
         gap: float = 1.4) -> tuple:
    for size in range(start, 22, -2):
        f = font(brand.font_path(role), size)
        lines = wrap_balanced(f, text, max_w)
        if len(lines) * size * gap <= max_h:
            return f, lines
    raise ValueError(f"text does not fit: {text[:40]}...")


def _base(brand: Brand, w: int, h: int, seed: int, index: int, total: int,
          look: str = "paper") -> Image.Image:
    img = backdrop(brand, look, w, h, seed + index, centre=(0.5, 0.5))
    d = ImageDraw.Draw(img)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    head = font(brand.font_path("header"), 34)
    draw_centered(d, [brand.header_text], head, w / 2, 60, ink_soft)
    foot = font(brand.font_path("footer"), 26)
    d.text((70, h - 80), brand.printed_handle, font=foot, fill=ink_soft)
    # Progress dots (where you are in the carousel), right-aligned.
    r, gap = 7, 26
    x = w - 70 - (total - 1) * gap
    for i in range(total):
        cx, cy = x + i * gap, h - 66
        if i == index:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hex_rgb(brand.colors.ink))
        else:
            d.ellipse([cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2], outline=ink_soft, width=2)
    return img


def _highlighted(img: Image.Image, brand: Brand, lines: list[str], f, cx: float, y: float,
                 seed: int, line_gap: float = 1.45) -> tuple[Image.Image, float]:
    """Lines with the same marker highlight as the reel; returns the image and the y after."""
    probe = ImageDraw.Draw(img)
    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rng = random.Random(seed)
    yy = y
    for line in lines:
        x = cx - text_width(f, line) / 2
        _highlight(hl, Box(*probe.textbbox((x, yy), line, font=f, language="hi")), brand, rng)
        yy += f.size * line_gap
    img = Image.alpha_composite(img.convert("RGBA"), hl.filter(ImageFilter.GaussianBlur(0.8)))
    end, _ = draw_centered(ImageDraw.Draw(img), lines, f, cx, y, hex_rgb(brand.colors.ink),
                           line_gap=line_gap)
    return img.convert("RGB"), end


def _accent(d: ImageDraw.ImageDraw, brand: Brand, cx: float, y: float) -> None:
    """Short highlight-coloured rule above a slide title."""
    d.rounded_rectangle([cx - 46, y, cx + 46, y + 8], radius=4,
                        fill=hex_rgb(brand.colors.highlight))


def render_slides(brand: Brand, draft: Draft, illustration: Image.Image | None,
                  reference: tuple[str, str] | None, seed: int = 0,
                  look: str = "paper") -> list[Image.Image]:
    """Cover with illustration + hook, then one slide per draft slide, verse slide if cited."""
    brand = look_brand(brand, look)
    ink = hex_rgb(brand.colors.ink)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    slides_spec = list(draft.slides)
    total = len(slides_spec) + 1 + (1 if reference else 0)
    out = []

    cover = _base(brand, SLIDE_W, SLIDE_H, seed, 0, total, look)
    hook = "\n".join(draft.hook_lines)
    f, lines = _fit(brand, "hook", hook, 880, 380, 68)
    hook_h = len(lines) * f.size * 1.45
    top, bottom = 130, SLIDE_H - 170
    if illustration is not None:
        ill_h = min(600, bottom - top - hook_h - 60)
        sketch = ImageOps.contain(ImageOps.autocontrast(illustration.convert("L"), cutoff=1),
                                  (860, int(ill_h)))
        group = sketch.height + 60 + hook_h
        y_ill = top + (bottom - top - group) / 2
        x0 = (SLIDE_W - sketch.width) // 2
        # Printed in the ink colour, so it shows on dark looks too.
        _place_illustration(cover, illustration, (x0, int(y_ill), x0 + sketch.width,
                                                  int(y_ill) + sketch.height), ink)
        y = y_ill + sketch.height + 60
    else:
        y = top + (bottom - top - hook_h) / 2
    cover, _ = _highlighted(cover, brand, lines, f, SLIDE_W / 2, y, seed)
    # Swipe cue: carousels are ranked on swipes, so ask for one.
    d = ImageDraw.Draw(cover)
    cue_f = font(brand.font_path("body"), 32)
    cue = "आगे पढ़ें"
    ax1 = SLIDE_W - 70  # arrow drawn as lines: the Devanagari fonts have no arrow glyph
    ax0, ay = ax1 - 38, SLIDE_H - 150 + cue_f.size * 0.62
    d.text((ax0 - 16 - text_width(cue_f, cue), SLIDE_H - 150), cue, font=cue_f,
           fill=ink_soft, language="hi")
    d.line([(ax0, ay), (ax1, ay)], fill=ink_soft, width=3)
    d.line([(ax1 - 12, ay - 10), (ax1, ay), (ax1 - 12, ay + 10)], fill=ink_soft, width=3)
    out.append(cover)

    if reference:
        label, verse = reference
        s = _base(brand, SLIDE_W, SLIDE_H, seed, len(out), total, look)
        d = ImageDraw.Draw(s)
        vf, vlines = _fit(brand, "hook", verse.replace("। ", "।\n"), 900, 620, 58, gap=1.6)
        block = len(vlines) * vf.size * 1.6 + 30 + 40
        y0 = (SLIDE_H - block) / 2
        _accent(d, brand, SLIDE_W / 2, y0 - 50)
        y, _ = draw_centered(d, vlines, vf, SLIDE_W / 2, y0, ink, line_gap=1.6)
        lf = font(brand.font_path("reference"), 34)
        draw_centered(d, [f"— {label}"], lf, SLIDE_W / 2, y + 30, ink_soft)
        out.append(s)

    for i, spec in enumerate(slides_spec):
        last = i == len(slides_spec) - 1
        s = _base(brand, SLIDE_W, SLIDE_H, seed, len(out), total, look)
        d = ImageDraw.Draw(s)
        tf, tlines = _fit(brand, "hook", spec.title, 880, 240, 64)
        bf, blines = _fit(brand, "body", spec.text, 860, 600, 48)
        block = len(tlines) * tf.size * 1.4 + 40 + len(blines) * bf.size * 1.5
        y = (SLIDE_H - block) / 2
        _accent(d, brand, SLIDE_W / 2, y - 44)
        y, _ = draw_centered(d, tlines, tf, SLIDE_W / 2, y, ink)
        draw_centered(d, blines, bf, SLIDE_W / 2, y + 40, ink, line_gap=1.5)
        if last:  # saves and sends are the strongest signals; ask for them softly
            cta_f = font(brand.font_path("body"), 32)
            draw_centered(d, ["सेव करें  ·  किसी अपने को भेजें"], cta_f, SLIDE_W / 2,
                          SLIDE_H - 165, ink_soft)
        out.append(s)
    return out


def render_story(brand: Brand, scene: Image.Image | None, line: str, seed: int = 0,
                 look: str = "card") -> Image.Image:
    """Story frame with the highlighted line; the lower part is left free for a poll sticker
    (stickers cannot be added through the API). "card": a paper card on the blurred scene
    photo; the other looks put the line straight onto a full-screen background."""
    if look != "card" or scene is None:
        return _story_full(look_brand(brand, look), line, seed,
                           "paper" if look == "card" else look)
    bg = ImageOps.fit(scene.convert("RGB"), (STORY_W, STORY_H)).filter(ImageFilter.GaussianBlur(9))
    card_w, card_h = 940, 780
    card = paper_texture(brand, card_w, card_h, seed).convert("RGB")
    d = ImageDraw.Draw(card)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    head = font(brand.font_path("header"), 32)
    draw_centered(d, [brand.header_text], head, card_w / 2, 46, ink_soft)
    f, lines = _fit(brand, "hook", line, card_w - 140, card_h - 260, 70)
    block = len(lines) * f.size * 1.45
    card, _ = _highlighted(card, brand, lines, f, card_w / 2, (card_h - block) / 2 + 10, seed)
    d = ImageDraw.Draw(card)
    foot = font(brand.font_path("footer"), 26)
    draw_centered(d, [brand.printed_handle], foot, card_w / 2, card_h - 70, ink_soft)
    x, y = (STORY_W - card_w) // 2, STORY_CARD_Y
    shadow = Image.new("L", bg.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle([x, y + 24, x + card_w, y + card_h + 24],
                                             radius=18, fill=160)
    bg.paste((15, 10, 5), (0, 0), shadow.filter(ImageFilter.GaussianBlur(32)))
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, card_w, card_h], radius=18, fill=255)
    bg.paste(card, (x, y), mask)
    return bg


STORY_CARD_Y = 330           # the text sits between here...
STORY_TEXT_BOTTOM = 1110     # ...and here; below is for the poll sticker


def _story_full(brand: Brand, line: str, seed: int, look: str) -> Image.Image:
    img = backdrop(brand, look, STORY_W, STORY_H, seed, centre=(0.5, 0.37))
    d = ImageDraw.Draw(img)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    head = font(brand.font_path("header"), 36)
    draw_centered(d, [brand.header_text], head, STORY_W / 2, STORY_CARD_Y, ink_soft)
    top, bottom = STORY_CARD_Y + 90, STORY_TEXT_BOTTOM - 70
    f, lines = _fit(brand, "hook", line, STORY_W - 220, bottom - top, 76)
    block = len(lines) * f.size * 1.45
    img, end = _highlighted(img, brand, lines, f, STORY_W / 2, top + (bottom - top - block) / 2,
                            seed)
    d = ImageDraw.Draw(img)
    foot = font(brand.font_path("footer"), 28)
    draw_centered(d, [brand.printed_handle], foot, STORY_W / 2, STORY_TEXT_BOTTOM - 20, ink_soft)
    return img
