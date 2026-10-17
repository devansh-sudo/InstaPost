"""Read-side aggregations for the stats page and the daily report."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost.db import AccountSnapshot, Item, MetricSnapshot, Publication
from instapost.schedule import load_schedule, to_local
from instapost.stats import score as scoring

PLATFORMS = ("instagram", "facebook", "youtube")


@dataclass
class PostRow:
    item: Item
    published_at: datetime  # first platform, UTC
    per_platform: dict[str, dict] = field(default_factory=dict)
    combined: dict = field(default_factory=dict)
    permalinks: dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> float | None:
        return self.combined.get("score")


def post_rows(s: Session, since: datetime | None = None) -> list[PostRow]:
    """Every published item with its latest numbers per platform, newest first."""
    q = select(Publication).where(Publication.status == "published")
    if since:
        q = q.where(Publication.published_at >= since)
    rows: dict[str, PostRow] = {}
    for pub in s.scalars(q):
        row = rows.setdefault(pub.item_id, PostRow(item=pub.item, published_at=pub.published_at))
        row.published_at = min(row.published_at, pub.published_at)
        if pub.permalink:
            row.permalinks[pub.platform] = pub.permalink
    if not rows:
        return []
    latest: dict[tuple[str, str], MetricSnapshot] = {}
    for snap in s.scalars(select(MetricSnapshot).where(MetricSnapshot.item_id.in_(rows))
                          .order_by(MetricSnapshot.taken_at)):
        latest[(snap.item_id, snap.platform)] = snap
    for (item_id, platform), snap in latest.items():
        rows[item_id].per_platform[platform] = snap.metrics
    for row in rows.values():
        row.combined = scoring.combined(row.per_platform)
    return sorted(rows.values(), key=lambda r: r.published_at, reverse=True)


def followers_series(s: Session, days: int = 30) -> dict[str, list[tuple[date, int]]]:
    """Last follower count of each local day, per platform."""
    start = (load_schedule().local_now().date() - timedelta(days=days)).isoformat()
    by: dict[str, dict[str, int]] = defaultdict(dict)
    for snap in s.scalars(select(AccountSnapshot).where(AccountSnapshot.kind == "followers",
                                                         AccountSnapshot.day >= start)
                          .order_by(AccountSnapshot.taken_at)):
        if snap.metrics.get("followers") is not None:
            by[snap.platform][snap.day] = int(snap.metrics["followers"])
    return {p: [(date.fromisoformat(d), v) for d, v in sorted(series.items())]
            for p, series in by.items()}


def follower_change(series: list[tuple[date, int]], days: int) -> int | None:
    if not series:
        return None
    cutoff = series[-1][0] - timedelta(days=days)
    before = [v for d, v in series if d <= cutoff]
    base = before[-1] if before else series[0][1]
    return series[-1][1] - base


def kpis(s: Session, days: int = 7) -> dict:
    from instapost.db import utcnow

    series = followers_series(s, days=60)
    recent = post_rows(s, since=utcnow() - timedelta(days=days))
    scores = [r.score for r in recent if r.score is not None]
    ratios = [r.combined["watch_ratio"] for r in recent if r.combined.get("watch_ratio")]
    return {
        "followers": {p: (series[p][-1][1] if series.get(p) else None) for p in PLATFORMS},
        "followers_change": {p: follower_change(series.get(p, []), days) for p in PLATFORMS},
        "posts": len(recent),
        "views": sum(r.combined.get("views") or 0 for r in recent),
        "shares_saves": sum((r.combined.get("shares") or 0) + (r.combined.get("saves") or 0)
                            for r in recent),
        "avg_watch_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
    }


def by_pillar(rows: list[PostRow]) -> list[tuple[str, float, int]]:
    """(pillar, average score, number of scored posts), best first."""
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.score is not None:
            acc[r.item.pillar].append(r.score)
    out = [(p, round(sum(v) / len(v), 1), len(v)) for p, v in acc.items()]
    return sorted(out, key=lambda t: t[1], reverse=True)


def slot_heatmap(rows: list[PostRow]) -> dict[tuple[int, int], tuple[float, int]]:
    """(weekday 0=Mon, local hour) -> (average score, posts)."""
    sched = load_schedule()
    acc: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in rows:
        if r.score is None:
            continue
        local = to_local(r.published_at, sched)
        acc[(local.weekday(), local.hour)].append(r.score)
    return {k: (round(sum(v) / len(v), 1), len(v)) for k, v in acc.items()}


def item_timeline(s: Session, item_id: str) -> list[MetricSnapshot]:
    return list(s.scalars(select(MetricSnapshot).where(MetricSnapshot.item_id == item_id)
                          .order_by(MetricSnapshot.platform, MetricSnapshot.taken_at)))
