"""Approval queue: item states, batch approval, autonomy levels, kill switch, slot scheduling.

Autonomy (PLAN §4): L0 = you approve everything; L1 = stories and carousels are approved
automatically, reels wait for you; L2 = everything is approved automatically. Automatic
approval only ever applies to items whose fact check passed outright.
"""

import json
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost.config import get_settings
from instapost.db import Item, ItemStatus, audit, get_kv, set_kv, utcnow
from instapost.learn.bandit import item_features
from instapost.models import FactCheckVerdict, Format
from instapost.pipeline import Result
from instapost.render.video import probe_duration
from instapost.schedule import Schedule, candidate_slots, load_schedule, to_local, to_utc_naive
from instapost.studio.scenes import SCENE_REEL  # Shiv-Parvati scenes: a kind of reel

AUTONOMY_LEVELS = ("L0", "L1", "L2")
MIN_LEAD = timedelta(minutes=10)  # never schedule closer than this to "now"
TAKEN = (ItemStatus.APPROVED, ItemStatus.PUBLISHING, ItemStatus.PUBLISHED, ItemStatus.PARTIAL)


class QueueError(ValueError):
    pass


# --- runtime switches -------------------------------------------------------------------

def autonomy(s: Session) -> str:
    return get_kv(s, "autonomy", "L0")


def set_autonomy(s: Session, level: str, actor: str) -> None:
    if level not in AUTONOMY_LEVELS:
        raise QueueError(f"autonomy must be one of {AUTONOMY_LEVELS}")
    set_kv(s, "autonomy", level)
    audit(s, actor, "set_autonomy", level=level)


def kill_switch_on(s: Session) -> bool:
    return get_kv(s, "kill_switch", "0") == "1"


def set_kill_switch(s: Session, on: bool, actor: str) -> None:
    set_kv(s, "kill_switch", "1" if on else "0")
    audit(s, actor, "kill_switch_on" if on else "kill_switch_off")


# --- items ------------------------------------------------------------------------------

def register(s: Session, result: Result, fmt: Format, pillar: str, for_date: date,
             actor: str = "agent", preferred_slot: str | None = None,
             scene: int | None = None) -> Item:
    draft = _read_draft(result)
    draft_hook = " ".join(draft["hook_lines"])
    duration = probe_duration(result.folder / "reel.mp4") \
        if (result.folder / "reel.mp4").exists() else None
    item = Item(
        id=result.item_id, format=fmt.value, pillar=pillar, folder=str(result.folder),
        status=ItemStatus.NEEDS_REVIEW if result.status == "needs_review" else ItemStatus.BLOCKED,
        hook=draft_hook, caption=(result.folder / "caption.txt").read_text(encoding="utf-8"),
        fact_check=result.fact.verdict.value, for_date=for_date.isoformat(),
        qa_summary="; ".join(f"{c.name}: {c.detail}" for c in result.qa.checks if not c.passed),
        duration_s=duration, preferred_slot=preferred_slot,
        features=_features(result.folder, draft, pillar, duration, scene, fmt),
        music=(result.folder / "music.txt").read_text(encoding="utf-8").strip()
        if (result.folder / "music.txt").exists() else None,
    )
    s.merge(item)
    s.flush()
    item = s.get(Item, result.item_id)
    audit(s, actor, "created", item.id, status=item.status, fact_check=item.fact_check)
    if item.status == ItemStatus.NEEDS_REVIEW and _auto_approvable(s, item):
        approve(s, item, f"{actor}:autonomy-{autonomy(s)}")
    return item


def _features(folder, draft: dict, pillar: str, duration, scene, fmt=None) -> dict:
    """What the learners track, plus the book photo, drawing and look this post used."""
    feats = item_features(draft, pillar, duration, scene)
    for kind, name in (("photo", "book_photo.txt"), ("drawing", "drawing.txt"),
                       ("template", "template.txt")):
        path = folder / name
        if path.exists():
            first = path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
            if first and first != "fallback":
                feats[kind] = first
    if "template" in feats and fmt is not None:  # carousel and story looks: "carousel/night"
        from instapost.render import templates
        feats["template"] = templates.key(templates.kind_of(fmt), feats["template"])
    return feats


def _read_draft(result: Result) -> dict:
    return json.loads((result.folder / "draft.json").read_text(encoding="utf-8"))


def _auto_approvable(s: Session, item: Item) -> bool:
    if item.fact_check != FactCheckVerdict.PASS.value:
        return False
    level = autonomy(s)
    return level == "L2" or (level == "L1" and item.format not in REEL_FORMATS)


def next_free_slot(s: Session, fmt: str, start_day: date, sched: Schedule | None = None,
                   now: datetime | None = None, preferred: str | None = None) -> datetime:
    """Earliest unused slot (UTC, naive) for this format on or after `start_day`."""
    sched = sched or load_schedule()
    now = now or utcnow()
    taken = {
        row for (row,) in s.execute(
            # Scene reels use the reel slots, so the two kinds of reel never collide.
            select(Item.scheduled_at).where(Item.format.in_(same_slots(fmt)),
                                            Item.status.in_(TAKEN),
                                            Item.scheduled_at.is_not(None)))
    }
    candidates = candidate_slots(sched, base_format(fmt), start_day)
    if preferred:
        # The plan's slot on the planned day comes first, if it is still free.
        first = [c for c in candidates
                 if c.date() == start_day and c.strftime("%H:%M") == preferred]
        candidates = first + [c for c in candidates if c not in first]
    for local in candidates:
        utc = to_utc_naive(local)
        if utc >= now + MIN_LEAD and utc not in taken:
            return utc
    raise QueueError(f"no free {fmt} slot in the next 14 days")


