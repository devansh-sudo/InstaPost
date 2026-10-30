"""Long YouTube videos (landscape, 1920x1080): the videos that build watch hours.

Two kinds, both saved as items of format "long_video" (approval, YouTube upload and numbers
work like the weekly video):

- audio videos: your own long audio (a jaap, a meditation, a katha, a bhajan you have the
  rights to) with a calm moving background, the mantra or line on screen and chapters. The
  picture is a short seamless loop repeated for the whole audio, so a one-hour video takes
  about a minute to make;
- collections: all posts of one theme over a period (for example the month's Gita sutras),
  built like the weekly video. The worker makes these by itself at the start of each month
  for every theme with enough posts.

Long audio files live in state/long_audio/ (upload on the dashboard: Long videos).
"""

import json
import re
import secrets
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy.orm import Session

from instapost.approvals import LONG_VIDEO
from instapost.brand import Brand, load_brand
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, audit
from instapost.render.backdrops import DARK, backdrop
from instapost.render.text import draw_centered, font, wrap_balanced

W, H, FPS = 1920, 1080, 30
LOOP_SECONDS = 12
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus"}
MAX_UPLOAD = 1_500_000_000  # 1.5 GB: about 3 hours of high-quality audio
CHAPTER = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2})\s+(.+?)\s*$")
PILLAR_HI = {"shiv_dhyan": "शिव", "gita": "गीता", "kabir": "कबीर", "jeevan_sutra": "जीवन",
             "life_lesson": "जीवन की सीख"}
MONTHLY_MIN_POSTS = 6  # posts of one theme in a month to make its collection
LOOKS = ("night", "ganga", "dusk", "gold_frame", "mandala", "sunrise", "paper", "manuscript")


# --- long audio library ----------------------------------------------------------------------

def audio_dir() -> Path:
    d = get_settings().state_dir / "long_audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9@._-]+", "-", Path(name).stem).strip("-.")[:60] or "audio"
    return stem + Path(name).suffix.lower()


def audio_path(name: str) -> Path:
    """A file in the library, never anything outside it."""
    root = audio_dir().resolve()
    path = (root / name).resolve()
    if root not in path.parents or path.suffix.lower() not in AUDIO_EXTENSIONS \
            or not path.is_file():
        raise KeyError(name)
    return path


