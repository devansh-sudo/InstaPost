import json
import random
from datetime import timedelta
from types import SimpleNamespace

from instapost import pipeline, qa
from instapost.config import get_settings
from instapost.db import utcnow
from instapost.learn import assets
from instapost.models import Format, Pillar
from instapost.render import video
from instapost.studio import plates
from tests.test_plates import QUAD, _book_photo


def _row(photo: str, score: float):
    item = SimpleNamespace(format="reel_book_pov", pillar="gita",
                           features={"pillar": "gita", "photo": photo, "drawing": "library:x"})
    return SimpleNamespace(item=item, score=score, published_at=utcnow() - timedelta(days=3))


def test_photos_that_score_well_come_up_more_often():
    rows = [_row("good", 80) for _ in range(8)] + [_row("bad", 15) for _ in range(8)]
    beliefs = assets.arms("photo", rows)
    assert beliefs["good"].mean > 0.7 and beliefs["bad"].mean < 0.3
    rng = random.Random(1)
    firsts = [assets.order("photo", ["bad", "good", "new"], rng, beliefs)[0]
              for _ in range(200)]
    assert firsts.count("good") > firsts.count("bad") * 5
    assert firsts.count("new") > 10          # a new photo still gets its turn
    table = assets.table("photo", rows=rows)
    assert table[0]["option"] == "good" and table[0]["posts"] == 8


def test_most_recently_used_goes_last():
    plates.mark_used("a")
    order = assets.order("photo", ["a", "b", "c"], random.Random(0), beliefs={})
    assert order[-1] == "a"


def _two_plates():
    own = get_settings().state_dir / "plates" / "own"
    own.mkdir(parents=True)
    for name in ("first", "second"):
        _book_photo().save(own / f"{name}.jpg")
        (own / f"{name}.json").write_text(json.dumps({"quad": [list(q) for q in QUAD]}))


def test_image_check_blames_the_photo_then_another_photo_is_used(monkeypatch):
    _two_plates()
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    calls = []

    def fake_structured(**kw):
        calls.append(kw)
        first = len(calls) == 1
        return qa.VisionVerdict(readable=True, looks_real=True, page_fits=not first,
                                problems=[])

    monkeypatch.setattr(qa.llm, "structured", fake_structured)
    r = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True, vision_qa=True,
                      template="book_photo")
    assert r.status == "needs_review", [c for c in r.qa.checks if not c.passed]
    used = (r.folder / "book_photo.txt").read_text().split()[0]
    index = plates.load_index()["plates"]
    flagged = [k for k, v in index.items() if v["status"] == "review"]
    assert len(calls) == 2 and len(flagged) == 1 and used not in flagged
    assert "image check" in index[flagged[0]]["reason"]
    assert "book photo" in calls[0]["content"][1]["text"]


def test_text_problems_block_the_post_and_do_not_blame_the_photo(monkeypatch):
    _two_plates()
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    monkeypatch.setattr(qa.llm, "structured", lambda **kw: qa.VisionVerdict(
        readable=False, looks_real=True, page_fits=True, problems=["broken matra"]))
    r = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True, vision_qa=True,
                      template="book_photo")
    assert r.status == "blocked"
    assert all(v["status"] == "on" for v in plates.load_index()["plates"].values())


def test_posts_record_their_photo_and_drawing(monkeypatch):
    from instapost import approvals
    from instapost.db import session

    _two_plates()
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    r = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True, template="book_photo")
    with session() as s:
        item = approvals.register(s, r, Format.REEL_BOOK_POV, "gita", utcnow().date())
        assert item.features["photo"].startswith("own-")
        assert item.features["drawing"].startswith("library:starter/")


def test_vision_check_is_on_by_default_except_offline(monkeypatch):
    monkeypatch.delenv("VISION_CHECK")
    get_settings.cache_clear()
    seen = []
    monkeypatch.setattr(qa, "check_vision", lambda *a, **k: seen.append(1) or qa.QACheck(
        name="vision", passed=True, detail=""))
    pipeline.make(Format.STORY_LINE, Pillar.GITA, offline=True)
    assert not seen and get_settings().vision_check
