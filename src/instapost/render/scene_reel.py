"""Scene reels: painted pictures with a slow camera move, crossfades between them, each line
as a subtitle with the speaker's name, the voices over soft music, and an end card with the
verse and a question.

Timeline: title (on the first picture) -> one line after another, each as long as its voice
(or its reading time when there is no voice) -> end card. Text stays inside Instagram's safe
area and away from the buttons on the right.
"""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from instapost.brand import Brand
from instapost.render import audio
from instapost.render.page import hex_rgb
from instapost.render.scene import FRAME_H, FRAME_W
from instapost.render.text import draw_centered, font, text_width, wrap_balanced
from instapost.render.video import FPS, _mux

TITLE_S = 2.0        # the hook on the first picture, before anyone speaks
GAP_S = 0.35         # pause after each line
LEAD_S = 0.15        # the voice starts a moment after its subtitle
END_S = 3.2          # end card
FADE_S = 0.5         # crossfade between pictures
SUB_FADE = 0.22
ZOOM = 0.07          # camera push over one picture
READ_WPS = 2.7       # words per second when there is no voice
SUB_BOTTOM = 1480    # subtitle box ends here (Instagram's caption starts below ~1536)
SUB_X0, SUB_X1 = 70, 930  # clear of the like/comment/share buttons on the right


@dataclass
class Line:
    speaker: str                 # name shown on the chip, e.g. "शिव"
    text: str
    picture: int
    voice: np.ndarray | None = None  # mono float32 at `voice_sr`
    voice_sr: int = 24000


@dataclass
class Timed:
    line: Line
    start: float
    end: float


def timeline(lines: list[Line]) -> tuple[list[Timed], float, float]:
    """(timed lines, when the end card starts, total length)."""
    t = TITLE_S
    out = []
    for ln in lines:
        if ln.voice is not None and len(ln.voice):
            dur = len(ln.voice) / ln.voice_sr + LEAD_S
        else:
            dur = len(ln.text.split()) / READ_WPS + 0.4
        out.append(Timed(ln, t, t + dur))
        t += dur + GAP_S
    return out, t, t + END_S


def _fit(pic: Image.Image) -> Image.Image:
    return ImageOps.fit(pic.convert("RGB"), (FRAME_W, FRAME_H), Image.Resampling.LANCZOS)


def _camera(img: Image.Image, u: float, k: int) -> Image.Image:
    """Slow push in (or out, every other picture) with a little drift; u = 0..1."""
    u = u * u * (3 - 2 * u)  # ease
    z = 1.02 + ZOOM * (u if k % 2 == 0 else 1 - u)
    dx = (1 if k % 3 else -1) * 18 * (u - 0.5)
    dy = -14 * (u - 0.5)
    cx, cy = FRAME_W / 2, FRAME_H / 2
    c = 1 / z
    coeffs = (c, 0, cx - c * (cx + dx), 0, c, cy - c * (cy + dy))
    return img.transform((FRAME_W, FRAME_H), Image.Transform.AFFINE, coeffs,
                         Image.Resampling.BILINEAR)


def _bottom_shade() -> np.ndarray:
    """(h, 1, 1): the lower part of every picture a little darker, so subtitles read on any
    painting."""
    y = np.linspace(0, 1, FRAME_H, dtype=np.float32)
    return (1 - 0.38 * np.clip((y - 0.55) / 0.45, 0, 1))[:, None, None]


def _subtitle(brand: Brand, ln: Line) -> tuple[Image.Image, int]:
    """RGBA subtitle panel and its top y in the frame."""
    w = SUB_X1 - SUB_X0
    f = font(brand.font_path("hook"), 50)
    lines = wrap_balanced(f, ln.text, w - 80)
    while len(lines) > 3 and f.size > 34:
        f = font(brand.font_path("hook"), f.size - 4)
        lines = wrap_balanced(f, ln.text, w - 80)
    chip_f = font(brand.font_path("footer"), 30)
    text_h = len(lines) * f.size * 1.45
    h = int(text_h + 120)
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle([0, 24, w, h], radius=26, fill=(16, 11, 8, 165))
    # Speaker chip on the panel's top edge.
    name = ln.speaker
    cw = text_width(chip_f, name) + 44
    accent = hex_rgb(brand.colors.highlight)
    d.rounded_rectangle([36, 0, 36 + cw, 50], radius=25, fill=(*accent, 255))
    d.text((58, 6), name, font=chip_f, fill=(40, 24, 14), language="hi")
    draw_centered(d, lines, f, w / 2, 76, (255, 244, 228), line_gap=1.45)
    return panel, SUB_BOTTOM - h


