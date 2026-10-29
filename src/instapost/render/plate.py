"""Book-photo reels: our page printed onto the blank page of a real photo.

A "plate" is a photo of an open book (ideally held in hands, warm home light) whose page is
still blank. We find the page's four corners, crop the photo to 9:16 around it, give it the
house colour grade, and then *print* the page onto it: the ink of our page (text, highlight,
drawing) multiplies the photo's own paper, so the photo's light, gutter shadow, paper grain
and fingers stay exactly as photographed. The text itself is always drawn by our code, so
the Hindi is correct and sharp (AI-made pictures garble Devanagari).

The result is the same `Composition` the classic scene makes, so the reel motion, the text
reveal and the QA checks work unchanged.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from instapost.brand import Brand
from instapost.render.page import PAGE_H, PAGE_W, RenderedPage, paper_texture
from instapost.render.scene import FRAME_H, FRAME_W, Composition, FrameLayer
from instapost.render.text import Box

Quad = list[tuple[float, float]]  # TL, TR, BR, BL

# Page in the frame: wide enough to read on a phone, inside Instagram's safe area.
MIN_PAGE_WIDTH = 600       # px of the 1080 frame (our page is 940 px wide)
TARGET_PAGE_SHARE = 0.80   # page width / frame width after cropping
PAGE_ASPECT = PAGE_W / PAGE_H


@dataclass
class Detection:
    quad: Quad
    fill: float        # how well the paper area fills the quad (1 = perfect)
    blank: float       # fine detail on the page (low = blank page)
    aspect: float      # estimated page width / height
    reason: str = ""   # why it is not usable ("" = usable)
    spine_cut: bool = False  # the facing page was cut off at a found spine (check it)

    @property
    def ok(self) -> bool:
        return not self.reason


@dataclass
class Plate:
    id: str
    image: Image.Image  # 1080x1920, graded
    quad: Quad          # page corners in the frame
    credit: str = ""


# --- geometry -------------------------------------------------------------------------------

def homography(src: Quad, dst: Quad) -> np.ndarray:
    """3x3 matrix mapping the 4 `src` points to the 4 `dst` points."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    h = np.linalg.solve(np.array(a, float), np.array(b, float))
    return np.append(h, 1.0).reshape(3, 3)


