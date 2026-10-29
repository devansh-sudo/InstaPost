"""Creates planned content: the next day's batch, or a regeneration of one rejected item."""

from collections.abc import Callable
from datetime import date

from sqlalchemy import select

from instapost import approvals, budget, pipeline
from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, PlanEntry, audit, get_kv, session, set_kv
from instapost.jobs import track
from instapost.learn import planner
from instapost.models import Format, Pillar
from instapost.publish.tokens import redact
from instapost.schedule import load_schedule
from instapost.studio.images import QuotaError

EPOCH = date(2026, 1, 1)
Notify = Callable[[str], None]


def _make_and_register(fmt: Format, pillar: Pillar, for_date: date, actor: str,
                       topic: str | None = None, entry_id: int | None = None) -> str | None:
    label = f"{fmt.value} / {pillar.value} for {for_date.isoformat()}"
    plan, slot, scene = None, None, None
    if entry_id is not None:
        with session() as s:
            e = s.get(PlanEntry, entry_id)
            topic = topic or e.topic
            slot, scene = e.slot, e.scene
            presets = load_brand().scene_presets
            plan = {"hook_style": e.hook_style, "scene": presets[e.scene % len(presets)],
                    "length": e.length, "experiment": e.experiment}
            plan = {k: v for k, v in plan.items() if v}
            label += f" (plan #{entry_id})"
    with track("generate", label) as job:
        result = pipeline.make(fmt, pillar, topic, on_step=job.step,
                               offline=get_settings().demo_mode, plan=plan)
        with session() as s:
            item = approvals.register(s, result, fmt, pillar.value, for_date, actor=actor,
                                      preferred_slot=slot, scene=scene)
            if entry_id is not None:
                e = s.get(PlanEntry, entry_id)
                e.status, e.item_id = "used", item.id
            return item.id


def make_weekly(actor: str, end_day: date | None = None) -> str:
    """Build the Sunday YouTube video from the week's posts (see instapost.weekly)."""
    from instapost import weekly

    with track("weekly_video", f"weekly video up to {end_day or 'today'}") as job:
        job.step("rendering the weekly video")
        with session() as s:
            return weekly.make(s, end_day, actor=actor,
                               days=load_schedule().weekly_video.days).id


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
    with session() as s:
        entries = [(Format(e.format), Pillar(e.pillar), e.id)
                   for e in planner.entries_for_day(s, day)]
    # The weekly plan decides what to make; without one, fall back to schedule.yaml rotation.
    plan = entries or [(fmt, pillar, None) for fmt, pillar in
                       load_schedule().plan_for(day, offset=(day - EPOCH).days)]
    for fmt, pillar, entry_id in plan:
        try:
            item_id = _make_and_register(fmt, pillar, day, "agent", entry_id=entry_id)
        except budget.BudgetExceeded as e:
            notify(f"⛔ Budget cap reached, generation paused: {e}")
            break
        except QuotaError as e:
            # Every other piece would fail the same way after paying for its text.
            notify(f"⛔ Image API quota problem, generation stopped: {redact(str(e))[:600]}\n"
                   "Fix it, then use \"Create plan\" on the dashboard's Live page.")
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
        if item.format == approvals.WEEKLY_VIDEO:
            end_day = date.fromisoformat(item.for_date)
        else:
            end_day = None
    if end_day is not None:
        return make_weekly(actor, end_day)
    with session() as s:
        item = s.get(Item, item_id)
        fmt, pillar, for_date = Format(item.format), Pillar(item.pillar), item.for_date
        entry = s.scalars(select(PlanEntry).where(PlanEntry.item_id == item_id)).first()
        entry_id = entry.id if entry else None
    return _make_and_register(fmt, pillar, date.fromisoformat(for_date), actor,
                              entry_id=entry_id)