def _title_overlay(brand: Brand, title: str, kicker: str) -> Image.Image:
    layer = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = font(brand.font_path("hook"), 70)
    lines = wrap_balanced(f, title, 860)
    y0 = 420
    k = font(brand.font_path("header"), 36)
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    draw_centered(sd, [kicker], k, FRAME_W / 2, y0 - 70, (0, 0, 0, 200))
    draw_centered(sd, lines, f, FRAME_W / 2, y0, (0, 0, 0, 220), line_gap=1.4)
    layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(9)))
    draw_centered(d, [kicker], k, FRAME_W / 2, y0 - 70, (*hex_rgb(brand.colors.highlight), 255))
    draw_centered(d, lines, f, FRAME_W / 2, y0, (255, 246, 232, 255), line_gap=1.4)
    return layer


def end_card(brand: Brand, last: Image.Image, verse: str | None, label: str | None,
             question: str) -> Image.Image:
    """The last picture, blurred and darkened, with the verse and the question."""
    bg = _fit(last).filter(ImageFilter.GaussianBlur(26))
    bg = ImageEnhance.Brightness(bg).enhance(0.42)
    d = ImageDraw.Draw(bg)
    cream, soft = (255, 244, 228), (235, 214, 190)
    accent = hex_rgb(brand.colors.highlight)
    y = 470
    if verse:
        vf = font(brand.font_path("hook"), 58)
        vlines = wrap_balanced(vf, verse.replace("। ", "।\n"), 860)
        y, _ = draw_centered(d, vlines, vf, FRAME_W / 2, y, cream, line_gap=1.5)
        if label:
            lf = font(brand.font_path("reference"), 34)
            y, _ = draw_centered(d, [f"— {label}"], lf, FRAME_W / 2, y + 18, soft)
        y += 70
        d.rounded_rectangle([FRAME_W / 2 - 46, y, FRAME_W / 2 + 46, y + 8], radius=4, fill=accent)
        y += 70
    qf = font(brand.font_path("body"), 48)
    qlines = wrap_balanced(qf, question, 860)
    y, _ = draw_centered(d, qlines, qf, FRAME_W / 2, y, cream, line_gap=1.45)
    hf = font(brand.font_path("footer"), 30)
    draw_centered(d, [brand.printed_handle], hf, FRAME_W / 2, max(y + 60, 1380), soft)
    return bg


class SceneMotion:
    """All the pieces of one scene reel; `frame_at(t)` renders any moment."""

    def __init__(self, brand: Brand, pictures: list[Image.Image], lines: list[Line],
                 title: str, kicker: str, verse: str | None, label: str | None,
                 question: str):
        self.pictures = [_fit(p) for p in pictures]
        self.timed, self.end_start, self.duration = timeline(lines)
        # When each picture appears: the first at 0, the others with their first line.
        starts = {0: 0.0}
        for tl in self.timed:
            starts.setdefault(tl.line.picture, tl.start)
        self.spans = sorted((t, i) for i, t in starts.items())
        self.title = _title_overlay(brand, title, kicker)
        self.subs = [_subtitle(brand, tl.line) for tl in self.timed]
        self.end = end_card(brand, self.pictures[self.spans[-1][1]], verse, label, question)
        self.end_arr = np.asarray(self.end, np.float32)
        self.shade = _bottom_shade()

    def _shot(self, n: int, t: float) -> np.ndarray:
        """Picture span n at time t, with its camera move."""
        start, idx = self.spans[n]
        stop = self.spans[n + 1][0] if n + 1 < len(self.spans) else self.end_start
        u = min(1.0, max(0.0, (t - start) / max(stop - start + FADE_S, 0.1)))
        return np.asarray(_camera(self.pictures[idx], u, n), np.float32)

    def _picture_at(self, t: float) -> np.ndarray:
        n = max(i for i, (start, _) in enumerate(self.spans) if t >= start or i == 0)
        arr = self._shot(n, t)
        since = t - self.spans[n][0]
        if n > 0 and since < FADE_S:  # crossfade from the previous picture
            prev = self._shot(n - 1, t)
            arr = prev + (arr - prev) * (since / FADE_S)
        return arr * self.shade

    def frame_at(self, t: float) -> np.ndarray:
        if t >= self.end_start + FADE_S:
            return self.end_arr.astype(np.uint8)
        arr = self._picture_at(t)
        if t < TITLE_S:
            a = min(1.0, t / 0.3, (TITLE_S - t) / 0.35)
            arr = _over(arr, self.title, 0, 0, a)
        for tl, (panel, top) in zip(self.timed, self.subs, strict=True):
            if tl.start - SUB_FADE < t < tl.end + SUB_FADE:
                a = min(1.0, (t - tl.start + SUB_FADE) / SUB_FADE,
                        (tl.end + SUB_FADE - t) / SUB_FADE)
                arr = _over(arr, panel, SUB_X0, top, max(0.0, a))
        if t >= self.end_start:
            a = (t - self.end_start) / FADE_S
            arr = arr + (self.end_arr - arr) * a
        return np.clip(arr, 0, 255).astype(np.uint8)

    def frames(self):
        for i in range(int(round(self.duration * FPS))):
            yield self.frame_at(i / FPS)

    def cover(self) -> Image.Image:
        return Image.fromarray(self.frame_at(TITLE_S * 0.6))


