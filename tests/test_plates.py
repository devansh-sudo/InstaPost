import json

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from instapost import pipeline
from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.models import Format, Pillar
from instapost.render import plate, video
from instapost.render.page import PageContent, render_page
from instapost.studio import plates
from tests.test_phase2 import _csrf, _login, web  # noqa: F401

QUAD = [(260.0, 520.0), (1180.0, 560.0), (1240.0, 1880.0), (200.0, 1900.0)]


def _book_photo(thumb: bool = True) -> Image.Image:
    """A fake photo: dark textured room, the facing page, the blank right page with a darker
    spine between them, and a skin-coloured thumb over the page's lower-left edge."""
    rng = np.random.default_rng(0)
    room = rng.normal(70, 18, (2400, 1440, 3)).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(room).filter(ImageFilter.GaussianBlur(3))
    d = ImageDraw.Draw(img)
    d.polygon([(40, 500), (QUAD[0][0] - 10, QUAD[0][1]), (QUAD[3][0] - 10, QUAD[3][1]),
               (30, 1920)], fill=(236, 228, 214))                        # facing page
    d.polygon(QUAD, fill=(228, 219, 203))                                 # our page
    d.line([QUAD[0], QUAD[3]], fill=(150, 140, 128), width=14)            # spine
    if thumb:
        d.ellipse([150, 1700, 330, 1960], fill=(208, 150, 118))
    return img


def _page(brand):
    return render_page(brand, PageContent(
        header=brand.header_text, page_number=108,
        hook_lines=["जिसे बदल नहीं सकते,", "उसे स्वीकार करना ही शांति है।"],
        body="जो हाथ में है उसे पूरे मन से करें।", reference_label="श्रीमद्भगवद्गीता 2.47",
        reference_text="कर्मण्येवाधिकारस्ते मा फलेषु कदाचन।", footer="@x", illustration=None), 1)


def test_homography_maps_corners():
    src = [(0, 0), (10, 0), (10, 20), (0, 20)]
    h = plate.homography(src, QUAD)
    for s, q in zip(src, QUAD, strict=True):
        assert np.allclose(plate.apply(h, *s), q, atol=1e-6)


def test_detects_the_blank_page_not_the_facing_page():
    det = plate.detect_page(_book_photo())
    assert det is not None and det.ok, det
    for found, true in zip(det.quad, QUAD, strict=True):
        assert abs(found[0] - true[0]) < 60 and abs(found[1] - true[1]) < 60, det.quad
    printed = _book_photo()
    d = ImageDraw.Draw(printed)
    for y in range(700, 1800, 40):
        d.line([(400, y), (1050, y)], fill=(40, 30, 25), width=6)
    assert "not blank" in plate.detect_page(printed).reason


def test_print_onto_photo_keeps_thumb_and_light():
    brand = load_brand()
    p = plate.prepare("t", _book_photo(), QUAD, seed=1)
    assert p is not None
    comp = plate.compose_on_plate(brand, p, _page(brand), seed=1)
    still, photo = np.asarray(comp.still, np.int16), np.asarray(p.image, np.int16)
    assert comp.style == "plate" and comp.layers
    # Outside the page nothing changes; on the page the text darkens the paper.
    b = comp.page_box
    assert np.abs(still[: int(b.y0) - 5] - photo[: int(b.y0) - 5]).max() == 0
    hook = comp.hook_boxes[0]
    y, x0, x1 = int((hook.y0 + hook.y1) / 2), int(hook.x0), int(hook.x1)
    assert still[y, x0:x1].mean() < photo[y, x0:x1].mean() - 15
    # The base frame (first reel frame) has the hook but not the body yet.
    body = comp.text_boxes[len(comp.hook_boxes)]
    yb = int((body.y0 + body.y1) / 2)
    base = np.asarray(comp.base, np.int16)
    assert np.abs(base[yb, int(body.x0):int(body.x1)] - photo[yb, int(body.x0):int(body.x1)]
                  ).mean() < 2
    # Ink is never printed onto the thumb.
    thumb = np.all(np.abs(photo - [208, 150, 118]) < 30, axis=2)
    if thumb.any():
        assert np.abs(still[thumb] - photo[thumb]).mean() < 3


