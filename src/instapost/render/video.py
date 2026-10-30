"""Turns the composed page into a seamlessly looping reel with subtle motion.

Motion: slow push-in and back (cosine, so the last frame meets the first), a warm diya-like
flicker, and a few floating dust specks on looping paths. Every periodic term uses a whole
number of cycles over the duration, which is what makes the loop seamless.

Text reveal: the hook is there from the first frame (it has to stop the scroll); the body
lines, divider and verse then rise in one after another, and fade out together in the last
half second, so the last frame equals the first and the loop has no seam.
"""

import math
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from instapost.brand import Brand
from instapost.render import audio
from instapost.render.page import hex_rgb
from instapost.render.scene import (
    FRAME_H,
    FRAME_W,
    PAGE_H,
    PAGE_W,
    PAGE_X,
    PAGE_Y,
    Composition,
)

FPS = 30
ZOOM_AMPLITUDE = 0.035


def zoom_at(t: float, duration: float) -> float:
    return 1 + ZOOM_AMPLITUDE * (1 - math.cos(2 * math.pi * t / duration)) / 2


def _glow(brand: Brand) -> np.ndarray:
    gx, gy = brand.glow_position
    yy, xx = np.mgrid[0:FRAME_H, 0:FRAME_W].astype(np.float32)
    d2 = (((xx - gx * FRAME_W) / (FRAME_W * 0.45)) ** 2
          + ((yy - gy * FRAME_H) / (FRAME_H * 0.3)) ** 2)
    color = np.array(hex_rgb(brand.colors.glow), np.float32) / 255
    return np.exp(-d2)[..., None] * color[None, None, :] * 38


def _dust(seed: int, duration: float, page=None, count: int = 26):
    rng = np.random.default_rng(seed)
    specks = []
    if page is None:  # the classic layout's page
        page = (PAGE_X - 160, PAGE_Y, PAGE_X + PAGE_W + 20, PAGE_Y + PAGE_H + 40)
    while len(specks) < count:
        x, y = rng.uniform(0, FRAME_W), rng.uniform(0, FRAME_H)
        inside_page = page[0] < x < page[2] and page[1] < y < page[3]
        if not inside_page:
            specks.append((x, y, rng.uniform(15, 60), int(rng.integers(1, 3)),
                           rng.uniform(0, 2 * math.pi), rng.uniform(0.25, 0.7)))
    return specks


def _sprite(radius: int = 6) -> np.ndarray:
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1].astype(np.float32)
    return np.exp(-(xx ** 2 + yy ** 2) / (radius * 0.6) ** 2)


REVEAL_START = 0.45   # s: first body line starts appearing
REVEAL_STEP = 0.30    # s between lines
REVEAL_FADE = 0.50    # s for one line to fade in
REVEAL_OUT = 0.45     # s: everything fades out at the end of the loop
REVEAL_RISE = 14      # px the line rises while it appears


def _smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def reveal_parts(order: int, count: int, t: float, duration: float
                 ) -> tuple[float, float, float]:
    """(written 0..1, still there 0..1, rise in px) of reveal layer `order` at time t."""
    step = REVEAL_STEP
    if count > 1:  # all lines are in by 40% of the reel, however many there are
        step = min(step, max(0.12, (0.4 * duration - REVEAL_START) / (count - 1)))
    a_in = _smooth((t - REVEAL_START - order * step) / REVEAL_FADE)
    a_out = _smooth((duration - t) / REVEAL_OUT)
    return a_in, a_out, (1 - a_in) * REVEAL_RISE


def reveal_state(order: int, count: int, t: float, duration: float) -> tuple[float, float]:
    """(opacity 0..1, rise in px) of reveal layer `order` at time t."""
    a_in, a_out, rise = reveal_parts(order, count, t, duration)
    return a_in * a_out, rise


WRITE_SOFT = 0.28   # width of the soft edge of the "being written" wipe (share of the line)

