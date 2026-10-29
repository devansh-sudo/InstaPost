import json
import subprocess
from datetime import date, timedelta

import pytest
from PIL import Image

from instapost import approvals, weekly
from instapost.db import Item, ItemStatus, session, utcnow
from instapost.publish import publisher, youtube
from instapost.publish.instagram import Published
from instapost.render.video import probe_duration

END = date(2026, 11, 1)


def _post(s, tmp_path, n: int, fmt: str, pillar: str, day: date, media: bool = True) -> Item:
    folder = tmp_path / "posts" / f"p{n}"
    folder.mkdir(parents=True)
    if media and fmt == "reel_book_pov":
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x806040:s=1080x1920:d=2:r=30", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(folder / "reel_for_app.mp4")], check=True)
        Image.new("RGB", (1080, 1920), (120, 90, 60)).save(folder / "cover.jpg")
    elif media and fmt == "carousel_shlok":
        for i in (1, 2):
            Image.new("RGB", (1080, 1350), (90, 70, 50)).save(folder / f"slide_{i:02d}.jpg")
    elif media:
        Image.new("RGB", (1080, 1920), (60, 50, 40)).save(folder / "story.jpg")
    (folder / "draft.json").write_text(json.dumps({"hashtags": ["#गीता", "#शांति"]}))
    (folder / "fact_check.json").write_text(json.dumps({"reference_label": "भगवद्गीता 2.47"}))
    item = Item(id=f"p{n}", format=fmt, pillar=pillar, folder=str(folder),
                status=ItemStatus.PUBLISHED, hook=f"मन शांत रखिए, सब ठीक होगा {n}",
                caption="", for_date=day.isoformat(), duration_s=2.0 if fmt.startswith("reel")
                else None)
    s.add(item)
    s.flush()
    return item


@pytest.fixture
def week(tmp_path):
    with session() as s:
        _post(s, tmp_path, 1, "reel_book_pov", "gita", END - timedelta(days=1))
        _post(s, tmp_path, 2, "reel_book_pov", "shiv_dhyan", END - timedelta(days=5))
        _post(s, tmp_path, 3, "carousel_shlok", "kabir", END - timedelta(days=2))
        _post(s, tmp_path, 4, "story_line", "life_lesson", END)
        _post(s, tmp_path, 5, "story_line", "jeevan_sutra", END - timedelta(days=3))
        _post(s, tmp_path, 6, "reel_book_pov", "gita", END - timedelta(days=9))   # last week
        _post(s, tmp_path, 7, "reel_book_pov", "gita", END, media=False)          # no file
    return tmp_path


def test_plan_chapters_and_metadata(week):
    with session() as s:
        p = weekly.plan(s, END)
        assert [i.id for i in p.posts] == ["p2", "p1", "p3", "p5", "p4"]  # reels by day first
        kinds = [seg.kind for seg in p.segments]
        assert kinds == ["intro", "reel_book_pov", "reel_book_pov", "carousel_shlok", "section",
                         "story_line", "story_line", "outro"]
        reel = p.segments[1]
        assert reel.seconds == 30  # a 2 s reel loops 15 times
        chapters = weekly.chapters(p)
        assert chapters[0][0] == 0 and chapters[0][1].startswith("सूत्र 1")
        assert all(b[0] - a[0] >= 10 for a, b in zip(chapters, chapters[1:], strict=False))
        meta = weekly.metadata(p, weekly.load_brand())
        assert meta["title"].startswith("इस हफ़्ते के 3 जीवन सूत्र")
        assert len(meta["title"]) <= 100
        assert "00:00 सूत्र 1" in meta["description"] and "#गीता" in meta["description"]
        assert meta["description"].count("#जीवनसूत्र") == 1


def test_not_enough_posts(tmp_path):
    with session() as s:
        _post(s, tmp_path, 1, "reel_book_pov", "gita", END)
        with pytest.raises(weekly.NotEnoughPosts):
            weekly.plan(s, END)


def test_render_approve_and_publish_to_youtube(week, monkeypatch):
    for name, value in (("REEL_SECONDS", 4), ("SLIDE_SECONDS", 2), ("STORY_SECONDS", 2),
                        ("INTRO_SECONDS", 2), ("SECTION_SECONDS", 2), ("OUTRO_SECONDS", 2)):
        monkeypatch.setattr(weekly, name, value)
    with session() as s:
        item = weekly.make(s, END)
        folder = weekly.Path(item.folder)
        assert item.format == "weekly_video" and item.status == ItemStatus.NEEDS_REVIEW
        planned = weekly.plan(s, END).duration
        assert abs(probe_duration(folder / "video.mp4") - planned) < 0.6
        assert Image.open(folder / "cover.jpg").size == (1280, 720)
        meta = json.loads((folder / "meta.json").read_text())
        assert meta["posts"] == ["p2", "p1", "p3", "p5", "p4"]

        # Approval: scheduled at the weekly publish time (or soon), YouTube only.
        approvals.approve(s, item, "test")
        assert item.scheduled_at > utcnow()
        monkeypatch.setattr(publisher, "configured_platforms",
                            lambda: {"instagram", "facebook", "youtube"})
        assert publisher.targets_for(item) == ["youtube"]
        sent = {}

        def fake_upload(video, title, description, tags, thumbnail=None):
            sent.update(video=video, title=title, thumbnail=thumbnail, tags=tags)
            return Published(remote_id="yt1", permalink="https://youtube.com/watch?v=yt1")

        monkeypatch.setattr(youtube, "upload_video", fake_upload)
        publisher.publish_item(s, item, host=object())
        assert item.status == ItemStatus.PUBLISHED
        assert sent["video"].name == "video.mp4" and sent["thumbnail"].name == "cover.jpg"
        assert sent["title"] == meta["title"]


def test_youtube_tags_are_limited():
    tags = youtube._tags(["#a" * 1] + [f"#tag{i}" for i in range(40)] + ["x" * 460])
    assert len(tags) <= 15 and sum(len(t) + 1 for t in tags) <= 450
    assert all(not t.startswith("#") for t in tags)


def test_weekly_is_due_once_on_sunday_after_make_at(monkeypatch):
    from datetime import datetime

    sched = weekly.load_schedule()
    sunday = datetime(2026, 11, 1, 17, 0, tzinfo=sched.tz)
    monkeypatch.setattr(type(sched), "local_now", lambda self: sunday)
    with session() as s:
        assert weekly.due(s)
        weekly.mark_tried(s, sunday.date())
        assert not weekly.due(s)
    monkeypatch.setattr(type(sched), "local_now", lambda self: sunday.replace(hour=15))
    with session() as s:
        assert not weekly.due(s)
