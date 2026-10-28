import numpy as np

from instapost import pipeline, qa
from instapost.brand import load_brand
from instapost.models import Format, Pillar
from instapost.render import audio, video
from instapost.render.page import PageContent, render_page
from instapost.render.scene import compose
from instapost.studio.images import PlaceholderProvider


def _reel_parts():
    brand = load_brand()
    draft = pipeline.load_sample(Format.REEL_BOOK_POV)
    provider = PlaceholderProvider()
    page = render_page(brand, PageContent(
        header=brand.header_text, page_number=108, hook_lines=draft.hook_lines, body=draft.body,
        reference_label="श्रीमद्भगवद्गीता 2.48", reference_text="योगस्थः कुरु कर्माणि।",
        footer=brand.handle, illustration=provider.generate("x", portrait=False)))
    still, hook_boxes, text_boxes = compose(brand, provider.generate("y", portrait=True), page)
    return brand, draft, still, hook_boxes, text_boxes


def test_reel_frame_passes_visual_qa():
    brand, draft, still, hook_boxes, text_boxes = _reel_parts()
    assert still.size == (1080, 1920)
    # Hook must be in the middle band of the screen, where the eye lands first.
    assert all(400 < b.y0 and b.y1 < 1300 for b in hook_boxes)
    duration = video.reel_duration(video.read_time_seconds(*draft.hook_lines, draft.body))
    checks = qa.check_reel(draft, video.Motion(still, brand, duration), text_boxes)
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]


def test_ambience_loops_without_click():
    a = audio.ambience(8.0)
    assert a.shape == (8 * audio.SR, 2)
    assert np.abs(a).max() <= 0.51
    # The jump from the last sample back to the first is no bigger than a normal step.
    step = np.abs(np.diff(a, axis=0)).max()
    assert np.abs(a[0] - a[-1]).max() <= step * 1.5


def test_offline_story_and_carousel_end_to_end():
    story = pipeline.make(Format.STORY_LINE, Pillar.LIFE_LESSON, offline=True)
    assert story.status == "needs_review"
    assert (story.folder / "story.jpg").exists()
    assert (story.folder / "story_poll.txt").exists()

    car = pipeline.make(Format.CAROUSEL_SHLOK, Pillar.GITA, offline=True)
    assert car.status == "needs_review"
    slides = sorted(car.folder.glob("slide_*.jpg"))
    assert 3 <= len(slides) <= 10


def test_bad_draft_is_blocked_before_images():
    draft = pipeline.load_sample(Format.REEL_BOOK_POV).model_copy(
        update={"cta": "शेयर नहीं किया तो बुरा होगा"})
    result = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True, draft=draft)
    assert result.status == "blocked"
    assert not (result.folder / "cover.jpg").exists()


def test_reel_reveals_body_after_the_hook_and_loops():
    from instapost.render.scene import compose_reel

    brand = load_brand()
    draft = pipeline.load_sample(Format.REEL_BOOK_POV)
    provider = PlaceholderProvider()
    page = render_page(brand, PageContent(
        header=brand.header_text, page_number=108, hook_lines=draft.hook_lines, body=draft.body,
        reference_label="श्रीमद्भगवद्गीता 2.48", reference_text="योगस्थः कुरु कर्माणि।",
        footer=brand.handle, illustration=provider.generate("x", portrait=False)))
    comp = compose_reel(brand, provider.generate("y", portrait=True), page)
    assert len(comp.layers) >= 3  # body line(s), divider, reference
    d = 10.0
    motion = video.Motion(comp.still, brand, d, comp=comp)
    first, mid = motion.frame_at(0).astype(np.int16), motion.frame_at(d / 2).astype(np.int16)
    last = motion.frame_at(d - 1 / 30).astype(np.int16)
    body = comp.layers[0]
    h, w = body.alpha.shape[:2]
    rows, cols = slice(body.y, body.y + h), slice(body.x, body.x + w)
    # The hook region is the same at the start; the body region changes as the line appears.
    assert np.abs(mid[rows, cols] - first[rows, cols]).mean() > 8
    assert np.abs(first - last).mean() < 1.5  # seamless loop
    # Opacity runs 0 -> 1 -> 0 across the reel.
    assert video.reveal_state(0, 4, 0, d)[0] == 0
    assert video.reveal_state(3, 4, d * 0.5, d)[0] == 1
    assert video.reveal_state(3, 4, d, d)[0] == 0


def test_balanced_wrap_has_no_orphan():
    from instapost.render.text import font, text_width, wrap, wrap_balanced

    brand = load_brand()
    f = font(brand.font_path("body"), 38)
    text = "जो हाथ में है उसे पूरे मन से करें, बाकी को शांत मन से स्वीकार करें। यही समता है।"
    greedy, balanced = wrap(f, text, 760), wrap_balanced(f, text, 760)
    assert len(balanced) == len(greedy) == 2
    widths = [text_width(f, ln) for ln in balanced]
    assert min(widths) / max(widths) > 0.7
