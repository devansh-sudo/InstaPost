"""Reel looks ("templates"). A feed that always looks the same gets scrolled past, so posts
rotate between a few looks; the learner then shows the ones that do well more often.

- book_photo: the page printed onto a real photo of an open book (Backgrounds page);
- classic:    our drawn book page lying over a softly blurred scene photo;
- paper:      the whole screen is a cream page, like a close-up of a printed page;
- night:      the same page in dark ink-paper with cream text and an amber highlight.

The two card looks need no photo at all, so they are free and never fail.
"""

import random
from dataclasses import dataclass

import numpy as np
from PIL import Image

from instapost.brand import Brand
from instapost.render.page import Layout, PageContent, RenderedPage, render_page
from instapost.render.scene import FRAME_H, FRAME_W, Composition, FrameLayer
from instapost.render.text import Box


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    about: str
    needs: str = ""  # "" (always works), "photo" (a ready book photo), "scene" (image API)


TEMPLATES: dict[str, Template] = {t.id: t for t in (
    Template("book_photo", "Book photo", "The page printed onto a real photo of an open book.",
             "photo"),
    Template("classic", "Drawn book", "A drawn book page over a softly blurred scene photo.",
             "scene"),
    Template("paper", "Full page", "The whole screen is a cream printed page, close up."),
    Template("night", "Night page", "Dark paper, cream text, amber highlight: calm, "
             "night-time feel."),
)}
DEFAULT_ORDER = ["book_photo", "paper", "night", "classic"]

# Text inside Instagram's safe area even at the reel's strongest zoom (see qa.unsafe_boxes).
CARD_LAYOUT = Layout(w=FRAME_W, h=FRAME_H, margin=150, header_y=232, top=330, bottom_pad=440,
                     footer_pad=300, hook_size=64, body_size=40, ref_size=30, illus_max=440,
                     spine=False)


def night_brand(brand: Brand) -> Brand:
    """The brand with dark paper and light ink."""
    colors = brand.colors.model_copy(update={
        "paper": "#1C1714", "paper_edge": "#120E0C", "ink": "#F2E7D3", "ink_soft": "#C2B29A",
        "highlight": "#9A5B1E", "highlight_alpha": 0.62, "glow": "#FF9A3C"})
    return brand.model_copy(update={"colors": colors})


def card_brand(brand: Brand, template_id: str) -> Brand:
    return night_brand(brand) if template_id == "night" else brand


def _vignette(strength: float) -> np.ndarray:
    """(h, w) brightness, 1 in the middle, darker toward the corners."""
    yy, xx = np.mgrid[0:FRAME_H, 0:FRAME_W].astype(np.float32)
    r = np.sqrt(((xx / FRAME_W - 0.5) * 1.3) ** 2 + ((yy / FRAME_H - 0.5) * 1.0) ** 2)
    return np.clip(1.08 - strength * r ** 2 * 2.2, 0.0, 1.0)


