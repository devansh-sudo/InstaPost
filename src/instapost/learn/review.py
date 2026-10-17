"""Weekly review: Claude Opus 5.5 reads last week's numbers and the learner's beliefs and writes
a short, plain analysis plus topic ideas for the next plan. About $0.05-0.15 per week.

The review only advises. It cannot change the schedule, the cadence or anything published;
its topic ideas feed the next weekly plan, which you can still edit.
"""

import json
from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost import llm
from instapost.db import WeeklyReview, utcnow
from instapost.learn.bandit import Learner
from instapost.models import Pillar
from instapost.schedule import load_schedule, to_local
from instapost.stats import queries

MODEL = "claude-opus-5-5"

SYSTEM = """You are the growth analyst for a small Hindi spiritual and peace-of-mind page
(Shiva, Krishna/Gita, Sant Kabir, life lessons) on Instagram, Facebook Reels and YouTube
Shorts. Each week you get the numbers and what the learning system currently believes.

Write for the page owner: simple English, short sentences, no jargon. Be honest about
uncertainty: a new page has few posts, and differences between a handful of posts are often
noise. Say so when that is the case instead of inventing patterns. Never recommend anything
against platform rules (engagement bait, follow-for-follow, buying followers, reposting
others' content, fear-based captions).

Score is 0-100 from sends/reach, saves/reach, watch time, comments and follows. Sends and
saves matter most for reach on Instagram.

topic_ideas: for each pillar, 5 fresh, specific, relatable topics in Hindi (Devanagari) that
fit what is working. Respectful, no medical or political claims, correct attributions."""


class PillarTopics(BaseModel):
    pillar: Pillar
    topics: list[str]


class ReviewOut(BaseModel):
    summary: str
    what_worked: list[str]
    what_did_not: list[str]
    try_next: list[str]
    cadence_note: str
    topic_ideas: list[PillarTopics]


def _payload(s: Session, week_start: date) -> dict:
    sched = load_schedule()
    end = week_start + timedelta(days=7)
    rows = queries.post_rows(s, since=utcnow() - timedelta(days=120))
    learner = Learner(rows)
    week_rows = [r for r in rows
                 if week_start <= to_local(r.published_at, sched).date() < end]
    posts = [{
        "format": r.item.format, "pillar": r.item.pillar, "hook": r.item.hook,
        "features": r.item.features, "posted": to_local(r.published_at, sched).isoformat(),
        "score": r.score, **{k: r.combined.get(k) for k in
                             ("views", "reach", "shares", "saves", "comments", "watch_ratio")},
    } for r in week_rows]
    beliefs = [b for b in learner.beliefs() if b["posts"] > 0]
    k = queries.kpis(s, days=7)
    previous = s.scalars(select(WeeklyReview)
                         .where(WeeklyReview.week_start < week_start.isoformat())
                         .order_by(WeeklyReview.week_start.desc())).first()
    return {
        "week": f"{week_start} to {end - timedelta(days=1)}",
        "posts_this_week": posts,
        "posts_scored_all_time": learner.observations,
        "learner_beliefs": beliefs,
        "followers": k["followers"], "followers_change_7d": k["followers_change"],
        "previous_try_next": previous.data.get("try_next") if previous else None,
    }


def render_text(week_start: date, out: ReviewOut) -> str:
    def bullets(xs):
        return "\n".join(f"• {x}" for x in xs) or "• –"
    return (f"🧠 Weekly review, week of {week_start.strftime('%d %b')}\n\n{out.summary}\n\n"
            f"✅ Worked\n{bullets(out.what_worked)}\n\n❌ Didn't\n{bullets(out.what_did_not)}\n\n"
            f"🔬 Try next\n{bullets(out.try_next)}\n\n📅 {out.cadence_note}")


def create(s: Session, week_start: date) -> WeeklyReview:
    payload = _payload(s, week_start)
    out = llm.structured(
        model=MODEL, system=SYSTEM,
        content="This week's data (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str),
        schema=ReviewOut, purpose="weekly_review", effort="high", max_tokens=16000,
        estimated_usd=0.25)
    data = out.model_dump(mode="json")
    data["topic_ideas"] = {t["pillar"]: t["topics"] for t in data["topic_ideas"]}
    text = render_text(week_start, out)
    existing = s.scalars(select(WeeklyReview)
                         .where(WeeklyReview.week_start == week_start.isoformat())).first()
    if existing:
        existing.text, existing.data = text, data
        return existing
    review = WeeklyReview(week_start=week_start.isoformat(), text=text, data=data)
    s.add(review)
    return review


def latest(s: Session, limit: int = 8) -> list[WeeklyReview]:
    return list(s.scalars(select(WeeklyReview).order_by(WeeklyReview.week_start.desc())
                          .limit(limit)))