WEEKLY_VIDEO = "weekly_video"  # instapost.weekly.WEEKLY (not a Format: never planned daily)
REEL_FORMATS = (Format.REEL_BOOK_POV.value, SCENE_REEL)


def base_format(fmt: str) -> Format:
    """The daily format whose slots and platforms a post uses (a scene reel is a reel)."""
    return Format.REEL_BOOK_POV if fmt == SCENE_REEL else Format(fmt)


def same_slots(fmt: str) -> list[str]:
    return list(REEL_FORMATS) if fmt in REEL_FORMATS else [fmt]


def approve(s: Session, item: Item, actor: str, when: datetime | None = None) -> Item:
    if item.status != ItemStatus.NEEDS_REVIEW:
        raise QueueError(f"{item.id} is {item.status}, not waiting for review")
    start = date.fromisoformat(item.for_date) if item.for_date else utcnow().date()
    if item.format == WEEKLY_VIDEO:
        from instapost import weekly
        item.scheduled_at = when or weekly.publish_time()
    else:
        item.scheduled_at = when or next_free_slot(s, item.format, start,
                                                   preferred=item.preferred_slot)
    item.status = ItemStatus.APPROVED
    item.approved_by = actor
    item.approved_at = utcnow()
    if item.format in REEL_FORMATS and get_settings().instagram_reels == "app":
        item.manual_platforms = sorted(set(item.manual_platforms or []) | {"instagram"})
    audit(s, actor, "approved", item.id, scheduled_at=item.scheduled_at.isoformat())
    return item


def reject(s: Session, item: Item, actor: str, reason: str = "") -> Item:
    if item.status not in (ItemStatus.NEEDS_REVIEW, ItemStatus.APPROVED, ItemStatus.BLOCKED):
        raise QueueError(f"{item.id} is {item.status} and can no longer be rejected")
    item.status = ItemStatus.REJECTED
    item.scheduled_at = None
    audit(s, actor, "rejected", item.id, reason=reason)
    return item


def unapprove(s: Session, item: Item, actor: str) -> Item:
    """Take an approved item back to the review queue (before it is published)."""
    if item.status != ItemStatus.APPROVED:
        raise QueueError(f"{item.id} is {item.status}, not approved")
    item.status = ItemStatus.NEEDS_REVIEW
    item.scheduled_at = None
    audit(s, actor, "unapproved", item.id)
    return item


def retry(s: Session, item: Item, actor: str) -> Item:
    """Send a failed or partly published item back for another publishing round.
    Platforms that already succeeded are skipped by the publisher."""
    if item.status not in (ItemStatus.FAILED, ItemStatus.PARTIAL):
        raise QueueError(f"{item.id} is {item.status}, nothing to retry")
    for pub in item.publications:
        if pub.status != "published":
            pub.status, pub.attempts, pub.container_id = "pending", 0, None
    item.status = ItemStatus.APPROVED
    item.scheduled_at = utcnow()
    audit(s, actor, "retry", item.id)
    return item


def reschedule(s: Session, item: Item, when_utc: datetime, actor: str) -> Item:
    if item.status != ItemStatus.APPROVED:
        raise QueueError("only approved items can be rescheduled")
    if when_utc < utcnow() + MIN_LEAD:
        raise QueueError("the new time must be at least 10 minutes from now")
    item.scheduled_at = when_utc
    audit(s, actor, "rescheduled", item.id, scheduled_at=when_utc.isoformat())
    return item


def pending_review(s: Session) -> list[Item]:
    return list(s.scalars(select(Item).where(Item.status == ItemStatus.NEEDS_REVIEW)
                          .order_by(Item.for_date, Item.created_at)))


def approve_next(s: Session, n: int, actor: str) -> list[Item]:
    """Approve the next `n` items waiting for review, oldest planned date first."""
    return [approve(s, item, actor) for item in pending_review(s)[:max(0, n)]]


def approve_day(s: Session, day: date, actor: str) -> list[Item]:
    return [approve(s, item, actor) for item in pending_review(s)
            if item.for_date == day.isoformat()]


def due(s: Session, now: datetime | None = None) -> list[Item]:
    now = now or utcnow()
    return list(s.scalars(select(Item).where(Item.status == ItemStatus.APPROVED,
                                             Item.scheduled_at <= now)
                          .order_by(Item.scheduled_at)))


def upcoming(s: Session, days: int = 7) -> list[Item]:
    end = utcnow() + timedelta(days=days)
    return list(s.scalars(select(Item).where(Item.status.in_(TAKEN + (ItemStatus.FAILED,)),
                                             Item.scheduled_at <= end,
                                             Item.scheduled_at >= utcnow() - timedelta(days=1))
                          .order_by(Item.scheduled_at)))


def local_time_label(item: Item) -> str:
    if not item.scheduled_at:
        return "not scheduled"
    return to_local(item.scheduled_at, load_schedule()).strftime("%a %d %b, %H:%M")
