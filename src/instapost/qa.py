"""Automatic QA gate (PLAN §3 "stop the scroll" rules). Nothing is offered for approval
unless every check passes; a failed item is kept with its report so it can be fixed."""

import base64
import io
import math

import numpy as np
from PIL import Image
from pydantic import BaseModel

from instapost import history, llm
from instapost.brand import Brand
from instapost.config import get_settings
from instapost.models import Draft, FactCheckResult, FactCheckVerdict, QACheck, QAReport
from instapost.render import text
from instapost.render.scene import FRAME_H, FRAME_W
from instapost.render.text import Box
from instapost.render.video import ZOOM_AMPLITUDE, Motion, read_time_seconds

MAX_HOOK_WORDS = 12
SAFE_TOP = 0.14 * FRAME_H
SAFE_BOTTOM = 0.80 * FRAME_H
SAFE_SIDE = 48
# Instagram's like/comment/share buttons sit in a column on the lower right.
RIGHT_RAIL = Box(FRAME_W - 130, 0.58 * FRAME_H, FRAME_W, FRAME_H)
DUPLICATE_RATIO = 0.8


def _zoomed(box: Box, z: float) -> Box:
    cx, cy = FRAME_W / 2, FRAME_H / 2
    return Box(cx + (box.x0 - cx) * z, cy + (box.y0 - cy) * z,
               cx + (box.x1 - cx) * z, cy + (box.y1 - cy) * z)


def _overlaps(a: Box, b: Box) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


def check_text(draft: Draft, fact: FactCheckResult, safety_issues: list[str]) -> list[QACheck]:
    hook_words = sum(len(line.split()) for line in draft.hook_lines)
    similarity, match = history.most_similar(" ".join(draft.hook_lines))
    dup_detail = f"closest earlier hook {similarity:.2f}: {match}" if match else "first post"
    try:
        text.require_raqm()
        shaping = QACheck(name="hindi_shaping", passed=True)
    except text.ShapingUnavailable as e:
        shaping = QACheck(name="hindi_shaping", passed=False, detail=str(e))
    return [
        QACheck(name="hook_length", passed=1 <= hook_words <= MAX_HOOK_WORDS,
                detail=f"{hook_words} words (max {MAX_HOOK_WORDS})"),
        QACheck(name="hashtags", passed=3 <= len(draft.hashtags) <= 5,
                detail=f"{len(draft.hashtags)} hashtags (3-5)"),
        QACheck(name="caption_length", passed=len(caption_text(draft)) <= 2000,
                detail=f"{len(caption_text(draft))} characters"),
        QACheck(name="safety", passed=not safety_issues, detail="; ".join(safety_issues)),
        QACheck(name="fact_check", passed=fact.verdict != FactCheckVerdict.FAIL,
                detail=f"{fact.verdict.value}: {'; '.join(fact.issues)}"),
        QACheck(name="not_duplicate", passed=similarity < DUPLICATE_RATIO,
                detail=dup_detail),
        shaping,
    ]


def unsafe_boxes(text_boxes: list[Box], photo: bool = False) -> list[Box]:
    """Text lines that would sit outside the safe area at the strongest zoom of the reel
    (book photos also drift and tilt a little, like a hand holding the book)."""
    from instapost.render.video import PLATE_ZOOM, SWAY_DEG, SWAY_PX

    z = (1 + ZOOM_AMPLITUDE) * (PLATE_ZOOM if photo else 1)
    m = SWAY_PX + FRAME_H / 2 * math.sin(math.radians(SWAY_DEG)) if photo else 0
    boxes = [_zoomed(b, z) for b in text_boxes]
    boxes = [Box(b.x0 - m, b.y0 - m, b.x1 + m, b.y1 + m) for b in boxes]
    return [b for b in boxes
            if b.y0 < SAFE_TOP or b.y1 > SAFE_BOTTOM or b.x0 < SAFE_SIDE
            or b.x1 > FRAME_W - SAFE_SIDE or _overlaps(b, RIGHT_RAIL)]


