import io

import numpy as np
from PIL import Image

from instapost import llm
from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import session
from instapost.models import Draft, FactCheckLLM, FactCheckVerdict, Format
from instapost.pipeline import load_sample
from instapost.render import carousel, custom_looks, templates, video
from instapost.render.page import PageContent
from instapost.studio import fact_check, images
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def _content(brand):
    return PageContent(header=brand.header_text, page_number=12, footer=brand.printed_handle,
                       illustration=None, **templates.SAMPLE_TEXT)


def test_new_designs_render_for_every_kind_and_loop():
    brand = load_brand()
    draft = load_sample(Format.CAROUSEL_SHLOK)
    for look in ("gold_frame", "sunrise", "ganga", "manuscript"):
        for kind in ("reel", "carousel", "story"):
            assert look in templates.looks(kind)
        comp = templates.compose_card(brand, look, _content(brand), 4)
        m = video.Motion(comp.still, brand, 8.0, 4, comp)
        first, last = m.frame_at(0).astype(int), m.frame_at(8 - 1 / 30).astype(int)
        assert np.abs(first - last).mean() < 1.5, look
        assert carousel.render_slides(brand, draft, None, None, 4, look=look)[0].size == (
            1080, 1350)
        assert carousel.render_story(brand, None, "मन शांत हो।", 4, look=look).size == (
            1080, 1920)
    # Dark looks get light text, light looks dark text.
    dark = templates.card_brand(brand, "ganga").colors.ink
    light = templates.card_brand(brand, "sunrise").colors.ink
    assert dark.upper().startswith("#FF") and light.upper().startswith("#3A")
    assert templates.compose_card(brand, "ganga", _content(brand), 1).particles == "embers"
    assert templates.compose_card(brand, "gold_frame", _content(brand), 1).particles == "petals"


def test_new_looks_switch_on_even_after_choices_were_saved():
    from instapost.db import set_kv
    with session() as s:
        set_kv(s, "templates:enabled", "paper,night")  # saved before the new designs existed
    assert {"paper", "night", "gold_frame", "ganga"} <= set(templates.enabled())
    assert "dusk" not in templates.enabled()  # switched off by you: stays off
    with session() as s:
        templates.set_enabled(s, ["paper"], "test")
    assert templates.enabled() == ["paper"]
    cfg = custom_looks.create("Mine", ["reel"])
    assert templates.enabled() == ["paper", cfg["id"]]  # a new look of yours starts on


def test_custom_gradient_and_picture_looks():
    brand = load_brand()
    grad = custom_looks.create("Kashi", ["reel", "story"], top="#102030", bottom="#d08040",
                               pattern="mandala", motion="embers")
    assert grad["id"] in templates.looks("reel") and grad["id"] not in templates.looks(
        "carousel")
    comp = templates.compose_card(brand, grad["id"], _content(brand), 2)
    assert comp.particles == "embers" and comp.still.size == (1080, 1920)
    pic = Image.new("RGB", (1200, 1800), (230, 220, 200))
    light = custom_looks.create("Light", ["carousel"], base="picture", picture=pic,
                                text="dark", dim=0.3)
    b = templates.card_brand(brand, light["id"])
    assert b.colors.ink == custom_looks.DARK_TEXT["ink"]
    slides = carousel.render_slides(brand, load_sample(Format.CAROUSEL_SHLOK), None, None, 2,
                                    look=light["id"])
    assert np.asarray(slides[1]).mean() > 150  # lightened picture, dark text
    custom_looks.delete(light["id"])
    assert light["id"] not in templates.looks("carousel")
    try:
        custom_looks.create("x", ["reel"], base="picture")
    except ValueError:
        pass
    else:
        raise AssertionError("picture look without a picture")


def test_fact_check_uses_the_small_model_first(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw["model"])
        ok = kw["model"] == get_settings().factcheck_first_model
        return FactCheckLLM(verdict=FactCheckVerdict.PASS if ok else FactCheckVerdict.FAIL,
                            issues=[])

    monkeypatch.setattr(llm, "structured", fake)
    draft = load_sample(Format.REEL_BOOK_POV).model_copy(update={"reference_id": None})
    assert fact_check.fact_check(draft).verdict == FactCheckVerdict.PASS
    assert calls == ["claude-haiku-5-5"]  # passed: the expensive model is not asked

    def unsure(**kw):
        calls.append(kw["model"])
        return FactCheckLLM(verdict=FactCheckVerdict.NEEDS_HUMAN
                            if kw["model"] == "claude-haiku-5-5" else FactCheckVerdict.PASS,
                            issues=[])

    calls.clear()
    monkeypatch.setattr(llm, "structured", unsure)
    assert fact_check.fact_check(draft).verdict == FactCheckVerdict.PASS
    assert calls == ["claude-haiku-5-5", "claude-sonnet-5-5"]  # the careful model decides


def test_writer_effort_and_scene_sources(monkeypatch):
    from instapost.models import Pillar
    from instapost.studio import writer

    seen = {}
    monkeypatch.setattr(llm, "structured", lambda **kw: seen.update(kw) or load_sample(
        Format.REEL_BOOK_POV))
    writer.write(load_brand(), Format.REEL_BOOK_POV, Pillar.GITA, None, [])
    assert seen["effort"] == "low" and seen["schema"] is Draft
    monkeypatch.setenv("IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key")
    get_settings.cache_clear()
    names = [p.name for p in images.scene_provider().providers]
    assert names[0] == "unsplash" and names[-1] == "gemini"


def test_builder_page_creates_and_deletes(web):  # noqa: F811
    _login(web)
    token = _csrf(web, "/templates")
    page = web.get("/templates").text
    assert "Create your own look" in page and "Gold frame" in page
    r = web.post("/templates/custom", data={"token": token, "name": "Ghat", "base": "gradient",
                                            "top": "#112233", "bottom": "#aa5522",
                                            "pattern": "lights", "text": "light", "dim": "40",
                                            "motion": "petals", "kinds": ["reel", "story"]},
                 follow_redirects=False)
    assert r.status_code == 303
    mine = custom_looks.all_looks()
    assert len(mine) == 1 and mine[0]["name"] == "Ghat" and mine[0]["motion"] == "petals"
    look_id = mine[0]["id"]
    assert web.get(f"/templates/sample/reel/{look_id}").status_code == 200
    assert web.get(f"/templates/sample/carousel/{look_id}").status_code == 404
    buf = io.BytesIO()
    Image.new("RGB", (900, 1600), (40, 60, 90)).save(buf, "JPEG")
    r = web.post("/templates/custom", data={"token": token, "name": "Photo", "base": "upload",
                                            "kinds": ["carousel"]},
                 files={"picture": ("bg.jpg", buf.getvalue(), "image/jpeg")},
                 follow_redirects=False)
    assert len(custom_looks.all_looks()) == 2
    r = web.post(f"/templates/custom/{look_id}/delete", data={"token": token},
                 follow_redirects=False)
    assert r.status_code == 303 and len(custom_looks.all_looks()) == 1
    assert web.post("/templates/custom/custom-zzzzzz/delete",
                    data={"token": token}).status_code == 404
