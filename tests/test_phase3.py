"""Phase 3: scoring, stats fetching (mocked APIs), collector timing, aggregations, report,
stats pages, and the forward-only database upgrade."""

from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa

from instapost import approvals, db
from instapost.config import get_settings
from instapost.db import AccountSnapshot, Item, MetricSnapshot, Publication, session, utcnow
from instapost.schedule import load_schedule
from instapost.stats import collect, fetch, queries, report
from instapost.stats import score as scoring
from tests.test_phase2 import _csrf, _login, _story, web  # noqa: F401 - fixtures reused

# --- score -----------------------------------------------------------------------------------


def test_score_is_ratio_based_and_capped():
    good = {"reach": 1000, "shares": 20, "saves": 30, "watch_ratio": 1.0, "comments": 10,
            "follows": 5}
    great = {k: (v if k == "reach" else v * 10) for k, v in good.items()}
    assert scoring.score(good) == pytest.approx(66.7, abs=0.1)  # exactly "good" = 1/1.5
    assert scoring.score(great) == 100.0  # capped
    assert scoring.score({**good, "reach": 10}) is None  # too few people to judge


def test_score_leaves_out_missing_signals():
    carousel = {"reach": 500, "shares": 10, "saves": 15}  # no watch time on carousels
    assert scoring.score(carousel) == pytest.approx(66.7, abs=0.1)


def test_combined_weights_by_reach():
    out = scoring.combined({"instagram": {"reach": 900, "shares": 18, "saves": 27},
                            "facebook": {"reach": 100, "shares": 0, "saves": 0}})
    assert out["reach"] == 1000 and out["shares"] == 18
    assert out["score"] == pytest.approx(60.0, abs=0.1)


# --- Instagram stats with metric fallback ------------------------------------------------------

def test_instagram_media_falls_back_per_metric_and_remembers(monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "17841")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "TOKEN")
    get_settings.cache_clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        metric = request.url.params.get("metric")
        calls.append(metric)
        if request.url.path.endswith("/m1"):
            return httpx.Response(200, json={"media_product_type": "REELS", "like_count": 7,
                                             "comments_count": 2})
        if "," in (metric or "") or metric == "ig_reels_video_view_total_time":
            return httpx.Response(400, json={"error": {"message": "invalid metric"}})
        values = {"reach": 400, "views": 900, "shares": 9, "saved": 12,
                  "ig_reels_avg_watch_time": 6500}
        return httpx.Response(200, json={"data": [
            {"name": metric, "values": [{"value": values.get(metric, 1)}]}]})

    client = fetch.InstagramStats(client=httpx.Client(transport=httpx.MockTransport(handler)))
    metrics, _raw = client.media("m1")
    assert metrics["reach"] == 400 and metrics["saves"] == 12
    assert metrics["avg_watch_s"] == 6.5  # API reports milliseconds
    assert "ig_reels_video_view_total_time" in fetch._unsupported("ig_unsupported:REELS")
    calls.clear()
    client.media("m1")
    assert "ig_reels_video_view_total_time" not in ",".join(c or "" for c in calls)


# --- collector -------------------------------------------------------------------------------

def _published_item(hours_ago: float, metrics: dict | None = None, pillar="gita") -> str:
    item_id = _story()
    with session() as s:
        item = s.get(Item, item_id)
        item.status, item.pillar = "published", pillar
        pub = Publication(item_id=item_id, platform="instagram", status="published",
                          remote_id="m1", attempts=1,
                          published_at=utcnow() - timedelta(hours=hours_ago))
        s.add(pub)
        s.flush()
        if metrics:
            s.add(MetricSnapshot(publication_id=pub.id, item_id=item_id, platform="instagram",
                                 age_hours=hours_ago, metrics=metrics))
    return item_id


class FakeIG:
    def media(self, media_id):
        return {"views": 800, "reach": 500, "shares": 10, "saves": 15}, {}

    def account(self):
        return {"followers_count": 321}


