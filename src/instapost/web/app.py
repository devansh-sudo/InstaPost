"""Admin dashboard (FastAPI, server-rendered pages + htmx for the live panel).

The web app only reads state and records decisions. Generating and publishing are done by the
worker; the dashboard queues requests for it (see jobs.enqueue).
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from instapost import approvals, budget, jobs, setup_status
from instapost.config import get_settings
from instapost.db import AuditLog, Item, ItemStatus, Job, get_kv, session, utcnow
from instapost.models import Format, Pillar
from instapost.publish import publisher, tokens, youtube
from instapost.publish.media_host import verify_local
from instapost.schedule import load_schedule, to_local, to_utc_naive
from instapost.stats import queries, report
from instapost.stats import score as scoring
from instapost.stats.labels import PLATFORM_LABEL, compact
from instapost.web import auth, charts

HERE = Path(__file__).parent
FILE_NAME = re.compile(r"^(reel\.mp4|cover\.jpg|story\.jpg|slide_\d{2}\.jpg)$")
templates = Jinja2Templates(directory=str(HERE / "templates"))


def _local(dt: datetime | None, fmt: str = "%a %d %b, %H:%M") -> str:
    return to_local(dt, load_schedule()).strftime(fmt) if dt else "–"


templates.env.filters["local"] = _local


class LoginRequired(Exception):
    pass


def create_app() -> FastAPI:
    s = get_settings()
    if not s.session_secret or len(s.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must be set (at least 32 characters); "
                           "run `instapost admin-setup`")
    app = FastAPI(title="InstaPost", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret, same_site="strict",
                       https_only=s.public_base_url is not None
                       and s.public_base_url.startswith("https"),
                       max_age=12 * 3600, session_cookie="ip_session")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(LoginRequired)
    async def _login_redirect(request: Request, _exc):
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    _routes(app)
    return app


# --- helpers ------------------------------------------------------------------------------

def user(request: Request) -> str:
    name = request.session.get("user")
    if not name:
        raise LoginRequired()
    return name


def csrf(request: Request, token: str = Form("")) -> None:
    expected = request.session.get("csrf")
    header = request.headers.get("X-CSRF-Token", "")
    if not expected or (token != expected and header != expected):
        raise HTTPException(403, "invalid form token; reload the page")


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    if "csrf" not in request.session:
        request.session["csrf"] = auth.new_csrf_token()
    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, {
        "csrf": request.session["csrf"], "flash": flash, "user": request.session.get("user"),
        **ctx})


def back(request: Request, message: str, to: str | None = None) -> RedirectResponse:
    request.session["flash"] = message
    if to is None:
        # Go back to the page the form was on, but never to another site.
        ref = urlsplit(request.headers.get("referer", ""))
        to = (ref.path + (f"?{ref.query}" if ref.query else "")) if ref.path.startswith("/") \
            and not ref.path.startswith("//") else "/"
    return RedirectResponse(to, status_code=303)


def _item_files(item: Item) -> dict:
    folder = Path(item.folder)
    return {"video": (folder / "reel.mp4").exists(), "cover": (folder / "cover.jpg").exists(),
            "story": (folder / "story.jpg").exists(),
            "slides": [p.name for p in sorted(folder.glob("slide_*.jpg"))]}


def _ops_context() -> dict:
    with session() as s:
        heartbeat = get_kv(s, "worker_heartbeat", "")
        hb = datetime.fromisoformat(heartbeat) if heartbeat else None
        return {
            "kill": approvals.kill_switch_on(s),
            "autonomy": approvals.autonomy(s),
            "worker_ok": bool(hb and utcnow() - hb < timedelta(minutes=2)),
            "heartbeat": hb,
            "running": list(s.scalars(select(Job).where(Job.status.in_(("running", "queued")))
                                      .order_by(Job.id.desc()))),
            "recent_jobs": list(s.scalars(select(Job).order_by(Job.id.desc()).limit(10))),
            "audit": list(s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(25))),
            "pending": len(approvals.pending_review(s)),
            "upcoming": approvals.upcoming(s, days=2)[:6],
            "spend_day": budget.spent("day"), "spend_month": budget.spent("month"),
            "settings": get_settings(),
        }


def _by_format(rows) -> list[tuple[str, float, int]]:
    acc: dict[str, list[float]] = {}
    for r in rows:
        if r.score is not None:
            acc.setdefault(r.item.format, []).append(r.score)
    return sorted(((f, round(sum(v) / len(v), 1), len(v)) for f, v in acc.items()),
                  key=lambda t: t[1], reverse=True)


# --- routes -------------------------------------------------------------------------------

def _routes(app: FastAPI) -> None:  # noqa: C901 - one place for all routes keeps it readable

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html")

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...),
              code: str = Form(...), _=Depends(csrf)):
        ip = request.client.host if request.client else "?"
        if not auth.verify_login(ip, username, password, code):
            msg = ("Too many attempts, wait 15 minutes." if auth.locked_out(ip)
                   else "Wrong username, password or code.")
            return back(request, msg, "/login")
        request.session.clear()
        request.session["user"] = username
        request.session["csrf"] = auth.new_csrf_token()
        with session() as s:
            from instapost.db import audit
            audit(s, f"web:{username}", "login", ip=ip)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request, _=Depends(csrf)):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # Overview / live ops
    @app.get("/", response_class=HTMLResponse)
    def ops(request: Request, who: str = Depends(user)):
        today = load_schedule().local_now().date()
        return render(request, "ops.html", **_ops_context(), formats=list(Format),
                      pillars=list(Pillar), tomorrow=today + timedelta(days=1))

    @app.get("/partials/live", response_class=HTMLResponse)
    def live(request: Request, who: str = Depends(user)):
        return render(request, "_live.html", **_ops_context())

    @app.post("/kill")
    def kill(request: Request, on: str = Form(...), who: str = Depends(user), _=Depends(csrf)):
        with session() as s:
            approvals.set_kill_switch(s, on == "1", f"web:{who}")
        return back(request, "Kill switch ON: nothing will be published." if on == "1"
                    else "Kill switch off: publishing resumes on schedule.")

    @app.post("/autonomy")
    def autonomy(request: Request, level: str = Form(...), who: str = Depends(user),
                 _=Depends(csrf)):
        with session() as s:
            approvals.set_autonomy(s, level, f"web:{who}")
        return back(request, f"Autonomy set to {level}.")

    @app.post("/make")
    def make(request: Request, format: str = Form(...), pillar: str = Form(...),
             for_date: str = Form(...), topic: str = Form(""), who: str = Depends(user),
             _=Depends(csrf)):
        Format(format), Pillar(pillar), date.fromisoformat(for_date)  # validate
        jobs.enqueue("make", f"{format} / {pillar} for {for_date}", format=format,
                     pillar=pillar, for_date=for_date, topic=topic.strip() or None,
                     actor=f"web:{who}")
        return back(request, "Queued. The worker starts within 30 seconds; "
                    "the result appears in the approval queue and on Telegram.")

    @app.post("/generate-day")
    def generate_day(request: Request, day: str = Form(...), who: str = Depends(user),
                     _=Depends(csrf)):
        date.fromisoformat(day)
        jobs.enqueue("generate_day", f"full plan for {day}", day=day, actor=f"web:{who}")
        return back(request, f"Queued the full plan for {day}.")

    # Approval queue
    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(request: Request, who: str = Depends(user)):
        with session() as s:
            items = approvals.pending_review(s)
            days = sorted({i.for_date for i in items if i.for_date})
            cards = [(i, _item_files(i)) for i in items]
            return render(request, "queue.html", cards=cards, days=days)

    def _act(request: Request, item_id: str, who: str, fn, message: str):
        try:
            with session() as s:
                item = s.get(Item, item_id)
                if item is None:
                    raise HTTPException(404)
                fn(s, item, f"web:{who}")
                label = approvals.local_time_label(item)
        except approvals.QueueError as e:
            return back(request, f"Not done: {e}")
        return back(request, message.format(when=label))

    @app.post("/items/{item_id}/approve")
    def approve(request: Request, item_id: str, who: str = Depends(user), _=Depends(csrf)):
        return _act(request, item_id, who, approvals.approve, "Approved. Posts {when}.")

    @app.post("/items/{item_id}/reject")
    def reject(request: Request, item_id: str, who: str = Depends(user), _=Depends(csrf)):
        return _act(request, item_id, who, approvals.reject, "Rejected.")

    @app.post("/items/{item_id}/unapprove")
    def unapprove(request: Request, item_id: str, who: str = Depends(user), _=Depends(csrf)):
        return _act(request, item_id, who, approvals.unapprove, "Moved back to the queue.")

    @app.post("/items/{item_id}/retry")
    def retry(request: Request, item_id: str, who: str = Depends(user), _=Depends(csrf)):
        return _act(request, item_id, who, approvals.retry,
                    "Retrying within a minute (platforms that worked are skipped).")

    @app.post("/items/{item_id}/publish-now")
    def publish_now(request: Request, item_id: str, who: str = Depends(user),
                    _=Depends(csrf)):
        def fn(s, item, actor):
            if item.status == ItemStatus.NEEDS_REVIEW:
                approvals.approve(s, item, actor, when=utcnow())
            elif item.status == ItemStatus.APPROVED:
                item.scheduled_at = utcnow()
            else:
                raise approvals.QueueError(f"{item.id} is {item.status}")
        return _act(request, item_id, who, fn, "Publishing within a minute.")

    @app.post("/items/{item_id}/regenerate")
    def regenerate(request: Request, item_id: str, who: str = Depends(user),
                   _=Depends(csrf)):
        jobs.enqueue("regenerate", f"regenerate {item_id}", item_id=item_id, actor=f"web:{who}")
        return back(request, "Regenerating. The new version will appear in the queue.")

    @app.post("/items/{item_id}/reschedule")
    def reschedule(request: Request, item_id: str, when: str = Form(...),
                   who: str = Depends(user), _=Depends(csrf)):
        sched = load_schedule()
        local = datetime.fromisoformat(when).replace(tzinfo=sched.tz)
        return _act(request, item_id, who,
                    lambda s, item, actor: approvals.reschedule(s, item, to_utc_naive(local),
                                                                actor),
                    "Rescheduled to {when}.")

    @app.post("/approve-next")
    def approve_next(request: Request, n: int = Form(...), who: str = Depends(user),
                     _=Depends(csrf)):
        with session() as s:
            items = approvals.approve_next(s, n, f"web:{who}")
        return back(request, f"Approved {len(items)} item(s).")

    @app.post("/approve-day")
    def approve_day(request: Request, day: str = Form(...), who: str = Depends(user),
                    _=Depends(csrf)):
        with session() as s:
            items = approvals.approve_day(s, date.fromisoformat(day), f"web:{who}")
        return back(request, f"Approved {len(items)} item(s) for {day}.")

    # Item detail, calendar, library
    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_page(request: Request, item_id: str, who: str = Depends(user)):
        import json
        with session() as s:
            item = s.get(Item, item_id)
            if item is None:
                raise HTTPException(404)
            folder = Path(item.folder)
            read = lambda n: json.loads((folder / n).read_text(encoding="utf-8")) \
                if (folder / n).exists() else {}  # noqa: E731
            history = list(s.scalars(select(AuditLog).where(AuditLog.item_id == item_id)
                                     .order_by(AuditLog.id)))
            timeline = queries.item_timeline(s, item_id)
            latest = {}
            for snap in timeline:
                latest[snap.platform] = snap.metrics
            combined = scoring.combined(latest) if latest else {}
            return render(request, "item.html", item=item, files=_item_files(item),
                          timeline=timeline, combined=combined, compact=compact,
                          score_of=scoring.score,
                          qa=read("qa.json"), fact=read("fact_check.json"),
                          draft=read("draft.json"), pubs=list(item.publications),
                          history=history, now_local=load_schedule().local_now())

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request, who: str = Depends(user)):
        with session() as s:
            items = approvals.upcoming(s, days=14)
            days: dict[str, list] = {}
            for i in items:
                days.setdefault(_local(i.scheduled_at, "%A %d %B"), []).append(i)
            return render(request, "calendar.html", days=days)

    @app.get("/library", response_class=HTMLResponse)
    def library(request: Request, status: str = "", who: str = Depends(user)):
        with session() as s:
            q = select(Item).order_by(Item.created_at.desc()).limit(200)
            if status:
                q = q.where(Item.status == status)
            return render(request, "library.html", items=list(s.scalars(q)), status=status,
                          statuses=[v for k, v in vars(ItemStatus).items() if k.isupper()])

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, who: str = Depends(user)):
        s = get_settings()
        sched = load_schedule()
        ig = tokens.info("ig_access_token")
        return render(request, "settings.html", s=s, sched=sched,
                      steps=setup_status.checklist(),
                      platforms=sorted(publisher.configured_platforms()),
                      ig_token=ig, youtube_ok=youtube.is_configured(),
                      spend_day=budget.spent("day"), spend_month=budget.spent("month"))

    # Stats & reports (Phase 3)
    @app.get("/stats", response_class=HTMLResponse)
    def stats_page(request: Request, days: int = 30, sort: str = "date",
                   who: str = Depends(user)):
        days = days if days in (7, 30, 90) else 30
        with session() as s:
            k = queries.kpis(s, days=7)
            rows = queries.post_rows(s, since=utcnow() - timedelta(days=days))
            keyfn = {"score": lambda r: r.score if r.score is not None else -1,
                     "views": lambda r: r.combined.get("views") or 0,
                     "date": lambda r: r.published_at}.get(sort, lambda r: r.published_at)
            rows = sorted(rows, key=keyfn, reverse=True)
            sched = load_schedule()
            slot_hours = sorted({int(t.split(":")[0]) for times in sched.slots.values()
                                 for t in times})
            return render(
                request, "stats.html", k=k, rows=rows, days=days, sort=sort,
                labels=PLATFORM_LABEL, compact=compact,
                followers_chart=charts.line_chart(queries.followers_series(s, days=days),
                                                  "Followers per platform"),
                pillar_chart=charts.bar_chart(queries.by_pillar(rows), "Average score by pillar"),
                format_chart=charts.bar_chart(_by_format(rows), "Average score by format"),
                heat=charts.heatmap(queries.slot_heatmap(rows), slot_hours,
                                    "Average score by weekday and posting hour"))

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request, who: str = Depends(user)):
        with session() as s:
            return render(request, "reports.html", reports=report.latest(s))

    # Files
    @app.get("/files/{item_id}/{name}")
    def item_file(item_id: str, name: str, who: str = Depends(user)):
        if not FILE_NAME.match(name):
            raise HTTPException(404)
        with session() as s:
            item = s.get(Item, item_id)
            if item is None:
                raise HTTPException(404)
            path = Path(item.folder) / name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})

    @app.get("/media/{rel:path}")
    def public_media(rel: str, e: int, s: str):
        """Signed, expiring public URL for Meta to fetch media (media_host=local only)."""
        if not verify_local(rel, e, s):
            raise HTTPException(403)
        root = get_settings().output_dir.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not FILE_NAME.match(path.name) or \
                not path.exists():
            raise HTTPException(404)
        return FileResponse(path)