# Book photos only: a hand holding the book. Every term has a whole number of cycles over the
# reel, so the last frame meets the first.
SWAY_PX = 6.0       # largest drift, px
SWAY_DEG = 0.25     # largest tilt
PLATE_ZOOM = 1.03   # a little zoom so the drift never shows the frame edge
FOCUS_BLUR = 3.0    # background softness at the loop point (the page stays sharp)
FOCUS_IN = 0.8      # s to sharpen after the loop point
LIGHT_GAIN = 16.0   # warm light band sweeping across the page once per loop


def sway_at(t: float, duration: float) -> tuple[float, float, float]:
    """(dx, dy, degrees) of the hand-held drift at time t."""
    ph = 2 * math.pi * t / duration
    dx = SWAY_PX * (0.6 * math.sin(ph + 0.3) + 0.4 * math.sin(2 * ph + 1.7))
    dy = SWAY_PX * 0.8 * (0.55 * math.sin(ph + 2.1) + 0.45 * math.sin(3 * ph + 0.4))
    rot = SWAY_DEG * (0.7 * math.sin(ph + 1.1) + 0.3 * math.sin(2 * ph + 2.6))
    return dx, dy, rot


def focus_at(t: float, duration: float) -> float:
    """1 = background softest (at the loop point), 0 = as photographed."""
    return max(1 - _smooth(t / FOCUS_IN), 1 - _smooth((duration - t) / REVEAL_OUT))


