"""One plain-text report of everything: followers, every post's numbers, what works by theme,
look, time and hook, what the learner believes, the AI reviews, costs, problems and what's
coming. For reading, for sharing with someone helping you plan, and for the next month's
plan. Read-only; it never contains keys or tokens.

    bash deploy/docker.sh cli insights            # last 30 days
    bash deploy/docker.sh cli insights --days 90  # longer
"""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instapost.db import AuditLog, Item, PlanEntry, Report, WeeklyReview, utcnow
from instapost.schedule import WEEKDAYS, load_schedule, to_local
from instapost.stats import queries

KEYS = ("views", "reach", "likes", "comments", "shares", "saves", "follows")


def _n(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.1f}"
    return f"{v:,}"


def _short(text: str, n: int = 48) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _group(rows, key) -> list[tuple[str, int, float | None, float]]:
    """(value, posts, average score of scored posts, average views)."""
    acc: dict[str, list] = defaultdict(list)
    for r in rows:
        k = key(r)
        if k:
            acc[str(k)].append(r)
    out = []
    for k, rs in acc.items():
        scores = [r.score for r in rs if r.score is not None]
        views = [r.combined.get("views") or 0 for r in rs]
        out.append((k, len(rs), round(sum(scores) / len(scores), 1) if scores else None,
                    round(sum(views) / len(views), 1)))
    return sorted(out, key=lambda t: (t[2] is None, -(t[2] or 0), -t[3]))


