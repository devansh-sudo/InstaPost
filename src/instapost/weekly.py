"""Weekly YouTube video: the week's posts as one long, landscape video (1920x1080).

YouTube pays from 4,000 hours of watch time on long videos; Shorts views count only in very
large numbers. This turns the week's reels, carousels and stories into one 6-9 minute video
for free (no paid calls): each post is one chapter, with the vertical picture in the middle,
its number and theme on the left, and the page name on the right, over one continuous calm
track from the music library.

It is saved as a normal item (format "weekly_video"), so approval, publishing to YouTube,
numbers and the dashboard work the same as for every other post.
"""

import json
import secrets
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost.approvals import WEEKLY_VIDEO
from instapost.brand import Brand, load_brand
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, audit
from instapost.render import audio, music
from instapost.render.text import draw_centered, font, strip_emoji, wrap_balanced
from instapost.schedule import load_schedule, to_local

WEEKLY = WEEKLY_VIDEO
W, H, FPS = 1920, 1080, 30
REEL_SECONDS = 30   # each reel loops until about this long
SLIDE_SECONDS = 8
STORY_SECONDS = 7
INTRO_SECONDS = 6
SECTION_SECONDS = 4
OUTRO_SECONDS = 8
MIN_CHAPTER_POSTS = 3  # YouTube shows chapters only with 3 or more

CREAM, SOFT, GOLD = (250, 244, 232), (214, 204, 188), (242, 199, 154)
MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर",
          "अक्टूबर", "नवंबर", "दिसंबर"]
PILLAR_HI = {"shiv_dhyan": "शिव ध्यान", "gita": "गीता", "kabir": "कबीर",
             "jeevan_sutra": "जीवन सूत्र", "life_lesson": "जीवन की सीख"}
FORMAT_ORDER = {"reel_book_pov": 0, "carousel_shlok": 1, "story_line": 2}


class NotEnoughPosts(RuntimeError):
    pass


@dataclass
class Segment:
    kind: str            # intro, reel, carousel, section, story, outro
    seconds: float
    title: str = ""      # chapter title ("" = no chapter of its own)
    item: Item | None = None
    start: float = 0.0


@dataclass
class Plan:
    start_day: date
    end_day: date
    posts: list[Item]
    segments: list[Segment] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(s.seconds for s in self.segments)


def hindi_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m:02d}:{s:02d}"


# --- what goes in ---------------------------------------------------------------------------

def week_posts(s: Session, end_day: date, days: int = 7) -> list[Item]:
    """Published posts whose day is in the `days` days ending on `end_day`, reels first."""
    start = end_day - timedelta(days=days - 1)
    items = s.scalars(select(Item).where(
        Item.status.in_((ItemStatus.PUBLISHED, ItemStatus.PARTIAL)),
        Item.format.in_(list(FORMAT_ORDER)),
        Item.for_date >= start.isoformat(), Item.for_date <= end_day.isoformat()))
    usable = [i for i in items if _media(i)]
    return sorted(usable, key=lambda i: (FORMAT_ORDER[i.format], i.for_date or "",
                                         i.scheduled_at or i.created_at))


def _media(item: Item) -> Path | list[Path] | None:
    folder = Path(item.folder)
    if item.format == "reel_book_pov":
        for name in ("reel_for_app.mp4", "reel.mp4"):  # no-music copy first: one bed for all
            if (folder / name).exists():
                return folder / name
        return None
    if item.format == "carousel_shlok":
        return sorted(folder.glob("slide_*.jpg")) or None
    return folder / "story.jpg" if (folder / "story.jpg").exists() else None


def plan(s: Session, end_day: date, days: int = 7) -> Plan:
    posts = week_posts(s, end_day, days)
    chaptered = [p for p in posts if p.format != "story_line"]
    if len(chaptered) + (1 if any(p.format == "story_line" for p in posts) else 0) \
            < MIN_CHAPTER_POSTS:
        raise NotEnoughPosts(
            f"only {len(posts)} published post(s) between "
            f"{(end_day - timedelta(days=days - 1)).isoformat()} and {end_day.isoformat()}; "
            f"a weekly video needs at least {MIN_CHAPTER_POSTS} reels or carousels")
    p = Plan(end_day - timedelta(days=days - 1), end_day, posts)
    p.segments.append(Segment("intro", INTRO_SECONDS))
    n = 0
    for item in chaptered:
        n += 1
        if item.format == "reel_book_pov":
            loops = max(1, round(REEL_SECONDS / max(item.duration_s or 11, 1)))
            secs = loops * (item.duration_s or 11)
        else:
            secs = SLIDE_SECONDS * len(_media(item))
        p.segments.append(Segment(item.format, secs, f"सूत्र {n}: {_short(item.hook)}", item))
    stories = [i for i in posts if i.format == "story_line"]
    if stories:
        p.segments.append(Segment("section", SECTION_SECONDS, "छोटी बातें, बड़ी सीख"))
        p.segments += [Segment("story_line", STORY_SECONDS, item=i) for i in stories]
    p.segments.append(Segment("outro", OUTRO_SECONDS))
    t = 0.0
    for seg in p.segments:
        seg.start, t = t, t + seg.seconds
    return p


