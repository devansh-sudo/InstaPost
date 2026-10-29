import json
from pathlib import Path

import numpy as np

from instapost import pipeline, producer, qa
from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, session
from instapost.learn import assets
from instapost.models import Format, Pillar
from instapost.render import templates, video
from instapost.render.page import PageContent
from instapost.render.scene import FRAME_H, FRAME_W
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def _content(brand, illus=None):
    return PageContent(header=brand.header_text, page_number=12, footer=brand.printed_handle,
                       illustration=illus, **templates.SAMPLE_TEXT)


def test_card_looks_fill_the_frame_stay_safe_and_loop():
    brand = load_brand()
    for look in ("paper", "night"):
        comp = templates.compose_card(brand, look, _content(brand), 3)
        assert comp.still.size == (FRAME_W, FRAME_H) and comp.style == "card"
        assert comp.layers and not qa.unsafe_boxes(comp.text_boxes)
        m = video.Motion(comp.still, brand, 8.0, 3, comp)
        first, last = m.frame_at(0).astype(int), m.frame_at(8 - 1 / 30).astype(int)
        assert np.abs(first - last).mean() < 1.5
        assert np.abs(first - m.frame_at(4.0).astype(int)).mean() > 2.0
    night = np.asarray(templates.compose_card(brand, "night", _content(brand), 3).still)
    assert night.mean() < 80  # dark paper, light text


def test_order_puts_asked_look_first_and_classic_last(monkeypatch):
    assert templates.order(["paper", "night", "classic"], 1, first="night")[0] == "night"
    assert templates.order(["classic", "paper"], 1)[-1] == "classic"
    # The one used last goes to the back.
    templates.mark_used("paper")
    assert templates.order(["paper", "night"], 1)[0] == "night"
    assert assets.PREFIX["template"] == "template:"


def test_reel_records_its_look_and_rotates(monkeypatch):
    from instapost import approvals
    from instapost.db import utcnow

    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.history, "most_similar", lambda hook: (0.0, None))
    looks = []
    for _ in range(2):
        r = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True)
        assert r.status == "needs_review", [c for c in r.qa.checks if not c.passed]
        looks.append((r.folder / "template.txt").read_text().strip())
    assert looks[0] != looks[1]  # no book photos ready: paper / night / classic rotate
    with session() as s:
        item = approvals.register(s, r, Format.REEL_BOOK_POV, "gita", utcnow().date())
        assert item.features["template"] == looks[1]


def test_change_look_keeps_the_text_and_replaces_the_item(monkeypatch):
    from instapost.db import utcnow

    monkeypatch.setenv("DEMO_MODE", "true")  # offline: sample text, placeholder images
    get_settings.cache_clear()
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.writer, "write", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no new writing for a new look")))
    old_id = producer.make_one(Format.REEL_BOOK_POV, Pillar.GITA, utcnow().date(), None,
                               "test", template="paper")
    new_id = producer.retemplate(old_id, "night", "test")
    with session() as s:
        old, new = s.get(Item, old_id), s.get(Item, new_id)
        assert old.status == ItemStatus.REJECTED and new.hook == old.hook
        assert json.loads((Path(new.folder) / "draft.json").read_text())["hook_lines"]
        assert new.features["template"] == "night"


def test_templates_page_toggle_and_create_select(web):  # noqa: F811
    _login(web)
    page = web.get("/templates")
    assert page.status_code == 200 and "Night page" in page.text
    for kind, look in (("reel", "night"), ("carousel", "dusk"), ("story", "mandala")):
        assert web.get(f"/templates/sample/{kind}/{look}").status_code == 200, (kind, look)
    assert web.get("/templates/sample/reel/nope").status_code == 404
    assert web.get("/templates/sample/story/book_photo").status_code == 404
    token = _csrf(web, "/templates")
    r = web.post("/templates", data={"token": token, "on": ["reel:paper", "reel:night",
                                                             "story:dusk"]},
                 follow_redirects=False)
    assert r.status_code == 303 and templates.enabled() == ["paper", "night"]
    assert templates.enabled("story") == ["dusk"]
    assert templates.enabled("carousel") == ["paper"]  # none ticked: one stays on
    create = web.get("/create").text
    assert 'name="template"' in create and "Night page" in create and "Drawn book" not in create
    assert 'value="story:dusk"' in create


def test_carousel_and_story_looks(monkeypatch):
    from instapost.render import carousel

    brand = load_brand()
    draft = pipeline.load_sample(Format.CAROUSEL_SHLOK)
    for look in templates.BY_KIND["carousel"]:
        slides = carousel.render_slides(brand, draft, templates._sample_drawing(), None, 2,
                                        look=look)
        assert slides[0].size == (1080, 1350)
    dark = np.asarray(carousel.render_slides(brand, draft, None, None, 2, look="night")[1])
    assert dark.mean() < 90
    for look in ("paper", "mandala", "night", "dusk"):
        story = carousel.render_story(brand, None, "मन शांत हो तो सब साफ़ दिखता है।", 2,
                                      look=look)
        assert story.size == (1080, 1920)
        # The lower part stays free for the poll sticker: no text there.
        low = np.asarray(story.convert("L"), np.float32)[1250:1700, 200:880]
        assert low.std() < 30, look


def test_story_and_carousel_record_their_look(monkeypatch):
    from instapost import approvals
    from instapost.db import utcnow

    monkeypatch.setattr(pipeline.history, "most_similar", lambda hook: (0.0, None))
    r = pipeline.make(Format.STORY_LINE, Pillar.GITA, offline=True, template="dusk")
    assert (r.folder / "template.txt").read_text().strip() == "dusk"
    c = pipeline.make(Format.CAROUSEL_SHLOK, Pillar.GITA, offline=True)
    assert (c.folder / "template.txt").read_text().strip() in templates.BY_KIND["carousel"]
    with session() as s:
        item = approvals.register(s, r, Format.STORY_LINE, "gita", utcnow().date())
        assert item.features["template"] == "story/dusk"
    assert templates.order(["paper", "night"], 1, kind="story")[-1] == "paper"