def _table(head: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def build(s: Session, days: int = 30) -> str:
    sched = load_schedule()
    now = utcnow()
    since = now - timedelta(days=days)
    today = sched.local_now().date()
    rows = queries.post_rows(s, since=since)
    out = [f"# Page report: last {days} days (to {today:%d %b %Y})", ""]

    # --- account ---------------------------------------------------------------------------
    k7, k30 = queries.kpis(s, 7), queries.kpis(s, min(days, 30))
    out += ["## Followers", *_table(
        ["platform", "now", "change 7 d", f"change {min(days, 30)} d"],
        [[p, _n(k30["followers"][p]), _n(k7["followers_change"][p]),
          _n(k30["followers_change"][p])] for p in queries.PLATFORMS]), ""]

    # --- totals ----------------------------------------------------------------------------
    total = {k: sum(r.combined.get(k) or 0 for r in rows) for k in KEYS}
    per_platform: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for p, m in r.per_platform.items():
            per_platform[p]["posts"] += 1
            for k in KEYS:
                per_platform[p][k] += m.get(k) or 0
    scores = [r.score for r in rows if r.score is not None]
    ratios = [r.combined["watch_ratio"] for r in rows if r.combined.get("watch_ratio")]
    mix = ", ".join(f"{v} {k}" for k, v in Counter(r.item.format for r in rows).most_common())
    out += ["## Totals",
            f"- Posts published: {len(rows)} ({mix})",
            f"- Views {_n(total['views'])} · likes {_n(total['likes'])} · comments "
            f"{_n(total['comments'])} · shares {_n(total['shares'])} · saves "
            f"{_n(total['saves'])} · new follows from posts {_n(total['follows'])}",
            f"- Average score {_n(round(sum(scores) / len(scores), 1)) if scores else '–'}/100 "
            f"over {len(scores)} scored posts · average watch "
            f"{f'{100 * sum(ratios) / len(ratios):.0f}%' if ratios else '–'} of the video", "",
            *_table(["platform", "posts", *KEYS],
                    [[p, c["posts"], *[_n(c[k]) for k in KEYS]]
                     for p, c in sorted(per_platform.items())]), ""]

    # --- every post ------------------------------------------------------------------------
    out += ["## Every post (newest first)"]
    table = []
    for r in rows:
        feats = r.item.features or {}
        local = to_local(r.published_at, sched)
        table.append([f"{local:%d %b %H:%M}", r.item.format.replace("_", " "), r.item.pillar,
                      feats.get("template", "–"), feats.get("hook_style", "–"),
                      _short(r.item.hook), " ".join(sorted(r.per_platform)) or "–",
                      _n(r.combined.get("views")), _n(r.combined.get("likes")),
                      _n(r.combined.get("comments")), _n(r.combined.get("shares")),
                      _n(r.combined.get("saves")),
                      f"{100 * r.combined['watch_ratio']:.0f}%" if r.combined.get("watch_ratio")
                      else "–", _n(r.score)])
    out += _table(["posted", "format", "theme", "look", "hook style", "hook", "platforms",
                   "views", "likes", "comm.", "shares", "saves", "watch", "score"], table) \
        if table else ["No published posts in this period yet."]
    out.append("")

    # --- what works ------------------------------------------------------------------------
    def local_hour(r):
        return f"{to_local(r.published_at, sched):%H}:00"

    def weekday(r):
        return WEEKDAYS[to_local(r.published_at, sched).weekday()]

    groups = [("Theme", lambda r: r.item.pillar), ("Format", lambda r: r.item.format),
              ("Look", lambda r: (r.item.features or {}).get("template")),
              ("Hook style", lambda r: (r.item.features or {}).get("hook_style")),
              ("Reel length", lambda r: (r.item.features or {}).get("length")),
              ("Hour posted", local_hour), ("Weekday", weekday),
              ("Book photo", lambda r: (r.item.features or {}).get("photo")),
              ("Drawing", lambda r: (r.item.features or {}).get("drawing"))]
    out += ["## What works (average per post)",
            "Score is 0–100: views against reach, watch time, shares, saves and comments."]
    for title, key in groups:
        g = _group(rows, key)
        if g:
            out += ["", f"### {title}", *_table(["value", "posts", "avg score", "avg views"],
                                                 [[k, n, _n(sc), _n(v)] for k, n, sc, v in g])]
    out.append("")

    # --- learner ---------------------------------------------------------------------------
    from instapost.learn.bandit import Learner
    from instapost.learn.planner import HISTORY_DAYS
    learner = Learner(queries.post_rows(s, since=now - timedelta(days=HISTORY_DAYS)))
    out += ["## What the learner believes",
            f"Based on {learner.observations} scored posts (posts count once their numbers "
            "settle). Score = expected 0–100, with the range it could plausibly be."]
    by_dim: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for b in learner.beliefs():
        if b["posts"]:
            by_dim[(b["format"], b["dimension"])].append(b)
    from instapost.models import Format
    for (fmt, dim), bs in sorted(by_dim.items()):
        verdict = learner.confidence(Format(fmt), dim)
        out += ["", f"### {fmt} · {dim}: {verdict}", *_table(
            ["option", "posts", "expected", "range"],
            [[b["option"], b["posts"], b["mean"], f"{b['low']}–{b['high']}"]
             for b in sorted(bs, key=lambda b: -b["mean"])])]
    if not by_dim:
        out.append("Nothing learned yet: posts need about 2 days of numbers first.")
    out.append("")

    # --- AI reviews and daily reports --------------------------------------------------------
    reviews = list(s.scalars(select(WeeklyReview).order_by(WeeklyReview.week_start.desc())
                             .limit(2)))
    out += ["## Weekly AI reviews (latest)"]
    out += [f"### Week of {rv.week_start}\n{rv.text}" for rv in reviews] or ["None yet."]
    reports = list(s.scalars(select(Report).order_by(Report.day.desc()).limit(3)))
    out += ["", "## Latest daily reports"]
    out += [f"### {rp.day}\n{rp.text}" for rp in reports] or ["None yet."]
    out.append("")

    # --- costs -----------------------------------------------------------------------------
    from instapost.stats import costs
    c = costs.summary(s, days)
    out += ["## Costs",
            f"- Spent in the last {days} days: ${c['total']:.2f} · this month so far "
            f"${c['month_spent']:.2f} · month projected ${c['projected']:.2f}",
            *[f"- {stage}: ${usd:.3f}" for stage, usd in c["by_stage"].items()],
            *[f"- Average per {fmt}: ${v['avg']:.3f} ({v['n']} posts)"
              for fmt, v in c["per_format"].items() if v["avg"] is not None], ""]

    # --- health ----------------------------------------------------------------------------
    status = Counter(st for (st,) in s.execute(
        select(Item.status).where(Item.created_at >= since)))
    actions = dict(s.execute(select(AuditLog.action, func.count()).where(
        AuditLog.ts >= since, AuditLog.action.in_(
            ["publish_error", "daily_limit_reached", "rejected", "approved",
             "publish_paused_kill_switch"])).group_by(AuditLog.action)).all())
    errors = list(s.scalars(select(AuditLog).where(AuditLog.ts >= since,
                                                   AuditLog.action == "publish_error")
                            .order_by(AuditLog.id.desc()).limit(8)))
    blocked = list(s.scalars(select(Item).where(Item.created_at >= since,
                                                Item.status == "blocked").limit(8)))
    out += ["## Health",
            "- Items made: " + ", ".join(f"{v} {k}" for k, v in status.most_common()),
            "- " + ", ".join(f"{k}: {v}" for k, v in sorted(actions.items())) if actions
            else "- No approvals or publishing events in this period.",
            *[f"- publish error {e.ts:%d %b} {(e.detail or {}).get('platform')}: "
              f"{_short(str((e.detail or {}).get('error')), 140)}" for e in errors],
            *[f"- blocked {b.id}: {_short(b.qa_summary or '', 140)}" for b in blocked], ""]

    # --- coming up -------------------------------------------------------------------------
    from instapost import festivals
    plan = list(s.scalars(select(PlanEntry).where(
        PlanEntry.day >= today.isoformat(),
        PlanEntry.day <= (today + timedelta(days=7)).isoformat(),
        PlanEntry.status == "planned").order_by(PlanEntry.day, PlanEntry.slot)))
    out += ["## Next 7 days",
            *[f"- {e.day} {e.slot} {e.format} · {e.pillar} · {_short(e.topic or '', 70)}"
              for e in plan],
            *(["- (no plan entries)"] if not plan else []),
            "", "### Festivals in the next 6 weeks",
            *[f"- {f['date']:%d %b} {f['name']} "
              f"({'confirmed' if f['confirmed'] else 'NOT checked'})"
              for f in festivals.upcoming(today, 42) if not f["skip"]]]
    return "\n".join(out) + "\n"