def test_library_check_review_and_lru(tmp_path, monkeypatch):
    own = get_settings().state_dir / "plates" / "own"
    own.mkdir(parents=True)
    _book_photo().save(own / "a.jpg")
    _book_photo(thumb=False).save(own / "b.jpg")
    (own / "b.json").write_text(json.dumps({"quad": [list(q) for q in QUAD]}))
    Image.new("RGB", (800, 1400), (40, 40, 40)).save(own / "c.jpg")   # no page at all
    data = plates.sync_library()
    st = {k: v["status"] for k, v in data["plates"].items()}
    assert st["own-b"] == "on"            # corners given by hand
    assert st["own-a"] in ("on", "review")  # "review" when it had to cut at a spine
    assert st["own-c"] == "off"
    plates.set_status("own-a", "on")
    first = next(plates.candidates(search=False))
    plates.mark_used(first.id)
    second = next(plates.candidates(search=False))
    assert second.id != first.id
    plates.set_status("own-a", "on", [list(q) for q in QUAD])
    assert plates.load_index()["plates"]["own-a"]["auto"] is False
    assert plates.preview("own-a").exists() and plates.sample("own-a").exists()


def test_reel_uses_a_book_photo_when_one_is_ready(monkeypatch):
    own = get_settings().state_dir / "plates" / "own"
    own.mkdir(parents=True)
    _book_photo().save(own / "sofa.jpg")
    (own / "sofa.json").write_text(json.dumps({"quad": [list(q) for q in QUAD]}))
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)  # the picture is what we test
    r = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True, template="book_photo")
    assert r.status == "needs_review", [c for c in r.qa.checks if not c.passed]
    assert (r.folder / "book_photo.txt").read_text().startswith("own-sofa")


def test_classic_when_reel_style_is_classic(monkeypatch):
    monkeypatch.setenv("REEL_STYLE", "classic")
    get_settings.cache_clear()
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    r = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True)
    assert not (r.folder / "book_photo.txt").exists()


@pytest.mark.parametrize("path", ["/backgrounds"])
def test_backgrounds_page_review_and_corners(web, path):  # noqa: F811
    own = get_settings().state_dir / "plates" / "own"
    own.mkdir(parents=True)
    _book_photo().save(own / "x.jpg")
    _login(web)
    page = web.get(path)
    assert page.status_code == 200 and "own-x" in page.text
    for kind in ("preview", "photo", "sample"):
        assert web.get(f"/backgrounds/own-x/{kind}.jpg").status_code == 200, kind
    assert web.get("/backgrounds/nope/photo.jpg").status_code == 404
    token = _csrf(web, "/backgrounds")
    assert web.get("/backgrounds/own-x/edit").status_code == 200
    bad = web.post("/backgrounds/own-x/corners", data={"token": token, "quad": "[[1,2]]"})
    assert bad.status_code == 400
    ok = web.post("/backgrounds/own-x/corners", follow_redirects=False,
                  data={"token": token, "quad": json.dumps([list(q) for q in QUAD])})
    assert ok.status_code == 303
    assert plates.load_index()["plates"]["own-x"]["status"] == "on"
    web.post("/backgrounds/own-x/status", data={"token": token, "status": "off"})
    assert plates.load_index()["plates"]["own-x"]["status"] == "off"
    up = web.post("/backgrounds/upload", data={"token": token}, follow_redirects=False,
                  files=[("files", ("my photo.jpg", (own / "x.jpg").read_bytes())),
                         ("files", ("evil.jpg", b"not a picture"))])
    assert up.status_code == 303 and (own / "my-photo.jpg").exists()
    assert not (own / "evil.jpg").exists()


def test_unsplash_photos_always_wait_for_approval(tmp_path):
    from instapost.studio import plates

    f = tmp_path / "unsplash-x.jpg"
    _book_photo(thumb=False).save(f)
    entry = plates._entry(f, "unsplash", "Photo by someone")
    assert entry["status"] in ("review", "off") and entry["status"] != "on"
    # Older versions used them straight away: they go back for a check once.
    data = plates.load_index()
    data["plates"]["unsplash-x"] = dict(entry, status="on", auto=True)
    plates.save_index(data)
    assert plates.sync_library()["plates"]["unsplash-x"]["status"] == "review"
    plates.set_status("unsplash-x", "on")
    assert plates.sync_library()["plates"]["unsplash-x"]["status"] == "on"
