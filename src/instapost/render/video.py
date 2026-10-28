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
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from PIL import Image

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


def _dust(seed: int, duration: float):
    rng = np.random.default_rng(seed)
    specks = []
    while len(specks) < 26:
        x, y = rng.uniform(0, FRAME_W), rng.uniform(0, FRAME_H)
        inside_page = PAGE_X - 160 < x < PAGE_X + PAGE_W + 20 and PAGE_Y < y < PAGE_Y + PAGE_H + 40
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


def reveal_state(order: int, count: int, t: float, duration: float) -> tuple[float, float]:
    """(opacity 0..1, rise in px) of reveal layer `order` at time t."""
    step = REVEAL_STEP
    if count > 1:  # all lines are in by 40% of the reel, however many there are
        step = min(step, max(0.12, (0.4 * duration - REVEAL_START) / (count - 1)))
    a_in = _smooth((t - REVEAL_START - order * step) / REVEAL_FADE)
    a_out = _smooth((duration - t) / REVEAL_OUT)
    return a_in * a_out, (1 - a_in) * REVEAL_RISE


class Motion:
    """Precomputed motion layers for one reel; `frame_at(t)` renders any moment."""

    def __init__(self, still: Image.Image, brand: Brand, duration: float, seed: int = 0,
                 comp: Composition | None = None):
        self.still = still  # everything visible (cover)
        self.duration = duration
        self.glow = _glow(brand)
        self.specks = _dust(seed, duration)
        self.sprite = _sprite()
        self.layers = comp.layers if comp else []
        self.base = np.asarray(comp.base if comp and comp.layers else still, np.float32)

    def _page_at(self, t: float) -> Image.Image:
        if not self.layers:
            return self.still
        arr = self.base.copy()
        n = len(self.layers)
        for layer in self.layers:
            a, rise = reveal_state(layer.order, n, t, self.duration)
            if a <= 0.003:
                continue
            h, w = layer.alpha.shape[:2]
            y0 = layer.y + int(round(rise))
            y1, x1 = min(FRAME_H, y0 + h), min(FRAME_W, layer.x + w)
            region = arr[y0:y1, layer.x:x1]
            k = layer.alpha[:y1 - y0, :x1 - layer.x] * a
            region += (layer.rgb[:y1 - y0, :x1 - layer.x] - region) * k
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    def frame_at(self, t: float) -> np.ndarray:
        z = zoom_at(t, self.duration)
        cw, ch = FRAME_W / z, FRAME_H / z
        left, top = (FRAME_W - cw) / 2, (FRAME_H - ch) / 2
        img = self._page_at(t).resize((FRAME_W, FRAME_H), Image.Resampling.BICUBIC,
                                      box=(left, top, left + cw, top + ch))
        arr = np.asarray(img, np.float32).copy()
        phase = 2 * math.pi * t / self.duration
        flicker = 0.6 + 0.22 * math.sin(3 * phase) + 0.12 * math.sin(7 * phase + 1.3)
        arr += self.glow * flicker
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
