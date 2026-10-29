"""Post looks ("templates") for reels, carousels and stories. A feed that always looks the
same gets scrolled past, so posts rotate between a few looks; the learner then shows the ones
that do well more often.

Reels
- book_photo: the page printed onto a real photo of an open book (Backgrounds page);
- classic:    our drawn book page lying over a softly blurred scene photo;
- paper:      the whole screen is a cream page, like a close-up of a printed page;
- mandala:    the cream page with a faint mandala behind the text;
- night:      dark paper, cream text, amber highlight, a few far-away diya lights;
- dusk:       a sunset sky with floating lights, cream text.
Carousels: paper, mandala, night, dusk. Stories: card (paper card on a scene photo), paper,
mandala, night, dusk. The backgrounds are drawn in code (render.backdrops), so they are free,
never fail, and move a little with every post.
"""

import random
from dataclasses import dataclass

import numpy as np
from PIL import Image

from instapost.brand import Brand
from instapost.models import Format
from instapost.render.backdrops import backdrop, look_brand
from instapost.render.page import Layout, PageContent, RenderedPage, render_page
from instapost.render.scene import FRAME_H, FRAME_W, Composition, FrameLayer
from instapost.render.text import Box

REEL, CAROUSEL, STORY = "reel", "carousel", "story"
KIND_NAMES = {REEL: "Reels", CAROUSEL: "Carousels", STORY: "Stories"}


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    about: str
    needs: str = ""  # "" (always works), "photo" (a ready book photo), "scene" (image API)


_PAPER = "The whole screen is a cream printed page, close up."
_MANDALA = "Cream page with a faint hand-drawn mandala behind the text."
_NIGHT = "Dark paper, cream text, amber highlight, a few far-away diya lights."
_DUSK = "A sunset sky (indigo to saffron) with floating lights and cream text."

BY_KIND: dict[str, dict[str, Template]] = {
    REEL: {t.id: t for t in (
        Template("book_photo", "Book photo",
                 "The page printed onto a real photo of an open book.", "photo"),
        Template("classic", "Drawn book",
                 "A drawn book page over a softly blurred scene photo.", "scene"),
        Template("paper", "Full page", _PAPER),
        Template("mandala", "Mandala page", _MANDALA),
        Template("night", "Night page", _NIGHT),
        Template("dusk", "Dusk sky", _DUSK),
    )},
    CAROUSEL: {t.id: t for t in (
        Template("paper", "Paper", "Cream pages, like the book."),
        Template("mandala", "Mandala", _MANDALA),
        Template("night", "Night", _NIGHT),
        Template("dusk", "Dusk sky", _DUSK),
    )},
    STORY: {t.id: t for t in (
        Template("card", "Card on photo", "A paper card over a blurred scene photo.", "scene"),
        Template("paper", "Full page", _PAPER),
        Template("mandala", "Mandala", _MANDALA),
        Template("night", "Night", _NIGHT),
        Template("dusk", "Dusk sky", _DUSK),
    )},
}
TEMPLATES = BY_KIND[REEL]  # reels (the first kind that had looks)
DEFAULT_ORDER = list(TEMPLATES)
# The look tried last, which always works: classic needs the image API, which has its own
# fallbacks; the paper looks need nothing.
FALLBACK = {REEL: "classic", CAROUSEL: "paper", STORY: "paper"}


def kind_of(fmt: Format | str) -> str:
    return {Format.REEL_BOOK_POV.value: REEL, Format.CAROUSEL_SHLOK.value: CAROUSEL,
            Format.STORY_LINE.value: STORY}[Format(fmt).value]


def key(kind: str, template_id: str) -> str:
    """The id the learner and the usage history know a look by (reels keep the plain id)."""
    return template_id if kind == REEL else f"{kind}/{template_id}"


# Text inside Instagram's safe area even at the reel's strongest zoom (see qa.unsafe_boxes).
CARD_LAYOUT = Layout(w=FRAME_W, h=FRAME_H, margin=150, header_y=232, top=330, bottom_pad=440,
                     footer_pad=300, hook_size=64, body_size=40, ref_size=30, illus_max=440,
                     spine=False)


def card_brand(brand: Brand, template_id: str) -> Brand:
    return look_brand(brand, template_id)


def compose_card(brand: Brand, template_id: str, content: PageContent,
                 seed: int = 0) -> Composition:
    """A full-screen page on a drawn background: no photo, the text written in like on the
    book page."""
    paper = backdrop(brand, template_id, FRAME_W, FRAME_H, seed, centre=(0.5, 0.36))
    page: RenderedPage = render_page(card_brand(brand, template_id), content, seed,
                                     CARD_LAYOUT, paper=paper)
    layers = []
    for rv in page.reveal:
        arr = np.asarray(rv.image, np.float32)
        layers.append(FrameLayer(arr[..., :3], arr[..., 3:] / 255.0, rv.x, rv.y, rv.order))
    boxes = page.text_boxes
    content_box = Box(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                      max(b.x1 for b in boxes), max(b.y1 for b in boxes)) if boxes else None
    still = page.image.convert("RGB")
    base = (page.base if page.base is not None else page.image).convert("RGB")
    return Composition(still, base, layers, page.hook_boxes, page.text_boxes,
                       page_box=content_box, style="card")