def audio_list() -> list[dict]:
    from instapost.render.video import probe_duration

    out = []
    for f in sorted(audio_dir().iterdir()):
        if f.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        info = _info(f)
        if "duration" not in info:
            info["duration"] = probe_duration(f)
            _save_info(f, info)
        out.append({"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 1), **info})
    return out


def _info(f: Path) -> dict:
    try:
        return json.loads(f.with_suffix(f.suffix + ".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"title": f.stem, "rights": False}


def _save_info(f: Path, info: dict) -> None:
    f.with_suffix(f.suffix + ".json").write_text(json.dumps(info, ensure_ascii=False),
                                                 encoding="utf-8")


async def add_audio(upload, title: str, rights: bool) -> dict:
    """Save an uploaded audio file in pieces (it can be hundreds of MB)."""
    from instapost.render.video import probe_duration

    if not rights:
        raise ValueError("tick the box confirming the audio is yours or licensed for YouTube")
    name = safe_name(getattr(upload, "filename", "") or "audio.mp3")
    if Path(name).suffix not in AUDIO_EXTENSIONS:
        raise ValueError(f"not an audio file; use one of {', '.join(sorted(AUDIO_EXTENSIONS))}")
    target = audio_dir() / name
    n = 1
    while target.exists():
        target = audio_dir() / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    size = 0
    with open(target, "wb") as out:
        while chunk := await upload.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                target.unlink(missing_ok=True)
                raise ValueError("the file is larger than 1.5 GB")
            out.write(chunk)
    seconds = probe_duration(target)
    if not seconds:
        target.unlink(missing_ok=True)
        raise ValueError("that file could not be read as audio")
    info = {"title": (title or Path(name).stem).strip()[:80], "rights": True,
            "duration": seconds, "uploaded": datetime.now(UTC).isoformat()}
    _save_info(target, info)
    return {"name": target.name, **info}


def confirm_rights(name: str) -> None:
    """For files copied into state/long_audio/ by hand: you confirm the rights here."""
    path = audio_path(name)
    info = _info(path)
    info["rights"] = True
    _save_info(path, info)


def remove_audio(name: str) -> None:
    path = audio_path(name)
    path.unlink()
    path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)


# --- chapters and text -----------------------------------------------------------------------

def parse_chapters(text: str, duration: float) -> list[tuple[float, str]]:
    """"00:00 आरंभ" lines -> chapters YouTube accepts (first at 0, at least 3, 10 s apart),
    or [] when they would not be accepted."""
    out = []
    for line in (text or "").splitlines():
        m = CHAPTER.match(line)
        if not m:
            continue
        parts = [int(x) for x in m.group(1).split(":")]
        secs = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
        if secs < duration:
            out.append((float(secs), m.group(2)[:90]))
    out.sort()
    ok = (len(out) >= 3 and out[0][0] == 0
          and all(b[0] - a[0] >= 10 for a, b in zip(out, out[1:], strict=False)))
    return out if ok else []


def ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m:02d}:{s:02d}"


# --- pictures --------------------------------------------------------------------------------

def _colours(look: str) -> tuple[tuple, tuple]:
    dark = look in DARK or (look.startswith("custom-") and _custom_light_text(look))
    return ((255, 244, 228), (232, 214, 190)) if dark else ((43, 36, 32), (90, 78, 69))


def _custom_light_text(look: str) -> bool:
    from instapost.render import custom_looks
    try:
        return custom_looks.get(look)["text"] == "light"
    except KeyError:
        return True


def screen(brand: Brand, look: str, line: str, meaning: str, seed: int) -> Image.Image:
    """The still picture of an audio video: background, the line, its meaning, the page."""
    img = backdrop(brand, look, W, H, seed, centre=(0.5, 0.45)).convert("RGBA")
    ink, soft = _colours(look)
    text = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(text)
    draw_centered(d, [brand.header_text], font(brand.font_path("header"), 40), W / 2, 110, soft)
    lf = font(brand.font_path("hook"), 104)
    lines = wrap_balanced(lf, line, W - 360) if line else []
    while len(lines) > 3 and lf.size > 60:
        lf = font(brand.font_path("hook"), lf.size - 8)
        lines = wrap_balanced(lf, line, W - 360)
    mf = font(brand.font_path("body"), 42)
    mlines = wrap_balanced(mf, meaning, W - 520) if meaning else []
    block = len(lines) * lf.size * 1.35 + (40 + len(mlines) * mf.size * 1.5 if mlines else 0)
    y = (H - block) / 2 - 10
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    shade = (0, 0, 0, 170) if ink[0] > 128 else (255, 248, 236, 170)
    draw_centered(sd, lines, lf, W / 2, y, shade, line_gap=1.35)
    text.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    y2, _ = draw_centered(d, lines, lf, W / 2, y, ink, line_gap=1.35)
    if mlines:
        draw_centered(d, mlines, mf, W / 2, y2 + 40, soft, line_gap=1.5)
    if brand.printed_handle:
        draw_centered(d, [brand.printed_handle], font(brand.font_path("footer"), 30), W / 2,
                      H - 90, soft)
    img.alpha_composite(text)
    return img.convert("RGB")


def thumbnail(brand: Brand, look: str, title: str, seed: int) -> Image.Image:
    img = backdrop(brand, look, W, H, seed + 1).resize((1280, 720))
    d = ImageDraw.Draw(img)
    ink, soft = _colours(look)
    tf = font(brand.font_path("hook"), 86)
    lines = wrap_balanced(tf, title, 1080)[:3]
    y = (720 - len(lines) * tf.size * 1.3) / 2
    draw_centered(d, lines, tf, 640, y, ink, line_gap=1.3)
    draw_centered(d, [brand.page_name], font(brand.font_path("footer"), 34), 640, 590, soft)
    return img


def _particles_for(look: str) -> str | None:
    if look.startswith("custom-"):
        from instapost.render import custom_looks
        return custom_looks.motion_of(look)
    from instapost.render.templates import PARTICLES
    return PARTICLES.get(look, "embers" if look in DARK else None)


def loop_frames(still: Image.Image, look: str, seed: int):
    """LOOP_SECONDS of gentle motion that ends where it began: the light breathes and embers
    rise (or petals fall)."""
    from instapost.render.video import _Particles

    base = np.asarray(still, np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    glow = np.exp(-(((xx - W / 2) / (W * 0.45)) ** 2 + ((yy - H * 0.45) / (H * 0.5)) ** 2))
    glow = glow[..., None] * np.array([255, 190, 120], np.float32) * 0.06
    kind = _particles_for(look)
    parts = _Particles(kind, seed, size=(W, H)) if kind else None
    n = LOOP_SECONDS * FPS
    for i in range(n):
        u = i / n
        arr = base + glow * (0.5 + 0.5 * np.sin(2 * np.pi * u))
        if parts is not None:
            parts.draw(arr, u)
        yield np.clip(arr, 0, 255).astype(np.uint8)


def _ffmpeg(args: list[str], what: str) -> None:
    out = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True,
                         text=True)
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({what}): {out.stderr.strip()[-300:]}")


def render_audio_video(still: Image.Image, look: str, audio: Path, out: Path,
                       seed: int = 0) -> float:
    """The loop repeated for the whole audio (the picture is copied, not re-encoded, so a
    long video is quick). Returns the length in seconds."""
    from instapost.render.video import probe_duration

    with tempfile.TemporaryDirectory() as tmp:
        loop = Path(tmp) / "loop.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
               "medium", "-crf", "23", "-pix_fmt", "yuv420p", "-g", str(LOOP_SECONDS * FPS),
               "-an", str(loop)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        assert proc.stdin is not None
        try:
            for frame in loop_frames(still, look, seed):
                proc.stdin.write(frame.tobytes())
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed to make the video loop")
        seconds = probe_duration(audio) or 0
        fade = min(3.0, seconds / 10)
        _ffmpeg(["-stream_loop", "-1", "-i", str(loop), "-i", str(audio), "-map", "0:v",
                 "-map", "1:a", "-c:v", "copy", "-af",
                 f"loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d={fade:.1f},"
                 f"afade=t=out:st={max(seconds - fade, 0):.1f}:d={fade:.1f}",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{seconds:.2f}",
                 "-movflags", "+faststart", str(out)], "audio video")
    return probe_duration(out) or seconds


# --- as items --------------------------------------------------------------------------------

def _new_folder(today: date) -> tuple[str, Path]:
    item_id = f"{today.isoformat()}-{LONG_VIDEO}-{secrets.token_hex(3)}"
    return item_id, get_settings().output_dir / today.isoformat() / item_id


def _item(s: Session, item_id: str, folder: Path, pillar: str, meta: dict, checks: list,
          actor: str, for_date: date) -> Item:
    passed = all(c["passed"] for c in checks)
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (folder / "qa.json").write_text(json.dumps({"passed": passed, "checks": checks},
                                               ensure_ascii=False), encoding="utf-8")
    (folder / "caption.txt").write_text(meta["description"] + "\n", encoding="utf-8")
    item = Item(id=item_id, format=LONG_VIDEO, pillar=pillar, folder=str(folder),
                status=ItemStatus.NEEDS_REVIEW if passed else ItemStatus.BLOCKED,
                hook=meta["title"], caption=meta["description"], fact_check="pass",
                for_date=for_date.isoformat(), duration_s=meta.get("duration"),
                qa_summary="; ".join(f"{c['name']}: {c['detail']}" for c in checks
                                     if not c["passed"]),
                features={"pillar": pillar, "template": f"long/{meta['kind']}"})
    s.add(item)
    s.flush()
    audit(s, actor, "created", item.id, status=item.status, kind=meta["kind"],
          minutes=round((meta.get("duration") or 0) / 60, 1))
    return item


def make_audio_video(s: Session, audio_name: str, title: str, line: str = "",
                     meaning: str = "", look: str = "night", pillar: str = "shiv_dhyan",
                     chapters: str = "", about: str = "", actor: str = "agent",
                     for_date: date | None = None) -> Item:
    path = audio_path(audio_name)
    info = _info(path)
    brand = load_brand()
    today = date.today()
    item_id, folder = _new_folder(today)
    folder.mkdir(parents=True, exist_ok=True)
    seed = int(secrets.token_hex(3), 16)
    title = " ".join(title.split())[:100] or info.get("title", "")
    still = screen(brand, look, line.strip(), meaning.strip(), seed)
    still.save(folder / "screen.jpg", quality=90)
    seconds = render_audio_video(still, look, path, folder / "video.mp4", seed)
    thumbnail(brand, look, title, seed).save(folder / "cover.jpg", quality=90)
    marks = parse_chapters(chapters, seconds)
    handle = brand.printed_handle
    lines = [*([about.strip(), ""] if about.strip() else []),
             *([f"{line.strip()}" + (f"\n{meaning.strip()}" if meaning.strip() else ""), ""]
               if line.strip() else []),
             *(["अध्याय:", *[f"{ts(t)} {name}" for t, name in marks], ""] if marks else []),
             f"🎧 ऑडियो: {info.get('title', path.stem)}",
             f"रोज़ नए सूत्र और Shorts{': ' + handle if handle else ''}"]
    tags = [PILLAR_HI.get(pillar, pillar), "jaap", "mantra", "meditation hindi",
            "bhakti", brand.page_name, *[w for w in re.findall(r"\w+", title)[:5]]]
    meta = {"kind": "audio", "title": title, "description": "\n".join(lines), "tags": tags,
            "chapters": [[t, n] for t, n in marks], "duration": seconds, "audio": path.name,
            "look": look}
    checks = [
        {"name": "length", "passed": seconds >= 60,
         "detail": f"{seconds / 60:.1f} min" + ("" if seconds >= 60 else " (at least 1 min)")},
        {"name": "audio_rights", "passed": bool(info.get("rights")),
         "detail": "confirmed when uploaded" if info.get("rights") else "not confirmed"},
        {"name": "chapters", "passed": not chapters.strip() or bool(marks),
         "detail": f"{len(marks)} chapters" if marks else
         ("" if not chapters.strip() else "need 3+ lines, the first at 00:00, 10 s apart")},
    ]
    return _item(s, item_id, folder, pillar, meta, checks, actor, for_date or today)


def make_collection(s: Session, pillar: str, days: int = 30, end_day: date | None = None,
                    actor: str = "agent") -> Item:
    """All published posts of one theme in the period, as one chaptered video."""
    from instapost import weekly

    end_day = end_day or date.today()
    p = weekly.plan(s, end_day, days, pillar=pillar)
    brand = load_brand()
    item_id, folder = _new_folder(date.today())
    meta = weekly.render(p, brand, folder)
    meta["kind"] = "collection"
    checks = json.loads((folder / "qa.json").read_text(encoding="utf-8"))["checks"]
    return _item(s, item_id, folder, pillar, meta, checks, actor, end_day)


def monthly_due(s: Session, today: date) -> list[str]:
    """Themes that get a collection for last month: made on the 1st, once per month."""
    from instapost import weekly
    from instapost.db import get_kv

    if today.day != 1 or get_kv(s, f"collections:{today:%Y-%m}", ""):
        return []
    end = today - timedelta(days=1)
    out = []
    for pillar in PILLAR_HI:
        posts = [i for i in weekly.week_posts(s, end, end.day, pillar=pillar)
                 if i.format != "story_line"]
        if len(posts) >= MONTHLY_MIN_POSTS:
            out.append(pillar)
    return out

