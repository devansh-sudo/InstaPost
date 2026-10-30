"""Weekly planner: turns what the learner knows into next week's plan.

How many pieces go out each day stays your decision (schedule.yaml `daily_plan`). The planner
decides *what* each piece is: pillar, hook style, background scene, posting slot, reel length
and topic. Rules, in order:

1. Confirmed festivals (data/calendar/festivals.yaml) set the first reel of their day.
2. Monday's first reel uses `monday_pillar` (Somvar).
3. About 20% of pieces are experiments: one dimension is set to its least-tried option, so
   the learner keeps learning instead of repeating early winners forever.
4. Everything else is chosen by Thompson sampling (see bandit.py), avoiding the same pillar
   twice in one day for the same format.

Entries you edit on the Plan page are never overwritten when the plan is rebuilt.
"""

import random
from datetime import date, timedelta

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost.brand import load_brand
from instapost.db import PlanEntry, WeeklyReview, audit, utcnow
from instapost.learn.bandit import Learner
from instapost.models import Format
from instapost.schedule import WEEKDAYS, load_schedule
from instapost.stats import queries

EXPLORE_RATE = 0.2
HISTORY_DAYS = 120


def week_start_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def festivals() -> dict[str, dict]:
    """Confirmed festivals by date (instapost.festivals: the file plus your dashboard edits)."""
    from instapost import festivals as fest

    return fest.confirmed_by_day()


def _topics(s: Session) -> dict[str, list[str]]:
    review = s.scalars(select(WeeklyReview).order_by(WeeklyReview.week_start.desc())).first()
    ideas = (review.data or {}).get("topic_ideas", {}) if review else {}
    return {p: list(v) for p, v in ideas.items()}


def _reason(learner: Learner, fmt: Format, dim: str, value: str) -> str:
    best = learner.best(fmt, dim)
    arm = learner.arms[(fmt, dim)][value]
    if best and best.option == value and arm.n >= 3:
        return f"{dim} {value}: best so far ({100 * arm.mean:.0f}, {arm.n} posts)"
    return ""


def build_week(s: Session, week_start: date, seed: int | None = None,
               actor: str = "agent") -> list[PlanEntry]:
    rng = random.Random(seed)
    sched = load_schedule()
    brand = load_brand()
    rows = queries.post_rows(s, since=utcnow() - timedelta(days=HISTORY_DAYS))
    learner = Learner(rows, brand)
    topics = _topics(s)
    ws = week_start.isoformat()

    existing = list(s.scalars(select(PlanEntry).where(PlanEntry.week_start == ws)))
    keep = [e for e in existing if e.edited_by or e.status != "planned"]
    for e in existing:
        if e not in keep:
            s.delete(e)
    kept_keys = {(e.day, e.format, e.position) for e in keep}

    entries = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        counts = sched.daily_plan[WEEKDAYS[day.weekday()]]
        festival = festivals().get(day.isoformat())
        for fmt, n in counts.items():
            if n <= 0:
                continue
            slots_all = sched.slots.get(fmt, [])
            chosen_slots: list[str] = []
            for _ in range(n):
                free = [sl for sl in slots_all if sl not in chosen_slots] or slots_all
                chosen_slots.append(learner.sample(fmt, "slot", rng, free))
            used_pillars: list[str] = []
            for pos in range(n):
                if (day.isoformat(), fmt.value, pos) in kept_keys:
                    continue
                dims = learner.arms.keys()
                fixed: dict[str, str] = {"slot": chosen_slots[pos]}
                notes: list[str] = []
                topic = None
                first_reel = fmt == Format.REEL_BOOK_POV and pos == 0
                first_story = fmt == Format.STORY_LINE and pos == 0
                if first_reel and festival:
                    fixed["pillar"] = festival["pillar"]
                    topic = festival.get("topic")
                    notes.append(f"festival: {festival['name']}")
                elif first_story and festival and festival.get("greeting"):
                    topic = festival["greeting"]  # the festival greeting is the morning story
                    notes.append(f"festival greeting: {festival['name']}")
                elif first_reel and day.weekday() == 0 and sched.monday_pillar:
                    fixed["pillar"] = sched.monday_pillar.value
                    notes.append("Somvar: Shiva")

                choice: dict[str, str] = dict(fixed)
                experiment = None
                free_dims = [d for (f, d) in dims if f == fmt and d not in fixed]
                if free_dims and rng.random() < EXPLORE_RATE:
                    dim = rng.choice(free_dims)
                    allowed = None
                    if dim == "pillar":
                        allowed = [p for p in learner.arms[(fmt, dim)] if p not in used_pillars] \
                            or None
                    choice[dim] = learner.least_tried(fmt, dim, rng, allowed)
                    shown = int(choice[dim]) + 1 if dim == "scene" else choice[dim]
                    experiment = f"testing {dim} = {shown}"
                for (f, dim) in dims:
                    if f != fmt or dim in choice:
                        continue
                    allowed = None
                    if dim == "pillar":
                        allowed = [p for p in learner.arms[(fmt, dim)] if p not in used_pillars] \
                            or None
                    choice[dim] = learner.sample(fmt, dim, rng, allowed)
                    if r := _reason(learner, fmt, dim, choice[dim]):
                        notes.append(r)
                used_pillars.append(choice["pillar"])
                if topic is None and topics.get(choice["pillar"]):
                    topic = topics[choice["pillar"]].pop(0)

                entry = PlanEntry(
                    week_start=ws, day=day.isoformat(), position=pos, format=fmt.value,
                    pillar=choice["pillar"], hook_style=choice.get("hook_style", "statement"),
                    scene=int(choice.get("scene", 0)), slot=choice["slot"],
                    length=choice.get("length"), topic=topic, experiment=experiment,
                    reason="; ".join(notes) or "learning: picked by sampling",
                    status="planned")
                s.add(entry)
                entries.append(entry)
    audit(s, actor, "plan_built", week_start=ws, entries=len(entries),
          observations=learner.observations)
    s.flush()  # give the new entries their ids
    return keep + entries