def order(enabled: list[str], seed: int, first: str | None = None,
          kind: str = REEL) -> list[str]:
    """Looks to try, best first: the one asked for, then the learner's pick among the
    enabled ones (the last few used go to the back), and the fallback look last."""
    from instapost.learn import assets

    looks = BY_KIND[kind]
    ids = [t for t in enabled if t in looks] or list(looks)
    picked = assets.order("template", sorted(key(kind, t) for t in ids), random.Random(seed))
    out = [k.split("/")[-1] for k in picked]
    if first in looks:
        out = [first] + [t for t in out if t != first]
    last = FALLBACK[kind]
    return [t for t in out if t != last] + [last]


# --- which looks are switched on (dashboard: Templates) ---------------------------------------

def _kv_key(kind: str) -> str:
    return "templates:enabled" if kind == REEL else f"templates:enabled:{kind}"


def enabled(kind: str = REEL) -> list[str]:
    """Look ids switched on; all of them until you change it on the dashboard."""
    from instapost.db import get_kv, session

    try:
        with session() as s:
            raw = get_kv(s, _kv_key(kind), "")
    except Exception:  # noqa: BLE001 - no database (CLI dry runs): everything is on
        raw = ""
    ids = [t for t in raw.split(",") if t in BY_KIND[kind]]
    return ids or list(BY_KIND[kind])


def set_enabled(s, ids: list[str], actor: str, kind: str = REEL) -> list[str]:
    from instapost.db import audit, set_kv

    ids = [t for t in BY_KIND[kind] if t in ids] or ["paper"]  # never all off
    set_kv(s, _kv_key(kind), ",".join(ids))
    audit(s, actor, "templates_enabled", kind=kind, templates=ids)
    return ids


def mark_used(template_id: str, kind: str = REEL) -> None:
    from instapost.studio.free_images import _mark_used

    _mark_used(f"template:{key(kind, template_id)}")


def label(template_id: str | None, kind: str = REEL) -> str:
    t = BY_KIND[kind].get(template_id or "")
    return t.name if t else (template_id or "")


# --- example post per look (dashboard) --------------------------------------------------------

SAMPLE_TEXT = dict(
    hook_lines=["जिसे बदल नहीं सकते,", "उसे स्वीकार करना ही शांति है।"],
    body="जो हाथ में है उसे पूरे मन से करें, बाकी को शांत मन से स्वीकार करें।",
    reference_label="श्रीमद्भगवद्गीता 2.47",
    reference_text="कर्मण्येवाधिकारस्ते मा फलेषु कदाचन।",
)


def _sample_drawing():
    from instapost.studio import drawings

    # A library drawing loaded directly, so the example does not count as a use.
    lib = {d.id: d for d in drawings.library(include_disabled=True)}
    d = lib.get("library:starter/diya") or next(iter(lib.values()), None)
    return drawings.ink(Image.open(d.path)) if d else None


def sample(template_id: str, kind: str = REEL):
    """A fixed example post in this look (540 px wide), made once and kept."""
    from instapost.brand import load_brand
    from instapost.config import get_settings
    from instapost.render import carousel
    from instapost.render.scene import compose_reel
    from instapost.studio import images, plates

    if kind == REEL and template_id == "book_photo":  # an approved book photo, if any
        for plate_id, _ in plates.ready():
            if (path := plates.sample(plate_id)) is not None:
                return path
    out = get_settings().state_dir / "templates" / f"{key(kind, template_id).replace('/', '-')}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    brand = load_brand()
    illus = _sample_drawing()

    def scene():
        return images.get_provider("placeholder").generate("cozy room, diya", portrait=True)

    if kind == CAROUSEL:
        draft = _sample_draft(Format.CAROUSEL_SHLOK)
        img = carousel.render_slides(brand, draft, illus, None, 7, look=template_id)[0]
    elif kind == STORY:
        img = carousel.render_story(brand, scene() if template_id == "card" else None,
                                    "जिसे बदल नहीं सकते, उसे स्वीकार करना ही शांति है।", 7,
                                    look=template_id)
    else:
        content = PageContent(header=brand.header_text, page_number=108,
                              footer=brand.printed_handle, illustration=illus, **SAMPLE_TEXT)
        if template_id in ("classic", "book_photo"):
            img = compose_reel(brand, scene(), render_page(brand, content, 7), 7).still
        else:
            img = compose_card(brand, template_id, content, 7).still
    img.resize((540, round(img.height * 540 / img.width))).save(out, quality=86)
    return out


def _sample_draft(fmt: Format):
    from instapost.pipeline import load_sample

    return load_sample(fmt)
