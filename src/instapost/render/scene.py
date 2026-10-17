"""Places the rendered book page into the background scene (1080x1920 frame)."""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from instapost.brand import Brand
from instapost.render.page import PAGE_H, PAGE_W, RenderedPage, hex_rgb, paper_texture
from instapost.render.text import Box

FRAME_W, FRAME_H = 1080, 1920
PAGE_X, PAGE_Y = 78, 190
TILT_DEG = -1.2


def _background(scene: Image.Image) -> Image.Image:
    bg = ImageOps.fit(scene.convert("RGB"), (FRAME_W, FRAME_H), Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(5))
    bg = ImageEnhance.Brightness(bg).enhance(0.82)
    # Vignette pulls the eye to the page.
    yy, xx = np.mgrid[0:FRAME_H, 0:FRAME_W].astype(np.float32)
    r = np.sqrt(((xx / FRAME_W - 0.5) * 1.2) ** 2 + ((yy / FRAME_H - 0.5) * 1.0) ** 2)
    v = np.clip(1.15 - r * 0.9, 0.55, 1.0)
    return Image.fromarray((np.asarray(bg, np.float32) * v[..., None]).astype(np.uint8))


def _book_layer(brand: Brand, page: RenderedPage, seed: int) -> Image.Image:
    """Right-hand page plus a sliver of the facing page, page edges and a soft shadow."""
    layer = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    edge = hex_rgb(brand.colors.paper_edge)

    # Shadow.
    shadow = Image.new("L", (FRAME_W, FRAME_H), 0)
    ImageDraw.Draw(shadow).rectangle(
        [PAGE_X - 90, PAGE_Y + 30, PAGE_X + PAGE_W + 30, PAGE_Y + PAGE_H + 50], fill=170)
    shadow = shadow.filter(ImageFilter.GaussianBlur(35))
    layer.paste((20, 12, 6, 255), (0, 0), shadow)

    # Stacked page edges under the page.
    d = ImageDraw.Draw(layer)
    for i in range(6, 0, -1):
        shade = tuple(max(0, c - 8 * i) for c in edge)
        d.rectangle([PAGE_X - 60, PAGE_Y + 6, PAGE_X + PAGE_W + i * 2, PAGE_Y + PAGE_H + i * 3],
                    fill=(*shade, 255))

    # Facing (left) page sliver with a dark gutter.
    facing = paper_texture(brand, 160, PAGE_H, seed + 1).convert("RGBA")
    g = np.linspace(1.0, 0.62, 160, dtype=np.float32)
    arr = np.asarray(facing, np.float32)
    arr[..., :3] *= g[None, :, None]
    layer.alpha_composite(Image.fromarray(arr.astype(np.uint8)), (PAGE_X - 150, PAGE_Y))

    layer.alpha_composite(page.image, (PAGE_X, PAGE_Y))
    return layer.rotate(TILT_DEG, resample=Image.Resampling.BICUBIC,
                        center=(FRAME_W / 2, FRAME_H / 2))


def _rotate_box(box: Box) -> Box:
    """Page-space box -> frame-space box after the book layer's tilt."""
    a = math.radians(TILT_DEG)
    cx, cy = FRAME_W / 2, FRAME_H / 2
    pts = []
    for x, y in ((box.x0, box.y0), (box.x1, box.y0), (box.x0, box.y1), (box.x1, box.y1)):
        x, y = x + PAGE_X - cx, y + PAGE_Y - cy
        # PIL rotates counter-clockwise for positive angles in image coordinates (y down).
        pts.append((cx + x * math.cos(a) + y * math.sin(a), cy - x * math.sin(a) + y * math.cos(a)))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return Box(min(xs), min(ys), max(xs), max(ys))


def compose(brand: Brand, scene: Image.Image, page: RenderedPage,
            seed: int = 0) -> tuple[Image.Image, list[Box], list[Box]]:
    """Return the still frame plus hook and text boxes in frame coordinates."""
    frame = _background(scene).convert("RGBA")
    frame.alpha_composite(_book_layer(brand, page, seed))
    return (frame.convert("RGB"), [_rotate_box(b) for b in page.hook_boxes],
            [_rotate_box(b) for b in page.text_boxes])
