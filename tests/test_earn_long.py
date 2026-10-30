import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from instapost import approvals, longform, producer
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, Job, session
from instapost.models import Format, Pillar
from instapost.publish import publisher
from instapost.render import video
from instapost.studio import earn, ebook
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def test_affiliate_link_fits_the_post_and_is_disclosed(monkeypatch):
    with pytest.raises(ValueError):
        earn.add_link("x", "http://not-https.example", [], [])
    gita = earn.add_link("श्रीमद्भगवद्गीता (गीता प्रेस)", "https://amzn.to/abc", ["gita"], ["किताब"])
    earn.add_link("रुद्राक्ष माला", "https://example.com/mala", ["shiv_dhyan"], ["माला"])
    assert earn.pick("gita", "कर्म पर ध्यान")["id"] == gita["id"]
    assert earn.pick("shiv_dhyan", "रुद्राक्ष माला")["title"] == "रुद्राक्ष माला"
    yt = earn.block("youtube", "gita", "कर्म")
    assert "https://amzn.to/abc" in yt and earn.DISCLOSURE in yt and earn.AMAZON_NOTE in yt
    assert earn.block("instagram", "gita", "x") == ""  # no public page without PUBLIC_BASE_URL
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://page.example")
    get_settings.cache_clear()
    assert "bio" in earn.block("instagram", "gita", "x")
    assert earn.public_page() == "https://page.example/links"
    earn.set_options(ebook_url="https://pay.example/ebook")
    assert "https://pay.example/ebook" in earn.block("facebook", "kabir", "x")
    earn.set_options(links_on=False)
    assert earn.block("youtube", "gita", "x") == ""


def _published(s, n: int, pillar=Pillar.GITA, days_ago: int = 0):
    """n published reels (offline sample text), each with a different hook."""
    import json

    from instapost import pipeline
    ids = []
    for _ in range(n):
        r = pipeline.make(Format.REEL_BOOK_POV, pillar, offline=True)
        draft = json.loads((r.folder / "draft.json").read_text())
        draft["hook_lines"] = [f"{draft['hook_lines'][0]} {r.item_id[-6:]}"]
        (r.folder / "draft.json").write_text(json.dumps(draft, ensure_ascii=False))
        (r.folder / "reel.mp4").write_bytes(b"x")
        item = approvals.register(s, r, Format.REEL_BOOK_POV, pillar.value,
                                  date.today() - timedelta(days=days_ago))
        item.status = ItemStatus.PUBLISHED
        ids.append(item.id)
    return ids


def test_ebook_from_approved_posts(monkeypatch):
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    monkeypatch.setattr("instapost.history.most_similar", lambda hook: (0.0, None))
    with session() as s:
        _published(s, 2)
        with pytest.raises(ebook.NotEnoughSutras):
            ebook.build(s, date.today(), date.today())
        _published(s, 4)
        meta = ebook.build(s, date.today() - timedelta(days=1), date.today(), "gita")
    assert meta["sutras"] == 6 and meta["pages"] == 6 + 3
    assert meta["title"] == "गीता के 6 सूत्र"
    pdf = ebook.file(meta["id"], "ebook.pdf")
    assert pdf.read_bytes().startswith(b"%PDF") and ebook.file(meta["id"], "preview.pdf")
    with pytest.raises(KeyError):
        ebook.file(meta["id"], "../../secrets")
    assert ebook.all_books()[0]["id"] == meta["id"]


def test_chapters_only_when_youtube_would_accept_them():
    ok = longform.parse_chapters("00:00 आरंभ\n00:40 जाप\n1:30 ध्यान", 200)
    assert [t for t, _ in ok] == [0, 40, 90]
    assert longform.parse_chapters("00:05 a\n00:40 b\n01:30 c", 200) == []  # not from 0
    assert longform.parse_chapters("00:00 a\n00:05 b\n01:30 c", 200) == []  # 5 s apart
    assert longform.ts(3725) == "1:02:05"


def _wav(path: Path, seconds: int = 70) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=185:duration={seconds}", str(path)], check=True)
    return path


