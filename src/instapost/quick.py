"""Make one piece on request (server command or Telegram /new) and post it right away."""

from datetime import date

from sqlalchemy.orm import Session

from instapost import approvals, jobs
from instapost.db import Item, ItemStatus, audit, utcnow
from instapost.models import Format, Pillar
from instapost.schedule import load_schedule

FORMAT_WORDS = {"reel": Format.REEL_BOOK_POV, "carousel": Format.CAROUSEL_SHLOK,
                "story": Format.STORY_LINE}
PILLAR_WORDS = {p.value: p for p in Pillar} | {
    "shiv": Pillar.SHIV_DHYAN, "shiva": Pillar.SHIV_DHYAN, "jeevan": Pillar.JEEVAN_SUTRA,
    "life": Pillar.LIFE_LESSON, "krishna": Pillar.GITA}

USAGE = ("Format reel|carousel|story (default reel), then optionally a pillar "
         "(gita, shiv, kabir, jeevan, life) and a topic, e.g.  reel gita गुस्सा")


def parse(words: list[str]) -> tuple[Format, Pillar, str | None]:
    """'reel gita गुस्सा पर काबू' -> (reel, gita, 'गुस्सा पर काबू'). Everything is optional."""
    words = list(words)
    fmt = Format.REEL_BOOK_POV
    if words and words[0].lower() in FORMAT_WORDS:
        fmt = FORMAT_WORDS[words.pop(0).lower()]
    if words and words[0].lower() in PILLAR_WORDS:
        pillar = PILLAR_WORDS[words.pop(0).lower()]
    else:  # follow the pillar rotation from schedule.yaml
        rotation = load_schedule().pillar_rotation[fmt]
        pillar = rotation[date.today().toordinal() % len(rotation)]
    return fmt, pillar, " ".join(words).strip() or None


def request_new(fmt: Format, pillar: Pillar, topic: str | None, actor: str) -> int:
    """Queue it for the worker; the result arrives in Telegram with the approval buttons."""
    today = load_schedule().local_now().date().isoformat()
    return jobs.enqueue("make", f"{fmt.value} / {pillar.value} for {today}", format=fmt.value,
                        pillar=pillar.value, for_date=today, topic=topic, actor=actor)


def post_now(s: Session, item: Item, actor: str) -> Item:
    """Approve (if still waiting) and publish at the worker's next tick (within ~30 s)."""
    now = utcnow()
    if item.status == ItemStatus.NEEDS_REVIEW:
        approvals.approve(s, item, actor, when=now)
    elif item.status == ItemStatus.APPROVED:
        item.scheduled_at = now
    else:
        raise approvals.QueueError(f"{item.id} is {item.status}; only items waiting for "
                                   "approval or approved can be posted now")
    audit(s, actor, "post_now", item.id)
    return item


def trending(s: Session, item: Item, actor: str) -> list[str]:
    """You post this reel on Instagram yourself (to add a trending song from Instagram's
    licensed library); every other connected platform is published now. Returns those."""
    from instapost.publish import publisher

    if item.format != Format.REEL_BOOK_POV:
        raise approvals.QueueError("trending songs are for reels only")
    if item.status not in (ItemStatus.NEEDS_REVIEW, ItemStatus.APPROVED):
        raise approvals.QueueError(f"{item.id} is {item.status}")
    item.manual_platforms = sorted(set(item.manual_platforms or []) | {"instagram"})
    others = publisher.targets_for(item)
    if item.status == ItemStatus.NEEDS_REVIEW:
        approvals.approve(s, item, actor, when=utcnow())
    item.scheduled_at = utcnow() if others else None  # None: waits for your /posted
    audit(s, actor, "manual_instagram", item.id, others=others)
    return others


def record_manual_post(s: Session, item: Item, permalink: str, actor: str) -> str:
    """Link a reel you posted in the Instagram app, so its numbers are collected too."""
    from instapost.db import Publication
    from instapost.publish.instagram import Instagram, shortcode

    if not shortcode(permalink):
        raise approvals.QueueError("that is not an Instagram post link (instagram.com/reel/...)")
    media = Instagram().find_media(permalink)
    pub = next((p for p in item.publications if p.platform == "instagram"), None)
    if pub is None:
        pub = Publication(item_id=item.id, platform="instagram", attempts=0)
        item.publications.append(pub)
    pub.status, pub.permalink, pub.published_at = "published", permalink, utcnow()
    pub.remote_id = media["id"] if media else None
    if item.status == ItemStatus.APPROVED and item.scheduled_at is None:
        item.status = ItemStatus.PUBLISHED
    audit(s, actor, "manual_posted", item.id, platform="instagram", permalink=permalink)
    if media:
        return "✅ Linked. Its views, saves and shares are collected like the others."
    return ("✅ Saved the link, but Instagram didn't list this post for your account yet, so its "
            "numbers can't be collected. Try /posted again in a few minutes.")
