"""Composes the "book page": paper, sketch illustration, highlighted hook, body, reference."""

import random
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from instapost.brand import Brand
from instapost.render.text import Box, draw_centered, font, text_width, wrap

PAGE_W, PAGE_H = 940, 1400
MARGIN_X = 80


@dataclass
class PageContent:
    header: str
    page_number: int
    hook_lines: list[str]
    body: str
    reference_label: str | None
    reference_text: str | None
    footer: str
    illustration: Image.Image | None


@dataclass
class RenderedPage:
    image: Image.Image  # RGBA
    hook_boxes: list[Box] = field(default_factory=list)
    text_boxes: list[Box] = field(default_factory=list)


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def paper_texture(brand: Brand, w: int, h: int, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = np.array(hex_rgb(brand.colors.paper), dtype=np.float32)
    noise = rng.normal(0, 4.0, (h, w, 1)).astype(np.float32)
    fibers = rng.normal(0, 1.0, (-(-h // 4), -(-w // 4), 1)).astype(np.float32)
    fibers = np.kron(fibers, np.ones((4, 4, 1), dtype=np.float32))[:h, :w] * 2.5
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # Gentle darkening toward the edges and the spine (left side).
    edge = 1.0 - 0.06 * (np.abs(xx / w - 0.5) * 2) ** 3 - 0.06 * (np.abs(yy / h - 0.5) * 2) ** 3
    spine = 1.0 - 0.10 * np.exp(-xx / (w * 0.05))
    img = (base + noise + fibers) * (edge * spine)[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def _place_illustration(page: Image.Image, illus: Image.Image, box: tuple[int, int, int, int]):
    """Multiply-blend a sketch onto the paper so its white background disappears."""
    x0, y0, x1, y1 = box
    sketch = ImageOps.autocontrast(illus.convert("L"), cutoff=1)
    sketch = ImageOps.contain(sketch, (x1 - x0, y1 - y0))
    sx = x0 + (x1 - x0 - sketch.width) // 2
    sy = y0 + (y1 - y0 - sketch.height) // 2
    # Soft-fade the sketch edges so no rectangular border shows.
    fade = Image.new("L", sketch.size, 0)
    ImageDraw.Draw(fade).rectangle([12, 12, sketch.width - 12, sketch.height - 12], fill=255)
    fade = fade.filter(ImageFilter.GaussianBlur(10))
    white = Image.new("L", sketch.size, 255)
    sketch = Image.composite(sketch, white, fade)
    region = page.crop((sx, sy, sx + sketch.width, sy + sketch.height))
    toned = ImageChops.multiply(region, Image.merge("RGB", (sketch, sketch, sketch)))
    page.paste(toned, (sx, sy))


def _highlight(layer: Image.Image, box: Box, brand: Brand, rng: random.Random):
    """Marker-style highlight with slightly uneven edges behind one hook line."""
    d = ImageDraw.Draw(layer)
    r, g, b = hex_rgb(brand.colors.highlight)
    alpha = int(255 * brand.colors.highlight_alpha)
    pad_x, pad_top, pad_bottom = 18, 6, 10
    x0, x1 = box.x0 - pad_x, box.x1 + pad_x
    y0, y1 = box.y0 - pad_top, box.y1 + pad_bottom
    top = [(x, y0 + rng.uniform(-3, 3)) for x in np.linspace(x0, x1, 9)]
    bottom = [(x, y1 + rng.uniform(-3, 3)) for x in np.linspace(x1, x0, 9)]
    d.polygon(top + bottom, fill=(r, g, b, alpha))


def render_page(brand: Brand, c: PageContent, seed: int = 0) -> RenderedPage:
    rng = random.Random(seed)
    page = paper_texture(brand, PAGE_W, PAGE_H, seed).convert("RGB")
    ink = hex_rgb(brand.colors.ink)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    cx = PAGE_W / 2
    max_w = PAGE_W - 2 * MARGIN_X

    # Header and page number.
    d = ImageDraw.Draw(page)
    header_f = font(brand.font_path("header"), 34)
    draw_centered(d, [c.header], header_f, cx, 52, ink_soft)
    num_f = font(brand.font_path("footer"), 30)
    num = str(c.page_number)
    d.text((PAGE_W - MARGIN_X + 20 - text_width(num_f, num), 50), num, font=num_f, fill=ink_soft)

    if c.illustration is not None:
        _place_illustration(page, c.illustration, (MARGIN_X, 120, PAGE_W - MARGIN_X, 600))

    # Auto-fit the text block (hook, body, divider, reference) into the space below.
    top, bottom = 640, PAGE_H - 90
    for scale in (1.0, 0.92, 0.85, 0.78, 0.72, 0.66):
        hook_f = font(brand.font_path("hook"), int(56 * scale))
        body_f = font(brand.font_path("body"), int(38 * scale))
        ref_f = font(brand.font_path("reference"), int(30 * scale))
        hook = [ln for line in c.hook_lines for ln in wrap(hook_f, line, max_w - 40)]
        body = wrap(body_f, c.body, max_w)
        ref_lines = []
        if c.reference_label:
            ref_lines = [f"संदर्भ: {c.reference_label}"]
            if c.reference_text:
                # One shloka half per line: split after the danda.
                halves = c.reference_text.replace("। ", "।\n")
                ref_lines += wrap(ref_f, halves, max_w)
        height = (len(hook) * hook_f.size * 1.45 + 24 + len(body) * body_f.size * 1.4
                  + 90 + len(ref_lines) * ref_f.size * 1.4)
        if height <= bottom - top:
            break
    else:
        raise ValueError("text does not fit on the page even at the smallest size")

    y = top + (bottom - top - height) / 2
    # Measure hook boxes first so the highlight goes underneath the text.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    hook_boxes = []
    yy = y
    for line in hook:
        x = cx - text_width(hook_f, line) / 2
        hook_boxes.append(Box(*probe.textbbox((x, yy), line, font=hook_f, language="hi")))
        yy += hook_f.size * 1.45
    hl = Image.new("RGBA", page.size, (0, 0, 0, 0))
    for box in hook_boxes:
        _highlight(hl, box, brand, rng)
    hl = hl.filter(ImageFilter.GaussianBlur(1.2))
    page = Image.alpha_composite(page.convert("RGBA"), hl)
    d = ImageDraw.Draw(page)

    y, _ = draw_centered(d, hook, hook_f, cx, y, ink, line_gap=1.45)
    y += 24
    y, body_boxes = draw_centered(d, body, body_f, cx, y, ink, line_gap=1.4)

    # Divider: line - symbol - line.
    y += 30
    sym_f = font(brand.font_path("header"), 40)
    sym_w = text_width(sym_f, brand.divider_symbol)
    d.text((cx - sym_w / 2, y - 6), brand.divider_symbol, font=sym_f, fill=ink, language="hi")
    mid = y + sym_f.size * 0.55
    d.line([(cx - sym_w / 2 - 170, mid), (cx - sym_w / 2 - 20, mid)], fill=ink_soft, width=2)
    d.line([(cx + sym_w / 2 + 20, mid), (cx + sym_w / 2 + 170, mid)], fill=ink_soft, width=2)
    y += 60

    _, ref_boxes = draw_centered(d, ref_lines, ref_f, cx, y, ink_soft, line_gap=1.4)

    foot_f = font(brand.font_path("footer"), 22)
    d.text((PAGE_W - MARGIN_X - text_width(foot_f, c.footer), PAGE_H - 58), c.footer,
           font=foot_f, fill=ink_soft)

    return RenderedPage(image=page, hook_boxes=hook_boxes,
                        text_boxes=hook_boxes + body_boxes + ref_boxes)
