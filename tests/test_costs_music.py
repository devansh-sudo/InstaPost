import subprocess
from types import SimpleNamespace

from instapost import budget, pipeline
from instapost.config import get_settings
from instapost.db import session
from instapost.models import Draft, Format, Pillar
from instapost.render import music
from instapost.stats import costs
from instapost.studio import writer
from tests.test_phase2 import _csrf, _login, _story, web  # noqa: F401


def _tone(path, seconds=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=330:duration={seconds}", "-ac", "2", str(path)], check=True)


def _library(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    return tmp_path / "data" / "music"


def test_folder_name_decides_the_pillar(tmp_path, monkeypatch):
    lib = _library(tmp_path, monkeypatch)
    _tone(lib / "shiv" / "night-calm.mp3")   # "calm" alone would suit life lessons
    _tone(lib / "krishna" / "track1.mp3")
    _tone(lib / ".removed" / "shiv" / "old.mp3")
    names = [music.rel(p) for p in music.tracks()]
    assert names == ["krishna/track1.mp3", "shiv/night-calm.mp3"]  # hidden folders ignored
    assert music.rel(music.pick(Pillar.SHIV_DHYAN)) == "shiv/night-calm.mp3"
    assert music.rel(music.pick(Pillar.GITA)) == "krishna/track1.mp3"
    assert [p.value for p in music.pillars_of(lib / "shiv" / "night-calm.mp3")] == ["shiv_dhyan"]
    assert music.last_used(lib / "krishna" / "track1.mp3")
    assert music.duration(lib / "shiv" / "night-calm.mp3") == 3.0
    assert music.resolve("../../etc/passwd") is None
    assert music.resolve("shiv/night-calm.mp3") is not None
    assert music.safe_name("../Om Namah शिव (1).MP3") == "Om-Namah-1-.MP3"


def test_music_page_upload_play_remove(tmp_path, monkeypatch, web):  # noqa: F811
    lib = _library(tmp_path, monkeypatch)
    _tone(tmp_path / "up.mp3")
    _login(web)
    token = _csrf(web, "/music")
    r = web.post("/music/upload", data={"token": token, "folder": "shiv", "new_folder": ""},
                 files=[("files", ("Om Chant.mp3", (tmp_path / "up.mp3").read_bytes())),
                        ("files", ("notes.txt", b"x"))], follow_redirects=False)
    assert r.status_code == 303
    assert (lib / "shiv" / "Om-Chant.mp3").exists() and not (lib / "shiv" / "notes.txt").exists()
    page = web.get("/music").text
    assert "Om-Chant.mp3" in page and "/music/file/shiv/Om-Chant.mp3" in page
    assert web.get("/music/file/shiv/Om-Chant.mp3").status_code == 200
    assert web.get("/music/file/..%2F..%2Fsecrets").status_code == 404
    r = web.post("/music/remove", data={"token": token, "name": "shiv/Om-Chant.mp3"},
                 follow_redirects=False)
    assert r.status_code == 303 and (lib / ".removed" / "shiv" / "Om-Chant.mp3").exists()
    assert music.tracks() == []


def test_every_paid_call_is_charged_to_its_post(web):  # noqa: F811
    item_id = _story()  # offline: no paid calls, so record two by hand as the pipeline would
    with budget.tagged(item_id=item_id, format="story_line"):
        budget.record("write:story_line", "claude-haiku-5-5", 0.002, **{"in": 900, "out": 400})
        budget.record("write:story_line", "claude-haiku-5-5", 0.002, **{"in": 900, "out": 400})
        budget.record("fact_check", "claude-sonnet-5-5", 0.01, **{"in": 700, "out": 600})
    budget.record("weekly_review", "claude-opus-5-5", 0.2)
    with session() as s:
        c = costs.summary(s, 30)
    post = next(p for p in c["posts"] if p.item_id == item_id)
    assert round(post.usd, 3) == 0.014 and post.stages["Writing"] == 0.004
    assert "written 2 times" in post.why[0]
    assert c["per_format"]["Story"]["n"] == 1 and c["other"]["Weekly review"] == 0.2
    _login(web)
    assert "$0.014" in web.get(f"/items/{item_id}").text
    assert item_id[:20] in web.get("/costs").text or "Cost per post" in web.get("/costs").text


def test_pipeline_tags_spend_with_the_item(monkeypatch):
    seen = {}

    def fake_record(kind, model, usd, **details):
        seen.setdefault("tags", budget._tags.get())

    monkeypatch.setattr(budget, "record", fake_record)
    result = pipeline.make(Format.STORY_LINE, Pillar.GITA, offline=True)
    assert budget._tags.get() is None  # reset afterwards
    with budget.tagged(x=1):
        budget.record("k", "m", 0)
    assert seen["tags"] == {"x": 1}
    assert result.item_id


def test_stories_use_the_small_model_and_the_verse_list_is_cached(monkeypatch):
    calls = []
    sample = pipeline.load_sample(Format.STORY_LINE)

    def fake(**kw):
        calls.append(kw)
        return sample

    monkeypatch.setattr(writer.llm, "structured", fake)
    brand = SimpleNamespace(page_name="t", scene_presets=["a"])
    writer.write(brand, Format.STORY_LINE, Pillar.GITA, None, [])
    writer.write(brand, Format.CAROUSEL_SHLOK, Pillar.GITA, None, [])
    story, carousel = calls
    assert story["model"] == "claude-haiku-5-5" and story["effort"] == "low"
    assert carousel["model"] == get_settings().writer_model
    assert "Verified references" in story["system"] and "Verified" not in story["content"]
    assert story["system"] == carousel["system"]  # same prefix -> one cache entry per model
    assert isinstance(sample, Draft)


def test_platform_badges_and_calendar_range(web, monkeypatch):  # noqa: F811
    from instapost.db import Item, Publication
    from instapost.publish import publisher

    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram", "youtube"})
    item_id = _story()
    with session() as s:
        item = s.get(Item, item_id)
        item.format = "reel_book_pov"
        assert publisher.destinations(item, {"instagram", "youtube"}) == [
            ("instagram", "planned"), ("facebook", "off"), ("youtube", "planned")]
        item.manual_platforms = ["instagram"]
        item.publications.append(Publication(platform="youtube", status="published",
                                             permalink="https://youtube.com/shorts/x"))
        assert publisher.destinations(item, {"instagram", "youtube"}) == [
            ("instagram", "manual"), ("facebook", "off"), ("youtube", "published")]
        assert publisher.post_links(item) == {"youtube": "https://youtube.com/shorts/x"}
    _login(web)
    page = web.get("/calendar?days=30").text
    assert 'class="month"' in page and "Weekly video" in page
    # The waiting post shows where it goes: by hand on Instagram, posted on YouTube.
    assert "Instagram: you post it in the app" in page and "YouTube: posted" in page
    assert "Facebook: not connected" in page
    lib = web.get("/library").text
    assert "YouTube: posted" in lib and 'href="https://youtube.com/shorts/x"' in lib


def test_list_thumbnails_are_small(web):  # noqa: F811
    from PIL import Image

    item_id = _story()
    _login(web)
    r = web.get(f"/thumb/{item_id}")
    assert r.status_code == 200 and len(r.content) < 40_000
    from io import BytesIO
    assert Image.open(BytesIO(r.content)).width <= 240
