"""Studio pipeline: one call produces one finished, QA-checked piece in output/<date>/<id>/.

Order matters for cost: free checks (safety rules) and the cheap fact check run before any
image is generated, so a bad draft never costs image money.
"""

import hashlib
import json
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image

from instapost import budget, history, qa
from instapost.brand import Brand, load_brand
from instapost.config import IMAGE_KEYS, get_settings, require
from instapost.models import Draft, FactCheckResult, FactCheckVerdict, Format, Pillar, QAReport
from instapost.render import carousel, music, video
from instapost.render.page import PageContent, render_page
from instapost.render.scene import compose_reel
from instapost.studio import fact_check, images, safety, writer


@dataclass
class Result:
    item_id: str
    folder: Path
    status: str  # "needs_review" or "blocked"
    qa: QAReport
    fact: FactCheckResult


def load_sample(fmt: Format) -> Draft:
    path = get_settings().data_dir / "samples" / f"{fmt.value}.json"
    return Draft.model_validate_json(path.read_text(encoding="utf-8"))


def _item_id(fmt: Format) -> str:
    return f"{date.today().isoformat()}-{fmt.value}-{secrets.token_hex(3)}"


def _write_json(path: Path, obj) -> None:
    data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class _KeepDraftOnQuota:
    """Saves photo credits next to the post; on a quota refusal, says where the
    already-paid text is so it can be reused."""

    def __init__(self, provider, folder: Path):
        self.provider, self.folder, self.name = provider, folder, provider.name

    def generate(self, prompt: str, portrait: bool):
        try:
            img = self.provider.generate(prompt, portrait)
            if credit := img.info.get("credit"):
                with open(self.folder / "image_credits.txt", "a", encoding="utf-8") as f:
                    f.write(f"{'background' if portrait else 'drawing'}: {credit}\n")
            return img
        except images.QuotaError as e:
            raise images.QuotaError(
                f"{e}\nThe text is saved; reuse it once images work, so you don't pay for it "
                f"again:  instapost make <format> --draft {self.folder / 'draft.json'}") from None


def make(
    fmt: Format,
    pillar: Pillar,
    topic: str | None = None,
    *,
    offline: bool = False,
    draft: Draft | None = None,
    image_provider: str | None = None,
    vision_qa: bool | None = None,
    brand: Brand | None = None,
    on_step: Callable[[str], None] | None = None,
    plan: dict | None = None,
    template: str | None = None,
) -> Result:
    """`on_step` receives a short label for each stage, for live progress on the dashboard.
    `template` asks for one reel look (render.templates); without it the looks rotate."""
    item_id = _item_id(fmt)
    # Every paid call below is recorded against this item (dashboard: Costs).
    with budget.tagged(item_id=item_id, format=fmt.value, pillar=Pillar(pillar).value):
        return _make(item_id, fmt, pillar, topic, offline=offline, draft=draft,
                     image_provider=image_provider, vision_qa=vision_qa, brand=brand,
                     on_step=on_step, plan=plan, template=template)


