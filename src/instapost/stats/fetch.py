"""Read stats from the platforms' official APIs and normalise them.

Metric names change between API versions, so every request degrades gracefully: if the API
rejects the whole list, each metric is asked for alone and the rejected ones are remembered
(in the kv table) and skipped next time. Missing numbers stay missing; they are never guessed.
"""

import httpx

from instapost.config import get_settings
from instapost.db import get_kv, session, set_kv
from instapost.publish import tokens, youtube

IG_MEDIA_METRICS = {
    "REELS": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
              "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"],
    "FEED": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
             "follows", "profile_visits"],
    "STORY": ["views", "reach", "replies", "shares", "total_interactions", "follows",
              "profile_visits"],
}
IG_ACCOUNT_DAILY = ["reach", "views", "profile_views", "accounts_engaged",
                    "total_interactions", "follows_and_unfollows"]
FB_REEL_METRICS = ["blue_reels_play_count", "fb_reels_total_plays", "post_impressions_unique",
                   "post_video_avg_time_watched", "post_video_followers",
                   "post_video_likes_by_reaction_type", "post_video_social_actions"]


class FetchError(RuntimeError):
    pass


def _value(entry: dict):
    """Insights values come either as values[0].value or total_value.value."""
    if "total_value" in entry:
        return entry["total_value"].get("value")
    values = entry.get("values") or [{}]
    return values[-1].get("value")


def _unsupported(key: str) -> set[str]:
    with session() as s:
        raw = get_kv(s, key, "")
    return set(raw.split(",")) - {""}


def _remember_unsupported(key: str, names: set[str]) -> None:
    with session() as s:
        set_kv(s, key, ",".join(sorted(_unsupported(key) | names)))


def _insights(get, path: str, metrics: list[str], cache_key: str, **extra) -> dict:
    """Ask for all metrics at once; on rejection, ask one by one and remember failures."""
    wanted = [m for m in metrics if m not in _unsupported(cache_key)]
    if not wanted:
        return {}
    try:
        data = get(path, metric=",".join(wanted), **extra).get("data", [])
        return {e["name"]: _value(e) for e in data}
    except FetchError:
        pass
    out, bad = {}, set()
    for m in wanted:
        try:
            for e in get(path, metric=m, **extra).get("data", []):
                out[e["name"]] = _value(e)
        except FetchError:
            bad.add(m)
    if bad:
        _remember_unsupported(cache_key, bad)
    return out


# --- Instagram ------------------------------------------------------------------------------

class InstagramStats:
    def __init__(self, client: httpx.Client | None = None):
        s = get_settings()
        token = tokens.get("ig_access_token")
        if not (s.ig_user_id and token):
            raise FetchError("Instagram is not connected")
        self.user_id, self.base = s.ig_user_id, s.ig_api_base
        self.http = client or httpx.Client(timeout=30)
        self.headers = {"Authorization": f"Bearer {token}"}

    def get(self, path: str, **params) -> dict:
        r = self.http.get(f"{self.base}/{path}", params=params, headers=self.headers)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or "error" in body:
            raise FetchError(tokens.redact(
                f"Instagram {r.status_code}: {body.get('error', {}).get('message', r.text[:200])}"))
        return body

    def media(self, media_id: str) -> tuple[dict, dict]:
        info = self.get(media_id, fields="media_product_type,like_count,comments_count,timestamp")
        kind = info.get("media_product_type", "FEED")
        raw = _insights(self.get, f"{media_id}/insights", IG_MEDIA_METRICS.get(kind, []),
                        f"ig_unsupported:{kind}")
        m = {
            "views": raw.get("views"), "reach": raw.get("reach"),
            "likes": raw.get("likes", info.get("like_count")),
            "comments": raw.get("comments", info.get("comments_count")),
            "shares": raw.get("shares"), "saves": raw.get("saved"),
            "follows": raw.get("follows"), "profile_visits": raw.get("profile_visits"),
            "replies": raw.get("replies"),
        }
        if raw.get("ig_reels_avg_watch_time") is not None:
            m["avg_watch_s"] = raw["ig_reels_avg_watch_time"] / 1000  # API reports ms
        return {k: v for k, v in m.items() if v is not None}, {"info": info, "insights": raw}

    def account(self) -> dict:
        return self.get("me", fields="followers_count,follows_count,media_count")

    def account_day(self, since: int, until: int) -> dict:
        return _insights(self.get, f"{self.user_id}/insights", IG_ACCOUNT_DAILY,
                         "ig_unsupported:account", period="day", metric_type="total_value",
                         since=since, until=until)


