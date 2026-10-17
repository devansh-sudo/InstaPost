"""Phase 4: learner, weekly planner, plan-driven generation, weekly review, pages."""

import random
from datetime import date, timedelta

import pytest

from instapost import approvals, producer
from instapost.db import Item, MetricSnapshot, PlanEntry, Publication, WeeklyReview, session
from instapost.learn import planner, review
from instapost.learn.bandit import Learner, length_bucket, nearest_slot
from instapost.models import Format
from instapost.schedule import load_schedule, to_utc_naive
from instapost.stats import queries
from tests.test_phase2 import _csrf, _login, _story, web  # noqa: F401 - fixtures reused


def _scored_reel(pillar: str, hook: str, score_shares: int, days_ago: int, slot="07:15") -> str:
    """A published reel with features and numbers that give it a known score."""
    item_id = _story()
    sched = load_schedule()
    day = sched.local_now().date() - timedelta(days=days_ago)
    h, m = map(int, slot.split(":"))
    from datetime import datetime
    published = to_utc_naive(datetime.combine(day, datetime.min.time(), tzinfo=sched.tz)
                             .replace(hour=h, minute=m))
    with session() as s:
        item = s.get(Item, item_id)
        item.format, item.pillar, item.status = "reel_book_pov", pillar, "published"
        item.features = {"pillar": pillar, "hook_style": hook, "scene": 0, "length": "medium"}
        pub = Publication(item_id=item_id, platform="instagram", status="published",
                          remote_id="x", attempts=1, published_at=published)
        s.add(pub)
        s.flush()
        s.add(MetricSnapshot(publication_id=pub.id, item_id=item_id, platform="instagram",
                             age_hours=72, metrics={"reach": 1000, "shares": score_shares,
                                                    "saves": score_shares}))
    return item_id


def test_helpers():
    assert length_bucket(7.5) == "short" and length_bucket(10) == "medium"
    assert length_bucket(12) == "long" and length_bucket(None) is None
    assert nearest_slot("20:52", ["07:15", "20:45"]) == "20:45"


def test_learner_prefers_what_scores_better():
    for i in range(6):
        _scored_reel("gita", "question", 40, days_ago=2 + i)   # strong
        _scored_reel("kabir", "statement", 2, days_ago=2 + i)  # weak
    _scored_reel("shiv_dhyan", "statement", 40, days_ago=0)   # too young to count
    with session() as s:
        learner = Learner(queries.post_rows(s))
    assert learner.observations == 12
    fmt = Format.REEL_BOOK_POV
    assert learner.best(fmt, "pillar").option == "gita"
    assert learner.best(fmt, "hook_style").option == "question"
    rng = random.Random(3)
    picks = [learner.sample(fmt, "pillar", rng, ["gita", "kabir"]) for _ in range(200)]
    assert picks.count("gita") > 170
    assert learner.confidence(fmt, "pillar").startswith(("clear leader", "leaning"))
    assert learner.least_tried(fmt, "pillar", rng) not in ("gita", "kabir")


def _next_monday() -> date:
    today = load_schedule().local_now().date()
    return planner.week_start_of(today) + timedelta(days=7)


def test_plan_follows_schedule_counts_and_rules():
    ws = _next_monday()
    with session() as s:
        entries = planner.build_week(s, ws, seed=1)
    sched = load_schedule()
    expected = sum(sum(sched.daily_plan[d].values()) for d in sched.daily_plan)
    assert len(entries) == expected
    monday_first_reel = [e for e in entries if e.day == ws.isoformat()
                         and e.format == "reel_book_pov" and e.position == 0][0]
    assert monday_first_reel.pillar == sched.monday_pillar.value
    for day in {e.day for e in entries}:
        reels = [e for e in entries if e.day == day and e.format == "reel_book_pov"]
        assert len({e.slot for e in reels}) == len(reels)  # no two reels in one slot
        assert len({e.pillar for e in reels}) == len(reels)  # no repeated pillar in a day
    assert all(e.slot in sched.slots[Format(e.format)] for e in entries)


def test_experiments_happen_at_about_the_explore_rate():
    total = experiments = 0
    for seed in range(15):
        with session() as s:
            entries = planner.build_week(s, _next_monday(), seed=seed)
            total += len(entries)
            experiments += sum(1 for e in entries if e.experiment)
    assert 0.08 < experiments / total < 0.35


def test_rebuild_keeps_edited_entries():
    ws = _next_monday()
    with session() as s:
        entries = planner.build_week(s, ws, seed=1)
        target = entries[0]
        target.topic, target.edited_by = "मेरा अपना विषय", "web:admin"
        target_id = target.id
    with session() as s:
        planner.build_week(s, ws, seed=99)
    with session() as s:
        kept = s.get(PlanEntry, target_id)
        assert kept is not None and kept.topic == "मेरा अपना विषय"
        same_slot = s.query(PlanEntry).filter_by(day=kept.day, format=kept.format,
                                                 position=kept.position).count()
        assert same_slot == 1