def _make(item_id: str, fmt: Format, pillar: Pillar, topic: str | None, *, offline: bool,
          draft: Draft | None, image_provider: str | None, vision_qa: bool | None,
          brand: Brand | None, on_step: Callable[[str], None] | None,
          plan: dict | None, template: str | None = None) -> Result:
    step = on_step or (lambda _label: None)
    if vision_qa is None:  # look at every finished picture, except in offline/demo runs
        vision_qa = get_settings().vision_check and not offline
    brand = brand or load_brand()
    if not offline:
        # Check every key before the first paid call, not halfway through.
        chain = images.chain_names(image_provider)
        # With fallbacks, a missing image key just means the next source is used.
        require("anthropic_api_key",
                *([IMAGE_KEYS[chain[0]]] if len(chain) == 1 and chain[0] in IMAGE_KEYS else []))
    step("writing")
    if draft is None:
        draft = load_sample(fmt) if offline else writer.write(
            brand, fmt, pillar, topic, history.recent_hooks(), plan)

    folder = get_settings().output_dir / date.today().isoformat() / item_id
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / "draft.json", draft)
    seed = int(hashlib.sha1(item_id.encode()).hexdigest()[:8], 16)

    step("safety and fact check")
    safety_issues = safety.check(draft)
    fact = (fact_check.offline_fact_check(draft) if offline or safety_issues
            else fact_check.fact_check(draft))
    _write_json(folder / "fact_check.json", fact)
    checks = qa.check_text(draft, fact, safety_issues)

    if any(not c.passed for c in checks):
        return _finish(folder, item_id, fmt, pillar, draft, fact, qa.report(checks))

    step("generating images")
    provider = _KeepDraftOnQuota(
        images.get_provider("placeholder" if offline else image_provider), folder)
    # Blurred background scenes: free sources first (the image API only as a last resort).
    scenes_src = provider if offline or image_provider else _KeepDraftOnQuota(
        images.scene_provider(), folder)
    reference = ((fact.reference_label, fact.reference_text)
                 if fact.reference_label and fact.reference_text else None)

    if fmt == Format.REEL_BOOK_POV:
        illus = _drawing(draft, pillar, brand, provider, folder, offline)
        step("rendering")
        page_no = 108 if pillar == Pillar.SHIV_DHYAN else random.Random(seed).randint(12, 240)
        content = PageContent(
            header=brand.header_text, page_number=page_no, hook_lines=draft.hook_lines,
            body=draft.body, reference_label=fact.reference_label,
            reference_text=fact.reference_text, footer=brand.printed_handle, illustration=illus,
        )
        comp, vision = _reel_look(brand, content, draft, seed, folder, scenes_src, offline,
                                  vision_qa, template)
        still, text_boxes = comp.still, comp.text_boxes
        duration = video.reel_duration(video.read_time_seconds(*draft.hook_lines, draft.body))
        motion = video.Motion(still, brand, duration, seed, comp)
        checks += qa.check_reel(draft, motion, text_boxes)
        still.save(folder / "cover.jpg", quality=92)
        if vision_qa:
            checks.append(vision or qa.check_vision(still, brand))
        if all(c.passed for c in checks):
            step("encoding video")
            track = music.pick(pillar)
            video.encode(still, brand, duration, folder / "reel.mp4", seed, comp, music=track,
                         app_copy=folder / "reel_for_app.mp4")
            if track is not None:
                (folder / "music.txt").write_text(music.rel(track) + "\n", encoding="utf-8")

    elif fmt == Format.CAROUSEL_SHLOK:
        illus = _drawing(draft, pillar, brand, provider, folder, offline)
        look = _pick_look(fmt, seed, template, folder)
        slides = carousel.render_slides(brand, draft, illus, reference, seed, look=look)
        for i, slide in enumerate(slides, 1):
            slide.save(folder / f"slide_{i:02d}.jpg", quality=92)
        checks.append(qa.QACheck(name="slide_count", passed=3 <= len(slides) <= 10,
                                 detail=f"{len(slides)} slides"))
        if vision_qa:
            checks.append(qa.check_vision(slides[0], brand))

    elif fmt == Format.STORY_LINE:
        line = draft.story_line or draft.hook_lines[0]
        look = _pick_look(fmt, seed, template, folder)
        scene = (scenes_src.generate(images.scene_prompt(draft.scene_prompt), portrait=True)
                 if look == "card" else None)  # the other looks need no photo
        story = carousel.render_story(brand, scene, line, seed, look=look)
        story.save(folder / "story.jpg", quality=92)
        poll = "\n".join([draft.poll_question or "", *draft.poll_options])
        (folder / "story_poll.txt").write_text(
            "Add this as a poll sticker in the Instagram app (the API cannot add stickers):\n"
            + poll + "\n", encoding="utf-8")
        if vision_qa:
            checks.append(qa.check_vision(story, brand))

    return _finish(folder, item_id, fmt, pillar, draft, fact, qa.report(checks))


def _drawing(draft: Draft, pillar: Pillar, brand: Brand, provider, folder: Path,
             offline: bool) -> Image.Image:
    """The page's ink drawing: library match, new Gemini drawing, pillar drawing, old way."""
    from instapost.studio import drawings

    def old_way() -> Image.Image:
        return provider.generate(
            images.illustration_prompt(draft.illustration_prompt, brand.illustration_style),
            portrait=False)

    img, source = drawings.illustration(draft.illustration_prompt, pillar, fallback=old_way,
                                        generate=False if offline else None)
    (folder / "drawing.txt").write_text(source + "\n", encoding="utf-8")
    return img


