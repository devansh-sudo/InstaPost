"""Creates planned content: the next day's batch, or a regeneration of one rejected item."""

from collections.abc import Callable
from datetime import date

from instapost import approvals, budget, pipeline
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, audit, get_kv, session, set_kv
from instapost.jobs import track
from instapost.models import Format, Pillar
from instapost.publish.tokens import redact
from instapost.schedule import load_schedule

EPOCH = date(2026, 1, 1)
Notify = Callable[[str], None]


def _make_and_register(fmt: Format, pillar: Pillar, for_date: date, actor: str,
                       topic: str | None = None) -> str | None:
    label = f"{fmt.value} / {pillar.value} for {for_date.isoformat()}"
    with track("generate", label) as job:
        result = pipeline.make(fmt, pillar, topic, on_step=job.step,
                               offline=get_settings().demo_mode)
        with session() as s:
            item = approvals.register(s, result, fmt, pillar.value, for_date, actor=actor)
            return item.id


def make_one(fmt: Format, pillar: Pillar, for_date: date, topic: str | None,
             actor: str) -> str:
    return _make_and_register(fmt, pillar, for_date, actor, topic)


def generate_day(day: date, notify: Notify, on_item: Callable[[str], None],
                 force: bool = False) -> list[str]:
    """Create everything planned for `day`. Runs once per day unless `force` is set."""
    key = f"generated:{day.isoformat()}"
    with session() as s:
        if get_kv(s, key, "") == "done" and not force:
            return []
        if approvals.kill_switch_on(s):
            return []
        set_kv(s, key, "running")
        audit(s, "agent", "generate_day_started", day=day.isoformat())

    created = []
    plan = load_schedule().plan_for(day, offset=(day - EPOCH).days)
    for fmt, pillar in plan:
        try:
            item_id = _make_and_register(fmt, pillar, day, "agent")
        except budget.BudgetExceeded as e:
            notify(f"⛔ Budget cap reached, generation paused: {e}")
            break
        except Exception as e:  # noqa: BLE001 - one failed piece must not stop the rest
            notify(f"⚠️ Could not create {fmt.value} ({pillar.value}): {redact(str(e))[:300]}")
            continue
        created.append(item_id)
        on_item(item_id)

    with session() as s:
        set_kv(s, key, "done")
        audit(s, "agent", "generate_day_finished", day=day.isoformat(), created=len(created))
    return created


def regenerate(item_id: str, actor: str) -> str:
    """Reject an item and create a fresh one with the same format, pillar and date."""
    with session() as s:
        item = s.get(Item, item_id)
        if item is None:
            raise approvals.QueueError(f"unknown item {item_id}")
        if item.status != ItemStatus.REJECTED:
            approvals.reject(s, item, actor, reason="regenerate")
        fmt, pillar, for_date = Format(item.format), Pillar(item.pillar), item.for_date
    return _make_and_register(fmt, pillar, date.fromisoformat(for_date), actor)
