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

from instapost import history, qa
from instapost.brand import Brand, load_brand
from instapost.config import get_settings
from instapost.models import Draft, FactCheckResult, FactCheckVerdict, Format, Pillar, QAReport
from instapost.render import carousel, video
from instapost.render.page import PageContent, render_page
from instapost.render.scene import compose
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


def make(
    fmt: Format,
    pillar: Pillar,
    topic: str | None = None,
    *,
    offline: bool = False,
    draft: Draft | None = None,
    image_provider: str | None = None,
    vision_qa: bool = False,
    brand: Brand | None = None,
    on_step: Callable[[str], None] | None = None,
    plan: dict | None = None,
) -> Result:
    """`on_step` receives a short label for each stage, for live progress on the dashboard."""
    step = on_step or (lambda _label: None)
    brand = brand or load_brand()
    step("writing")
    if draft is None:
        draft = load_sample(fmt) if offline else writer.write(
            brand, fmt, pillar, topic, history.recent_hooks(), plan)

    item_id = _item_id(fmt)
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
    provider = images.get_provider("placeholder" if offline else image_provider)
    reference = ((fact.reference_label, fact.reference_text)
                 if fact.reference_label and fact.reference_text else None)

    if fmt == Format.REEL_BOOK_POV:
        illus = provider.generate(
            images.illustration_prompt(draft.illustration_prompt, brand.illustration_style),
            portrait=False)
        scene = provider.generate(images.scene_prompt(draft.scene_prompt), portrait=True)
        step("rendering")
        page_no = 108 if pillar == Pillar.SHIV_DHYAN else random.Random(seed).randint(12, 240)
        page = render_page(brand, PageContent(
            header=brand.header_text, page_number=page_no, hook_lines=draft.hook_lines,
            body=draft.body, reference_label=fact.reference_label,
            reference_text=fact.reference_text, footer=brand.handle, illustration=illus,
        ), seed)
        still, _, text_boxes = compose(brand, scene, page, seed)
        duration = video.reel_duration(video.read_time_seconds(*draft.hook_lines, draft.body))
        motion = video.Motion(still, brand, duration, seed)
        checks += qa.check_reel(draft, motion, text_boxes)
        still.save(folder / "cover.jpg", quality=92)
        if vision_qa:
            checks.append(qa.check_vision(still))
        if all(c.passed for c in checks):
            step("encoding video")
            video.encode(still, brand, duration, folder / "reel.mp4", seed)

    elif fmt == Format.CAROUSEL_SHLOK:
        illus = provider.generate(
            images.illustration_prompt(draft.illustration_prompt, brand.illustration_style),
            portrait=False)
        slides = carousel.render_slides(brand, draft, illus, reference, seed)
        for i, slide in enumerate(slides, 1):
            slide.save(folder / f"slide_{i:02d}.jpg", quality=92)
        checks.append(qa.QACheck(name="slide_count", passed=3 <= len(slides) <= 10,
                                 detail=f"{len(slides)} slides"))
        if vision_qa:
            checks.append(qa.check_vision(slides[0]))

    elif fmt == Format.STORY_LINE:
        line = draft.story_line or draft.hook_lines[0]
        scene = provider.generate(images.scene_prompt(draft.scene_prompt), portrait=True)
        story = carousel.render_story(brand, scene, line, seed)
        story.save(folder / "story.jpg", quality=92)
        poll = "\n".join([draft.poll_question or "", *draft.poll_options])
        (folder / "story_poll.txt").write_text(
            "Add this as a poll sticker in the Instagram app (the API cannot add stickers):\n"
            + poll + "\n", encoding="utf-8")
        if vision_qa:
            checks.append(qa.check_vision(story))

    return _finish(folder, item_id, fmt, pillar, draft, fact, qa.report(checks))


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
