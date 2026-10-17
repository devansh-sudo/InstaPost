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
from functools import lru_cache

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import PlanEntry, WeeklyReview, audit, utcnow
from instapost.learn.bandit import Learner
from instapost.models import Format
from instapost.schedule import WEEKDAYS, load_schedule
from instapost.stats import queries

EXPLORE_RATE = 0.2
HISTORY_DAYS = 120


def week_start_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


@lru_cache
def festivals() -> dict[str, dict]:
    path = get_settings().data_dir / "calendar" / "festivals.yaml"
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(f["date"]): f for f in doc.get("festivals", []) if f.get("confirmed")}


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
                if first_reel and festival:
                    fixed["pillar"] = festival["pillar"]
                    topic = festival.get("topic")
                    notes.append(f"festival: {festival['name']}")
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
