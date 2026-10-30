"""Backgrounds drawn in code for cards, slides and stories: free, never the same twice (each
post's seed moves the light and the pattern), and in the page's own calm style.

- paper:   the cream page;
- mandala: the cream page with a faint hand-drawn mandala behind the text;
- night:   dark paper with a few warm, out-of-focus diya lights;
- dusk:    a sunset sky (indigo to saffron) with a soft sun glow and floating lights;
- gold_frame: deep maroon with an ornate double gold border, like a temple poster;
- sunrise: warm saffron light with soft rays from the top, dark text;
- ganga:   night on the river: a row of diyas on the far bank, their light on the water;
- manuscript: an old parchment page with darkened, uneven edges;
- custom-…: your own looks from the dashboard (an uploaded or AI-made picture, your colours).

Dark backgrounds take light ink (`look_brand`), so text stays readable on every one.
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from instapost.brand import Brand
from instapost.render.page import hex_rgb, paper_texture

DARK = {"night", "dusk", "gold_frame", "ganga"}


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


def _palette(**colors):
    def apply(brand: Brand) -> Brand:
        return brand.model_copy(update={"colors": brand.colors.model_copy(update=colors)})
    return apply


PALETTES = {
    "night": night_brand,
    "dusk": dusk_brand,
    "gold_frame": _palette(paper="#3A0F10", paper_edge="#240809", ink="#FFF1DA",
                           ink_soft="#E9CFA4", highlight="#8E5A12", highlight_alpha=0.78,
                           glow="#FFB04A"),
    "sunrise": _palette(ink="#3A1E0E", ink_soft="#6B3F22", highlight="#FFF3D6",
                        highlight_alpha=0.85, glow="#FFC46B"),
    "ganga": _palette(paper="#0E1A2B", paper_edge="#08111C", ink="#FFF3E2", ink_soft="#D7C6AC",
                      highlight="#A4581B", highlight_alpha=0.66, glow="#FF9E40"),
    "manuscript": _palette(paper="#E6CFA0", paper_edge="#C9AE7A", ink="#3B2412",
                           ink_soft="#6A4A2C", highlight="#D9915A", highlight_alpha=0.55),
}


def look_brand(brand: Brand, look: str) -> Brand:
    if look.startswith("custom-"):
        from instapost.render import custom_looks
        return custom_looks.brand_for(brand, look)
    return PALETTES.get(look, lambda b: b)(brand)


def _smooth_noise(w: int, h: int, rng, cell: int) -> np.ndarray:
    """Soft low-frequency noise 0..1 (blotches, uneven edges)."""
    small = rng.random((max(2, h // cell), max(2, w // cell))).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.Resampling.BICUBIC)
    return np.asarray(img.filter(ImageFilter.GaussianBlur(cell / 3)), np.float32) / 255


def _gold_frame(w: int, h: int, rng) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx / w - 0.5) * 1.2) ** 2 + (yy / h - 0.45) ** 2)
    base = np.array([74, 20, 22], np.float32)
    arr = base * np.clip(1.15 - r * 1.1, 0.45, 1.1)[..., None]
    arr = _grain(arr, rng, 2.0)
    img = _to_image(arr)
    d = ImageDraw.Draw(img)
    gold, soft = (214, 168, 84), (160, 118, 52)
    m = int(w * 0.04)
    d.rectangle([m, m, w - m, h - m], outline=gold, width=4)
    m2 = m + int(w * 0.016)
    d.rectangle([m2, m2, w - m2, h - m2], outline=soft, width=2)
    # Corner ornaments: a quarter lotus of petals in each corner.
    rr = int(w * 0.09)
    for cx, cy, a0 in ((m2, m2, 0), (w - m2, m2, 90), (w - m2, h - m2, 180), (m2, h - m2, 270)):
        for k in range(5):
            a = math.radians(a0 + 9 + k * 18)
            tip = (cx + rr * math.cos(a), cy + rr * math.sin(a))
            left = (cx + rr * 0.45 * math.cos(a - 0.22), cy + rr * 0.45 * math.sin(a - 0.22))
            right = (cx + rr * 0.45 * math.cos(a + 0.22), cy + rr * 0.45 * math.sin(a + 0.22))
            d.line([(cx, cy), left, tip, right, (cx, cy)], fill=gold, width=2, joint="curve")
        d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=gold)
    # Small diamonds at the middle of each side.
    for x, y in ((w / 2, m2), (w / 2, h - m2), (m2, h / 2), (w - m2, h / 2)):
        d.polygon([(x, y - 12), (x + 12, y), (x, y + 12), (x - 12, y)], fill=gold)
    return img


def _sunrise(w: int, h: int, rng) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (yy / h)[..., None]
    top, low = np.array([255, 214, 150], np.float32), np.array([250, 236, 214], np.float32)
    arr = top + (low - top) * np.clip(t * 1.3, 0, 1)
    sx, sy = w * rng.uniform(0.4, 0.6), -h * 0.05
    ang = np.arctan2(yy - sy, xx - sx)
    rays = 0.5 + 0.5 * np.sin(ang * rng.integers(16, 24) + rng.uniform(0, 6.28))
    dist = np.sqrt((xx - sx) ** 2 + (yy - sy) ** 2) / h
    fade = np.clip(1 - dist * 1.25, 0, 1)
    arr += (rays * fade * 26)[..., None] * np.array([1.0, 0.75, 0.35], np.float32)
    glow = np.exp(-(dist / 0.28) ** 2)
    arr += glow[..., None] * np.array([18, 10, 0], np.float32)
    return _to_image(_grain(arr, rng, 2.0))


def _ganga(w: int, h: int, rng) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    horizon = 0.8 * h
    t = (yy / horizon)[..., None]
    sky_top, sky_low = np.array([10, 16, 34], np.float32), np.array([30, 34, 58], np.float32)
    arr = sky_top + (sky_low - sky_top) * np.clip(t, 0, 1)
    water = yy > horizon
    arr[water] = np.array([12, 20, 34], np.float32)
    # Stars, faint.
    for _ in range(70):
        x, y = int(rng.uniform(0, w)), int(rng.uniform(0, horizon * 0.55))
        arr[y, x] += rng.uniform(40, 110)
    # A row of diyas on the far bank and their light on the water.
    glow = np.zeros((h, w), np.float32)
    lamps = np.sort(rng.uniform(0.03, 0.97, 16)) * w
    for x in lamps:
        glow += np.exp(-(((xx - x) / 9) ** 2 + ((yy - horizon) / 7) ** 2)) * rng.uniform(0.7, 1)
        streak = (np.exp(-((xx - x) / 8) ** 2)
                  * np.exp(-np.clip(yy - horizon, 0, None) / (h * 0.08)))
        ripple = 0.7 + 0.3 * np.sin(yy * 0.09 + rng.uniform(0, 6.28))
        glow += streak * ripple * water * 0.55
    halo = np.exp(-((yy - horizon) / (h * 0.06)) ** 2) * 0.18
    arr += (glow * 230)[..., None] * np.array([1.0, 0.62, 0.25], np.float32)
    arr += (halo * 255)[..., None] * np.array([1.0, 0.55, 0.2], np.float32)
    return _to_image(_grain(arr, rng, 2.0))


def _manuscript(brand: Brand, w: int, h: int, rng) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.array(hex_rgb(PALETTES["manuscript"](brand).colors.paper), np.float32)
    blot = _smooth_noise(w, h, rng, 90)
    arr = base * (0.92 + 0.12 * blot)[..., None]
    # Darkened, uneven edges, as on an old page.
    edge = np.minimum.reduce([xx, w - xx, yy, h - yy]) / (w * 0.12)
    wobble = _smooth_noise(w, h, rng, 40) * 0.6
    burn = np.clip(1 - (edge * 1.6 + wobble * 0.6 - 0.15), 0, 1) ** 2.5
    arr *= (1 - 0.42 * burn)[..., None]
    arr -= (burn * 28)[..., None] * np.array([0.2, 0.5, 0.9], np.float32)
    return _to_image(_grain(arr, rng, 3.0))



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
    """The background for `look` (see the list at the top), w x h."""
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
    if look.startswith("custom-"):
        from instapost.render import custom_looks
        return custom_looks.backdrop(look, w, h, seed)
    if look == "gold_frame":
        return _gold_frame(w, h, rng)
    if look == "sunrise":
        return _sunrise(w, h, rng)
    if look == "ganga":
        return _ganga(w, h, rng)
    if look == "manuscript":
        return _manuscript(brand, w, h, rng)
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