def _short(text: str, n: int = 60) -> str:
    text = strip_emoji(" ".join(text.split())).rstrip("।.")
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


# --- title, description, chapters ------------------------------------------------------------

def chapters(p: Plan) -> list[tuple[float, str]]:
    """YouTube chapters: the first starts at 00:00 and each lasts 10 s or more."""
    out: list[tuple[float, str]] = []
    titled = [seg for seg in p.segments if seg.title]
    for i, seg in enumerate(titled):
        start = 0.0 if i == 0 else seg.start
        end = titled[i + 1].start if i + 1 < len(titled) else p.duration
        if end - start >= 10 or i == 0:
            out.append((start, seg.title))
    return out


def metadata(p: Plan, brand: Brand) -> dict:
    count = len([i for i in p.posts if i.format != "story_line"])
    pillars = sorted({i.pillar for i in p.posts}, key=lambda x: -sum(
        1 for i in p.posts if i.pillar == x))
    themes = ", ".join(PILLAR_HI.get(x, x) for x in pillars[:3])
    span = f"{hindi_date(p.start_day)} – {hindi_date(p.end_day)}"
    title = f"इस हफ़्ते के {count} जीवन सूत्र | {themes} | {span}"[:100]
    handle = brand.printed_handle
    hashtags: list[str] = ["#जीवनसूत्र", "#हिंदी"]
    for item in p.posts:
        draft = _draft(item)
        for tag in draft.get("hashtags", []):
            tag = "#" + tag.lstrip("#")
            if tag not in hashtags:
                hashtags.append(tag)
    lines = [
        f"इस हफ़्ते के {count} सूत्र एक साथ: {themes}।",
        "शांत संगीत के साथ धीरे-धीरे पढ़िए, और जो बात दिल को छू जाए उसे किसी अपने को भेजिए।",
        "",
        "अध्याय:",
        *[f"{ts(t)} {name}" for t, name in chapters(p)],
        "",
        f"रोज़ नए Shorts और हर रविवार नया वीडियो{': ' + handle if handle else ''}",
        "",
        " ".join(hashtags[:8]),
    ]
    tags = ["जीवन सूत्र", "हिंदी विचार", "spiritual hindi", "motivation hindi",
            *[PILLAR_HI.get(x, x) for x in pillars], brand.page_name,
            *[t.lstrip("#") for t in hashtags[2:10]]]
    return {"title": title, "description": "\n".join(lines), "tags": tags,
            "chapters": [[t, name] for t, name in chapters(p)],
            "posts": [i.id for i in p.posts], "duration": p.duration,
            "start_day": p.start_day.isoformat(), "end_day": p.end_day.isoformat()}


def _draft(item: Item) -> dict:
    path = Path(item.folder) / "draft.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _reference(item: Item) -> str:
    try:
        fact = json.loads((Path(item.folder) / "fact_check.json").read_text(encoding="utf-8"))
        return fact.get("reference_label") or ""
    except (OSError, ValueError):
        return ""


# --- pictures (cards, side panels, thumbnail) -----------------------------------------------

