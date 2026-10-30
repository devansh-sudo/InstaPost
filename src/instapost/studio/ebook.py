"""The sutra e-book: your posts of a period as a book (PDF), one sutra per page in the same
book-page style as the reels, with a cover, an opening page and a closing page.

Free to make (no paid calls): the text, verses and drawings are the ones you already
approved. Sell it through any payment page (Instamojo, Razorpay, Gumroad …): put that link on
the dashboard's Earn page and it appears on your link page and under YouTube videos.

Saved in state/earn/ebooks/<id>/: ebook.pdf, preview.pdf (first pages, to share free),
cover.jpg (for posts and the shop page) and meta.json.
"""

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost.brand import Brand, load_brand
from instapost.config import get_settings
from instapost.db import Item, ItemStatus
from instapost.render.backdrops import backdrop
from instapost.render.page import (
    PAGE_H,
    PAGE_W,
    PageContent,
    _place_illustration,
    hex_rgb,
    render_page,
)
from instapost.render.text import draw_centered, font, wrap_balanced

FORMATS = ("reel_book_pov", "carousel_shlok", "scene_reel")
DONE = (ItemStatus.APPROVED, ItemStatus.PUBLISHED, ItemStatus.PARTIAL, ItemStatus.PUBLISHING)
MIN_SUTRAS = 5
PREVIEW_PAGES = 4
PILLAR_TITLE = {"shiv_dhyan": "शिव ध्यान के", "gita": "गीता के", "kabir": "कबीर के",
                "jeevan_sutra": "जीवन के", "life_lesson": "जीवन के"}


class NotEnoughSutras(RuntimeError):
    pass


def _dir() -> Path:
    d = get_settings().state_dir / "earn" / "ebooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def sutras(s: Session, start: date, end: date, pillar: str | None = None) -> list[dict]:
    """Approved and published posts of the period as book entries, oldest first, no repeats."""
    q = select(Item).where(Item.status.in_(DONE), Item.format.in_(FORMATS),
                           Item.for_date >= start.isoformat(), Item.for_date <= end.isoformat())
    if pillar:
        q = q.where(Item.pillar == pillar)
    out, seen = [], set()
    for item in sorted(s.scalars(q), key=lambda i: (i.for_date or "", i.created_at)):
        folder = Path(item.folder)
        draft, fact = _read(folder / "draft.json"), _read(folder / "fact_check.json")
        hook = [ln for ln in draft.get("hook_lines", []) if ln.strip()]
        key = " ".join(hook)
        if not hook or key in seen:
            continue
        seen.add(key)
        drawing = (folder / "drawing.txt").read_text(encoding="utf-8").split("\n")[0].strip() \
            if (folder / "drawing.txt").exists() else ""
        out.append({"item": item.id, "pillar": item.pillar, "hook": hook,
                    "body": draft.get("body") or draft.get("caption_hi", "").split("\n")[0],
                    "label": fact.get("reference_label"), "verse": fact.get("reference_text"),
                    "drawing": drawing})
    return out


def _drawing(drawing_id: str):
    if not drawing_id:
        return None
    from instapost.studio import drawings

    for d in drawings.library(include_disabled=True):
        if d.id == drawing_id:
            return drawings.ink(Image.open(d.path))
    return None


def _text_page(brand: Brand, look: str, title: str, lines: list[str], seed: int,
               footer: str = "") -> Image.Image:
    img = backdrop(brand, look, PAGE_W, PAGE_H, seed)
    d = ImageDraw.Draw(img)
    dark = look in ("night", "dusk", "gold_frame", "ganga")
    ink = (255, 244, 228) if dark else hex_rgb(brand.colors.ink)
    soft = (232, 214, 190) if dark else hex_rgb(brand.colors.ink_soft)
    tf = font(brand.font_path("hook"), 66)
    t_lines = wrap_balanced(tf, title, PAGE_W - 180)
    y, _ = draw_centered(d, t_lines, tf, PAGE_W / 2, 330, ink, line_gap=1.35)
    bf = font(brand.font_path("body"), 36)
    y += 60
    for para in lines:
        y, _ = draw_centered(d, wrap_balanced(bf, para, PAGE_W - 200), bf, PAGE_W / 2, y, soft,
                             line_gap=1.5)
        y += 24
    if footer:
        ff = font(brand.font_path("footer"), 28)
        draw_centered(d, [footer], ff, PAGE_W / 2, PAGE_H - 140, soft)
    return img