def apply(h: np.ndarray, x: float, y: float) -> tuple[float, float]:
    p = h @ np.array([x, y, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def _area(q: Quad) -> float:
    xs, ys = [p[0] for p in q], [p[1] for p in q]
    return 0.5 * abs(sum(xs[i] * ys[(i + 1) % 4] - xs[(i + 1) % 4] * ys[i] for i in range(4)))


def _convex(q: Quad) -> bool:
    signs = []
    for i in range(4):
        (x0, y0), (x1, y1), (x2, y2) = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        signs.append((x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1) > 0)
    return all(signs) or not any(signs)


def _dist(a, b) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def size_of(q: Quad) -> tuple[float, float]:
    """Average width and height of the quad (top/bottom and left/right edges)."""
    tl, tr, br, bl = q
    return (_dist(tl, tr) + _dist(bl, br)) / 2, (_dist(tl, bl) + _dist(tr, br)) / 2


# --- finding the page -----------------------------------------------------------------------

def _largest_region(mask: np.ndarray) -> np.ndarray:
    """Largest 4-connected True region (PIL flood fill does the work in C)."""
    # .copy(): an image made from an array shares its memory read-only, and flood fill
    # would silently change nothing.
    img = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).copy()
    best, best_n, label = None, 0, 1
    h, w = mask.shape
    for y in range(0, h, 6):
        for x in range(0, w, 6):
            if label > 250:
                break
            if img.getpixel((x, y)) == 255:
                ImageDraw.floodfill(img, (x, y), label)
                n = int((np.asarray(img) == label).sum())
                if n > best_n:
                    best, best_n = label, n
                label += 1
    if best is None:
        return np.zeros_like(mask)
    return np.asarray(img) == best


def _fill_holes(region: np.ndarray) -> np.ndarray:
    """Close holes (the drawing, text) inside the page region."""
    h, w = region.shape
    pad = np.zeros((h + 2, w + 2), np.uint8)
    pad[1:-1, 1:-1] = np.where(region, 255, 0)
    img = Image.fromarray(pad).copy()
    ImageDraw.floodfill(img, (0, 0), 128)  # everything reachable from outside
    return (np.asarray(img)[1:-1, 1:-1] != 128)


def detect_page(img: Image.Image) -> Detection | None:
    """Find the brightest, paper-coloured four-sided area (a page) in a photo."""
    w0, h0 = img.size
    scale = 360 / w0
    small = img.convert("RGB").resize((360, max(1, round(h0 * scale))), Image.Resampling.BILINEAR)
    hsv = np.asarray(small.convert("HSV"), np.float32)
    s, v = hsv[..., 1], hsv[..., 2]
    paper = (v > max(150.0, float(np.percentile(v, 55)))) & (s < 75)
    m = Image.fromarray(np.where(paper, 255, 0).astype(np.uint8))
    # Closing joins the paper across text lines.
    m = m.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    best: tuple[float, list, float, int] | None = None
    # Opening cuts thin bridges (to the facing page, a wall); try gentle to strong and keep
    # the result that is most like one clean four-sided page.
    for k in (3, 7, 11, 15):
        opened = m.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
        whole = _fill_holes(_largest_region(np.asarray(opened) > 127))
        region = _cut_at_spine(whole, v)
        cut = region is not whole
        area = int(region.sum())
        if area < 0.06 * region.size:
            continue
        quad = _corners(region)
        fill = area / max(_area(quad), 1.0)
        score = fill * min(1.0, area / (0.15 * region.size))
        if best is None or score > best[0] + 0.01:
            best = (score, quad, fill, cut)
    if best is None:
        return None
    _, quad, fill, cut = best
    width, height = size_of(quad)
    if width / max(height, 1) > 1.05:  # a two-page spread: use the right-hand page
        tl, tr, br, bl = quad
        top_mid = ((tl[0] + tr[0]) / 2, (tl[1] + tr[1]) / 2)
        bottom_mid = ((bl[0] + br[0]) / 2, (bl[1] + br[1]) / 2)
        quad = [top_mid, tr, br, bottom_mid]
        width, height = size_of(quad)
    full = [(x / scale, y / scale) for x, y in quad]
    det = Detection(full, round(fill, 3), round(_blankness(img, full), 2),
                    round(width / max(height, 1), 3), spine_cut=cut)
    if not _convex(full):
        det.reason = "page corners not found"
    elif fill < 0.88:
        det.reason = f"page shape unclear ({fill:.2f})"
    elif not 0.45 <= det.aspect <= 1.0:
        det.reason = f"page proportions {det.aspect:.2f}"
    elif det.blank > 9:
        det.reason = f"page is not blank ({det.blank:.1f})"
    return det


def _cut_at_spine(region: np.ndarray, v: np.ndarray) -> np.ndarray:
    """If the region holds a strip of the facing page, cut it off at the spine: the dark,
    roughly straight line running down the paper near one side."""
    rows = np.nonzero(region.any(axis=1))[0]
    if len(rows) < 40:
        return region
    smooth = np.asarray(Image.fromarray(v.astype(np.uint8)).filter(
        ImageFilter.BoxBlur(1)), np.float32)
    best = None
    for side in ("left", "right"):
        pts = []
        for y in rows[len(rows) // 10: -len(rows) // 10]:
            xs = np.nonzero(region[y])[0]
            if len(xs) < 30:
                continue
            x0, x1 = xs.min(), xs.max()
            band = int((x1 - x0) * 0.3)
            lo, hi = (x0 + 3, x0 + band) if side == "left" else (x1 - band, x1 - 3)
            if hi - lo < 5:
                continue
            seg = smooth[y, lo:hi]
            i = int(seg.argmin())
            x = lo + i
            # Darker than the paper on both sides of it (the pages can differ in brightness).
            left = float(smooth[y, max(x0, x - 9):max(x0, x - 3)].mean()) if x - 3 > x0 else 0
            right = float(smooth[y, x + 3:min(x1, x + 9)].mean()) if x + 9 < x1 else 0
            if min(left, right) - float(seg[i]) > 12 and 2 <= i <= len(seg) - 3:
                pts.append((y, x))
        if len(pts) < 0.3 * len(rows):
            continue
        ys_, xs_ = np.array(pts, float).T
        slope, icept = np.polyfit(ys_, xs_, 1)
        resid = np.abs(xs_ - (slope * ys_ + icept))
        keep = resid < 4
        if keep.sum() < 0.25 * len(rows):
            continue
        slope, icept = np.polyfit(ys_[keep], xs_[keep], 1)
        strength = keep.sum()
        if best is None or strength > best[0]:
            best = (strength, side, slope, icept)
    if best is None:
        return region
    _, side, slope, icept = best
    yy, xx = np.mgrid[0:region.shape[0], 0:region.shape[1]]
    line = slope * yy + icept
    cut = region & ((xx > line + 2) if side == "left" else (xx < line - 2))
    return cut if cut.sum() > 0.5 * region.sum() else region


def _corners(region: np.ndarray) -> Quad:
    """The four corners. First guess: extreme points along the two diagonals; then a straight
    line is fitted to each edge of the paper and neighbouring edges are intersected, so a
    corner hidden under a thumb is still placed where the paper's edges meet."""
    ys, xs = np.nonzero(region)
    ssum, sdiff = xs + ys, xs - ys
    quad = [(xs[ssum.argmin()], ys[ssum.argmin()]), (xs[sdiff.argmax()], ys[sdiff.argmax()]),
            (xs[ssum.argmax()], ys[ssum.argmax()]), (xs[sdiff.argmin()], ys[sdiff.argmin()])]
    quad = [(float(x), float(y)) for x, y in quad]
    inner = np.asarray(Image.fromarray(region.astype(np.uint8) * 255).filter(
        ImageFilter.MinFilter(3))) > 127
    by, bx = np.nonzero(region & ~inner)          # boundary pixels
    pts = np.stack([bx, by], axis=1).astype(float)
    h, w = region.shape
    for band in (25.0, 15.0, 10.0):  # each pass starts from the better corners
        lines = []
        for i in range(4):
            line = _fit_edge(pts, np.array(quad[i]), np.array(quad[(i + 1) % 4]), band)
            if line is None:
                return quad
            lines.append(line)
        refined = [_intersect(lines[i - 1], lines[i]) for i in range(4)]
        if any(c is None for c in refined) or not all(
                -0.05 * w <= x <= 1.05 * w and -0.05 * h <= y <= 1.05 * h for x, y in refined):
            return quad
        quad = refined
    return quad


def _fit_edge(pts: np.ndarray, a: np.ndarray, b: np.ndarray, band: float = 15.0):
    """Straight line (point, direction) through the boundary pixels along edge a->b; pixels
    of notches (a thumb, a shadow) are trimmed away step by step."""
    d = b - a
    length = float(np.hypot(*d))
    if length < 10:
        return None
    u = d / length
    n = np.array([-u[1], u[0]])
    rel = pts - a
    along = rel @ u
    sel = pts[(along > 0.08 * length) & (along < 0.92 * length) & (np.abs(rel @ n) < band)]
    for tol in (8.0, 4.0, 2.5):
        if len(sel) < 12:
            return None
        c = sel.mean(axis=0)
        _, _, vt = np.linalg.svd(sel - c)
        direction = vt[0]
        normal = np.array([-direction[1], direction[0]])
        dist = np.abs((sel - c) @ normal)
        sel = sel[dist < tol]
    return c, direction


def _intersect(l1, l2) -> tuple[float, float] | None:
    (p, r), (q, s) = l1, l2
    cross = r[0] * s[1] - r[1] * s[0]
    if abs(cross) < 1e-6:
        return None
    t_ = ((q - p)[0] * s[1] - (q - p)[1] * s[0]) / cross
    x, y = p + t_ * r
    return float(x), float(y)


def _blankness(img: Image.Image, quad: Quad) -> float:
    """Fine detail inside the page (text, lines). Blank paper is low, a printed page high."""
    g = img.convert("L")
    w, h = g.size
    cx, cy = sum(p[0] for p in quad) / 4, sum(p[1] for p in quad) / 4
    inner = [(cx + (x - cx) * 0.7, cy + (y - cy) * 0.7) for x, y in quad]  # away from edges
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(inner, fill=255)
    arr = np.asarray(g, np.float32)
    detail = arr - np.asarray(g.filter(ImageFilter.GaussianBlur(4)), np.float32)
    sel = np.asarray(mask) > 0
    return float(detail[sel].std()) if sel.any() else 99.0


# --- framing and look -----------------------------------------------------------------------

def fit_to_frame(img: Image.Image, quad: Quad) -> tuple[Image.Image, Quad] | None:
    """Crop to 9:16 around the page so it is ~80% of the frame width and inside the safe
    area; None if the photo cannot give that."""
    w0, h0 = img.size
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    qw, qh = max(xs) - min(xs), max(ys) - min(ys)
    cw = qw / TARGET_PAGE_SHARE
    ch = cw * FRAME_H / FRAME_W
    if ch < qh / 0.74:  # tall page: keep it inside the safe band (14%..86% of the height)
        ch = qh / 0.74
        cw = ch * FRAME_W / FRAME_H
    if cw > w0 or ch > h0:  # the photo is too small for that crop: use what there is
        k = min(w0 / cw, h0 / ch)
        cw, ch = cw * k, ch * k
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    left = min(max(cx - cw / 2, 0), w0 - cw)
    top = min(max(cy - ch * 0.48, 0), h0 - ch)
    k = FRAME_W / cw
    fq = [((x - left) * k, (y - top) * k) for x, y in quad]
    fx, fy = [p[0] for p in fq], [p[1] for p in fq]
    if min(fx) < -2 or max(fx) > FRAME_W + 2 or min(fy) < FRAME_H * 0.08 \
            or max(fy) > FRAME_H * 0.92:
        return None
    if size_of(fq)[0] < MIN_PAGE_WIDTH:
        return None
    crop = img.convert("RGB").resize((FRAME_W, FRAME_H), Image.Resampling.LANCZOS,
                                     box=(left, top, left + cw, top + ch))
    return crop, fq


def grade(img: Image.Image, seed: int = 0) -> Image.Image:
    """The house look on every photo: warm highlights, soft lifted shadows, gentle vignette,
    fine grain. Same for every post, so the grid looks like one page."""
    a = np.asarray(img.convert("RGB"), np.float32) / 255
    lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
    a = a * 0.94 + 0.04                           # lift blacks, soften whites
    warm = np.array([1.035, 1.0, 0.93], np.float32)
    a = a * (1 + (warm - 1) * lum[..., None])     # warmer in the highlights
    a = a + (lum[..., None] - a) * 0.08           # a little less saturation
    h, w = lum.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx / w - 0.5) * 1.1) ** 2 + ((yy / h - 0.5) * 0.9) ** 2)
    a = a * np.clip(1.08 - r * 0.42, 0.78, 1.0)[..., None]
    rng = np.random.default_rng(seed)
    a = a + rng.normal(0, 0.012, (h, w, 1)).astype(np.float32)
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


