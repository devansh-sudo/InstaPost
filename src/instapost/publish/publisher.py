"""Publishes one approved item to all of its platforms.

Safety properties:
- one Publication row per (item, platform): a platform that already succeeded is never posted
  again, even if the item is retried after a crash;
- an Instagram container created before an interruption is resumed, not recreated;
- the kill switch is checked before every platform;
- per-platform daily limits from schedule.yaml;
- at most MAX_ATTEMPTS tries per platform, with growing delays between them.
"""

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instapost import approvals
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, Publication, audit, utcnow
from instapost.models import Format
from instapost.publish import tokens, youtube
from instapost.publish.instagram import Instagram, Published, PublishError
from instapost.schedule import load_schedule, to_local, to_utc_naive

MAX_ATTEMPTS = 3


def configured_platforms() -> set[str]:
    s = get_settings()
    out = set()
    if s.ig_user_id and tokens.get("ig_access_token"):
        out.add("instagram")
    if s.fb_page_id and tokens.get("fb_page_token"):
        out.add("facebook")
    if youtube.is_configured():
        out.add("youtube")
    return out


def targets_for(item: Item) -> list[str]:
    wanted = load_schedule().targets.get(Format(item.format), [])
    available = configured_platforms()
    manual = set(item.manual_platforms or [])
    return [p for p in wanted if p in available and p not in manual]


def published_today(s: Session, platform: str) -> int:
    sched = load_schedule()
    start_local = sched.local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return s.scalar(select(func.count()).select_from(Publication).where(
        Publication.platform == platform, Publication.status == "published",
        Publication.published_at >= to_utc_naive(start_local))) or 0


def _files(item: Item) -> dict:
    folder = Path(item.folder)
    return {"video": folder / "reel.mp4", "cover": folder / "cover.jpg",
            "slides": sorted(folder.glob("slide_*.jpg")), "story": folder / "story.jpg",
            "draft": json.loads((folder / "draft.json").read_text(encoding="utf-8"))}


def _publish_one(s: Session, item: Item, pub: Publication, files: dict, host) -> Published:
    fmt = Format(item.format)

    def remember(container_id: str) -> None:
        pub.container_id = container_id
        s.commit()  # persist before waiting, so a crash can resume this container

    if pub.platform == "instagram":
        ig = Instagram()
        if pub.container_id:
            return ig.resume(pub.container_id)
        if fmt == Format.REEL_BOOK_POV:
            return ig.reel(host.url_for(files["video"]), item.caption,
                           host.url_for(files["cover"]), on_container=remember)
        if fmt == Format.CAROUSEL_SHLOK:
            return ig.carousel([host.url_for(p) for p in files["slides"]], item.caption,
                               on_container=remember)
        return ig.story(host.url_for(files["story"]), on_container=remember)

    if pub.platform == "facebook":
        from instapost.publish.facebook import Facebook
        return Facebook().reel(host.url_for(files["video"]), item.caption)

    if pub.platform == "youtube":
        draft = files["draft"]
        title = " ".join(draft["hook_lines"]) + " #Shorts"
        return youtube.upload_short(files["video"], title, item.caption, draft["hashtags"])

    raise PublishError(f"unknown platform {pub.platform}")


def publish_item(s: Session, item: Item, host=None) -> Item:
    """Try every pending platform once. Caller commits."""
    from instapost.publish.media_host import get_media_host

    if item.status != ItemStatus.APPROVED:
        raise approvals.QueueError(f"{item.id} is {item.status}, not approved")
    item.status = ItemStatus.PUBLISHING
    s.commit()

    targets = targets_for(item)
    if not targets:
        item.status = ItemStatus.FAILED
        audit(s, "agent", "publish_failed", item.id, reason="no configured platform")
        return item

    files = _files(item)
    host = host or get_media_host()
    limits = load_schedule().max_posts_per_day
    existing = {p.platform: p for p in item.publications}
    retry_needed = False
    limit_hit = False

    for platform in targets:
        pub = existing.get(platform)
        if pub is None:
            pub = Publication(item_id=item.id, platform=platform, status="pending", attempts=0)
            item.publications.append(pub)
        if pub.status == "published" or pub.attempts >= MAX_ATTEMPTS:
            continue
        if approvals.kill_switch_on(s):
            audit(s, "agent", "publish_paused_kill_switch", item.id, platform=platform)
            retry_needed = True
            break
        if published_today(s, platform) >= limits.get(platform, 0):
            audit(s, "agent", "daily_limit_reached", item.id, platform=platform)
            retry_needed = limit_hit = True
            continue

        pub.attempts += 1
        try:
            result = _publish_one(s, item, pub, files, host)
        except Exception as e:  # noqa: BLE001 - every failure is recorded, nothing is lost
            pub.status = "failed"
            pub.error = tokens.redact(str(e))[:2000]
            audit(s, "agent", "publish_error", item.id, platform=platform, error=pub.error,
                  attempt=pub.attempts)
            if pub.attempts < MAX_ATTEMPTS:
                pub.status = "pending"
                retry_needed = True
            continue
        pub.status = "published"
        pub.remote_id, pub.permalink = result.remote_id, result.permalink
        pub.published_at, pub.error = utcnow(), None
        audit(s, "agent", "published", item.id, platform=platform, permalink=result.permalink)
        s.commit()

    states = [p.status for p in item.publications if p.platform in targets]
    if all(st == "published" for st in states):
        item.status = ItemStatus.PUBLISHED
    elif retry_needed:
        attempts = max((p.attempts for p in item.publications), default=1)
        item.status = ItemStatus.APPROVED
        item.scheduled_at = _next_morning() if limit_hit else _retry_time(attempts)
    elif any(st == "published" for st in states):
        item.status = ItemStatus.PARTIAL
    else:
        item.status = ItemStatus.FAILED
    return item


def _retry_time(attempts: int):
    """15, 30, 45 minutes from now, but never in the middle of the night."""
    when = utcnow() + timedelta(minutes=15 * max(1, attempts))
    local = to_local(when, load_schedule())
    if local.hour >= 23 or local.hour < 6:
        return _next_morning(local)
    return when


def _next_morning(after=None):
    """07:00 local on the next morning (today, if it is still before 07:00)."""
    sched = load_schedule()
    local = after or sched.local_now()
    morning = local.replace(hour=7, minute=0, second=0, microsecond=0)
    if morning <= local:
        morning += timedelta(days=1)
    return to_utc_naive(morning)