def title_for(n: int, pillar: str | None) -> str:
    return f"{PILLAR_TITLE.get(pillar or 'jeevan_sutra', 'जीवन के')} {n} सूत्र"


def build(s: Session, start: date, end: date, pillar: str | None = None,
          title: str | None = None) -> dict:
    """Make the e-book for the period; returns its meta (with the paths)."""
    items = sutras(s, start, end, pillar)
    if len(items) < MIN_SUTRAS:
        raise NotEnoughSutras(f"only {len(items)} approved post(s) in this period; an e-book "
                              f"needs at least {MIN_SUTRAS}")
    brand = load_brand()
    title = (title or title_for(len(items), pillar)).strip()[:80]
    handle = brand.printed_handle
    book_id = f"{date.today().isoformat()}-{secrets.token_hex(3)}"
    folder = _dir() / book_id
    folder.mkdir(parents=True)

    cover = _text_page(brand, "gold_frame", title, [
        "रोज़ एक सूत्र: जो मन को शांत करे और रास्ता दिखाए।",
        f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}"], 1, handle)
    art = _drawing("library:starter/diya")
    if art is not None:  # a gold diya in the middle of the cover
        _place_illustration(cover, art, (200, 720, PAGE_W - 200, 1180), (214, 168, 84))
    opening = _text_page(brand, "paper", "इस पुस्तिका के बारे में", [
        "हर पन्ने पर एक सूत्र है, जो पहले हमारे पेज पर साझा हुआ था।",
        "जहाँ श्लोक या दोहा दिया है, उसका पाठ प्रमाणित स्रोत से लिया गया है।",
        "रोज़ सुबह एक पन्ना पढ़िए, और दिन भर उस एक बात को साथ रखिए।"], 2, handle)
    pages = [cover, opening]
    for n, x in enumerate(items, 1):
        page = render_page(brand, PageContent(
            header=brand.header_text, page_number=n, hook_lines=x["hook"], body=x["body"] or "",
            reference_label=x["label"], reference_text=x["verse"], footer=handle,
            illustration=_drawing(x["drawing"])), seed=n)
        pages.append(page.image.convert("RGB"))
    from instapost.studio import earn
    links = earn.public_page()
    pages.append(_text_page(brand, "night", "धन्यवाद", [
        "अगर कोई सूत्र आपके काम आया, तो इस पुस्तिका को किसी अपने के साथ बाँटिए।",
        f"रोज़ नए सूत्र: Instagram, Facebook और YouTube पर {handle}" if handle else
        "रोज़ नए सूत्र हमारे पेज पर।",
        *([f"सभी लिंक: {links}"] if links else [])], 3, handle))

    pdf = folder / "ebook.pdf"
    pages[0].save(pdf, "PDF", save_all=True, append_images=pages[1:], resolution=110,
                  title=title, author=brand.page_name, quality=85)
    preview = folder / "preview.pdf"
    pages[0].save(preview, "PDF", save_all=True, append_images=pages[1:PREVIEW_PAGES],
                  resolution=110, title=f"{title} (झलक)", author=brand.page_name, quality=85)
    cover.save(folder / "cover.jpg", quality=90)
    meta = {"id": book_id, "title": title, "start": start.isoformat(), "end": end.isoformat(),
            "pillar": pillar, "sutras": len(items), "pages": len(pages),
            "items": [x["item"] for x in items], "made": datetime.now(UTC).isoformat(),
            "size_mb": round(pdf.stat().st_size / 1e6, 1)}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    return meta


def all_books() -> list[dict]:
    books = [_read(d / "meta.json") for d in _dir().iterdir() if d.is_dir()]
    return sorted([b for b in books if b], key=lambda b: b["made"], reverse=True)


def file(book_id: str, name: str) -> Path:
    if name not in ("ebook.pdf", "preview.pdf", "cover.jpg") or "/" in book_id \
            or not book_id.replace("-", "").isalnum():
        raise KeyError(name)
    path = _dir() / book_id / name
    if not path.exists():
        raise KeyError(name)
    return path


def last_month(today: date) -> tuple[date, date]:
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end
