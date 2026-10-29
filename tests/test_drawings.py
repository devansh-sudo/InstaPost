import io
import json

import numpy as np
from PIL import Image, ImageDraw

from instapost import pipeline
from instapost.config import get_settings
from instapost.models import Format, Pillar
from instapost.render import motifs, video
from instapost.studio import drawings, images
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def _sketch(colour=(30, 30, 30), paper=(236, 228, 210)) -> Image.Image:
    img = Image.new("RGB", (600, 400), paper)
    d = ImageDraw.Draw(img)
    d.ellipse([150, 80, 450, 330], outline=colour, width=6)
    for x in range(180, 430, 12):
        d.line([(x, 200), (x + 40, 300)], fill=colour, width=2)
    return img


def test_starter_set_draws_and_is_tagged(tmp_path):
    paths = motifs.write_starters(tmp_path)
    assert len(paths) == len(motifs.STARTERS) >= 10
    for p in paths:
        img = Image.open(p)
        assert drawings.is_drawing(img), p.name
        assert json.loads(p.with_suffix(".json").read_text())["tags"]


def test_ink_cleans_any_drawing_to_black_on_white():
    out = np.asarray(drawings.ink(_sketch(paper=(214, 200, 170))))
    assert out.max() == 255 and out.min() < 30           # paper -> white, ink -> black
    assert (out == 255).mean() > 0.6
    photo = Image.fromarray(np.random.default_rng(0).integers(0, 255, (300, 300, 3),
                                                              dtype=np.uint8))
    assert not drawings.is_drawing(photo)
    assert drawings.is_drawing(_sketch())


def test_matching_and_least_recently_used():
    sh = drawings.match("Lord Shiva meditating facing the Himalayas, crescent moon",
                        "shiv_dhyan")
    assert sh[0][1].id == "library:starter/kailash-moon" and sh[0][0] >= 6
    kr = drawings.match("Krishna playing the flute", "gita")
    assert kr[0][1].id == "library:starter/bansuri-feather"
    first, src = drawings.illustration("a diya", "life_lesson", generate=False)
    assert src == "library:starter/diya" and first.mode == "L"
    # Two drawings tie on the pillar only: the one not used yet goes first.
    a = drawings.illustration("something unusual", "kabir", generate=False)[1]
    b = drawings.illustration("something unusual", "kabir", generate=False)[1]
    assert a != b


def test_new_subject_asks_gemini_once_then_reuses_it(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    get_settings.cache_clear()
    calls = []

    class FakeGemini:
        def generate(self, prompt, portrait):
            calls.append(prompt)
            return _sketch()

    monkeypatch.setattr(images, "GeminiProvider", FakeGemini)
    img, src = drawings.illustration("A grandmother telling a story to children",
                                     "life_lesson")
    assert src.startswith("made:") and len(calls) == 1
    assert "engraving" in calls[0] and "no text" in calls[0].lower()
    again, src2 = drawings.illustration("grandmother story children", "life_lesson")
    assert src2 == src and len(calls) == 1                 # reused, not paid twice


def test_photo_from_gemini_is_not_used(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    get_settings.cache_clear()

    class PhotoGemini:
        def generate(self, prompt, portrait):
            return Image.fromarray(np.random.default_rng(1).integers(
                0, 255, (300, 400, 3), dtype=np.uint8))

    monkeypatch.setattr(images, "GeminiProvider", PhotoGemini)
    _, src = drawings.illustration("A grandmother telling a story", "life_lesson")
    assert src.startswith("library:")                      # fell back to the library


def test_posts_use_library_drawings(monkeypatch):
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    r = pipeline.make(Format.CAROUSEL_SHLOK, Pillar.GITA, offline=True)
    assert (r.folder / "drawing.txt").read_text().startswith("library:starter/")


def test_drawings_page_upload_and_toggle(web):  # noqa: F811
    _login(web)
    page = web.get("/drawings")
    assert page.status_code == 200 and "kailash-moon" in page.text
    assert web.get("/drawings/preview?id=library:starter/diya").status_code == 200
    token = _csrf(web, "/drawings")
    buf = io.BytesIO()
    _sketch().save(buf, "PNG")
    r = web.post("/drawings/upload", data={"token": token, "tags": "shiva, nandi"},
                 files=[("files", ("Nandi Bull.png", buf.getvalue())),
                        ("files", ("photo.png", b"nope"))], follow_redirects=False)
    assert r.status_code == 303
    own = {d.id: d for d in drawings.library() if d.source == "own"}
    assert "own:Nandi-Bull" in own and {"shiva", "nandi"} <= own["own:Nandi-Bull"].tags
    web.post("/drawings/toggle", data={"token": token, "id": "own:Nandi-Bull", "on": "0"})
    assert "own:Nandi-Bull" not in {d.id for d in drawings.library()}
