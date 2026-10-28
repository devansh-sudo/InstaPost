"""Numbers for the dashboard: KPIs per platform with the change against the previous period,
views per day, top posts and the links to each platform."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import get_kv, utcnow
from instapost.publish import publisher
from instapost.schedule import load_schedule, to_local
from instapost.stats import queries
from instapost.stats.queries import PLATFORMS, PostRow

COUNTS = ("views", "reach", "likes", "comments", "shares", "saves", "follows")


@dataclass
class Kpi:
    value: float | int | None
    previous: float | int | None = None

    @property
    def change(self) -> float | None:
        """Relative change against the previous period (0.25 = +25%)."""
        if self.value is None or not self.previous:
            return None
        return (self.value - self.previous) / self.previous


@dataclass
class PlatformKpis:
    platform: str
    connected: bool
    followers: int | None = None
    followers_change: int | None = None
    posts: Kpi = field(default_factory=lambda: Kpi(0, 0))
    counts: dict[str, Kpi] = field(default_factory=dict)
    watch_ratio: Kpi = field(default_factory=lambda: Kpi(None))
    score: Kpi = field(default_factory=lambda: Kpi(None))
    links: dict[str, str] = field(default_factory=dict)

    @property
    def engagement(self) -> Kpi:
        """Sends + saves: the two signals the ranking weights most."""
        cur = (self.counts["shares"].value or 0) + (self.counts["saves"].value or 0)
        prev = (self.counts["shares"].previous or 0) + (self.counts["saves"].previous or 0)
        return Kpi(cur, prev)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _period_numbers(rows: list[PostRow], platform: str | None) -> dict:
    """Totals of the latest numbers of these posts, for one platform or all."""
    out = {k: 0 for k in COUNTS}
    watch, scores, posts = [], [], 0
    for r in rows:
        if platform is None:
            m = r.combined
        else:
            m = r.per_platform.get(platform)
            if m is None:
                if platform in r.permalinks:
                    posts += 1  # published there, no numbers yet
                continue
            posts += 1
        for k in COUNTS:
            out[k] += m.get(k) or 0
        if m.get("watch_ratio"):
            watch.append(m["watch_ratio"])
        sc = m.get("score") if platform is None else _score(m)
        if sc is not None:
            scores.append(sc)
    out["posts"] = posts if platform else len(rows)
    out["watch_ratio"] = _avg(watch)
    out["score"] = round(sum(scores) / len(scores), 1) if scores else None
    return out


def _score(metrics: dict) -> float | None:
    from instapost.stats import score as scoring
    return scoring.score(metrics)


def platform_links(s: Session) -> dict[str, dict[str, str]]:
    """Profile and analytics links for each platform (no API calls)."""
    st = get_settings()
    handle = load_brand().printed_handle.lstrip("@")
    links: dict[str, dict[str, str]] = {"instagram": {}, "facebook": {}, "youtube": {}}
    if handle:
        links["instagram"]["Profile"] = f"https://www.instagram.com/{handle}/"
    links["instagram"]["Insights"] = "https://business.facebook.com/latest/insights"
    if st.fb_page_id:
        links["facebook"]["Page"] = f"https://www.facebook.com/{st.fb_page_id}"
    links["facebook"]["Meta Business Suite"] = "https://business.facebook.com/latest/home"
    channel = get_kv(s, "youtube_channel_id", "")
    if channel:
        links["youtube"]["Channel"] = f"https://www.youtube.com/channel/{channel}"
        links["youtube"]["Studio"] = f"https://studio.youtube.com/channel/{channel}/analytics"
    else:
        links["youtube"]["Studio"] = "https://studio.youtube.com/"
    return links


def overview(s: Session, days: int = 7) -> dict:
    """Everything the Home and Analytics pages show for a period of `days`."""
    now = utcnow()
    start, prev_start = now - timedelta(days=days), now - timedelta(days=2 * days)
    rows = queries.post_rows(s, since=prev_start)
    current = [r for r in rows if r.published_at >= start]
    previous = [r for r in rows if r.published_at < start]
    series = queries.followers_series(s, days=max(days * 2, 30))
    connected = publisher.configured_platforms()
    links = platform_links(s)

    def build(platform: str | None) -> PlatformKpis:
        cur, prev = _period_numbers(current, platform), _period_numbers(previous, platform)
        k = PlatformKpis(platform or "all", platform in connected if platform else True)
        k.posts = Kpi(cur["posts"], prev["posts"])
        k.counts = {c: Kpi(cur[c], prev[c]) for c in COUNTS}
        k.watch_ratio = Kpi(cur["watch_ratio"], prev["watch_ratio"])
        k.score = Kpi(cur["score"], prev["score"])
        if platform:
            pts = series.get(platform, [])
            k.followers = pts[-1][1] if pts else None
            k.followers_change = queries.follower_change(pts, days)
            k.links = links.get(platform, {})
        return k

    platforms = {p: build(p) for p in PLATFORMS}
    total = build(None)
    followers = [p.followers for p in platforms.values() if p.followers is not None]
    total.followers = sum(followers) if followers else None
    changes = [p.followers_change for p in platforms.values() if p.followers_change is not None]
    total.followers_change = sum(changes) if changes else None
    return {"days": days, "total": total, "platforms": platforms, "rows": current,
            "series": {p: v for p, v in series.items()
                       if v and v[-1][0] >= (now - timedelta(days=days)).date()}}


def daily_views(rows: list[PostRow], days: int, platforms: tuple[str, ...] = PLATFORMS
                ) -> tuple[list[date], dict[str, list[int]]]:
    """Views (latest numbers) of the posts published each local day, per platform."""
    sched = load_schedule()
    today = sched.local_now().date()
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    index = {d: i for i, d in enumerate(day_list)}
    out = {p: [0] * len(day_list) for p in platforms}
    for r in rows:
        i = index.get(to_local(r.published_at, sched).date())
        if i is None:
            continue
        for p in platforms:
            out[p][i] += int((r.per_platform.get(p) or {}).get("views") or 0)
    return day_list, out


def top_posts(rows: list[PostRow], n: int = 3) -> list[PostRow]:
    scored = [r for r in rows if r.score is not None]
    if scored:
        return sorted(scored, key=lambda r: r.score, reverse=True)[:n]
    return sorted(rows, key=lambda r: r.combined.get("views") or 0, reverse=True)[:n]