# --- Facebook -------------------------------------------------------------------------------

class FacebookStats:
    def __init__(self, client: httpx.Client | None = None):
        s = get_settings()
        token = tokens.get("fb_page_token")
        if not (s.fb_page_id and token):
            raise FetchError("Facebook is not connected")
        self.page_id, self.base = s.fb_page_id, s.fb_api_base
        self.http = client or httpx.Client(timeout=30)
        self.headers = {"Authorization": f"OAuth {token}"}

    def get(self, path: str, **params) -> dict:
        r = self.http.get(f"{self.base}/{path}", params=params, headers=self.headers)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or "error" in body:
            raise FetchError(tokens.redact(
                f"Facebook {r.status_code}: {body.get('error', {}).get('message', r.text[:200])}"))
        return body

    def reel(self, video_id: str) -> tuple[dict, dict]:
        raw = _insights(self.get, f"{video_id}/video_insights", FB_REEL_METRICS,
                        "fb_unsupported:reel")
        social = raw.get("post_video_social_actions") or {}
        reactions = raw.get("post_video_likes_by_reaction_type") or {}
        m = {
            "views": raw.get("blue_reels_play_count") or raw.get("fb_reels_total_plays"),
            "reach": raw.get("post_impressions_unique"),
            "follows": raw.get("post_video_followers"),
            "likes": sum(reactions.values()) if isinstance(reactions, dict) else None,
            "shares": social.get("SHARE") if isinstance(social, dict) else None,
            "comments": social.get("COMMENT") if isinstance(social, dict) else None,
        }
        if raw.get("post_video_avg_time_watched") is not None:
            m["avg_watch_s"] = raw["post_video_avg_time_watched"] / 1000
        return {k: v for k, v in m.items() if v is not None}, {"insights": raw}

    def account(self) -> dict:
        return self.get(self.page_id, fields="followers_count,fan_count")


# --- YouTube --------------------------------------------------------------------------------

class YouTubeStats:
    def __init__(self):
        if not youtube.is_configured():
            raise FetchError("YouTube is not connected")
        self.data = youtube._service()
        self.analytics = youtube._service("youtubeAnalytics", "v2")

    def video(self, video_id: str, published_day: str, today: str) -> tuple[dict, dict]:
        stats = self.data.videos().list(part="statistics", id=video_id).execute()
        st = (stats.get("items") or [{}])[0].get("statistics", {})
        m = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
             "comments": int(st.get("commentCount", 0))}
        raw = {"statistics": st}
        try:  # Analytics lags 1-2 days; missing rows are normal for new videos.
            rep = self.analytics.reports().query(
                ids="channel==MINE", startDate=published_day, endDate=today,
                metrics="views,averageViewDuration,averageViewPercentage,shares,"
                        "subscribersGained",
                dimensions="video", filters=f"video=={video_id}").execute()
            raw["analytics"] = rep
            if rep.get("rows"):
                cols = [h["name"] for h in rep["columnHeaders"]]
                row = dict(zip(cols, rep["rows"][0], strict=False))
                m["avg_watch_s"] = row.get("averageViewDuration")
                if row.get("averageViewPercentage") is not None:
                    m["watch_ratio"] = row["averageViewPercentage"] / 100
                m["shares"] = row.get("shares")
                m["follows"] = row.get("subscribersGained")
        except Exception as e:  # noqa: BLE001
            raw["analytics_error"] = str(e)[:300]
        return {k: v for k, v in m.items() if v is not None}, raw

    def account(self) -> dict:
        ch = self.data.channels().list(part="statistics", mine=True).execute()
        st = (ch.get("items") or [{}])[0].get("statistics", {})
        return {"followers_count": int(st.get("subscriberCount", 0)),
                "views_total": int(st.get("viewCount", 0)),
                "media_count": int(st.get("videoCount", 0))}