def test_confirmed_festival_sets_first_reel(monkeypatch):
    ws = _next_monday()
    wed = (ws + timedelta(days=2)).isoformat()
    planner.festivals.cache_clear()
    monkeypatch.setattr(planner, "festivals", lambda: {wed: {
        "name": "Test Utsav", "pillar": "gita", "topic": "उत्सव का संदेश", "confirmed": True}})
    with session() as s:
        entries = planner.build_week(s, ws, seed=4)
    first = [e for e in entries if e.day == wed and e.format == "reel_book_pov"
             and e.position == 0][0]
    assert first.pillar == "gita" and first.topic == "उत्सव का संदेश"
    assert "Test Utsav" in first.reason


def test_unconfirmed_festivals_are_ignored():
    planner.festivals.cache_clear()
    assert planner.festivals() == {}  # the shipped file has only unconfirmed dates


def test_generation_uses_the_plan(monkeypatch):
    day = load_schedule().local_now().date() + timedelta(days=1)
    with session() as s:
        planner.build_week(s, planner.week_start_of(day), seed=2)
        planned = planner.entries_for_day(s, day)
        stories = [e for e in planned if e.format == "story_line"]
    calls = []
    from instapost import pipeline
    real = pipeline.make
    monkeypatch.setattr(pipeline, "make", lambda fmt, pillar, topic=None, **kw: (
        calls.append((fmt, pillar, topic, kw.get("plan"))) or real(fmt, pillar, topic,
                                                                    offline=True)))
    # Only make the stories (fast); mark the rest skipped.
    with session() as s:
        for e in planner.entries_for_day(s, day):
            if e.format != "story_line":
                e.status = "skipped"
    created = producer.generate_day(day, lambda m: None, lambda i: None, force=True)
    assert len(created) == len(stories)
    assert all(plan and "hook_style" in plan for *_x, plan in calls)
    with session() as s:
        used = s.query(PlanEntry).filter_by(day=day.isoformat(), status="used").all()
        assert {e.item_id for e in used} == set(created)
        item = s.get(Item, created[0])
        assert item.preferred_slot in load_schedule().slots[Format.STORY_LINE]
        approvals.approve(s, item, "test")
        from instapost.schedule import to_local
        assert to_local(item.scheduled_at, load_schedule()).strftime("%H:%M") \
            == item.preferred_slot


def test_weekly_review_feeds_topics_into_the_plan(monkeypatch):
    for i in range(3):
        _scored_reel("gita", "question", 30, days_ago=2 + i)
    sent = {}

    def fake_structured(**kw):
        sent.update(kw)
        return review.ReviewOut(
            summary="Gita reels did best.", what_worked=["question hooks"],
            what_did_not=["late slots"], try_next=["more Gita"], cadence_note="Keep cadence.",
            topic_ideas=[review.PillarTopics(pillar="gita", topics=["क्रोध पर गीता"] * 5)])

    monkeypatch.setattr(review.llm, "structured", fake_structured)
    ws = planner.week_start_of(load_schedule().local_now().date())
    with session() as s:
        rv = review.create(s, ws)
        assert "Gita reels did best." in rv.text
    assert sent["model"] == "claude-opus-5-5" and sent["schema"] is review.ReviewOut
    with session() as s:
        assert s.query(WeeklyReview).count() == 1
        entries = planner.build_week(s, ws + timedelta(days=7), seed=5)
        gita = [e for e in entries if e.pillar == "gita"]
        assert gita and any(e.topic == "क्रोध पर गीता" for e in gita)


def test_plan_and_learning_pages(web):  # noqa: F811
    _login(web)
    ws = _next_monday()
    assert "No plan for this week yet" in web.get(f"/plan?week={ws}").text
    with session() as s:
        entries = planner.build_week(s, ws, seed=1)
        entry_id, fmt = entries[0].id, entries[0].format
    page = web.get(f"/plan?week={ws}")
    assert page.status_code == 200 and f'id="pe-{entry_id}"' in page.text
    slot = load_schedule().slots[Format(fmt)][0]
    r = web.post(f"/plan/{entry_id}", data={
        "token": _csrf(web, f"/plan?week={ws}"), "pillar": "kabir", "hook_style": "question",
        "slot": slot, "scene": "1", "length": "short", "topic": "धैर्य", "action": "save"},
        follow_redirects=False)
    assert r.status_code == 303
    with session() as s:
        e = s.get(PlanEntry, entry_id)
        assert (e.pillar, e.hook_style, e.topic, e.edited_by) == ("kabir", "question", "धैर्य",
                                                                  "web:admin")
    bad = web.post(f"/plan/{entry_id}", data={
        "token": _csrf(web, "/plan"), "pillar": "kabir", "hook_style": "question",
        "slot": "03:33", "action": "save"}, follow_redirects=False)
    assert bad.status_code == 400
    learning = web.get("/learning")
    assert learning.status_code == 200 and "not tried yet" in learning.text


@pytest.fixture(autouse=True)
def _clear_festival_cache():
    planner.festivals.cache_clear()
    yield
    if hasattr(planner.festivals, "cache_clear"):  # may still be monkeypatched here
        planner.festivals.cache_clear()
