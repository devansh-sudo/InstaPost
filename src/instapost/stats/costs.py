"""What the agent spent, per post and per stage, from the spend ledger (state/spend.jsonl).

Every paid call is one ledger line. Lines written while a post was being made carry its
item id (budget.tagged), so each post's cost can be split into writing, checks and images,
with the token counts that explain it.
"""

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost import budget
from instapost.db import AuditLog, Item, ItemStatus
from instapost.schedule import load_schedule

# Ledger "kind" -> stage shown on the dashboard. The three groups get the three chart colours.
STAGES = {"write": "Writing", "fact_check": "Fact check", "vision_qa": "Image check",
          "image": "Images", "weekly_review": "Weekly review"}
GROUPS = {"Writing": "Writing", "Fact check": "Checks", "Image check": "Checks",
          "Images": "Images & other", "Weekly review": "Images & other"}
GROUP_SLOTS = {"Writing": 1, "Checks": 2, "Images & other": 3}
FORMAT_LABEL = {"reel_book_pov": "Reel", "carousel_shlok": "Carousel", "story_line": "Story"}


def stage_of(kind: str) -> str:
    return STAGES.get(kind.split(":")[0], kind.replace("_", " ").capitalize())


@dataclass
class PostCost:
    item_id: str
    format: str
    first: datetime
    usd: float = 0.0
    calls: int = 0
    stages: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    writes: int = 0
    item: Item | None = None
    regenerated: bool = False

    @property
    def why(self) -> list[str]:
        """Plain reasons this post cost what it did (or why the money was wasted)."""
        out = []
        if self.writes > 1:
            out.append(f"written {self.writes} times (first draft too long for 12 s)")
        if self.item is None:
            out.append("the run stopped before a post was saved (error); paid calls are lost")
        elif self.item.status == ItemStatus.BLOCKED:
            out.append("blocked by the checks: paid but not usable")
        elif self.item.status == ItemStatus.REJECTED:
            out.append("redone" if self.regenerated else "rejected: paid but not posted")
        if self.cache_read:
            out.append(f"{self.cache_read:,} prompt tokens came from the cache (10% price)")
        if self.tokens_out > 3 * max(self.tokens_in, 1):
            out.append("most of the cost is the model's thinking/answer tokens")
        return out


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(UTC).replace(tzinfo=None)


def summary(s: Session, days: int = 30) -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    since = now - timedelta(days=days)
    entries = [e | {"at": _parse(e["ts"])} for e in budget.entries()]
    recent = [e for e in entries if e["at"] >= since]

    posts: dict[str, PostCost] = {}
    other = defaultdict(float)
    by_stage = defaultdict(float)
    for e in recent:
        stage = stage_of(e["kind"])
        by_stage[stage] += e["usd"]
        item_id = e.get("item_id")
        if not item_id:
            other[stage] += e["usd"]
            continue
        p = posts.get(item_id) or posts.setdefault(
            item_id, PostCost(item_id, e.get("format", ""), e["at"]))
        p.usd += e["usd"]
        p.calls += 1
        p.stages[stage] += e["usd"]
        p.tokens_in += e.get("in", 0) + e.get("cache_read", 0) + e.get("cache_write", 0)
        p.tokens_out += e.get("out", 0)
        p.cache_read += e.get("cache_read", 0)
        p.writes += stage == "Writing"

    if posts:
        items = {i.id: i for i in s.scalars(select(Item).where(Item.id.in_(list(posts))))}
        redone = {a.item_id for a in s.scalars(select(AuditLog).where(
            AuditLog.item_id.in_(list(posts)), AuditLog.action == "rejected"))
            if (a.detail or {}).get("reason") == "regenerate"}
        for p in posts.values():
            p.item = items.get(p.item_id)
            p.format = p.format or (p.item.format if p.item else "")
            p.regenerated = p.item_id in redone

    per_format = {}
    for fmt, label in FORMAT_LABEL.items():
        costs = [p.usd for p in posts.values() if p.format == fmt]
        per_format[label] = {"avg": sum(costs) / len(costs) if costs else None,
                             "n": len(costs), "total": sum(costs)}
    wasted = sum(p.usd for p in posts.values()
                 if p.item is None or p.item.status in (ItemStatus.BLOCKED, ItemStatus.REJECTED))

    # Days in local time, oldest first, stacked by stage group.
    sched = load_schedule()
    today = sched.local_now().date()
    day_list = [today - timedelta(days=i) for i in range(min(days, 30) - 1, -1, -1)]
    index = {d: i for i, d in enumerate(day_list)}
    per_day = {g: [0.0] * len(day_list) for g in GROUP_SLOTS}
    from instapost.schedule import to_local
    for e in recent:
        i = index.get(to_local(e["at"], sched).date())
        if i is not None:
            per_day[GROUPS.get(stage_of(e["kind"]), "Images & other")][i] += e["usd"]

    month_spent = budget.spent("month")
    d = date.today()
    days_in_month = calendar.monthrange(d.year, d.month)[1]
    projected = month_spent / d.day * days_in_month if d.day else month_spent
    total = sum(e["usd"] for e in recent)
    return {
        "days": days, "total": total, "posts": sorted(posts.values(), key=lambda p: p.first,
                                                      reverse=True),
        "per_format": per_format, "by_stage": dict(sorted(by_stage.items(),
                                                          key=lambda kv: -kv[1])),
        "other": dict(other), "wasted": wasted, "day_list": day_list, "per_day": per_day,
        "month_spent": month_spent, "projected": projected, "day_spent": budget.spent("day"),
        "calls": len(recent),
        "untracked": sum(e["usd"] for e in recent if not e.get("item_id")
                         and stage_of(e["kind"]) != "Weekly review"),
    }


def item_cost(item_id: str) -> PostCost | None:
    """One post's cost (for its page), over the whole ledger."""
    p = None
    for e in budget.entries():
        if e.get("item_id") != item_id:
            continue
        p = p or PostCost(item_id, e.get("format", ""), _parse(e["ts"]))
        stage = stage_of(e["kind"])
        p.usd += e["usd"]
        p.calls += 1
        p.stages[stage] += e["usd"]
        p.tokens_in += e.get("in", 0) + e.get("cache_read", 0) + e.get("cache_write", 0)
        p.tokens_out += e.get("out", 0)
        p.cache_read += e.get("cache_read", 0)
        p.writes += stage == "Writing"
    return p
