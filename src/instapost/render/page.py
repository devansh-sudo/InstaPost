"""Composes the "book page": paper, sketch illustration, highlighted hook, body, reference."""

import random
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from instapost.brand import Brand
from instapost.render.text import Box, draw_centered, font, text_width, wrap, wrap_balanced

PAGE_W, PAGE_H = 940, 1400
MARGIN_X = 80


@dataclass
class Layout:
    """Size and spacing of a page. The default is the book page; card templates use the
    full 9:16 frame with the text inside Instagram's safe area."""
    w: int = PAGE_W
    h: int = PAGE_H
    margin: int = MARGIN_X
    header_y: int = 52
    top: int = 130          # content (drawing + text) starts here...
    bottom_pad: int = 100   # ...and ends this far above the bottom
    footer_pad: int = 58
    hook_size: int = 58
    body_size: int = 38
    ref_size: int = 29
    illus_max: int = 520
    spine: bool = True      # darker paper toward the left edge, as on a book's right page


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
class RevealLayer:
    """Text that fades in during the reel: an RGBA crop and where it sits on the page."""
    image: Image.Image
    x: int
    y: int
    order: int  # 0, 1, 2 ... in the order it appears


@dataclass
class RenderedPage:
    image: Image.Image  # RGBA, everything visible (cover, carousel, QA)
    hook_boxes: list[Box] = field(default_factory=list)
    text_boxes: list[Box] = field(default_factory=list)
    base: Image.Image | None = None  # RGBA, without the reveal layers (first reel frame)
    reveal: list[RevealLayer] = field(default_factory=list)


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def paper_texture(brand: Brand, w: int, h: int, seed: int, spine: bool = True) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = np.array(hex_rgb(brand.colors.paper), dtype=np.float32)
    noise = rng.normal(0, 4.0, (h, w, 1)).astype(np.float32)
    fibers = rng.normal(0, 1.0, (-(-h // 4), -(-w // 4), 1)).astype(np.float32)
    fibers = np.kron(fibers, np.ones((4, 4, 1), dtype=np.float32))[:h, :w] * 2.5
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # Gentle darkening toward the edges and the spine (left side).
    edge = 1.0 - 0.06 * (np.abs(xx / w - 0.5) * 2) ** 3 - 0.06 * (np.abs(yy / h - 0.5) * 2) ** 3
    gutter = 1.0 - 0.10 * np.exp(-xx / (w * 0.05)) if spine else 1.0
    img = (base + noise + fibers) * (edge * gutter)[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def _place_illustration(page: Image.Image, illus: Image.Image, box: tuple[int, int, int, int],
                        ink: tuple[int, int, int] = (0, 0, 0)):
    """Print a line drawing onto the paper in the ink colour: its white background disappears,
    so it works on cream paper (dark lines) and on dark paper (light lines)."""
    x0, y0, x1, y1 = box
    sketch = ImageOps.autocontrast(illus.convert("L"), cutoff=1)
    sketch = ImageOps.contain(sketch, (x1 - x0, y1 - y0))
    sx = x0 + (x1 - x0 - sketch.width) // 2
    sy = y0 + (y1 - y0 - sketch.height) // 2
    # Soft-fade the sketch edges so no rectangular border shows.
    fade = Image.new("L", sketch.size, 0)
    ImageDraw.Draw(fade).rectangle([12, 12, sketch.width - 12, sketch.height - 12], fill=255)
    fade = fade.filter(ImageFilter.GaussianBlur(10))
    strength = ImageChops.multiply(ImageOps.invert(sketch), fade)  # 255 = full ink
    region = page.crop((sx, sy, sx + sketch.width, sy + sketch.height))
    inked = Image.composite(Image.new("RGB", sketch.size, ink), region, strength)
    page.paste(inked, (sx, sy))


def _highlight(layer: Image.Image, box: Box, brand: Brand, rng: random.Random):
    """Marker-style band behind one hook line: even padding, soft rounded ends."""
    d = ImageDraw.Draw(layer)
    r, g, b = hex_rgb(brand.colors.highlight)
    alpha = int(255 * brand.colors.highlight_alpha)
    pad_x, pad_top, pad_bottom = 22, 4, 12
    x0, x1 = box.x0 - pad_x, box.x1 + pad_x
    y0, y1 = box.y0 - pad_top, box.y1 + pad_bottom
    tilt = rng.uniform(-1.5, 1.5)  # a hand-drawn marker is never perfectly level
    d.rounded_rectangle([x0, y0 + tilt, x1, y1 - tilt], radius=(y1 - y0) * 0.28,
                        fill=(r, g, b, alpha))


def _layer_from(draw_fn, size: tuple[int, int], order: int) -> RevealLayer | None:
    """Draw onto a transparent page-sized layer, keep only the painted part."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    bbox = layer.getbbox()
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - 4), max(0, y0 - 4)
    x1, y1 = min(size[0], x1 + 4), min(size[1], y1 + 4)
    return RevealLayer(layer.crop((x0, y0, x1, y1)), x0, y0, order)


def render_page(brand: Brand, c: PageContent, seed: int = 0,
                layout: Layout | None = None,
                paper: Image.Image | None = None) -> RenderedPage:
    """`paper` replaces the paper texture (a background from render.backdrops)."""
    L = layout or Layout()
    rng = random.Random(seed)
    page = (paper.convert("RGB").copy() if paper is not None
            else paper_texture(brand, L.w, L.h, seed, L.spine).convert("RGB"))
    ink = hex_rgb(brand.colors.ink)
    ink_soft = hex_rgb(brand.colors.ink_soft)
    cx = L.w / 2
    max_w = L.w - 2 * L.margin

    # Header and page number.
    d = ImageDraw.Draw(page)
    header_f = font(brand.font_path("header"), 34)
    draw_centered(d, [c.header], header_f, cx, L.header_y, ink_soft)
    num_f = font(brand.font_path("footer"), 30)
    num = str(c.page_number)
    d.text((L.w - L.margin + 20 - text_width(num_f, num), L.header_y - 2), num, font=num_f,
           fill=ink_soft)

    # Fit the text block; the drawing takes what is left above it, so there is no empty gap.
    top, bottom = L.top, L.h - L.bottom_pad
    illus_min = 300 if c.illustration is not None else 0
    gap_illus, gap_body, gap_div, gap_ref = 56, 28, 34, 26
    for scale in (1.0, 0.94, 0.88, 0.82, 0.76, 0.7, 0.64):
        hook_f = font(brand.font_path("hook"), int(L.hook_size * scale))
        body_f = font(brand.font_path("body"), int(L.body_size * scale))
        ref_f = font(brand.font_path("reference"), int(L.ref_size * scale))
        hook = [ln for line in c.hook_lines for ln in wrap_balanced(hook_f, line, max_w - 40)]
        body = wrap_balanced(body_f, c.body, max_w - 20)
        ref_lines = []
        if c.reference_label:
            ref_lines = [f"संदर्भ: {c.reference_label}"]
            if c.reference_text:
                # One shloka half per line: split after the danda.
                halves = c.reference_text.replace("। ", "।\n")
                ref_lines += wrap(ref_f, halves, max_w)
        divider_h = 44 if ref_lines else 0
        text_h = (len(hook) * hook_f.size * 1.45 + gap_body + len(body) * body_f.size * 1.42
                  + (gap_div + divider_h + gap_ref + len(ref_lines) * ref_f.size * 1.42
                     if ref_lines else 0))
        if text_h + illus_min + (gap_illus if illus_min else 0) <= bottom - top:
            break
    else:
        raise ValueError("text does not fit on the page even at the smallest size")

    if c.illustration is not None:
        # The drawing gets the free space (up to 520 px), and the group is centred.
        illus_h = min(L.illus_max, bottom - top - text_h - gap_illus)
        group_h = illus_h + gap_illus + text_h
        y_illus = top + (bottom - top - group_h) / 2
        _place_illustration(page, c.illustration,
                            (L.margin, int(y_illus), L.w - L.margin, int(y_illus + illus_h)),
                            ink)
        y = y_illus + illus_h + gap_illus
    else:
        y = top + (bottom - top - text_h) / 2

    # Hook: highlight underneath, then the text. Visible from the first frame.
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
    hl = hl.filter(ImageFilter.GaussianBlur(0.8))
    page = Image.alpha_composite(page.convert("RGBA"), hl)
    d = ImageDraw.Draw(page)
    y, _ = draw_centered(d, hook, hook_f, cx, y, ink, line_gap=1.45)
    y += gap_body

    # Everything below the hook goes on reveal layers (animated in the reel).
    reveal: list[RevealLayer] = []
    body_boxes, ref_boxes = [], []
    order = 0
    for line in body:
        line_y = y

        def draw_body(dd, line=line, line_y=line_y):
            _, boxes = draw_centered(dd, [line], body_f, cx, line_y, ink)
            body_boxes.extend(boxes)

        if layer := _layer_from(draw_body, page.size, order):
            reveal.append(layer)
            order += 1
        y += body_f.size * 1.42

    if ref_lines:
        y += gap_div
        div_y = y

        def draw_divider(dd):
            sym_f = font(brand.font_path("header"), 40)
            sym_w = text_width(sym_f, brand.divider_symbol)
            dd.text((cx - sym_w / 2, div_y - 6), brand.divider_symbol, font=sym_f, fill=ink,
                    language="hi")
            mid = div_y + sym_f.size * 0.55
            dd.line([(cx - sym_w / 2 - 150, mid), (cx - sym_w / 2 - 22, mid)], fill=ink_soft,
                    width=2)
            dd.line([(cx + sym_w / 2 + 22, mid), (cx + sym_w / 2 + 150, mid)], fill=ink_soft,
                    width=2)

        if layer := _layer_from(draw_divider, page.size, order):
            reveal.append(layer)
            order += 1
        y += divider_h + gap_ref
        for line in ref_lines:
            line_y = y

            def draw_ref(dd, line=line, line_y=line_y):
                _, boxes = draw_centered(dd, [line], ref_f, cx, line_y, ink_soft)
                ref_boxes.extend(boxes)

            if layer := _layer_from(draw_ref, page.size, order):
                reveal.append(layer)
                order += 1
            y += ref_f.size * 1.42

    foot_f = font(brand.font_path("footer"), 22)
    if c.footer:
        d.text((L.w - L.margin - text_width(foot_f, c.footer), L.h - L.footer_pad), c.footer,
               font=foot_f, fill=ink_soft)

    base = page.copy()
    full = page.copy()
    for layer in reveal:
        full.alpha_composite(layer.image, (layer.x, layer.y))
    return RenderedPage(image=full, hook_boxes=hook_boxes,
                        text_boxes=hook_boxes + body_boxes + ref_boxes, base=base,
                        reveal=reveal)
