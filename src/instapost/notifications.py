"""Dashboard notifications, built from what the agent already records (no extra table).

Two kinds:
- alerts: conditions that are true right now (worker offline, kill switch on, budget nearly
  used, an image source refusing, a token about to expire, setup unfinished). They stay until
  the condition is gone.
- events: things that happened (published, failed, tomorrow's posts ready ...), from the audit
  log and job list of the last 7 days. "Mark all read" stores the time; older events are read.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from instapost import approvals, budget
from instapost.config import get_settings
from instapost.db import AuditLog, Item, Job, get_kv, set_kv, utcnow

SEEN_KEY = "notifications_seen_at"
WINDOW = timedelta(days=7)


@dataclass
class Notification:
    level: str      # ok / info / warn / bad
    title: str
    detail: str = ""
    link: str = ""
    at: datetime | None = None  # None for alerts (true right now)
    unread: bool = True


def _seen_at(s: Session) -> datetime:
    raw = get_kv(s, SEEN_KEY, "")
    return datetime.fromisoformat(raw) if raw else datetime(2000, 1, 1)


def mark_all_read(s: Session) -> None:
    set_kv(s, SEEN_KEY, utcnow().isoformat())


def alerts(s: Session) -> list[Notification]:
    from instapost import setup_status
    from instapost.publish import tokens
    from instapost.studio.images import chain_names, recent_refusal

    out: list[Notification] = []
    st = get_settings()
    hb = get_kv(s, "worker_heartbeat", "")
    if not hb or utcnow() - datetime.fromisoformat(hb) > timedelta(minutes=2):
        out.append(Notification("bad", "Worker is not running",
                                "Nothing is created or published. On the server: "
                                "bash deploy/docker.sh status", "/activity"))
    if approvals.kill_switch_on(s):
        out.append(Notification("bad", "Kill switch is ON", "Nothing will be published.",
                                "/settings"))
    for period, cap in (("day", st.daily_budget_usd), ("month", st.monthly_budget_usd)):
        spent = budget.spent(period)
        if cap and spent >= 0.8 * cap:
            out.append(Notification("warn" if spent < cap else "bad",
                                    f"Budget {period}: ${spent:.2f} of ${cap:.2f} used",
                                    "Paid steps stop at the cap.", "/settings"))
    chain = chain_names()
    if (reason := recent_refusal(chain[0])) and len(chain) > 1:
        out.append(Notification("info", f"{chain[0].title()} images unavailable",
                                f"Using {', '.join(chain[1:])} instead. {reason[:160]}",
                                "/settings"))
    ig = tokens.info("ig_access_token")
    if ig.get("expires_at"):
        left = datetime.fromisoformat(ig["expires_at"]) - datetime.now(UTC)
        if left < timedelta(days=10):
            out.append(Notification("warn", "Instagram token expires soon",
                                    f"In {max(left.days, 0)} days; the worker renews it daily. "
                                    "If this stays, generate a new token.", "/settings"))
    todo = [x.label for x in setup_status.checklist() if x.required and not x.done]
    if todo:
        out.append(Notification("warn", f"Setup: {len(todo)} step(s) left",
                                ", ".join(todo[:3]), "/settings"))
    if st.reel_style == "plate":
        from instapost.studio import plates
        data = plates.load_index()  # reads the index only; photos are checked elsewhere
        review = sum(1 for e in data["plates"].values() if e.get("status") == "review")
        in_use = len(plates.ready(data))
        if review:
            out.append(Notification("info", f"{review} book photo(s) waiting for your check",
                                    "Approve them or set the page corners.", "/backgrounds"))
        elif not in_use:
            out.append(Notification("info", "No book photos yet",
                                    "Reels use the classic look until you add some.",
                                    "/backgrounds"))
    waiting = len(approvals.pending_review(s))
    if waiting:
        out.append(Notification("info", f"{waiting} post(s) waiting for your approval",
                                "Approve, post now or reject them.", "/inbox"))
    for a in out:
        a.unread = a.level in ("warn", "bad")
    return out


def events(s: Session) -> list[Notification]:
    since = utcnow() - WINDOW
    seen = _seen_at(s)
    hooks = {}
    out: list[Notification] = []

    def hook(item_id: str | None) -> str:
        if not item_id:
            return ""
        if item_id not in hooks:
            item = s.get(Item, item_id)
            hooks[item_id] = (item.hook[:60] if item else item_id)
        return hooks[item_id]

    logs = s.scalars(select(AuditLog).where(AuditLog.ts >= since)
                     .order_by(AuditLog.id.desc()).limit(300))
    for a in logs:
        d = a.detail or {}
        link = f"/items/{a.item_id}" if a.item_id else ""
        n = None
        if a.action == "published":
            n = Notification("ok", f"Published on {d.get('platform', '?').title()}",
                             hook(a.item_id), d.get("permalink") or link)
        elif a.action in ("publish_error", "publish_failed"):
            where = f" on {d['platform'].title()}" if d.get("platform") else ""
            n = Notification("bad", f"Publishing failed{where}",
                             (d.get("error") or d.get("reason") or "")[:200] or hook(a.item_id),
                             link)
        elif a.action == "daily_limit_reached":
            n = Notification("warn", f"Daily limit reached on {d.get('platform', '?').title()}",
                             "The post goes out tomorrow morning.", link)
        elif a.action == "generate_day_finished":
            n = Notification("info", f"{d.get('created', 0)} new post(s) for {d.get('day', '')}",
                             "Ready for your approval.", "/inbox")
        elif a.action == "manual_instagram":
            n = Notification("info", "Waiting for your Instagram post",
                             f"Post it in the app, then /posted {a.item_id} <link>", link)
        elif a.action == "plan_built":
            n = Notification("info", "Next week's plan is ready", "Check and edit it.", "/plan")
        elif a.action == "ig_token_refreshed":
            n = Notification("ok", "Instagram token renewed", "", "/settings")
        if n:
            n.at, n.unread = a.ts, a.ts > seen
            out.append(n)
    for j in s.scalars(select(Job).where(Job.status == "failed", Job.started_at >= since)
                       .order_by(Job.id.desc()).limit(30)):
        out.append(Notification("bad", f"{j.kind.replace('_', ' ').title()} failed",
                                (j.error or j.label)[:200], "/activity", j.started_at,
                                j.started_at > seen))
    return sorted(out, key=lambda n: n.at, reverse=True)


def build(s: Session) -> dict:
    al, ev = alerts(s), events(s)
    return {"alerts": al, "events": ev,
            "unread": sum(n.unread for n in al) + sum(n.unread for n in ev)}