def _pick_look(fmt: Format, seed: int, template: str | None, folder: Path) -> str:
    """The carousel or story look for this post (rotating, see render.templates)."""
    from instapost.render import templates

    kind = templates.kind_of(fmt)
    look = templates.order(templates.enabled(kind), seed, template, kind)[0]
    templates.mark_used(look, kind)
    (folder / "template.txt").write_text(look + "\n", encoding="utf-8")
    return look


def _reel_look(brand: Brand, content: PageContent, draft: Draft, seed: int, folder: Path,
               provider, offline: bool, vision_qa: bool, template: str | None):
    """(composition, vision check or None) in the first look that works, rotating between
    the enabled templates; the look used is saved in template.txt."""
    from instapost.render import templates

    for look in templates.order(templates.enabled(), seed, template):
        vision = None
        if look == "book_photo":
            if get_settings().reel_style != "plate":
                continue
            comp, vision = _book_photo_reel(brand, render_page(brand, content, seed), seed,
                                            folder, offline, vision_qa)
            if comp is None:
                continue  # no ready book photo fits: the next look
        elif look == "classic":
            scene = provider.generate(images.scene_prompt(draft.scene_prompt), portrait=True)
            comp = compose_reel(brand, scene, render_page(brand, content, seed), seed)
        else:
            comp = templates.compose_card(brand, look, content, seed)
        templates.mark_used(look)
        (folder / "template.txt").write_text(look + "\n", encoding="utf-8")
        return comp, vision
    raise RuntimeError("no reel template could be made")  # unreachable: classic is last


def _book_photo_reel(brand: Brand, page, seed: int, folder: Path, offline: bool,
                     vision_qa: bool):
    """(composition, vision check) on a book photo, or (None, None). When the image check
    blames the photo itself (page not sitting on the paper, not natural), the next photo is
    tried and that photo goes back to the Backgrounds page for a check."""
    from instapost.studio import plates

    tried: set[str] = set()
    for _ in range(3):
        found = _on_book_photo(brand, page, seed, search=not offline, exclude=tried)
        if found is None:
            return None, None
        comp, book = found
        check = qa.check_vision(comp.still, brand, photo=True) if vision_qa else None
        if check is not None and qa.photo_fault(check):
            plates.flag(book.id, check.detail)
            tried.add(book.id)
            continue
        plates.mark_used(book.id)
        (folder / "book_photo.txt").write_text(f"{book.id}\n{book.credit}\n", encoding="utf-8")
        if book.credit:
            with open(folder / "image_credits.txt", "a", encoding="utf-8") as f:
                f.write(f"book photo: {book.credit}\n")
        return comp, check
    return None, None


def _on_book_photo(brand: Brand, page, seed: int, search: bool = True,
                   exclude: set[str] | None = None):
    """(composition, plate) for the first ready photo the page fits on, or None."""
    from instapost.render.plate import compose_on_plate
    from instapost.studio import plates

    for n, book in enumerate(plates.candidates(seed, exclude=exclude, search=search)):
        if n >= 3:
            break
        comp = compose_on_plate(brand, book, page, seed)
        if qa.unsafe_boxes(comp.text_boxes, photo=True):
            continue  # text would sit under Instagram's buttons on this photo
        return comp, book
    return None


def _finish(folder: Path, item_id: str, fmt: Format, pillar: Pillar, draft: Draft,
            fact: FactCheckResult, report: QAReport) -> Result:
    status = "needs_review" if report.passed else "blocked"
    _write_json(folder / "qa.json", report)
    (folder / "caption.txt").write_text(qa.caption_text(draft) + "\n", encoding="utf-8")
    _write_json(folder / "status.json", {
        "id": item_id, "format": fmt.value, "pillar": pillar.value, "status": status,
        "fact_check": fact.verdict.value,
        "needs_human_fact_review": fact.verdict == FactCheckVerdict.NEEDS_HUMAN,
        "alternative_hooks": draft.alternative_hooks, "alt_text": draft.alt_text,
    })
    if report.passed:
        history.add(item_id, fmt.value, pillar.value, draft.topic, " ".join(draft.hook_lines))
    return Result(item_id=item_id, folder=folder, status=status, qa=report, fact=fact)