def check_reel(draft: Draft, motion: Motion, text_boxes: list[Box]) -> list[QACheck]:
    checks = []
    bad = unsafe_boxes(text_boxes, photo=getattr(motion, "photo", False))
    checks.append(QACheck(name="safe_area", passed=not bad,
                          detail=f"{len(bad)} text lines outside the safe area" if bad else ""))

    read = read_time_seconds(*draft.hook_lines, draft.body)
    d = motion.duration
    checks.append(QACheck(name="duration", passed=7 <= d <= 12 and d >= read + 1.0,
                          detail=f"{d:.1f}s for {read:.1f}s of reading"))

    first = motion.frame_at(0).astype(np.int16)
    last = motion.frame_at(d - 1 / 30).astype(np.int16)
    middle = motion.frame_at(d / 2).astype(np.int16)
    seam = float(np.abs(first - last).mean())
    movement = float(np.abs(first - middle).mean())
    checks.append(QACheck(name="seamless_loop", passed=seam < 1.5,
                          detail=f"first/last frame difference {seam:.2f}"))
    checks.append(QACheck(name="has_motion", passed=movement > 2.0,
                          detail=f"first/middle frame difference {movement:.2f}"))
    return checks


class VisionVerdict(BaseModel):
    readable: bool
    looks_real: bool
    page_fits: bool
    problems: list[str]


VISION_SYSTEM = """You check finished Instagram images before they are posted.
Report problems only: text that is hard to read, cut off or overlapping, misspelled or broken
Hindi characters, distorted hands, faces or anatomy, disrespectful depiction of a deity,
any stray letters or watermarks inside the illustration or background.
The request lists text that belongs on the design (page header, page number, the page's own
handle, divider symbol); never report those as watermarks or stray text.

Some images are a photo of an open book with our page printed onto its blank page. For those:
- looks_real: false only if the whole image clearly does not look like one natural photo
  (the page looks pasted on, floating, glowing, or of a different sharpness than the paper).
- page_fits: false only if the printed text or drawing leaves the book's paper, runs onto
  fingers, the facing page or the background, or clearly does not follow the page's angle.
For images that are not book photos, answer true for both. Be strict only about real faults:
a small tilt, soft background or warm colour is how these photos are meant to look."""

PHOTO_FAULT = "photo:"  # detail prefix: the book photo, not the text, is the problem


def check_vision(image: Image.Image, brand: Brand | None = None,
                 photo: bool = False) -> QACheck:
    buf = io.BytesIO()
    image.convert("RGB").resize((540, 960)).save(buf, "JPEG", quality=85)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    kind = ("This is a book photo with our page printed on it." if photo
            else "This is a designed image (not a book photo).")
    verdict = llm.structured(
        model=get_settings().qa_model, system=VISION_SYSTEM,
        content=[{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                              "data": data}},
                 {"type": "text", "text": f"Check this image. {kind}" + _intended_text(brand)}],
        schema=VisionVerdict, purpose="vision_qa", effort="low", max_tokens=2000,
        estimated_usd=0.005,
    )
    photo_bad = photo and not (verdict.looks_real and verdict.page_fits)
    problems = list(verdict.problems)
    if photo_bad:
        what = [w for w, ok in (("does not look like one natural photo", verdict.looks_real),
                                ("page does not sit on the book's paper", verdict.page_fits))
                if not ok]
        problems.insert(0, PHOTO_FAULT + " " + "; ".join(what))
    return QACheck(name="vision", passed=verdict.readable and not problems,
                   detail="; ".join(problems))


def photo_fault(check: QACheck) -> bool:
    """The vision check failed because of the book photo (try another photo)."""
    return check.name == "vision" and not check.passed and check.detail.startswith(PHOTO_FAULT)


def _intended_text(brand: Brand | None) -> str:
    if brand is None:
        return ""
    parts = [f"page header {brand.header_text!r}", "the page number",
             f"divider symbol {brand.divider_symbol!r}"]
    if brand.printed_handle:
        parts.append(f"the page's own Instagram handle {brand.printed_handle!r} in the footer")
    return " Intended text on the design (not problems): " + "; ".join(parts) + "."


def caption_text(draft: Draft) -> str:
    return "\n\n".join([draft.caption_hi, draft.caption_hinglish, draft.cta,
                        " ".join(draft.hashtags)])


def report(checks: list[QACheck]) -> QAReport:
    return QAReport(passed=all(c.passed for c in checks), checks=checks)
