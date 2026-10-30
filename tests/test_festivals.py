from datetime import date, timedelta

from instapost import festivals
from instapost.db import Job, PlanEntry, session
from instapost.learn import planner
from tests.test_phase2 import _csrf, _login, web  # noqa: F401


def test_shipped_list_covers_the_year_and_only_fixed_days_are_confirmed():
    every = festivals.all_festivals()
    names = {f["name"] for f in every}
    assert {"Diwali", "Maha Shivratri", "Holi", "Krishna Janmashtami", "Raksha Bandhan",
            "Republic Day", "Independence Day", "Kabir Jayanti", "Gita Jayanti"} <= names
    assert all(f["topic"] and f["pillar"] in festivals.PILLARS for f in every)
    confirmed = [f for f in every if f["confirmed"]]
    assert {f["kind"] for f in confirmed} == {"national"}
    assert len({f["id"] for f in every}) == len(every)  # stable, unique ids


def test_confirm_move_skip_and_add(monkeypatch):
    diwali = next(f for f in festivals.all_festivals() if f["id"] == "diwali-2026")
    assert diwali["date"] == date(2026, 11, 8) and not diwali["confirmed"]
    festivals.edit("diwali-2026", confirmed=True, date="2026-11-09")
    assert "2026-11-09" in planner.festivals()
    festivals.edit("diwali-2026", skip=True)
    assert "2026-11-09" not in planner.festivals()
    mine = festivals.add("Teej", date(2027, 8, 6), "jeevan_sutra", "तीज: इंतज़ार और प्रेम",
                         "हरियाली तीज की शुभकामनाएँ")
    assert planner.festivals()["2027-08-06"]["name"] == "Teej" and mine["id"].endswith("-mine")
    festivals.remove_mine(mine["id"])
    assert "2027-08-06" not in planner.festivals()


def test_festival_day_shapes_the_first_reel_and_the_morning_story():
    ws = date.today() + timedelta(days=7 - date.today().weekday())  # next Monday
    wed = ws + timedelta(days=2)
    festivals.add("Test Utsav", wed, "gita", "उत्सव का संदेश", "उत्सव की शुभकामनाएँ")
    with session() as s:
        planner.build_week(s, ws, seed=3)
        rows = s.query(PlanEntry).filter_by(day=wed.isoformat(), position=0).all()
        by_fmt = {e.format: e for e in rows}
        assert by_fmt["reel_book_pov"].pillar == "gita"
        assert by_fmt["reel_book_pov"].topic == "उत्सव का संदेश"
        assert by_fmt["story_line"].topic == "उत्सव की शुभकामनाएँ"


def test_festivals_page(web):  # noqa: F811
    _login(web)
    page = web.get("/festivals")
    assert page.status_code == 200 and "होली" in page.text
    token = _csrf(web, "/festivals")
    fid = festivals.upcoming(date.today(), 420)[0]["id"]
    r = web.post(f"/festivals/{fid}", data={"token": token, "action": "make"},
                 follow_redirects=False)
    assert r.status_code == 303
    with session() as s:
        kinds = [j.payload.get("template") for j in s.query(Job).filter(Job.kind == "make")]
        assert "gold_frame" in kinds
    r = web.post("/festivals", data={"token": token, "name": "Local Mela", "day": "2027-02-02",
                                     "pillar": "life_lesson", "topic": "मेला: साथ की खुशी"},
                 follow_redirects=False)
    assert any(f["name"] == "Local Mela" for f in festivals.all_festivals())
    assert web.post("/festivals/nope-2026", data={"token": token, "action": "skip"}
                    ).status_code == 404