def test_collector_follows_checkpoints():
    _published_item(hours_ago=0.5)
    with session() as s:
        assert collect.due_publications(s) == []  # first checkpoint is 1 h
    fresh = _published_item(hours_ago=2)
    fetchers = collect.Fetchers()
    fetchers._clients["instagram"] = FakeIG()
    assert collect.collect_due(fetchers)["collected"] == 1
    with session() as s:
        assert collect.due_publications(s) == []  # next one at 3 h
        snap = s.query(MetricSnapshot).filter_by(item_id=fresh).one()
        assert snap.metrics["reach"] == 500
    assert collect.collect_accounts(fetchers) == {"instagram": 321}


def test_unconnected_platforms_are_skipped_quietly():
    _published_item(hours_ago=5)
    summary = collect.collect_due()
    assert summary["collected"] == 0 and "instagram" in summary["platform_errors"]


# --- aggregations, report, pages ----------------------------------------------------------------

def _seed_stats():
    good = {"views": 900, "reach": 600, "shares": 12, "saves": 18, "comments": 6}
    weak = {"views": 300, "reach": 200, "shares": 0, "saves": 1, "comments": 0}
    a = _published_item(30, good, pillar="gita")
    b = _published_item(20, weak, pillar="kabir")
    today = load_schedule().local_now().date()
    with session() as s:
        for d, n in ((today - timedelta(days=8), 100), (today - timedelta(days=1), 130),
                     (today, 150)):
            s.add(AccountSnapshot(platform="instagram", kind="followers", day=d.isoformat(),
                                  metrics={"followers": n}))
    return a, b


def test_queries_rank_pillars_and_track_followers():
    _seed_stats()
    with session() as s:
        rows = queries.post_rows(s)
        pillars = queries.by_pillar(rows)
        assert [p for p, _v, _n in pillars] == ["gita", "kabir"]
        k = queries.kpis(s)
        assert k["followers"]["instagram"] == 150
        assert k["followers_change"]["instagram"] == 50
        assert k["views"] == 1200
        assert sum(n for _v, n in queries.slot_heatmap(rows).values()) == 2


def test_report_mentions_followers_and_best_posts():
    _seed_stats()
    day = load_schedule().local_now().date()
    with session() as s:
        text, data = report.build(s, day)
    assert "Instagram 150 (+20)" in text
    assert "Best of the last 7 days" in text and data["top"][0]["pillar"] == "gita"
    assert "Setup still open" in text
    rep = report.create(day)
    assert report.create(day).id == rep.id  # one report per day, updated in place


def test_stats_and_reports_pages_render(web):  # noqa: F811
    _login(web)
    empty = web.get("/analytics")
    assert empty.status_code == 200 and "No follower data yet" in empty.text
    _seed_stats()
    page = web.get("/analytics?days=7&sort=score")
    assert page.status_code == 200
    assert "<svg" in page.text and "data-tip" in page.text and "Table" in page.text
    assert "Platforms side by side" in page.text
    one = web.get("/analytics?days=7&platform=instagram")
    assert one.status_code == 200 and "Platforms side by side" not in one.text
    home = web.get("/")
    assert "Top posts" in home.text and "Views per day" in home.text
    assert web.get("/reports").status_code == 200
    settings = web.get("/settings")
    assert "Setup checklist" in settings.text and "Claude API key" in settings.text


def test_item_page_shows_numbers(web):  # noqa: F811
    a, _ = _seed_stats()
    _login(web)
    page = web.get(f"/items/{a}")
    assert page.status_code == 200 and "Numbers" in page.text and "600" in page.text


# --- database upgrade ----------------------------------------------------------------------------

def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    eng = sa.create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:  # an "items" table as Phase 2 created it, without duration_s
        conn.execute(sa.text("CREATE TABLE items (id VARCHAR(80) PRIMARY KEY, format VARCHAR,"
                             " pillar VARCHAR, status VARCHAR, folder TEXT)"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    db.reset_for_tests()
    cols = {c["name"] for c in sa.inspect(db.engine()).get_columns("items")}
    assert "duration_s" in cols and "telegram_message_id" in cols


def test_register_records_reel_duration(monkeypatch):
    from instapost import pipeline
    from instapost.models import Format, Pillar

    result = pipeline.make(Format.REEL_BOOK_POV, Pillar.GITA, offline=True)
    with session() as s:
        item = approvals.register(s, result, Format.REEL_BOOK_POV, "gita",
                                  load_schedule().local_now().date())
        assert 7 <= item.duration_s <= 12