def _shade(img: Image.Image, v: np.ndarray) -> Image.Image:
    arr = np.asarray(img.convert("RGB"), np.float32) * v[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def compose_card(brand: Brand, template_id: str, content: PageContent,
                 seed: int = 0) -> Composition:
    """A full-screen page: no photo, the text written in like on the book page."""
    page: RenderedPage = render_page(card_brand(brand, template_id), content, seed, CARD_LAYOUT)
    v = _vignette(0.55 if template_id == "night" else 0.2)
    still = _shade(page.image, v)
    base = _shade(page.base if page.base is not None else page.image, v)
    # The text layers get the same vignette, so a written-in line matches the cover.
    layers = []
    for rv in page.reveal:
        arr = np.asarray(rv.image, np.float32)
        h, w = arr.shape[:2]
        rgb = arr[..., :3] * v[rv.y:rv.y + h, rv.x:rv.x + w, None]
        layers.append(FrameLayer(rgb, arr[..., 3:] / 255.0, rv.x, rv.y, rv.order))
    boxes = page.text_boxes
    content_box = Box(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                      max(b.x1 for b in boxes), max(b.y1 for b in boxes)) if boxes else None
    return Composition(still, base, layers, page.hook_boxes, page.text_boxes,
                       page_box=content_box, style="card")


def order(enabled: list[str], seed: int, first: str | None = None) -> list[str]:
    """Templates to try, best first: the one asked for, then the learner's pick among the
    enabled ones (the last few used go to the back). "classic" is always last: it is the
    fallback that works whenever the image API does."""
    from instapost.learn import assets

    ids = [t for t in enabled if t in TEMPLATES] or list(DEFAULT_ORDER)
    out = assets.order("template", sorted(ids), random.Random(seed))
    if first in TEMPLATES:
        out = [first] + [t for t in out if t != first]
    return [t for t in out if t != "classic"] + ["classic"]


# --- which looks are switched on (dashboard: Templates) ---------------------------------------

KV_KEY = "templates:enabled"


def enabled() -> list[str]:
    """Template ids switched on; all of them until you change it on the dashboard."""
    from instapost.db import get_kv, session

    try:
        with session() as s:
            raw = get_kv(s, KV_KEY, "")
    except Exception:  # noqa: BLE001 - no database (CLI dry runs): everything is on
        raw = ""
    ids = [t for t in raw.split(",") if t in TEMPLATES]
    return ids or list(DEFAULT_ORDER)


def set_enabled(s, ids: list[str], actor: str) -> list[str]:
    from instapost.db import audit, set_kv

    ids = [t for t in DEFAULT_ORDER if t in ids] or ["paper"]  # never all off
    set_kv(s, KV_KEY, ",".join(ids))
    audit(s, actor, "templates_enabled", templates=ids)
    return ids


def mark_used(template_id: str) -> None:
    from instapost.studio.free_images import _mark_used

    _mark_used(f"template:{template_id}")


def label(template_id: str | None) -> str:
    t = TEMPLATES.get(template_id or "")
    return t.name if t else (template_id or "")


# --- example post per look (dashboard) --------------------------------------------------------

SAMPLE_TEXT = dict(
    hook_lines=["जिसे बदल नहीं सकते,", "उसे स्वीकार करना ही शांति है।"],
    body="जो हाथ में है उसे पूरे मन से करें, बाकी को शांत मन से स्वीकार करें।",
    reference_label="श्रीमद्भगवद्गीता 2.47",
    reference_text="कर्मण्येवाधिकारस्ते मा फलेषु कदाचन।",
)


def sample(template_id: str):
    """A fixed example post in this look (540x960), made once and kept."""
    from instapost.brand import load_brand
    from instapost.config import get_settings
    from instapost.render.scene import compose_reel
    from instapost.studio import drawings, images, plates

    if template_id == "book_photo":  # the example post of an approved book photo, if any
        for plate_id, _ in plates.ready():
            if (path := plates.sample(plate_id)) is not None:
                return path
    out = get_settings().state_dir / "templates" / f"{template_id}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    brand = load_brand()
    # A library drawing loaded directly, so the example does not count as a use.
    lib = {d.id: d for d in drawings.library(include_disabled=True)}
    d = lib.get("library:starter/diya") or next(iter(lib.values()), None)
    illus = drawings.ink(Image.open(d.path)) if d else None
    content = PageContent(header=brand.header_text, page_number=108, footer=brand.printed_handle,
                          illustration=illus, **SAMPLE_TEXT)
    if template_id in ("classic", "book_photo"):
        scene = images.get_provider("placeholder").generate("cozy room, diya", portrait=True)
        comp = compose_reel(brand, scene, render_page(brand, content, 7), 7)
    else:
        comp = compose_card(brand, template_id, content, 7)
    comp.still.resize((540, 960)).save(out, quality=86)
    return out