def test_long_audio_upload_and_audio_video(web, tmp_path, monkeypatch):  # noqa: F811
    _login(web)
    token = _csrf(web, "/long-videos")
    wav = _wav(tmp_path / "jaap.wav")
    r = web.post("/long-videos/audio", data={"token": token, "title": "ॐ जाप"},
                 files={"file": ("jaap.wav", wav.read_bytes(), "audio/wav")},
                 follow_redirects=False)
    assert r.status_code == 303 and longform.audio_list() == []  # rights not confirmed
    web.post("/long-videos/audio", data={"token": token, "title": "ॐ जाप", "rights": "1"},
             files={"file": ("jaap.wav", wav.read_bytes(), "audio/wav")})
    [audio] = longform.audio_list()
    assert audio["title"] == "ॐ जाप" and 69 < audio["duration"] < 71
    assert web.get(f"/long-videos/audio/{audio['name']}").status_code == 200
    assert web.get("/long-videos/audio/..%2F..%2F.env").status_code == 404

    def fake_render(still, look, audio_path, out, seed=0):
        out.write_bytes(b"mp4")
        return 70.0

    monkeypatch.setattr(longform, "render_audio_video", fake_render)
    with session() as s:
        item = longform.make_audio_video(s, audio["name"], "ॐ नमः शिवाय 108 जाप",
                                         line="ॐ नमः शिवाय", look="ganga",
                                         chapters="00:00 आरंभ\n00:20 जाप\n00:45 ध्यान")
        assert item.format == approvals.LONG_VIDEO and item.status == ItemStatus.NEEDS_REVIEW
        assert "00:20 जाप" in item.caption and "ॐ जाप" in item.caption
        assert publisher.planned_platforms(item.format) == ["youtube"]
        approvals.approve(s, item, "test")
        assert item.scheduled_at is not None
        assert (Path(item.folder) / "cover.jpg").exists()
    r = web.post("/long-videos/make", data={"token": token, "kind": "audio",
                                            "audio": audio["name"], "title": "T"})
    with session() as s:
        job = s.query(Job).filter(Job.kind == "long_video").one()
        assert job.payload["audio_name"] == audio["name"]
        assert job.payload["video_kind"] == "audio"
    with pytest.raises(approvals.QueueError):
        producer.regenerate(item.id, "test")


def test_audio_video_picture_loops():
    from instapost.brand import load_brand
    still = longform.screen(load_brand(), "ganga", "ॐ नमः शिवाय", "अर्थ", 3)
    assert still.size == (longform.W, longform.H)
    frames = longform.loop_frames(still, "ganga", 3)
    first = next(frames).astype(int)
    *_, last = frames
    assert abs(first - last.astype(int)).mean() < 1.5


def test_monthly_collection_is_due_for_themes_with_enough_posts(monkeypatch):
    monkeypatch.setattr(video, "encode", lambda *a, **k: None)
    monkeypatch.setattr("instapost.history.most_similar", lambda hook: (0.0, None))
    first = date.today().replace(day=1)
    with session() as s:
        for item_id in _published(s, longform.MONTHLY_MIN_POSTS, Pillar.GITA):
            s.get(Item, item_id).for_date = (first - timedelta(days=3)).isoformat()
        _published(s, 2, Pillar.KABIR)
        s.flush()
        assert longform.monthly_due(s, first) == ["gita"]
        assert longform.monthly_due(s, first + timedelta(days=1)) == []  # only on the 1st


def test_public_link_page_and_earn_page(web):  # noqa: F811
    earn.add_link("गीता (गीता प्रेस)", "https://amzn.to/x", ["gita"], [])
    page = web.get("/links")
    assert page.status_code == 200 and "https://amzn.to/x" in page.text
    assert earn.DISCLOSURE in page.text and 'rel="sponsored noopener"' in page.text
    assert web.get("/earn", follow_redirects=False).status_code == 303  # login needed
    _login(web)
    token = _csrf(web, "/earn")
    web.post("/earn/links", data={"token": token, "title": "माला", "url": "https://e.com/m",
                                  "pillars": ["shiv_dhyan"], "tags": "माला"})
    assert any(x["title"] == "माला" for x in earn.load()["links"])
    r = web.post("/earn/ebook", data={"token": token, "start": "2026-10-01",
                                      "end": "2026-10-31"}, follow_redirects=False)
    assert r.status_code == 303
    with session() as s:
        assert s.query(Job).filter(Job.kind == "ebook").count() == 1
