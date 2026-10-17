"""Collector: takes stats snapshots of published posts and of the accounts.

Each post is measured at fixed ages, so posts can be compared at the same age:
1 h, 3 h, 1 day, 3 days, 7 days, 14 days, 30 days. Stories disappear after 24 h, so they are
measured at 1 h, 3 h and 20 h. Follower counts are recorded every 6 hours, and Instagram's
account totals once a day.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instapost.db import (
    AccountSnapshot,
    Item,
    MetricSnapshot,
    Publication,
    get_kv,
    session,
    set_kv,
    utcnow,
)
from instapost.publish import tokens
from instapost.schedule import load_schedule, to_local
from instapost.stats import fetch

CHECKPOINT_HOURS = [1, 3, 24, 72, 168, 336, 720]
STORY_CHECKPOINT_HOURS = [1, 3, 20]
RUN_EVERY = timedelta(minutes=10)
ACCOUNT_EVERY = timedelta(hours=6)
MAX_POSTS_PER_RUN = 25


def checkpoints(fmt: str) -> list[int]:
    return STORY_CHECKPOINT_HOURS if fmt == "story_line" else CHECKPOINT_HOURS


def due_publications(s: Session, now: datetime | None = None) -> list[Publication]:
    now = now or utcnow()
    counts = dict(s.execute(select(MetricSnapshot.publication_id, func.count())
                            .group_by(MetricSnapshot.publication_id)).all())
    due = []
    pubs = s.scalars(select(Publication).where(Publication.status == "published",
                                               Publication.remote_id.is_not(None)))
    for pub in pubs:
        cps = checkpoints(pub.item.format)
        n = counts.get(pub.id, 0)
        if n < len(cps) and now >= pub.published_at + timedelta(hours=cps[n]):
            due.append(pub)
    return sorted(due, key=lambda p: p.published_at)


class Fetchers:
    """Creates one API client per platform on first use; remembers platforms that fail."""

    def __init__(self):
        self._clients: dict[str, object] = {}
        self.errors: dict[str, str] = {}

    def get(self, platform: str):
        if platform in self.errors:
            return None
        if platform not in self._clients:
            cls = {"instagram": fetch.InstagramStats, "facebook": fetch.FacebookStats,
                   "youtube": fetch.YouTubeStats}[platform]
            try:
                self._clients[platform] = cls()
            except Exception as e:  # noqa: BLE001 - platform not connected / auth problem
                self.errors[platform] = tokens.redact(str(e))[:300]
                return None
        return self._clients[platform]


def _post_metrics(client, pub: Publication, item: Item) -> tuple[dict, dict]:
    if pub.platform == "youtube":
        sched = load_schedule()
        published_day = to_local(pub.published_at, sched).date().isoformat()
        return client.video(pub.remote_id, published_day, sched.local_now().date().isoformat())
    if pub.platform == "facebook":
        return client.reel(pub.remote_id)
    return client.media(pub.remote_id)


def collect_post(s: Session, pub: Publication, fetchers: Fetchers) -> MetricSnapshot | None:
    client = fetchers.get(pub.platform)
    if client is None:
        return None
    item = pub.item
    metrics, raw = _post_metrics(client, pub, item)
    if "watch_ratio" not in metrics and metrics.get("avg_watch_s") and item.duration_s:
        metrics["watch_ratio"] = round(metrics["avg_watch_s"] / item.duration_s, 3)
    snap = MetricSnapshot(
        publication_id=pub.id, item_id=item.id, platform=pub.platform,
        age_hours=round((utcnow() - pub.published_at).total_seconds() / 3600, 1),
        metrics=metrics, raw=raw)
    s.add(snap)
    return snap


def collect_due(fetchers: Fetchers | None = None) -> dict:
    fetchers = fetchers or Fetchers()
    done, failed = 0, []
    with session() as s:
        for pub in due_publications(s)[:MAX_POSTS_PER_RUN]:
            try:
                if collect_post(s, pub, fetchers) is not None:
                    done += 1
            except fetch.FetchError as e:
                failed.append(f"{pub.platform} {pub.item_id}: {e}")
            except Exception as e:  # noqa: BLE001 - one post must not stop the others
                failed.append(f"{pub.platform} {pub.item_id}: {tokens.redact(str(e))[:200]}")
            s.commit()
    return {"collected": done, "failed": failed, "platform_errors": fetchers.errors}


def collect_accounts(fetchers: Fetchers | None = None) -> dict:
    fetchers = fetchers or Fetchers()
    sched = load_schedule()
    day = sched.local_now().date().isoformat()
    saved = {}
    with session() as s:
        for platform in ("instagram", "facebook", "youtube"):
            client = fetchers.get(platform)
            if client is None:
                continue
            try:
                data = client.account()
            except Exception as e:  # noqa: BLE001
                fetchers.errors[platform] = tokens.redact(str(e))[:300]
                continue
            followers = data.get("followers_count", data.get("fan_count"))
            s.add(AccountSnapshot(platform=platform, kind="followers", day=day,
                                  metrics={**data, "followers": followers}))
            saved[platform] = followers
    return saved


def collect_instagram_day(day_local, fetchers: Fetchers | None = None) -> dict | None:
    """Instagram account totals (reach, views, profile views...) for one finished local day."""
    fetchers = fetchers or Fetchers()
    client = fetchers.get("instagram")
    if client is None:
        return None
    sched = load_schedule()
    start = datetime.combine(day_local, datetime.min.time(), tzinfo=sched.tz)
    since = int(start.timestamp())
    until = int((start + timedelta(days=1)).timestamp())
    data = client.account_day(since, until)
    with session() as s:
        s.add(AccountSnapshot(platform="instagram", kind="daily", day=day_local.isoformat(),
                              metrics=data))
    return data


def run(notify=None) -> dict | None:
    """Worker duty. Throttled internally, so it is cheap to call every tick."""
    now = utcnow()
    with session() as s:
        last = get_kv(s, "collector_last_run", "")
        if last and now - datetime.fromisoformat(last) < RUN_EVERY:
            return None
        set_kv(s, "collector_last_run", now.isoformat())
        last_acc = get_kv(s, "collector_last_accounts", "")
        last_daily = get_kv(s, "collector_last_ig_daily", "")

    fetchers = Fetchers()
    summary = collect_due(fetchers)
    if not last_acc or now - datetime.fromisoformat(last_acc) >= ACCOUNT_EVERY:
        summary["followers"] = collect_accounts(fetchers)
        with session() as s:
            set_kv(s, "collector_last_accounts", now.isoformat())
    yesterday = load_schedule().local_now().date() - timedelta(days=1)
    if last_daily != yesterday.isoformat():
        try:
            summary["ig_daily"] = collect_instagram_day(yesterday, fetchers)
        except Exception as e:  # noqa: BLE001
            summary.setdefault("failed", []).append(f"instagram daily: {e}")
        with session() as s:
            set_kv(s, "collector_last_ig_daily", yesterday.isoformat())
    if notify and summary.get("failed"):
        # A failing post is retried every run; alert at most once a day about it.
        today = load_schedule().local_now().date().isoformat()
        with session() as s:
            if get_kv(s, "collector_last_alert", "") != today:
                set_kv(s, "collector_last_alert", today)
                notify("⚠️ Stats collection problems:\n" + "\n".join(summary["failed"][:5]))
    return summary
