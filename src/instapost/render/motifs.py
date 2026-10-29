"""Starter ink drawings, drawn in code: devotional symbols in a book-engraving style (fine
lines, cross-hatching, white paper). They make the page look right from the first post;
richer drawings (deities, scenes) come from your uploads or Gemini (studio/drawings.py).

`instapost drawings starter` writes them to data/drawings/starter/.
"""

import math
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SS = 3                 # drawn at 3x, then reduced: smooth lines
W, H = 900, 640        # final size (landscape, like the page's drawing area)
INK = 0


class Pen:
    """Drawing helpers at supersampled scale: lines, curves, hatching inside shapes."""

    def __init__(self, w: int = W, h: int = H):
        self.w, self.h = w * SS, h * SS
        self.img = Image.new("L", (self.w, self.h), 255)
        self.d = ImageDraw.Draw(self.img)

    def p(self, x: float, y: float) -> tuple[float, float]:
        return x * SS, y * SS

    def line(self, pts, width: float = 2.2):
        pts = [self.p(*q) for q in pts]
        self.d.line(pts, fill=INK, width=max(1, round(width * SS)), joint="curve")
        r = width * SS / 2
        for x, y in (pts[0], pts[-1]):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=INK)

    def curve(self, *ctrl, width: float = 2.2, steps: int = 60):
        self.line(bezier(ctrl, steps), width)

    def shape(self, pts, width: float = 2.2, fill: int | None = None):
        q = [self.p(*p) for p in pts]
        if fill is not None:
            self.d.polygon(q, fill=fill)
        self.d.line([*q, q[0]], fill=INK, width=max(1, round(width * SS)), joint="curve")

    def circle(self, cx, cy, r, width: float = 2.2, fill: int | None = None):
        x, y, rr = cx * SS, cy * SS, r * SS
        self.d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=INK, fill=fill,
                       width=max(1, round(width * SS)))

    def hatch(self, pts, angle: float = 35, gap: float = 6, width: float = 1.0,
              cross: bool = False):
        """Parallel ink lines inside a shape (engraving-style shading)."""
        mask = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(mask).polygon([self.p(*p) for p in pts], fill=255)
        for a in ([angle, angle + 90] if cross else [angle]):
            layer = Image.new("L", (self.w, self.h), 255)
            ld = ImageDraw.Draw(layer)
            t = math.radians(a)
            dx, dy = math.cos(t), math.sin(t)
            span = int(math.hypot(self.w, self.h))
            step = gap * SS
            for k in range(-span, span, max(1, int(step))):
                ox, oy = -dy * k + self.w / 2, dx * k + self.h / 2
                ld.line([(ox - dx * span, oy - dy * span), (ox + dx * span, oy + dy * span)],
                        fill=INK, width=max(1, round(width * SS)))
            self.img.paste(Image.composite(layer, self.img, mask), (0, 0))

    def text(self, xy, s: str, font):
        self.d.text(self.p(*xy), s, font=font, fill=INK, anchor="mm", language="hi")

    def done(self) -> Image.Image:
        small = self.img.resize((self.w // SS, self.h // SS), Image.Resampling.LANCZOS)
        return small.filter(ImageFilter.UnsharpMask(1.2, 60, 2))


def bezier(ctrl, steps: int = 60) -> list[tuple[float, float]]:
    n = len(ctrl) - 1
    out = []
    for i in range(steps + 1):
        t = i / steps
        x = y = 0.0
        for k, (px, py) in enumerate(ctrl):
            b = math.comb(n, k) * t ** k * (1 - t) ** (n - k)
            x += b * px
            y += b * py
        out.append((x, y))
    return out


def ellipse_pts(cx, cy, rx, ry, a0=0, a1=360, n=90):
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def ground(p: Pen, y: float, x0: float, x1: float):
    """A few short horizontal strokes under an object: it stands on something."""
    for i, (a, b) in enumerate([(x0, x1), (x0 + 60, x1 - 40), (x0 + 130, x1 - 120)]):
        p.line([(a, y + i * 9), (b, y + i * 9)], width=1.3 - i * 0.25)


# --- the motifs -------------------------------------------------------------------------------

def diya() -> Image.Image:
    p = Pen()
    cx = W / 2
    # Flame: teardrop with an inner flame and a soft glow of short rays.
    outer = bezier([(cx, 150), (cx + 52, 255), (cx + 40, 330), (cx, 338)], 40) + \
        bezier([(cx, 338), (cx - 40, 330), (cx - 52, 255), (cx, 150)], 40)
    p.shape(outer, 2.4)
    inner = bezier([(cx, 222), (cx + 22, 280), (cx + 16, 322), (cx, 326)], 30) + \
        bezier([(cx, 326), (cx - 16, 322), (cx - 22, 280), (cx, 222)], 30)
    p.shape(inner, 1.6)
    for k in range(14):
        a = math.radians(-160 + k * (140 / 13))
        r0, r1 = 120, 150 + (k % 2) * 18
        p.line([(cx + r0 * math.cos(a), 280 + r0 * math.sin(a)),
                (cx + r1 * math.cos(a), 280 + r1 * math.sin(a))], 1.2)
    # Wick and lamp bowl.
    p.line([(cx, 338), (cx + 4, 362)], 3)
    bowl = [(cx - 200, 368)] + bezier([(cx - 200, 368), (cx - 150, 470), (cx + 150, 470),
                                       (cx + 200, 368)], 50) + [(cx + 245, 352)]
    rim = ellipse_pts(cx, 368, 200, 26, 180, 360, 50)
    p.shape(bowl + list(reversed(rim)), 2.6)
    p.line(ellipse_pts(cx, 368, 200, 26, 0, 180, 50), 1.6)
    p.hatch(bowl + list(reversed(rim)), angle=60, gap=7, width=0.9)
    p.hatch([(cx - 140, 420), (cx + 140, 420), (cx + 90, 452), (cx - 90, 452)], 120, 7, 0.8)
    # Base.
    base = [(cx - 70, 456), (cx + 70, 456), (cx + 95, 500), (cx - 95, 500)]
    p.shape(base, 2.4, fill=255)
    p.hatch(base, angle=-30, gap=6, width=0.9)
    ground(p, 512, cx - 230, cx + 230)
    return p.done()


def lotus() -> Image.Image:
    p = Pen()
    cx, by = W / 2, 430

    def petal(angle: float, length: float, width: float, shade: bool):
        a = math.radians(angle)
        tip = (cx + length * math.sin(a), by - length * math.cos(a))
        nx, ny = math.cos(a), math.sin(a)
        left = bezier([(cx, by), (cx - nx * width + (tip[0] - cx) * 0.5,
                                  by - ny * width + (tip[1] - by) * 0.5), tip], 40)
        right = bezier([tip, (cx + nx * width + (tip[0] - cx) * 0.5,
                              by + ny * width + (tip[1] - by) * 0.5), (cx, by)], 40)
        pts = left + right
        p.shape(pts, 2.2, fill=255)
        p.line(bezier([(cx, by), ((cx + tip[0]) / 2 + nx * 6, (by + tip[1]) / 2 + ny * 6),
                       tip], 20), 1.0)
        if shade:
            p.hatch(right, angle=angle + 70, gap=6, width=0.8)

    for ang, ln, wd in ((-78, 230, 52), (78, 230, 52), (-55, 260, 60), (55, 260, 60)):
        petal(ang, ln, wd, True)
    for ang, ln, wd in ((-28, 285, 64), (28, 285, 64)):
        petal(ang, ln, wd, False)
    petal(0, 300, 66, False)
    # Water: leaves and ripples.
    for sx in (-1, 1):
        leaf = ellipse_pts(cx + sx * 250, by + 40, 150, 34)
        p.shape(leaf, 2.0, fill=255)
        p.hatch(leaf, angle=10, gap=7, width=0.8)
        p.line([(cx + sx * 120, by + 40), (cx + sx * 380, by + 40)], 1.0)
    for i, (rx, y) in enumerate(((330, 500), (260, 530), (180, 556))):
        p.line(ellipse_pts(cx, y, rx, 10, 0, 180, 60), 1.4 - i * 0.3)
    return p.done()


def trishul() -> Image.Image:
    p = Pen()
    cx = W / 2
    # Shaft with a ribbon and the damru.
    p.shape([(cx - 7, 230), (cx + 7, 230), (cx + 7, 600), (cx - 7, 600)], 2.0, fill=255)
    p.hatch([(cx, 230), (cx + 7, 230), (cx + 7, 600), (cx, 600)], 80, 5, 0.8)
    # Prongs: centre spear and two curved side prongs.
    p.shape([(cx, 60), (cx + 22, 150), (cx + 9, 175), (cx + 9, 235), (cx - 9, 235),
             (cx - 9, 175), (cx - 22, 150)], 2.4, fill=255)
    for sx in (-1, 1):
        prong = bezier([(cx + sx * 8, 232), (cx + sx * 120, 230), (cx + sx * 112, 140),
                        (cx + sx * 96, 92)], 50) + bezier(
            [(cx + sx * 96, 92), (cx + sx * 86, 140), (cx + sx * 82, 200), (cx + sx * 8, 205)], 50)
        p.shape(prong, 2.4, fill=255)
        p.hatch(prong, angle=45 * sx, gap=6, width=0.8)
    p.shape([(cx - 30, 232), (cx + 30, 232), (cx + 22, 250), (cx - 22, 250)], 2.2, fill=255)
    # Damru tied below the prongs.
    dy = 300
    for side in (-1, 1):
        tri = [(cx + 10 * side, dy), (cx + 70 * side, dy - 38), (cx + 70 * side, dy + 38)]
        p.shape(tri, 2.2, fill=255)
        p.hatch(tri, angle=90, gap=5, width=0.8)
        p.line(ellipse_pts(cx + 70 * side, dy, 8, 38, -90, 90, 30) if side > 0 else
               ellipse_pts(cx - 70, dy, 8, 38, 90, 270, 30), 2.0)
    p.curve((cx, dy + 4), (cx + 40, dy + 70), (cx - 30, dy + 90), (cx + 10, dy + 140), width=1.4)
    p.circle(cx + 10, dy + 146, 6, 1.6, fill=0)
    # Cloth ribbon fluttering from the shaft.
    p.curve((cx + 7, 270), (cx + 90, 285), (cx + 130, 330), (cx + 190, 320), width=1.8)
    p.curve((cx + 7, 282), (cx + 80, 310), (cx + 120, 360), (cx + 175, 360), width=1.8)
    ground(p, 606, cx - 160, cx + 160)
    return p.done()


def flute() -> Image.Image:
    p = Pen()
    # Bansuri lying diagonally, with holes and bindings.
    a = math.radians(-14)
    x0, y0, length, r = 110, 440, 700, 16
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy, ux
    body = [(x0 + nx * r, y0 + ny * r), (x0 + ux * length + nx * r, y0 + uy * length + ny * r),
            (x0 + ux * length - nx * r, y0 + uy * length - ny * r), (x0 - nx * r, y0 - ny * r)]
    p.shape(body, 2.4, fill=255)
    lower = [body[0], body[1], (x0 + ux * length, y0 + uy * length), (x0, y0)]
    p.hatch(lower, angle=-14, gap=5, width=0.8)
    for t in (0.2, 0.34, 0.42, 0.5, 0.58, 0.66, 0.74):
        cx, cy = x0 + ux * length * t, y0 + uy * length * t
        p.circle(cx - nx * 3, cy - ny * 3, 5.5, 1.6, fill=0)
    for t in (0.06, 0.1, 0.88, 0.92):
        cx, cy = x0 + ux * length * t, y0 + uy * length * t
        p.line([(cx + nx * r, cy + ny * r), (cx - nx * r, cy - ny * r)], 2.2)
    # Peacock feather crossing behind.
    fx, fy = 520, 150
    p.curve((300, 600), (380, 420), (450, 260), (fx, fy), width=2.0)
    for k in range(26):
        t = 0.25 + 0.75 * k / 25
        sx, sy = bezier([(300, 600), (380, 420), (450, 260), (fx, fy)], 25)[int(t * 25)]
        for side in (-1, 1):
            p.curve((sx, sy), (sx + side * 40, sy - 20), (sx + side * 70, sy - 10), width=0.9)
    eye = ellipse_pts(fx + 10, fy - 30, 60, 78)
    p.shape(eye, 2.0, fill=255)
    p.hatch(eye, angle=30, gap=7, width=0.8)
    mid = ellipse_pts(fx + 10, fy - 22, 38, 50)
    p.shape(mid, 1.8, fill=255)
    inner = ellipse_pts(fx + 10, fy - 14, 18, 24)
    p.shape(inner, 1.6, fill=40)
    return p.done()


def kailash() -> Image.Image:
    p = Pen()
    # Moon, Himalaya peaks with snow lines, a calm lake with reflections.
    moon = ellipse_pts(660, 120, 46, 46, 60, 300, 70)
    inner = ellipse_pts(684, 108, 40, 40, 300, 60, 70)  # the bite that makes the crescent
    crescent = moon + inner
    p.shape(crescent, 2.2, fill=255)
    p.hatch(crescent, angle=35, gap=4, width=0.7)
    far = [(40, 400), (150, 300), (220, 340), (330, 190), (420, 300), (520, 230), (640, 330),
           (760, 260), (860, 400)]
    p.line(far, 2.0)
    for (ax, ay), (bx, by) in zip(far, far[1:], strict=False):
        if by < ay:  # slopes facing the light get hatching on the other side
            p.hatch([(ax, ay), (bx, by), (bx, 400), (ax, 400)], 60, 7, 0.7)
    peak = [(250, 400), (440, 150), (630, 400)]
    p.shape(peak, 2.6, fill=255)
    p.hatch([(440, 150), (630, 400), (440, 400)], 70, 5, 0.9, cross=True)
    p.line([(395, 210), (420, 236), (440, 205), (462, 240), (485, 212)], 1.8)  # snow line
    for i, y in enumerate(range(420, 560, 16)):
        x0 = 80 + i * 18
        p.line([(x0, y), (W - x0, y)], 1.4 - i * 0.12)
    for i, y in enumerate(range(426, 520, 18)):
        p.line([(440 - 70 + i * 6, y), (440 + 70 - i * 6, y)], 1.0)
    return p.done()


def om() -> Image.Image:
    from instapost.brand import load_brand
    from instapost.render.text import font

    p = Pen()
    cx, cy = W / 2, H / 2 - 10
    for r, wd in ((236, 2.4), (222, 1.0)):
        p.circle(cx, cy, r, wd)
    for k in range(48):  # rays between the rings and a petal border
        a = math.radians(k * 7.5)
        r0, r1 = 236, 262 + (k % 2) * 14
        p.line([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                (cx + r1 * math.cos(a), cy + r1 * math.sin(a))], 1.2)
    f = font(load_brand().font_path("header"), 260 * SS)
    p.text((cx, cy + 10), "ॐ", f)
    return p.done()


def sunrise() -> Image.Image:
    p = Pen()
    cx, hy = W / 2, 400
    sun = ellipse_pts(cx, hy, 120, 120, 180, 360, 80)
    p.line(sun, 2.4)
    for k in range(19):
        a = math.radians(180 + k * 10)
        p.line([(cx + 140 * math.cos(a), hy + 140 * math.sin(a)),
                (cx + (190 + (k % 2) * 30) * math.cos(a), hy + (190 + (k % 2) * 30) * math.sin(a))],
               1.4)
    hills = bezier([(20, 470), (220, 360), (380, 430), (520, 400)], 40) + \
        bezier([(520, 400), (680, 360), (800, 420), (880, 470)], 40)
    p.line(hills, 2.4)
    p.hatch(hills + [(880, 560), (20, 560)], angle=20, gap=7, width=0.8)
    p.line([(20, 470), (880, 470)], 1.6)
    for bx, by, s in ((250, 230, 1.0), (300, 200, 0.8), (620, 250, 0.9)):  # birds
        p.curve((bx - 24 * s, by), (bx - 10 * s, by - 14 * s), (bx, by), width=1.6)
        p.curve((bx, by), (bx + 10 * s, by - 14 * s), (bx + 24 * s, by), width=1.6)
    return p.done()


def chariot_wheel() -> Image.Image:
    p = Pen()
    cx, cy, R = W / 2, H / 2, 250
    for r, wd in ((R, 3.0), (R - 22, 1.6), (60, 2.4), (30, 2.0)):
        p.circle(cx, cy, r, wd)
    rim = ellipse_pts(cx, cy, R, R) + list(reversed(ellipse_pts(cx, cy, R - 22, R - 22)))
    p.hatch(rim, angle=45, gap=6, width=0.8)
    for k in range(16):
        a = math.radians(k * 22.5)
        p.line([(cx + 60 * math.cos(a), cy + 60 * math.sin(a)),
                (cx + (R - 22) * math.cos(a), cy + (R - 22) * math.sin(a))], 2.2)
    hub = ellipse_pts(cx, cy, 60, 60) + list(reversed(ellipse_pts(cx, cy, 30, 30)))
    p.hatch(hub, angle=-45, gap=5, width=0.8, cross=True)
    return p.done()


def matka() -> Image.Image:
    """The potter's clay pot of Kabir's dohe ("माटी कहे कुम्हार से"), on a ring stand."""
    p = Pen()
    cx = W / 2
    left = bezier([(cx - 62, 150), (cx - 70, 190), (cx - 230, 260), (cx - 215, 380),
                   (cx - 190, 500), (cx - 70, 520), (cx, 522)], 70)
    right = [(2 * cx - x, y) for x, y in reversed(left)]
    body = left + right
    p.shape(body, 2.6, fill=255)

    def half_width(y: float) -> float:
        return max((cx - x for x, yy in left if abs(yy - y) < 4), default=0.0)

    # Shade the side away from the light: the right part of the body.
    inner = [(cx + (x - cx) * 0.5, y) for x, y in reversed(right)]  # curved light edge
    shade = right + inner
    p.hatch(shade, 75, 6, 0.85)
    p.shape(ellipse_pts(cx, 150, 70, 16), 2.2, fill=255)       # mouth
    p.line(ellipse_pts(cx, 136, 82, 18, 180, 360, 40), 2.0)    # lip
    for y in (280, 304):                                       # painted bands
        p.line(ellipse_pts(cx, y, half_width(y) - 2, 22, 0, 180, 60), 1.4)
    hw = half_width(292) - 2
    for k in range(9):                                         # dots between the bands
        a = math.radians(10 + k * 20)
        p.circle(cx + hw * math.cos(a), 292 + 22 * math.sin(a), 3.2, 1.0, fill=0)
    ring = ellipse_pts(cx, 540, 120, 26)
    p.shape(ring, 2.2, fill=255)
    p.hatch(ring, 0, 5, 0.7)
    ground(p, 580, cx - 240, cx + 240)
    return p.done()


def mala() -> Image.Image:
    """A rudraksha mala with its guru bead and tassel."""
    p = Pen()
    cx, cy, rx, ry = W / 2, 290, 270, 190
    n = 54
    for k in range(n):
        a = math.radians(90 + 6 + k * (348 / (n - 1)))
        x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
        p.circle(x, y, 14, 1.8, fill=255)
        p.hatch(ellipse_pts(x + 3, y + 3, 10, 10), 45, 3.2, 0.6)
    gy = cy + ry + 16
    p.circle(cx, gy, 22, 2.2, fill=255)                       # guru bead
    p.hatch(ellipse_pts(cx + 4, gy + 4, 16, 16), -45, 3.2, 0.7, cross=True)
    p.shape([(cx - 14, gy + 22), (cx + 14, gy + 22), (cx + 10, gy + 44), (cx - 10, gy + 44)],
            1.8, fill=255)
    for k in range(-7, 8):                                    # tassel threads
        p.curve((cx + k * 1.6, gy + 44), (cx + k * 3, gy + 80), (cx + k * 5.5, gy + 120),
                width=1.0)
    p.line([(cx - 34, gy + 122), (cx + 34, gy + 122)], 1.2)
    return p.done()


STARTERS: dict[str, tuple[Callable[[], Image.Image], list[str]]] = {
    "diya": (diya, ["diya", "lamp", "light", "flame", "deepawali", "diwali", "jeevan_sutra",
                    "life_lesson", "hope"]),
    "lotus": (lotus, ["lotus", "flower", "peace", "water", "jeevan_sutra", "life_lesson",
                      "calm", "purity"]),
    "trishul-damru": (trishul, ["shiva", "shiv", "mahadev", "trishul", "damru",
                                "shiv_dhyan"]),
    "bansuri-feather": (flute, ["krishna", "flute", "bansuri", "peacock", "feather", "gita"]),
    "kailash-moon": (kailash, ["shiva", "shiv", "himalaya", "mountain", "kailash", "moon",
                               "lake", "shiv_dhyan", "meditation"]),
    "om": (om, ["om", "mantra", "meditation", "shiv_dhyan", "jeevan_sutra", "peace"]),
    "sunrise-hills": (sunrise, ["sun", "sunrise", "morning", "hope", "hills", "life_lesson",
                                "new", "beginning"]),
    "chariot-wheel": (chariot_wheel, ["chariot", "wheel", "arjuna", "dharma", "karma", "gita",
                                      "time"]),
    "matka": (matka, ["kabir", "pot", "clay", "potter", "matka", "kumhar", "doha",
                      "patience", "life_lesson"]),
    "mala": (mala, ["mala", "rudraksha", "beads", "japa", "chant", "mantra", "shiv_dhyan",
                    "shiva", "kabir", "meditation"]),
}


def write_starters(folder: Path) -> list[Path]:
    """Draw every starter motif into `folder` (PNG + tags file)."""
    import json

    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name, (draw, tags) in STARTERS.items():
        img = draw()
        img = ImageChops.lighter(img, Image.new("L", img.size, 0))
        path = folder / f"{name}.png"
        img.save(path, optimize=True)
        (folder / f"{name}.json").write_text(json.dumps({"tags": tags}), encoding="utf-8")
        out.append(path)
    return out
