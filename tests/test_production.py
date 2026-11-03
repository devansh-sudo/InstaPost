from pathlib import Path

from sqlalchemy import select

from instapost import approvals, quick
from instapost.config import get_settings
from instapost.db import AuditLog, Item, ItemStatus, PlanEntry, session, utcnow
from instapost.learn import planner
from instapost.models import Pillar
from instapost.publish import publisher
from instapost.render import music
from instapost.worker import Worker
from tests.test_music import _reel_item

MONTH = Path(__file__).parent.parent / "data" / "plan" / "2026-10-29.yaml"


def test_month_plan_loads_and_reloads_without_duplicates():
    with session() as s:
        first = planner.load_month(s, MONTH)
        assert first["entries"] == 107 and first["days"] == 30
        ours = s.scalars(select(PlanEntry).where(PlanEntry.edited_by == planner.MONTH_PLAN_ACTOR,
                                                 PlanEntry.status == "planned")).all()
        assert len(ours) == 107
        assert all(Pillar(e.pillar) and e.topic for e in ours)
        assert all(e.audio for e in ours if e.format == "reel_book_pov")
        planner.load_month(s, MONTH)
        again = s.scalars(select(PlanEntry).where(PlanEntry.edited_by == planner.MONTH_PLAN_ACTOR,
                                                  PlanEntry.status == "planned")).all()
        assert len(again) == 107
        # Days already made are left alone when the file is loaded again mid-month.
        from instapost.db import set_kv
        set_kv(s, "generated:2026-10-29", "done")
        s.flush()
        third = planner.load_month(s, MONTH)
        assert third["entries"] == 107 - 4
        assert any("2026-10-29: already made" in w for w in third["warnings"])


def test_trending_suggestions_match_pillar_and_mood(tmp_path, monkeypatch):
    folder = tmp_path / "data" / "music"
    folder.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    assert music.suggest_trending(Pillar.GITA) == []
    (folder / "trending.yaml").write_text(
        (Path(__file__).parent.parent / "data/music/trending.example.yaml").read_text())
    names = [t["name"] for t in music.suggest_trending(Pillar.GITA, "soft flute morning")]
    assert names[0] == "Krishna flute (bansuri)"
    assert "Har Har Mahadev" not in names
    # The music picker ignores the yaml file.
    assert music.pick(Pillar.GITA) is None


def test_app_mode_sends_the_package_once_and_waits_for_posted(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_REELS", "app")
    get_settings.cache_clear()
    monkeypatch.setattr(publisher, "configured_platforms", lambda: {"instagram"})
    with session() as s:
        item = _reel_item(s)
        approvals.approve(s, item, "test", when=utcnow())
        assert item.manual_platforms == ["instagram"]
        item_id = item.id
        assert "• search:" in quick.sound_suggestions(s, item)[0]

    sent: list = []

    class FakeBot:
        def _send_for_app(self, *args):
            sent.append(args)

    w = Worker()
    w.bot = FakeBot()
    w.notify = lambda text: None
    w.publish_due()
    w.publish_due()
    assert len(sent) == 1 and sent[0][0] == item_id and sent[0][3] == []
    with session() as s:
        item = s.get(Item, item_id)
        assert item.status == ItemStatus.APPROVED and item.scheduled_at is None
        actions = s.scalars(select(AuditLog.action).where(AuditLog.item_id == item_id)).all()
        assert actions.count("app_package_sent") == 1


def test_three_reels_a_day_at_set_times_and_the_plan_uses_them():
    from instapost import cli
    from instapost.schedule import load_schedule

    assert cli.main(["reel-times", "13:00,08:00,20:00"]) == 0
    sched = load_schedule()
    assert sched.slots["reel_book_pov"] and [t.strftime("%H:%M") for t in
                                             sched.slot_times("reel_book_pov")] == \
        ["08:00", "13:00", "20:00"]
    assert all(sched.daily_plan[d]["reel_book_pov"] == 3 for d in sched.daily_plan)
    assert sched.max_posts_per_day["youtube"] >= 5 and sched.max_posts_per_day["facebook"] >= 4
    with session() as s:
        planner.load_month(s, MONTH)
        reels = s.scalars(select(PlanEntry).where(PlanEntry.day == "2026-11-03",
                                                  PlanEntry.format == "reel_book_pov",
                                                  PlanEntry.status == "planned")).all()
        assert sorted(e.slot for e in reels) == ["08:00", "13:00", "20:00"]
        ours = [e for e in reels if e.edited_by == planner.MONTH_PLAN_ACTOR]
        assert len(ours) == 1 and ours[0].slot == "20:00" and "नीलकंठ" in ours[0].topic
        diwali = s.scalars(select(PlanEntry).where(
            PlanEntry.day == "2026-11-08", PlanEntry.format == "reel_book_pov",
            PlanEntry.edited_by == planner.MONTH_PLAN_ACTOR)).one()
        assert diwali.slot == "08:00"