class Motion:
    """Precomputed motion layers for one reel; `frame_at(t)` renders any moment."""

    def __init__(self, still: Image.Image, brand: Brand, duration: float, seed: int = 0,
                 comp: Composition | None = None):
        self.still = still  # everything visible (cover)
        self.duration = duration
        photo = comp is not None and comp.style == "plate"
        # A real photo has its own light: a softer glow and fewer specks, never on the page.
        self.glow = _glow(brand) * (0.45 if photo else 1.0)
        # Specks never cross the page (book photo) or the text (full-screen cards).
        box = comp.page_box if comp is not None and comp.page_box else None
        card = comp is not None and comp.style == "card"
        self.specks = _dust(seed, duration,
                            (box.x0 - 20, box.y0 - 20, box.x1 + 20, box.y1 + 20) if box else None,
                            count=14 if photo or card else 26)
        self.sprite = _sprite()
        self.particles = (_Particles(comp.particles, seed, box)
                          if comp is not None and comp.particles else None)
        self.layers = comp.layers if comp else []
        self.base = np.asarray(comp.base if comp and comp.layers else still, np.float32)
        self.photo = photo
        self.ramps = [np.clip(np.linspace(0, 1, layer.alpha.shape[1], dtype=np.float32),
                              0, 1)[None, :, None] for layer in self.layers]
        self.bg_blur = self.bg_mask = self.light = None
        if photo and box is not None:
            # Background softened once; blended in near the loop point outside the page.
            blurred = Image.fromarray(np.clip(self.base, 0, 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(FOCUS_BLUR))
            self.bg_blur = np.asarray(blurred, np.float32)
            # The page's real outline (not its bounding box), so no rectangle ever shows.
            page = Image.new("L", (FRAME_W, FRAME_H), 0)
            if comp.page_quad:
                ImageDraw.Draw(page).polygon([tuple(q) for q in comp.page_quad], fill=255)
            else:
                ImageDraw.Draw(page).rectangle([box.x0, box.y0, box.x1, box.y1], fill=255)
            soft = np.asarray(page.filter(ImageFilter.GaussianBlur(10)), np.float32) / 255
            self.bg_mask = (1 - soft)[..., None]
            self.light = _light_band(box, soft)

    def _page_at(self, t: float) -> Image.Image:
        if not self.layers and self.bg_blur is None:
            return self.still
        arr = self.base.copy() if self.layers else np.asarray(self.still, np.float32).copy()
        n = len(self.layers)
        for layer, ramp in zip(self.layers, self.ramps, strict=True):
            a_in, a_out, rise = reveal_parts(layer.order, n, t, self.duration)
            if a_in * a_out <= 0.003:
                continue
            # Written left to right with a soft edge, rising a little into place.
            written = np.clip((a_in * (1 + WRITE_SOFT) - ramp) / WRITE_SOFT, 0, 1)
            h, w = layer.alpha.shape[:2]
            y0 = layer.y + int(round(rise))
            y1, x1 = min(FRAME_H, y0 + h), min(FRAME_W, layer.x + w)
            region = arr[y0:y1, layer.x:x1]
            k = layer.alpha[:y1 - y0, :x1 - layer.x] * written[:, :x1 - layer.x] * a_out
            region += (layer.rgb[:y1 - y0, :x1 - layer.x] - region) * k
        if self.bg_blur is not None:
            f = focus_at(t, self.duration)
            if f > 0.003:
                arr += (self.bg_blur - arr) * self.bg_mask * f
            self.light(arr, t / self.duration)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    def frame_at(self, t: float) -> np.ndarray:
        z = zoom_at(t, self.duration)
        if self.photo:
            dx, dy, rot = sway_at(t, self.duration)
            img = _handheld(self._page_at(t), z * PLATE_ZOOM, dx, dy, rot)
        else:
            cw, ch = FRAME_W / z, FRAME_H / z
            left, top = (FRAME_W - cw) / 2, (FRAME_H - ch) / 2
            img = self._page_at(t).resize((FRAME_W, FRAME_H), Image.Resampling.BICUBIC,
                                          box=(left, top, left + cw, top + ch))
        arr = np.asarray(img, np.float32).copy()
        phase = 2 * math.pi * t / self.duration
        flicker = 0.6 + 0.22 * math.sin(3 * phase) + 0.12 * math.sin(7 * phase + 1.3)
        arr += self.glow * flicker
        if self.particles is not None:
            self.particles.draw(arr, t / self.duration)
            return np.clip(arr, 0, 255).astype(np.uint8)
        r = self.sprite.shape[0] // 2
        for x, y, radius, k, phi, bright in self.specks:
            px = int(x + radius * math.cos(k * phase + phi))
            py = int(y + radius * math.sin(k * phase + phi) - 0.5 * radius * math.sin(phase))
            if r <= px < FRAME_W - r and r <= py < FRAME_H - r:
                arr[py - r:py + r + 1, px - r:px + r + 1] += (
                    self.sprite[..., None] * 255 * bright * 0.5)
        return np.clip(arr, 0, 255).astype(np.uint8)

    def frames(self) -> Iterator[np.ndarray]:
        for i in range(int(round(self.duration * FPS))):
            yield self.frame_at(i / FPS)


class _Particles:
    """Embers that rise or petals that fall across the whole frame and wrap around, a whole
    number of times per loop (so the reel still loops without a seam). They dim over the text
    so it stays readable."""

    def __init__(self, kind: str, seed: int, text_box=None):
        rng = np.random.default_rng(seed + 11)
        n = 22 if kind == "embers" else 16
        self.rise = kind == "embers"
        self.x = rng.uniform(0, FRAME_W, n)
        self.y = rng.uniform(0, FRAME_H, n)
        self.laps = rng.integers(1, 3, n)  # 1 or 2 trips across the frame per loop
        self.sway = rng.uniform(10, 40, n)
        self.sway_k = rng.integers(1, 4, n)
        self.phase = rng.uniform(0, 2 * math.pi, n)
        self.bright = rng.uniform(0.35, 0.9, n)
        self.colour = (np.array([255, 170, 80], np.float32) if self.rise
                       else np.array([255, 150, 30], np.float32))
        self.sprites = [_sprite(r) for r in ((3, 4, 5) if self.rise else (9, 11, 13))]
        self.size = rng.integers(0, 3, n)
        self.box = text_box

    def draw(self, arr: np.ndarray, u: float) -> None:
        travel = self.laps * (FRAME_H + 40) * u * (-1 if self.rise else 1)
        ys = (self.y + travel) % (FRAME_H + 40) - 20
        xs = self.x + self.sway * np.sin(2 * math.pi * self.sway_k * u + self.phase)
        for x, y, b, sz in zip(xs, ys, self.bright, self.size, strict=True):
            sp = self.sprites[sz]
            r = sp.shape[0] // 2
            px, py = int(x), int(y)
            if not (r <= px < FRAME_W - r and r <= py < FRAME_H - r):
                continue
            if self.box is not None and (self.box.x0 - 30 < px < self.box.x1 + 30
                                         and self.box.y0 - 30 < py < self.box.y1 + 30):
                b *= 0.25
            arr[py - r:py + r + 1, px - r:px + r + 1] += sp[..., None] * self.colour * b


def _handheld(img: Image.Image, zoom: float, dx: float, dy: float, rot: float) -> Image.Image:
    """Zoom about the centre, tilt by `rot` degrees and drift by (dx, dy): one resampling."""
    a = math.radians(rot)
    cx, cy = FRAME_W / 2, FRAME_H / 2
    c, s = math.cos(a) / zoom, math.sin(a) / zoom
    # Output pixel -> input pixel (PIL's affine maps that way).
    coeffs = (c, s, cx - c * (cx + dx) - s * (cy + dy),
              -s, c, cy + s * (cx + dx) - c * (cy + dy))
    # Bilinear: at this tiny zoom it looks the same as bicubic and is 2.5x faster.
    return img.transform((FRAME_W, FRAME_H), Image.Transform.AFFINE, coeffs,
                         Image.Resampling.BILINEAR)


def _light_band(box, page: np.ndarray) -> Callable[[np.ndarray, float], None]:
    """Warm window light: a soft diagonal band that glides across the page once per loop,
    starting and ending off the page (so the loop has no seam)."""
    x0, y0, x1, y1 = (int(v) for v in (box.x0, box.y0, box.x1, box.y1))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(FRAME_W, x1), min(FRAME_H, y1)
    inside = page[y0:y1, x0:x1]  # light only falls on the paper
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    diag = ((xx - x0) / max(x1 - x0, 1) * 0.7 + (yy - y0) / max(y1 - y0, 1) * 0.3)
    warm = np.array([1.0, 0.86, 0.62], np.float32) * LIGHT_GAIN

    def add(arr: np.ndarray, u: float) -> None:
        centre = -0.35 + 1.7 * u            # from before the page to past it
        near = np.abs(diag - centre) < 0.5  # rows/cols far from the band add nothing
        if not near.any():
            return
        band = np.exp(-((diag - centre) / 0.16) ** 2) * near * inside
        arr[y0:y1, x0:x1] += band[..., None] * warm

    return add


def read_time_seconds(*texts: str) -> float:
    """Hindi readers on a phone: about 2.7 words per second."""
    words = sum(len(t.split()) for t in texts if t)
    return words / 2.7


def reel_duration(read_seconds: float) -> float:
    return float(min(12.0, max(7.0, round(read_seconds + 1.5, 1))))


def _mux(video: Path, wav: Path, out: Path) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav),
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
           "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out)]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        raise RuntimeError("ffmpeg failed to add the audio to the reel")