def entries_for_day(s: Session, day: date) -> list[PlanEntry]:
    return list(s.scalars(select(PlanEntry).where(PlanEntry.day == day.isoformat(),
                                                  PlanEntry.status == "planned")
                          .order_by(PlanEntry.format, PlanEntry.position)))


def week_entries(s: Session, week_start: date) -> list[PlanEntry]:
    return list(s.scalars(select(PlanEntry).where(PlanEntry.week_start == week_start.isoformat())
                          .order_by(PlanEntry.day, PlanEntry.slot, PlanEntry.position)))


MONTH_PLAN_ACTOR = "month plan"


def load_month(s: Session, path, actor: str = MONTH_PLAN_ACTOR) -> dict:
    """Apply a hand-written plan file (see data/plan/*.yaml) on top of the weekly plans.

    The learner still fills slot, hook style, scene and length; the file sets pillar, topic and
    audio mood for each entry. Entries are marked as edited, so weekly rebuilds keep them.
    """
    from pathlib import Path

    from instapost.models import Pillar

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    days = {date.fromisoformat(str(d)): spec or {} for d, spec in (doc.get("days") or {}).items()}
    if not days:
        raise ValueError(f"{path}: no days")
    start = min(days)
    sched = load_schedule()
    weeks = sorted({week_start_of(d) for d in days})
    for ws in weeks:
        build_week(s, ws, actor=actor)
    # Days of those weeks before the plan starts are past or today: nothing to plan there.
    week_keys = [w.isoformat() for w in weeks]
    for e in s.scalars(select(PlanEntry).where(PlanEntry.week_start.in_(week_keys),
                                               PlanEntry.day < start.isoformat(),
                                               PlanEntry.status == "planned")):
        s.delete(e)
    s.flush()

    from instapost.db import get_kv

    summary = {"days": len(days), "entries": 0, "warnings": []}
    for day, spec in sorted(days.items()):
        # Posts already made for this day stay as they are, so loading the file again
        # (e.g. after editing it mid-month) never adds a second set for a past day.
        if get_kv(s, f"generated:{day.isoformat()}", "") in ("done", "running"):
            summary["warnings"].append(f"{day}: already made, left as it is")
            continue
        for fmt in Format:
            wanted = spec.get(fmt.value) or []
            entries = list(s.scalars(select(PlanEntry).where(
                PlanEntry.day == day.isoformat(), PlanEntry.format == fmt.value,
                PlanEntry.status == "planned").order_by(PlanEntry.position)))
            expected = sched.daily_plan[WEEKDAYS[day.weekday()]].get(fmt, 0)
            if len(wanted) != expected:
                summary["warnings"].append(
                    f"{day} {fmt.value}: {len(wanted)} in the file, schedule.yaml says {expected}")
            slots = sched.slots.get(fmt, [])
            for pos, item in enumerate(wanted):
                pillar = Pillar(item["pillar"]).value  # raises on a typo
                if pos < len(entries):
                    e = entries[pos]
                else:  # more than the learner planned: add one in the next free slot
                    used = {x.slot for x in entries}
                    slot = next((sl for sl in slots if sl not in used), slots[pos % len(slots)])
                    e = PlanEntry(week_start=week_start_of(day).isoformat(), day=day.isoformat(),
                                  position=pos, format=fmt.value, pillar=pillar,
                                  hook_style="statement", scene=0, slot=slot,
                                  length="medium" if fmt == Format.REEL_BOOK_POV else None,
                                  status="planned")
                    s.add(e)
                    entries.append(e)
                e.pillar, e.topic = pillar, str(item["topic"]).strip()
                e.audio = item.get("audio")
                e.edited_by = actor
                e.experiment = None  # the content is fixed by the plan; slots etc. still learn
                note = spec.get("note")
                e.reason = "month plan" + (f" · {note}" if note else "")
                summary["entries"] += 1
            for extra in entries[len(wanted):]:
                extra.status, extra.edited_by = "skipped", actor
    audit(s, actor, "month_plan_loaded", start=start.isoformat(), end=max(days).isoformat(),
          entries=summary["entries"])
    return summary
