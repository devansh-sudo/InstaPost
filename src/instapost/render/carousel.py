"""Carousel slides (1080x1350) and story frames (1080x1920) in the same paper style."""

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from instapost.brand import Brand
from instapost.models import Draft
from instapost.render.page import hex_rgb, paper_texture
from instapost.render.text import draw_centered, font, text_width, wrap

SLIDE_W, SLIDE_H = 1080, 1350
STORY_W, STORY_H = 1080, 1920


def _fit(brand: Brand, role: str, text: str, max_w: int, max_h: int, start: int,
         gap: float = 1.4) -> tuple:
    for size in range(start, 22, -2):
        f = font(brand.font_path(role), size)
        lines = wrap(f, text, max_w)
        if len(lines) * size * gap <= max_h:
            return f, lines
    raise ValueError(f"text does not fit: {text[:40]}...")


def _base(brand: Brand, w: int, h: int, seed: int, index: int, total: int) -> Image.Image:
    img = paper_texture(brand, w, h, seed + index).convert("RGB")
    d = ImageDraw.Draw(img)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    head = font(brand.font_path("header"), 34)
    draw_centered(d, [brand.header_text], head, w / 2, 60, ink_soft)
    foot = font(brand.font_path("footer"), 26)
    d.text((70, h - 80), brand.handle, font=foot, fill=ink_soft)
    counter = f"{index + 1}/{total}"
    d.text((w - 70 - text_width(foot, counter), h - 80), counter, font=foot, fill=ink_soft)
    return img


def render_slides(brand: Brand, draft: Draft, illustration: Image.Image | None,
                  reference: tuple[str, str] | None, seed: int = 0) -> list[Image.Image]:
    """Cover with illustration + hook, then one slide per draft slide, verse slide if cited."""
    ink = hex_rgb(brand.colors.ink)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    slides_spec = list(draft.slides)
    total = len(slides_spec) + 1 + (1 if reference else 0)
    out = []

    cover = _base(brand, SLIDE_W, SLIDE_H, seed, 0, total)
    if illustration is not None:
        sketch = ImageOps.contain(ImageOps.autocontrast(illustration.convert("L"), cutoff=1),
                                  (840, 560))
        x0 = (SLIDE_W - sketch.width) // 2
        region = cover.crop((x0, 130, x0 + sketch.width, 130 + sketch.height))
        cover.paste(ImageChops.multiply(region, Image.merge("RGB", (sketch,) * 3)), (x0, 130))
    d = ImageDraw.Draw(cover)
    hook = "\n".join(draft.hook_lines)
    f, lines = _fit(brand, "hook", hook, 900, 360, 64)
    hl = Image.new("RGBA", cover.size, (0, 0, 0, 0))
    y = 760
    probe = ImageDraw.Draw(hl)
    r, g, b = hex_rgb(brand.colors.highlight)
    for line in lines:
        x = SLIDE_W / 2 - text_width(f, line) / 2
        bx = probe.textbbox((x, y), line, font=f, language="hi")
        probe.rectangle([bx[0] - 16, bx[1] - 6, bx[2] + 16, bx[3] + 10],
                        fill=(r, g, b, int(255 * brand.colors.highlight_alpha)))
        y += f.size * 1.45
    cover = Image.alpha_composite(cover.convert("RGBA"),
                                  hl.filter(ImageFilter.GaussianBlur(1.2))).convert("RGB")
    draw_centered(ImageDraw.Draw(cover), lines, f, SLIDE_W / 2, 760, ink, line_gap=1.45)
    out.append(cover)

    if reference:
        label, verse = reference
        s = _base(brand, SLIDE_W, SLIDE_H, seed, len(out), total)
        d = ImageDraw.Draw(s)
        vf, vlines = _fit(brand, "hook", verse.replace("। ", "।\n"), 900, 600, 54, gap=1.6)
        y0 = (SLIDE_H - len(vlines) * vf.size * 1.6) / 2 - 40
        y, _ = draw_centered(d, vlines, vf, SLIDE_W / 2, y0, ink, line_gap=1.6)
        lf = font(brand.font_path("reference"), 32)
        draw_centered(d, [f"— {label}"], lf, SLIDE_W / 2, y + 30, ink_soft)
        out.append(s)

    for spec in slides_spec:
        s = _base(brand, SLIDE_W, SLIDE_H, seed, len(out), total)
        d = ImageDraw.Draw(s)
        tf, tlines = _fit(brand, "hook", spec.title, 900, 220, 58)
        bf, blines = _fit(brand, "body", spec.text, 880, 640, 44)
        block = len(tlines) * tf.size * 1.4 + 50 + len(blines) * bf.size * 1.5
        y = (SLIDE_H - block) / 2
        y, _ = draw_centered(d, tlines, tf, SLIDE_W / 2, y, ink)
        sym = font(brand.font_path("header"), 34)
        draw_centered(d, [brand.divider_symbol], sym, SLIDE_W / 2, y, ink_soft)
        draw_centered(d, blines, bf, SLIDE_W / 2, y + 50, ink, line_gap=1.5)
        out.append(s)
    return out


def render_story(brand: Brand, scene: Image.Image, line: str, seed: int = 0) -> Image.Image:
    """Story frame: blurred scene, a paper card with the line, and empty space below it for
    a poll sticker (stickers cannot be added through the API, so they are added in the app)."""
    bg = ImageOps.fit(scene.convert("RGB"), (STORY_W, STORY_H)).filter(ImageFilter.GaussianBlur(8))
    card_w, card_h = 900, 620
    card = paper_texture(brand, card_w, card_h, seed).convert("RGBA")
    d = ImageDraw.Draw(card)
    f, lines = _fit(brand, "hook", line, card_w - 120, card_h - 200, 60)
    block = len(lines) * f.size * 1.45
    draw_centered(d, lines, f, card_w / 2, (card_h - block) / 2, hex_rgb(brand.colors.ink),
                  line_gap=1.45)
    foot = font(brand.font_path("footer"), 24)
    draw_centered(d, [brand.handle], foot, card_w / 2, card_h - 60, hex_rgb(brand.colors.ink_soft))
    x, y = (STORY_W - card_w) // 2, 420
    shadow = Image.new("L", bg.size, 0)
    ImageDraw.Draw(shadow).rectangle([x, y + 20, x + card_w, y + card_h + 20], fill=150)
    bg.paste((15, 10, 5), (0, 0), shadow.filter(ImageFilter.GaussianBlur(30)))
    bg.paste(card, (x, y), card)
    return bg