def _backdrop(source: Path | None, size=(W, H), dim: float = 0.45) -> Image.Image:
    if source is None or not source.exists():
        return Image.new("RGB", size, (28, 22, 18))
    img = ImageOps.fit(Image.open(source).convert("RGB"), size)
    img = img.resize((size[0] // 8, size[1] // 8)).filter(ImageFilter.GaussianBlur(4))
    img = img.resize(size, Image.Resampling.BICUBIC)
    return Image.blend(img, Image.new("RGB", size, (12, 9, 6)), dim)


def _lines(d: ImageDraw.ImageDraw, text: str, role: str, brand: Brand, size: int, cx: float,
           top: float, max_w: float, fill, max_lines: int = 4) -> float:
    f = font(brand.font_path(role), size)
    lines = wrap_balanced(f, strip_emoji(text), max_w)
    while len(lines) > max_lines and size > 26:
        size -= 4
        f = font(brand.font_path(role), size)
        lines = wrap_balanced(f, strip_emoji(text), max_w)
    y, _ = draw_centered(d, lines[:max_lines], f, cx, top, fill)
    return y


def card(brand: Brand, title: str, subtitle: str, footer: str, source: Path | None) -> Image.Image:
    img = _backdrop(source, dim=0.55)
    d = ImageDraw.Draw(img)
    y = _lines(d, title, "hook", brand, 92, W / 2, H * 0.33, W * 0.75, CREAM, 2)
    d.line([(W / 2 - 60, y + 18), (W / 2 + 60, y + 18)], fill=GOLD, width=3)
    _lines(d, subtitle, "body", brand, 44, W / 2, y + 48, W * 0.7, SOFT, 2)
    if footer:
        f = font(brand.font_path("footer"), 34)
        draw_centered(d, [footer], f, W / 2, H - 110, GOLD)
    return img


def panel(brand: Brand, content_w: int, number: int | None, item: Item) -> Image.Image:
    """Transparent overlay: chapter number and theme on the left, page name on the right."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    side = (W - content_w) / 2
    left_cx, right_cx, max_w = side / 2, W - side / 2, side - 90
    if number is not None:
        f = font(brand.font_path("footer"), 30)
        draw_centered(d, [PILLAR_HI.get(item.pillar, item.pillar)], f, left_cx, H * 0.30, GOLD)
        f = font(brand.font_path("hook"), 96)
        y, _ = draw_centered(d, [f"सूत्र {number}"], f, left_cx, H * 0.30 + 50, CREAM)
        ref = _reference(item)
        if ref:
            _lines(d, ref, "reference", brand, 34, left_cx, y + 20, max_w, SOFT, 2)
    f = font(brand.font_path("header"), 40)
    draw_centered(d, [brand.page_name], f, right_cx, H * 0.40, CREAM)
    if brand.printed_handle:
        f = font(brand.font_path("footer"), 30)
        draw_centered(d, [brand.printed_handle], f, right_cx, H * 0.40 + 64, GOLD)
    f = font(brand.font_path("body"), 28)
    draw_centered(d, ["Save करें, किसी अपने को भेजें"], f, right_cx, H * 0.40 + 130, SOFT)
    return img


def thumbnail(brand: Brand, count: int, span: str, cover: Path | None) -> Image.Image:
    tw, th = 1280, 720
    img = _backdrop(cover, (tw, th), dim=0.6)
    if cover and cover.exists():
        pic = ImageOps.fit(Image.open(cover).convert("RGB"), (380, 676))
        img.paste(pic, (tw - 380 - 40, 22))
    d = ImageDraw.Draw(img)
    f = font(brand.font_path("hook"), 150)
    d.text((70, 120), f"{count} जीवन", font=f, fill=CREAM, language="hi")
    d.text((70, 300), "सूत्र", font=f, fill=GOLD, language="hi")
    f = font(brand.font_path("footer"), 44)
    d.text((76, 520), f"इस हफ़्ते · {span}", font=f, fill=CREAM, language="hi")
    if brand.printed_handle:
        f = font(brand.font_path("footer"), 34)
        d.text((76, 600), brand.printed_handle, font=f, fill=SOFT, language="hi")
    return img


# --- video -----------------------------------------------------------------------------------

def _ffmpeg(args: list[str], what: str) -> None:
    out = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True,
                         text=True)
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg failed while making the weekly video ({what}): "
                           f"{out.stderr.strip()[-300:]}")


ENCODE = ["-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
          "-pix_fmt", "yuv420p", "-an"]


def _fades(seconds: float) -> str:
    return f"fade=t=in:st=0:d=0.5,fade=t=out:st={max(seconds - 0.5, 0):.2f}:d=0.5"


def _still_clip(png: Path, seconds: float, out: Path) -> None:
    _ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", str(png), "-t", f"{seconds:.2f}",
             "-vf", f"scale={W}:{H},{_fades(seconds)},format=yuv420p", "-tune", "stillimage",
             *ENCODE, str(out)], "card")


def _framed_still(source: Path, overlay: Image.Image, seconds: float, out: Path) -> None:
    """A picture (slide or story) framed like a reel, drawn once and held."""
    pic = Image.open(source).convert("RGB")
    frame = _backdrop(source, dim=0.5)
    pic = pic.resize((round(pic.width * H / pic.height), H), Image.Resampling.LANCZOS)
    frame.paste(pic, ((W - pic.width) // 2, 0))
    frame.paste(overlay, (0, 0), overlay)
    png = out.with_suffix(".png")
    frame.save(png)
    _still_clip(png, seconds, out)


def _framed_clip(source: Path, overlay: Path, seconds: float, out: Path) -> None:
    """The vertical reel in the middle, a blurred copy of it behind, the panel on top."""
    src = ["-stream_loop", "-1", "-i", str(source)]
    graph = (f"[0:v]fps={FPS},split[a][b];"
             f"[a]scale=240:135:force_original_aspect_ratio=increase,crop=240:135,"
             f"boxblur=6:2,scale={W}:{H},eq=brightness=-0.28[bg];"
             f"[b]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0[v];"
             f"[v][1:v]overlay=0:0,{_fades(seconds)},format=yuv420p")
    _ffmpeg([*src, "-i", str(overlay), "-filter_complex", graph, "-t", f"{seconds:.2f}",
             *ENCODE, str(out)], "post")


def _music_bed(seconds: float, tmp: Path, out: Path) -> list[str]:
    """One calm bed under the whole video: up to 3 library tracks crossfaded, looped and
    levelled; the built-in tanpura ambience when the library is empty."""
    picked: list[Path] = []
    for pillar in ("shiv_dhyan", "gita", "jeevan_sutra"):
        track = music.pick(pillar)
        if track and track not in picked:
            picked.append(track)
    bed = tmp / "source.wav"
    if not picked:
        audio.write_wav(bed, audio.ambience(15.0))  # slow to make; looped below
    elif len(picked) == 1:
        _ffmpeg(["-i", str(picked[0]), "-ar", "44100", "-ac", "2", str(bed)], "music")
    else:
        inputs = [a for p in picked for a in ("-i", str(p))]
        chain = "".join(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}];"
                        for i in range(len(picked)))
        last = "a0"
        for i in range(1, len(picked)):
            chain += f"[{last}][a{i}]acrossfade=d=3[x{i}];"
            last = f"x{i}"
        _ffmpeg([*inputs, "-filter_complex", chain.rstrip(";"), "-map", f"[{last}]",
                 str(bed)], "music")
    fade_out = min(4.0, seconds / 4)
    _ffmpeg(["-stream_loop", "-1", "-i", str(bed), "-t", f"{seconds:.2f}", "-af",
             f"loudnorm=I=-18:TP=-1.5:LRA=11,afade=t=in:d=2,"
             f"afade=t=out:st={seconds - fade_out:.2f}:d={fade_out:.2f}",
             "-ar", "44100", "-ac", "2", str(out)], "music")
    return [music.rel(p) for p in picked]


def render(p: Plan, brand: Brand, folder: Path) -> dict:
    """Write video.mp4, cover.jpg (the YouTube thumbnail), meta.json and qa.json."""
    folder.mkdir(parents=True, exist_ok=True)
    meta = metadata(p, brand)
    count = len([i for i in p.posts if i.format != "story_line"])
    span = f"{hindi_date(p.start_day)} – {hindi_date(p.end_day)}"
    first_cover = next((Path(i.folder) / "cover.jpg" for i in p.posts
                        if (Path(i.folder) / "cover.jpg").exists()), None)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        clips, number = [], 0
        for k, seg in enumerate(p.segments):
            clip = tmp / f"c{k:03d}.mp4"
            if seg.kind in ("intro", "section", "outro"):
                text = {"intro": (f"इस हफ़्ते के {count} जीवन सूत्र", span),
                        "section": (seg.title, "इस हफ़्ते की स्टोरी"),
                        "outro": ("अगले रविवार फिर मिलेंगे", "रोज़ नए Shorts के लिए Subscribe करें")}
                title, sub = text[seg.kind]
                png = tmp / f"c{k:03d}.png"
                card(brand, title, sub, brand.printed_handle, first_cover).save(png)
                _still_clip(png, seg.seconds, clip)
            else:
                item = seg.item
                media = _media(item)
                numbered = seg.kind != "story_line"
                number += numbered
                if seg.kind == "carousel_shlok":
                    ov = panel(brand, int(H * 1080 / 1350), number, item)
                    parts = []
                    for j, slide in enumerate(media):
                        part = tmp / f"c{k:03d}_{j}.mp4"
                        _framed_still(slide, ov, SLIDE_SECONDS, part)
                        parts.append(part)
                    _concat(parts, clip, tmp / f"l{k:03d}.txt")
                elif seg.kind == "story_line":
                    _framed_still(media, panel(brand, int(H * 1080 / 1920), None, item),
                                  seg.seconds, clip)
                else:
                    ov = tmp / f"o{k:03d}.png"
                    panel(brand, int(H * 1080 / 1920), number, item).save(ov)
                    _framed_clip(media, ov, seg.seconds, clip)
            clips.append(clip)
        silent = tmp / "video.mp4"
        _concat(clips, silent, tmp / "list.txt")
        bed = tmp / "bed.wav"
        meta["music"] = _music_bed(p.duration, tmp, bed)
        _ffmpeg(["-i", str(silent), "-i", str(bed), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
                 str(folder / "video.mp4")], "sound")
    thumbnail(brand, count, span, first_cover).save(folder / "cover.jpg", quality=90)
    from instapost.render.video import probe_duration
    real = probe_duration(folder / "video.mp4") or 0
    meta["duration"] = real
    size_mb = (folder / "video.mp4").stat().st_size / 1e6
    checks = [
        {"name": "length", "passed": real >= 60, "detail": f"{real:.0f} s ({real / 60:.1f} min)"},
        {"name": "chapters", "passed": len(meta["chapters"]) >= MIN_CHAPTER_POSTS,
         "detail": f"{len(meta['chapters'])} chapters"},
        {"name": "file_size", "passed": size_mb < 2000, "detail": f"{size_mb:.0f} MB"},
    ]
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (folder / "qa.json").write_text(json.dumps({"passed": all(c["passed"] for c in checks),
                                                "checks": checks}, ensure_ascii=False),
                                    encoding="utf-8")
    (folder / "caption.txt").write_text(meta["description"] + "\n", encoding="utf-8")
    return meta


def _concat(parts: list[Path], out: Path, listing: Path) -> None:
    listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)], "join")


# --- as an item ------------------------------------------------------------------------------

def make(s: Session, end_day: date | None = None, actor: str = "agent",
         days: int = 7) -> Item:
    """Build the video for the week ending `end_day` (default: today) and put it in the inbox."""
    sched = load_schedule()
    end_day = end_day or sched.local_now().date()
    p = plan(s, end_day, days)
    brand = load_brand()
    item_id = f"{end_day.isoformat()}-{WEEKLY}-{secrets.token_hex(3)}"
    folder = get_settings().output_dir / end_day.isoformat() / item_id
    meta = render(p, brand, folder)
    counts: dict[str, int] = {}
    for i in p.posts:
        counts[i.pillar] = counts.get(i.pillar, 0) + 1
    item = Item(id=item_id, format=WEEKLY, pillar=max(counts, key=counts.get),
                folder=str(folder), status=ItemStatus.NEEDS_REVIEW, hook=meta["title"],
                caption=meta["description"], fact_check="pass", for_date=end_day.isoformat(),
                duration_s=meta["duration"], qa_summary="")
    s.add(item)
    s.flush()
    audit(s, actor, "created", item.id, status=item.status, posts=len(p.posts),
          minutes=round(meta["duration"] / 60, 1))
    return item


def publish_time(now_local: datetime | None = None) -> datetime:
    """When an approved weekly video goes out: today's publish_at, or soon if that passed."""
    from instapost.schedule import to_utc_naive

    sched = load_schedule()
    now_local = now_local or sched.local_now()
    hh, mm = map(int, sched.weekly_video.publish_at.split(":"))
    at = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if at <= now_local:
        at = now_local + timedelta(minutes=10)
        if at.hour >= 23:
            at = (at + timedelta(days=1)).replace(hour=hh, minute=mm)
    return to_utc_naive(at)


def last_made(s: Session) -> Item | None:
    return s.scalars(select(Item).where(Item.format == WEEKLY)
                     .order_by(Item.created_at.desc())).first()


def due(s: Session) -> bool:
    """Sunday (schedule.yaml weekly_video.day) after make_at, and not made yet this week."""
    from instapost.schedule import WEEKDAYS

    sched = load_schedule()
    cfg = sched.weekly_video
    now = sched.local_now()
    if not cfg.enabled or WEEKDAYS[now.weekday()] != cfg.day:
        return False
    hh, mm = map(int, cfg.make_at.split(":"))
    if (now.hour, now.minute) < (hh, mm):
        return False
    last = last_made(s)
    return not (last and to_local(last.created_at, sched).date() == now.date()) \
        and not _tried_today(s, now.date())


def _tried_today(s: Session, day: date) -> bool:
    from instapost.db import get_kv
    return get_kv(s, "weekly_video_tried", "") == day.isoformat()


def mark_tried(s: Session, day: date) -> None:
    from instapost.db import set_kv
    set_kv(s, "weekly_video_tried", day.isoformat())