# --- printing the page onto the photo -------------------------------------------------------

def _content_quad(quad: Quad) -> Quad:
    """Where our page goes inside the photo's page: same proportions as our page, centred,
    a little inside the paper edge."""
    pw, ph = size_of(quad)
    aspect = pw / max(ph, 1)
    u0, u1, v0, v1 = 0.03, 0.97, 0.025, 0.975
    if aspect > PAGE_ASPECT:      # photo page is wider: narrow ours
        span = (u1 - u0) * PAGE_ASPECT / aspect
        u0, u1 = 0.5 - span / 2, 0.5 + span / 2
    else:                         # photo page is taller: shorten ours, a bit above centre
        span = (v1 - v0) * aspect / PAGE_ASPECT
        v0 = 0.47 - span / 2
        v1 = v0 + span
    unit = homography([(0, 0), (1, 0), (1, 1), (0, 1)], quad)
    return [apply(unit, u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]


def _warp(src: Image.Image, h_page_to_frame: np.ndarray, box: tuple[int, int, int, int],
          fill, ss: int = 2) -> Image.Image:
    """Warp a page-sized image into the frame region `box`, supersampled for clean text."""
    x0, y0, x1, y1 = box
    # PIL wants the inverse map (output pixel -> input pixel) for the output region at ss x.
    scale = np.array([[1 / ss, 0, x0], [0, 1 / ss, y0], [0, 0, 1]], float)
    inv = np.linalg.inv(h_page_to_frame) @ scale
    inv = inv / inv[2, 2]
    coeffs = tuple(inv.flatten()[:8])
    out = src.transform(((x1 - x0) * ss, (y1 - y0) * ss), Image.Transform.PERSPECTIVE, coeffs,
                        Image.Resampling.BICUBIC, fillcolor=fill)
    return out.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)


