"""Daily report: a short plain summary of yesterday, sent to Telegram and kept on the
dashboard. Built only from collected numbers (no AI call, no cost)."""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost import approvals, budget
from instapost.db import AccountSnapshot, Report, session
from instapost.schedule import load_schedule, to_utc_naive
from instapost.setup_status import open_items
from instapost.stats import queries
from instapost.stats.labels import PLATFORM_LABEL, compact

TOP_N = 3


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    sched = load_schedule()
    start = datetime.combine(day, datetime.min.time(), tzinfo=sched.tz)
    return to_utc_naive(start), to_utc_naive(start + timedelta(days=1))


def _fmt_pct(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def build(s: Session, day: date) -> tuple[str, dict]:
    start, end = _day_bounds(day)
    series = queries.followers_series(s, days=40)
    followers = {}
    for p, pts in series.items():
        upto = [v for d, v in pts if d <= day]
        before = [v for d, v in pts if d <= day - timedelta(days=1)]
        if upto:
            followers[p] = {"now": upto[-1], "change": upto[-1] - before[-1] if before else None}

    rows_7d = queries.post_rows(s, since=end - timedelta(days=7))
    yesterday = [r for r in rows_7d if start <= r.published_at < end]
    scored = sorted((r for r in rows_7d if r.score is not None), key=lambda r: r.score,
                    reverse=True)
    ig_daily = s.scalars(select(AccountSnapshot).where(
        AccountSnapshot.platform == "instagram", AccountSnapshot.kind == "daily",
        AccountSnapshot.day == day.isoformat())).first()

    lines = [f"📊 Report for {day.strftime('%a %d %b')}"]
    if followers:
        parts = []
        for p, f in followers.items():
            ch = f["change"]
            delta = "" if ch is None else f" ({'+' if ch >= 0 else ''}{ch})"
            parts.append(f"{PLATFORM_LABEL[p]} {compact(f['now'])}{delta}")
        lines.append("👥 Followers: " + " · ".join(parts))
    else:
        lines.append("👥 Followers: no data yet")
    if ig_daily and ig_daily.metrics:
        m = ig_daily.metrics
        lines.append(f"📈 Instagram day: reach {compact(m.get('reach'))} · views "
                     f"{compact(m.get('views'))} · profile visits "
                     f"{compact(m.get('profile_views'))}")
    lines.append(f"📝 Posted: {len(yesterday)}")
    for r in yesterday:
        sc = "not scored yet" if r.score is None else f"score {r.score}"
        lines.append(f"  • {r.item.hook[:45]} · {compact(r.combined.get('views'))} views · {sc}")
    if scored:
        lines.append("🏆 Best of the last 7 days:")
        for r in scored[:TOP_N]:
            c = r.combined
            lines.append(f"  {r.score} · {r.item.hook[:40]} · sends {compact(c.get('shares'))}"
                         f" · saves {compact(c.get('saves'))} · watch "
                         f"{_fmt_pct(c.get('watch_ratio'))} [{r.item.pillar}]")
    pending = len(approvals.pending_review(s))
    lines.append(f"⏳ Waiting for approval: {pending}")
    lines.append(f"💰 Spend: month ${budget.spent('month'):.2f}")
    todo = [step.label for step in open_items() if step.required]
    if todo:
        lines.append("🧩 Setup still open: " + ", ".join(todo[:4])
                     + (f" (+{len(todo) - 4} more)" if len(todo) > 4 else ""))

    data = {
        "followers": followers,
        "posted": [{"id": r.item.id, "score": r.score, **r.combined} for r in yesterday],
        "top": [{"id": r.item.id, "score": r.score, "pillar": r.item.pillar} for r in
                scored[:TOP_N]],
        "ig_day": ig_daily.metrics if ig_daily else None,
        "pending": pending,
    }
    return "\n".join(lines), data


def create(day: date) -> Report:
    with session() as s:
        text, data = build(s, day)
        existing = s.scalars(select(Report).where(Report.day == day.isoformat())).first()
        if existing:
            existing.text, existing.data = text, data
            return existing
        rep = Report(day=day.isoformat(), text=text, data=data)
        s.add(rep)
        return rep


def latest(s: Session, limit: int = 30) -> list[Report]:
    return list(s.scalars(select(Report).order_by(Report.day.desc()).limit(limit)))
