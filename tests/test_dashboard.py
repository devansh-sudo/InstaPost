
from instapost import notifications
from instapost.db import AuditLog, session, set_kv, utcnow
from instapost.stats import dashboard
from tests.test_phase3 import _published_item, _seed_stats


def test_overview_compares_with_the_previous_period():
    _seed_stats()  # two posts in the last 30 h, followers 100 -> 150 for Instagram
    _published_item(24 * 10, {"views": 100, "reach": 80, "shares": 1, "saves": 1})  # previous
    with session() as s:
        ov = dashboard.overview(s, days=7)
    t, ig = ov["total"], ov["platforms"]["instagram"]
    assert t.posts.value == 2 and t.posts.previous == 1
    assert t.counts["views"].value == 1200 and t.counts["views"].previous == 100
    assert round(t.counts["views"].change, 1) == 11.0  # +1100%
    assert ig.followers == 150 and ig.followers_change == 50
    assert t.followers == 150
    assert "Profile" in ig.links and "Studio" in ov["platforms"]["youtube"].links


def test_daily_views_per_platform():
    _seed_stats()
    with session() as s:
        rows = dashboard.overview(s, days=7)["rows"]
    days, series = dashboard.daily_views(rows, 7)
    assert len(days) == 7 and sum(series["instagram"]) == 1200
    assert sum(series["facebook"]) == 0


def test_notifications_alerts_events_and_read_state():
    with session() as s:
        notes = notifications.build(s)
        titles = [n.title for n in notes["alerts"]]
        assert "Worker is not running" in titles  # no heartbeat yet
        set_kv(s, "worker_heartbeat", utcnow().isoformat())
        s.add(AuditLog(actor="agent", action="publish_error", item_id="x",
                       detail={"platform": "facebook", "error": "boom"}))
        s.add(AuditLog(actor="agent", action="published", item_id="y",
                       detail={"platform": "instagram", "permalink": "https://instagram.com/reel/a/"}))
    with session() as s:
        notes = notifications.build(s)
        assert "Worker is not running" not in [n.title for n in notes["alerts"]]
        ev = {n.title: n for n in notes["events"]}
        assert ev["Publishing failed on Facebook"].level == "bad"
        assert ev["Published on Instagram"].link.startswith("https://instagram.com")
        unread_before = notes["unread"]
        notifications.mark_all_read(s)
    with session() as s:
        after = notifications.build(s)
        assert all(not n.unread for n in after["events"])
        assert after["unread"] < unread_before


def test_youtube_channel_link_uses_cached_id():
    with session() as s:
        set_kv(s, "youtube_channel_id", "UC123")
        links = dashboard.platform_links(s)
    assert links["youtube"]["Channel"].endswith("/UC123")
    assert "UC123" in links["youtube"]["Studio"]