def _paperness(plate: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """1 where the photo shows the page's paper, 0 on fingers, thumbs or objects over it,
    so ink is never printed onto a thumb."""
    img = Image.fromarray(plate.astype(np.uint8))
    hsv = np.asarray(img.convert("HSV"), np.float32)
    s, v = hsv[..., 1], hsv[..., 2]
    inside = mask > 0.5
    if not inside.any():
        return np.zeros(mask.shape, np.float32)
    s_ref = float(np.median(s[inside]))
    v_local = np.asarray(Image.fromarray(v.astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(40)), np.float32)
    p = np.clip(1 - (s - s_ref - 22) / 20, 0, 1) * np.clip((v / np.maximum(v_local, 1) - 0.45)
                                                          / 0.15, 0, 1)
    p = np.asarray(Image.fromarray((p * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(1.5)), np.float32) / 255
    return p


def _ink(page_rgb: np.ndarray, paper: np.ndarray) -> np.ndarray:
    """How much each pixel of our page darkens our paper (1 = no ink)."""
    return np.clip(page_rgb / np.maximum(paper, 1), 0, 1)


def compose_on_plate(brand: Brand, plate: Plate, page: RenderedPage,
                     seed: int = 0) -> Composition:
    paper = np.asarray(paper_texture(brand, PAGE_W, PAGE_H, seed).convert("RGB"), np.float32)
    content = _content_quad(plate.quad)
    h = homography([(0, 0), (PAGE_W, 0), (PAGE_W, PAGE_H), (0, PAGE_H)], content)
    xs, ys = [p[0] for p in content], [p[1] for p in content]
    box = (max(0, int(min(xs)) - 2), max(0, int(min(ys)) - 2),
           min(FRAME_W, int(max(xs)) + 3), min(FRAME_H, int(max(ys)) + 3))
    x0, y0, x1, y1 = box

    def warped_factor(page_rgb: np.ndarray) -> np.ndarray:
        f = (_ink(page_rgb, paper) * 255).astype(np.uint8)
        return np.asarray(_warp(Image.fromarray(f), h, box, (255, 255, 255)), np.float32) / 255

    mask = np.asarray(_warp(Image.new("L", (PAGE_W, PAGE_H), 255), h, box, 0),
                      np.float32) / 255
    photo = np.asarray(plate.image, np.float32)
    region = photo[y0:y1, x0:x1]
    weight = (_paperness(region, mask) * mask)[..., None]

    def printed(factor: np.ndarray) -> np.ndarray:
        return 1 - (1 - factor) * weight  # ink only on paper, faded at the page edge

    base_page = page.base if page.base is not None and page.reveal else page.image
    f_base = printed(warped_factor(np.asarray(base_page.convert("RGB"), np.float32)))
    base = photo.copy()
    base[y0:y1, x0:x1] = region * f_base

    layers, still = [], base.copy()
    for rv in (page.reveal if page.base is not None else []):
        canvas = Image.fromarray(paper.astype(np.uint8)).convert("RGBA")
        canvas.alpha_composite(rv.image, (rv.x, rv.y))
        f = printed(warped_factor(np.asarray(canvas.convert("RGB"), np.float32)))
        ys_, xs_ = np.nonzero((f < 0.995).any(axis=2))
        if not len(xs_):
            continue
        ly0, ly1, lx0, lx1 = ys_.min(), ys_.max() + 1, xs_.min(), xs_.max() + 1
        fl = f[ly0:ly1, lx0:lx1]
        under = base[y0 + ly0:y0 + ly1, x0 + lx0:x0 + lx1]
        # At rest the frame must equal photo x ink: alpha = ink strength, rgb solves for it.
        alpha = (1 - fl).max(axis=2, keepdims=True)
        safe = np.maximum(alpha, 1e-3)
        rgb = under * (1 - (1 - fl) / safe)
        rgb = np.where(alpha > 0.004, rgb, under)
        layers.append(FrameLayer(rgb.astype(np.float32), np.where(alpha > 0.004, alpha, 0),
                                 x0 + lx0, y0 + ly0, rv.order))
        still[y0 + ly0:y0 + ly1, x0 + lx0:x0 + lx1] *= fl

    def to_frame(b: Box) -> Box:
        pts = [apply(h, x, y) for x, y in ((b.x0, b.y0), (b.x1, b.y0), (b.x0, b.y1),
                                            (b.x1, b.y1))]
        return Box(min(p[0] for p in pts), min(p[1] for p in pts),
                   max(p[0] for p in pts), max(p[1] for p in pts))

    def img(a: np.ndarray) -> Image.Image:
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    return Composition(img(still), img(base) if layers else img(still), layers,
                       [to_frame(b) for b in page.hook_boxes],
                       [to_frame(b) for b in page.text_boxes],
                       page_box=Box(*[float(v) for v in box]), style="plate",
                       page_quad=[tuple(q) for q in plate.quad])


def prepare(id: str, photo: Image.Image, quad: Quad, credit: str = "",
            seed: int = 0) -> Plate | None:
    """A usable plate from a photo and its page corners (None if it cannot be framed)."""
    framed = fit_to_frame(photo, quad)
    if framed is None:
        return None
    image, fq = framed
    return Plate(id, grade(image, seed), fq, credit)
