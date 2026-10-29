"""Backgrounds drawn in code for cards, slides and stories: free, never the same twice (each
post's seed moves the light and the pattern), and in the page's own calm style.

- paper:   the cream page;
- mandala: the cream page with a faint hand-drawn mandala behind the text;
- night:   dark paper with a few warm, out-of-focus diya lights;
- dusk:    a sunset sky (indigo to saffron) with a soft sun glow and floating lights.

Dark backgrounds take light ink (`look_brand`), so text stays readable on every one.
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from instapost.brand import Brand
from instapost.render.page import hex_rgb, paper_texture

DARK = {"night", "dusk"}


def night_brand(brand: Brand) -> Brand:
    """The brand with dark paper and light ink."""
    colors = brand.colors.model_copy(update={
        "paper": "#1C1714", "paper_edge": "#120E0C", "ink": "#F2E7D3", "ink_soft": "#C2B29A",
        "highlight": "#9A5B1E", "highlight_alpha": 0.62, "glow": "#FF9A3C"})
    return brand.model_copy(update={"colors": colors})


def dusk_brand(brand: Brand) -> Brand:
    colors = brand.colors.model_copy(update={
        "paper": "#2A2140", "paper_edge": "#1A1530", "ink": "#FFF4E4", "ink_soft": "#EBD3BE",
        "highlight": "#B4532A", "highlight_alpha": 0.72, "glow": "#FF9A3C"})
    return brand.model_copy(update={"colors": colors})


def look_brand(brand: Brand, look: str) -> Brand:
    return {"night": night_brand, "dusk": dusk_brand}.get(look, lambda b: b)(brand)


def _grain(arr: np.ndarray, rng, amount: float = 3.0) -> np.ndarray:
    return arr + rng.normal(0, amount, arr.shape[:2] + (1,)).astype(np.float32)


def _to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _bokeh(w: int, h: int, rng, count: int, colour, y_range=(0.0, 1.0),
           radius=(0.02, 0.06), strength=(0.25, 0.6)) -> np.ndarray:
    """Soft round lights, as from lamps far out of focus: an additive (h, w, 3) layer.
    `radius` is a share of the width, so the look is the same at any size."""
    radius = (radius[0] * w, radius[1] * w)
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        x = rng.uniform(0, w)
        y = rng.uniform(y_range[0] * h, y_range[1] * h)
        r = rng.uniform(*radius)
        k = rng.uniform(*strength)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(c * k) for c in colour))
    layer = layer.filter(ImageFilter.GaussianBlur(radius[0] * 0.45))
    return np.asarray(layer, np.float32)


def _mandala(size: int, colour, seed: int) -> Image.Image:
    """A line mandala (RGBA) of rings of petals, dots and arcs, drawn at 2x and reduced."""
    rng = np.random.default_rng(seed)
    s = size * 2
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    line = (*colour, 255)
    w = 3
    r = s * 0.06
    d.ellipse([c - r, c - r, c + r, c + r], outline=line, width=w)
    rings = 6
    for ring in range(1, rings + 1):
        r0 = s * (0.06 + 0.072 * ring)
        n = int(8 * (1 + ring // 2) * (1 if ring % 2 else 1.5))
        petal = s * rng.uniform(0.045, 0.06)
        kind = ring % 3
        d.ellipse([c - r0, c - r0, c + r0, c + r0], outline=line, width=w - 1)
        for i in range(n):
            a = 2 * math.pi * i / n + (math.pi / n if ring % 2 else 0)
            x, y = c + r0 * math.cos(a), c + r0 * math.sin(a)
            if kind == 0:  # petals pointing outwards
                tip = (c + (r0 + petal * 1.6) * math.cos(a), c + (r0 + petal * 1.6) * math.sin(a))
                left = (c + r0 * math.cos(a - math.pi / n * 0.8),
                        c + r0 * math.sin(a - math.pi / n * 0.8))
                right = (c + r0 * math.cos(a + math.pi / n * 0.8),
                         c + r0 * math.sin(a + math.pi / n * 0.8))
                d.line([left, tip, right], fill=line, width=w, joint="curve")
            elif kind == 1:  # dots
                rr = petal * 0.22
                d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=line, width=w - 1)
            else:  # small arcs between the rings
                rr = petal * 0.8
                deg = math.degrees(a)
                d.arc([x - rr, y - rr, x + rr, y + rr], deg - 90, deg + 90, fill=line, width=w)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def backdrop(brand: Brand, look: str, w: int, h: int, seed: int = 0,
             centre: tuple[float, float] = (0.5, 0.45)) -> Image.Image:
    """The background for `look` (paper, mandala, night or dusk), w x h."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx / w - 0.5) * 1.3) ** 2 + (yy / h - 0.5) ** 2)
    if look == "night":
        b = night_brand(brand)
        base = np.array(hex_rgb(b.colors.paper), np.float32)
        arr = np.broadcast_to(base, (h, w, 3)).copy()
        arr *= np.clip(1.1 - r * 0.8, 0.45, 1.05)[..., None]
        glow = hex_rgb(b.colors.glow)
        arr += _bokeh(w, h, rng, 9, glow, (0.0, 0.2), (0.012, 0.035), (0.2, 0.5))
        arr += _bokeh(w, h, rng, 7, glow, (0.86, 1.0), (0.015, 0.045), (0.15, 0.4))
        return _to_image(_grain(arr, rng, 2.5))
    if look == "dusk":
        top, mid, low = (np.array(c, np.float32) for c in
                         ((24, 22, 58), (86, 44, 82), (214, 110, 58)))
        t = (yy / h)[..., None]
        arr = np.where(t < 0.6, top + (mid - top) * (t / 0.6),
                       mid + (low - mid) * ((t - 0.6) / 0.4))
        # Soft sun low in the sky, off to one side (moves with the seed).
        sx, sy = rng.uniform(0.25, 0.75) * w, 0.9 * h
        sun = np.exp(-(((xx - sx) / (w * 0.35)) ** 2 + ((yy - sy) / (h * 0.16)) ** 2))
        arr += sun[..., None] * np.array([70, 45, 15], np.float32)
        arr += _bokeh(w, h, rng, 16, (255, 196, 120), (0.0, 1.0), (0.01, 0.03), (0.1, 0.3))
        arr *= np.clip(1.12 - r * 0.6, 0.6, 1.0)[..., None]
        return _to_image(_grain(arr, rng, 2.5))
    img = paper_texture(brand, w, h, seed, spine=False)
    if look == "mandala":
        size = int(min(w, h) * 1.15)
        m = _mandala(size, hex_rgb(brand.colors.ink_soft), seed)
        alpha = m.getchannel("A").point(lambda v: int(v * 0.16))
        m.putalpha(alpha)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.alpha_composite(m, (int(w * centre[0] - size / 2), int(h * centre[1] - size / 2)))
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    arr = np.asarray(img, np.float32) * np.clip(1.06 - r ** 2 * 0.45, 0.0, 1.0)[..., None]
    return _to_image(arr)