def _over(arr: np.ndarray, layer: Image.Image, x: int, y: int, alpha: float) -> np.ndarray:
    if alpha <= 0.003:
        return arr
    lay = np.asarray(layer, np.float32)
    h, w = lay.shape[:2]
    region = arr[y:y + h, x:x + w]
    k = lay[..., 3:] / 255 * alpha
    arr[y:y + h, x:x + w] = region + (lay[..., :3] - region) * k
    return arr


def voice_track(timed: list[Timed], duration: float) -> np.ndarray:
    """Stereo float32 at audio.SR with every line at its place."""
    out = np.zeros((int(round(duration * audio.SR)), 2), np.float32)
    for tl in timed:
        v = tl.line.voice
        if v is None or not len(v):
            continue
        n = int(len(v) * audio.SR / tl.line.voice_sr)
        res = np.interp(np.linspace(0, len(v) - 1, n), np.arange(len(v)), v).astype(np.float32)
        peak = float(np.abs(res).max()) or 1.0
        res *= 0.8 / peak
        s = int((tl.start + LEAD_S) * audio.SR)
        e = min(len(out), s + n)
        out[s:e] += res[:e - s, None]
    return out


def mix(voices_wav: Path, bed: Path | None, duration: float, out_wav: Path,
        ambience_wav: Path) -> None:
    """Voices on top; the music (or the soft tanpura ambience) underneath, ducked while
    someone speaks."""
    from instapost.render.music import start_offset

    if bed is None:
        graph = ("[1:a]volume=0.5[b];[0:a][b]amix=inputs=2:duration=first:normalize=0,"
                 f"atrim=duration={duration}[out]")
        inputs = ["-i", str(voices_wav), "-i", str(ambience_wav)]
    else:
        fade_out = min(1.5, duration / 4)
        graph = (
            f"[1:a]atrim=start={start_offset(bed)}:duration={duration},asetpts=N/SR/TB,"
            "aformat=sample_rates=44100:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11,"
            f"volume=0.32,afade=t=in:d=0.6,afade=t=out:st={duration - fade_out:.2f}:"
            f"d={fade_out:.2f}[m];"
            "[0:a]asplit=2[v][sc];"
            "[m][sc]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=400[duck];"
            f"[v][duck]amix=inputs=2:duration=first:normalize=0,atrim=duration={duration}[out]")
        inputs = ["-i", str(voices_wav), "-stream_loop", "-1", "-i", str(bed)]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph,
           "-map", "[out]", "-ar", "44100", "-ac", "2", str(out_wav)]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        raise RuntimeError("could not mix the voices and the music")


def encode(motion: SceneMotion, out: Path, music: Path | None = None, seed: int = 0) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        silent = Path(tmp) / "video.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{FRAME_W}x{FRAME_H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
               "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-profile:v", "high",
               "-an", str(silent)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        assert proc.stdin is not None
        try:
            for frame in motion.frames():
                proc.stdin.write(frame.tobytes())
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed to encode the scene reel")
        voices = Path(tmp) / "voices.wav"
        audio.write_wav(voices, voice_track(motion.timed, motion.duration))
        amb = Path(tmp) / "ambience.wav"
        audio.write_wav(amb, audio.ambience(motion.duration, seed) * 0.6)
        mixed = Path(tmp) / "mix.wav"
        mix(voices, music, motion.duration, mixed, amb)
        _mux(silent, mixed, out)


def text_boxes(motion: SceneMotion):
    """Frame-space boxes of all text (for the safe-area check)."""
    from instapost.render.text import Box

    return [Box(SUB_X0, top, SUB_X0 + panel.width, top + panel.height)
            for panel, top in motion.subs]