def encode(still: Image.Image, brand: Brand, duration: float, out: Path, seed: int = 0,
           comp: Composition | None = None, music: Path | None = None,
           app_copy: Path | None = None) -> None:
    """Write the reel. With `music`, `out` has the track over the ambience; `app_copy` (if
    given) gets the same picture with only the quiet ambience, for posting in the Instagram
    app with a song from Instagram's own music library."""
    from instapost.render import music as music_mod

    with tempfile.TemporaryDirectory() as tmp:
        silent = Path(tmp) / "video.mp4"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{FRAME_W}x{FRAME_H}", "-r", str(FPS),
            "-i", "-",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-an", str(silent),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        assert proc.stdin is not None
        try:
            for frame in Motion(still, brand, duration, seed, comp).frames():
                proc.stdin.write(frame.tobytes())
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed to encode the reel")

        ambience = Path(tmp) / "ambience.wav"
        audio.write_wav(ambience, audio.ambience(duration, seed))
        soundtrack = ambience
        if music is not None:
            soundtrack = Path(tmp) / "mix.wav"
            music_mod.mix(ambience, music, duration, soundtrack)
        _mux(silent, soundtrack, out)
        if app_copy is not None:
            _mux(silent, ambience, app_copy)


def probe_duration(path: Path) -> float | None:
    """Length of a video file in seconds (None if it cannot be read)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=30, check=True)
        return round(float(out.stdout.strip()), 2)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None
