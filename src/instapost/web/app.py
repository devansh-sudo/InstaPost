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

from instapost import approvals, budget, jobs, notifications, setup_status
from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.db import AuditLog, Item, ItemStatus, Job, PlanEntry, audit, get_kv, session, utcnow
from instapost.learn import planner, review
from instapost.learn.bandit import LENGTHS, Learner, dimensions
from instapost.models import Format, HookStyle, Pillar
from instapost.publish import publisher, tokens, youtube
from instapost.publish.media_host import verify_local
from instapost.schedule import WEEKDAYS, load_schedule, to_local, to_utc_naive
from instapost.stats import dashboard, queries, report
from instapost.stats import score as scoring
from instapost.stats.labels import PLATFORM_LABEL, compact
from instapost.web import auth, charts

HERE = Path(__file__).parent
FILE_NAME = re.compile(r"^(reel\.mp4|video\.mp4|cover\.jpg|story\.jpg|slide_\d{2}\.jpg)$")
MAX_TRACK_MB = 25
LIBRARY_PAGE = 40  # posts per Library page
FORMAT_LABEL = {"reel_book_pov": "Reel", "scene_reel": "Scene reel", "carousel_shlok": "Carousel",
                "story_line": "Story", "weekly_video": "Weekly video"}
PILLAR_LABEL = {"jeevan_sutra": "Jeevan sutra", "shiv_dhyan": "Shiv dhyan", "gita": "Gita",
                "kabir": "Kabir", "life_lesson": "Life lesson"}
templates = Jinja2Templates(directory=str(HERE / "templates"))


def _local(dt: datetime | None, fmt: str = "%a %d %b, %H:%M") -> str:
    return to_local(dt, load_schedule()).strftime(fmt) if dt else "–"


templates.env.filters["local"] = _local


def _static_version() -> str:
    """Changes whenever a file in static/ changes, for the ?v= on its links."""
    return str(max(int(f.stat().st_mtime) for f in (HERE / "static").iterdir() if f.is_file()))


templates.env.globals["static_v"] = _static_version()
templates.env.globals["destinations"] = publisher.destinations
templates.env.globals["planned_platforms"] = publisher.planned_platforms
templates.env.globals["post_links"] = publisher.post_links
templates.env.filters["plan_state"] = lambda p, available: (p, "planned" if p in available
                                                              else "off")
templates.env.globals["dest_label"] = publisher.DEST_LABEL
templates.env.globals["platform_label"] = PLATFORM_LABEL


class LoginRequired(Exception):
    pass


class _PageGzip:
    """Gzip for pages, CSS and JS only. Video, audio and pictures are already compressed,
    and the player's range requests (seeking) need them untouched."""

    SKIP = ("/files/", "/music/file/", "/media/", "/thumb/")

    def __init__(self, app):
        from starlette.middleware.gzip import GZipMiddleware
        self.app, self.gzip = app, GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope["path"].startswith(self.SKIP):
            return await self.gzip(scope, receive, send)
        return await self.app(scope, receive, send)


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
    # Pages are 15-90 KB of HTML; compressed they are 5-10x smaller over the internet.
    app.add_middleware(_PageGzip)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        # same-origin: our own pages see where a form came from (to return there after an
        # action), other sites never see dashboard URLs.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/static/") and request.query_params.get("v"):
            # Versioned links (?v=...) change whenever the file changes: cache for a year.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
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


def _shell() -> dict:
    """What every page shows around its content: inbox count, status, notifications."""
    brand = load_brand()
    with session() as s:
        heartbeat = get_kv(s, "worker_heartbeat", "")
        hb = datetime.fromisoformat(heartbeat) if heartbeat else None
        return {"pending": len(approvals.pending_review(s)), "kill": approvals.kill_switch_on(s),
                "autonomy": approvals.autonomy(s),
                "worker_ok": bool(hb and utcnow() - hb < timedelta(minutes=2)),
                "notes": notifications.build(s), "page_name": "Thoughtory",
                "handle": brand.printed_handle or brand.page_name}


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    if "csrf" not in request.session:
        request.session["csrf"] = auth.new_csrf_token()
    flash = request.session.pop("flash", None)
    shell = _shell() if request.session.get("user") else {}
    return templates.TemplateResponse(request, name, {
        "csrf": request.session["csrf"], "flash": flash, "user": request.session.get("user"),
        "shell": shell, "compact": compact, "labels": PLATFORM_LABEL,
        "available": publisher.configured_platforms(), **ctx})


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
            "long": (folder / "video.mp4").exists(),
            "story": (folder / "story.jpg").exists(),
            "slides": [p.name for p in sorted(folder.glob("slide_*.jpg"))]}


THUMB_W = 240  # list thumbnails are shown at most 120 px wide (2x for sharp screens)


def _small_thumb(folder: Path) -> Path | None:
    """A ~15 KB copy of the post's picture for lists (the originals are ~400 KB each), made
    once next to the post and remade when the picture changes."""
    from PIL import Image

    for name in ("cover.jpg", "story.jpg", "slide_01.jpg"):
        src = folder / name
        if not src.exists():
            continue
        small = folder / "thumb.jpg"
        try:
            if not small.exists() or small.stat().st_mtime < src.stat().st_mtime:
                with Image.open(src) as img:
                    img = img.convert("RGB")
                    img.thumbnail((THUMB_W, THUMB_W * 2))
                    tmp = small.with_suffix(".tmp")
                    img.save(tmp, "JPEG", quality=78, optimize=True)
                    tmp.replace(small)
            return small
        except OSError:
            return src  # read-only folder: fall back to the original
    return None


def _running_context() -> dict:
    with session() as s:
        running = list(s.scalars(select(Job).where(Job.status.in_(("running", "queued")))
                                 .order_by(Job.id.desc())))
        last = s.scalars(select(Job).where(Job.status.in_(("done", "failed")))
                         .order_by(Job.id.desc()).limit(1)).first()
        return {"running": running, "last_job": last}


def _by_format(rows) -> list[tuple[str, float, int]]:
    acc: dict[str, list[float]] = {}
    for r in rows:
        if r.score is not None:
            acc.setdefault(r.item.format, []).append(r.score)
    return sorted(((f, round(sum(v) / len(v), 1), len(v)) for f, v in acc.items()),
                  key=lambda t: t[1], reverse=True)


def _option_label(dim: str, option: str, presets: list[str]) -> str:
    if dim == "scene" and option.isdigit() and int(option) < len(presets):
        return f"{int(option) + 1}: {presets[int(option)][:14]}…"
    return option


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

    # Home
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, days: int = 7, who: str = Depends(user)):
        days = days if days in (7, 30) else 7
        sched = load_schedule()
        hour = sched.local_now().hour
        greeting = ("Good morning" if 4 <= hour < 12 else "Good afternoon" if hour < 17
                    else "Good evening")
        with session() as s:
            ov = dashboard.overview(s, days=days)
            day_list, per_platform = dashboard.daily_views(ov["rows"], 14)
            return render(
                request, "home.html", ov=ov, greeting=greeting, sched=sched,
                pending=approvals.pending_review(s)[:4], upcoming=approvals.upcoming(s, days=2)[:6],
                top=dashboard.top_posts(ov["rows"]), settings=get_settings(),
                spend_day=budget.spent("day"), spend_month=budget.spent("month"),
                views_chart=charts.stacked_columns(day_list, per_platform, "Views per day"),
                followers_chart=charts.line_chart(queries.followers_series(s, days=30),
                                                  "Followers per platform"),
                **_running_context())

    @app.get("/partials/running", response_class=HTMLResponse)
    def running(request: Request, who: str = Depends(user)):
        return templates.TemplateResponse(request, "_running.html", _running_context())

    @app.get("/thumb/{item_id}")
    def thumb(item_id: str, who: str = Depends(user)):
        with session() as s:
            item = s.get(Item, item_id)
            folder = Path(item.folder) if item else None
        small = _small_thumb(folder) if folder else None
        if small is not None:
            return FileResponse(small, headers={"Cache-Control": "private, max-age=86400"})
        # A neutral placeholder (blocked items have no image).
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 4">'
               '<rect width="3" height="4" fill="#8f877e" fill-opacity="0.22"/></svg>')
        return Response(svg, media_type="image/svg+xml")

    @app.get("/create", response_class=HTMLResponse)
    def create_page(request: Request, who: str = Depends(user)):
        from instapost.render import templates as looks
        today = load_schedule().local_now().date()
        return render(request, "create.html", formats=list(Format), pillars=list(Pillar),
                      pillar_labels=PILLAR_LABEL, today=today, tomorrow=today + timedelta(days=1),
                      sched=load_schedule(),
                      looks=[(kind, looks.KIND_NAMES[kind],
                              [looks.BY_KIND[kind][t] for t in looks.enabled(kind)])
                             for kind in looks.BY_KIND])

    @app.get("/notifications", response_class=HTMLResponse)
    def notifications_page(request: Request, who: str = Depends(user)):
        return render(request, "notifications.html")

    @app.post("/notifications/read")
    def notifications_read(request: Request, who: str = Depends(user), _=Depends(csrf)):
        with session() as s:
            notifications.mark_all_read(s)
        return back(request, "All notifications marked as read.")

    @app.get("/activity", response_class=HTMLResponse)
    def activity(request: Request, who: str = Depends(user)):
        with session() as s:
            return render(request, "activity.html",
                          recent_jobs=list(s.scalars(select(Job).order_by(Job.id.desc()).limit(15))),
                          audit=list(s.scalars(select(AuditLog).order_by(AuditLog.id.desc())
                                               .limit(100))),
                          **_running_context())

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
             for_date: str = Form(...), topic: str = Form(""), template: str = Form(""),
             who: str = Depends(user), _=Depends(csrf)):
        Format(format), Pillar(pillar), date.fromisoformat(for_date)  # validate
        jobs.enqueue("make", f"{format} / {pillar} for {for_date}", format=format,
                     pillar=pillar, for_date=for_date, topic=topic.strip() or None,
                     template=_look_for(format, template),
                     actor=f"web:{who}")
        return back(request, "Queued. The worker starts within 30 seconds; "
                    "the result appears in the approval queue and on Telegram.")

    @app.post("/weekly-video")
    def weekly_video(request: Request, who: str = Depends(user), _=Depends(csrf)):
        jobs.enqueue("weekly_video", "weekly video (dashboard)", actor=f"web:{who}")
        return back(request, "Making this week's YouTube video. It appears in the Inbox in a "
                    "few minutes.")

    @app.post("/generate-day")
    def generate_day(request: Request, day: str = Form(...), who: str = Depends(user),
                     _=Depends(csrf)):
        date.fromisoformat(day)
        jobs.enqueue("generate_day", f"full plan for {day}", day=day, actor=f"web:{who}")
        return back(request, f"Queued the full plan for {day}.")

    # Inbox (approval queue)
    @app.get("/inbox", response_class=HTMLResponse)
    def inbox(request: Request, who: str = Depends(user)):
        with session() as s:
            items = approvals.pending_review(s)
            days = sorted({i.for_date for i in items if i.for_date})
            cards = [(i, _item_files(i)) for i in items]
            return render(request, "inbox.html", cards=cards, days=days, sched=load_schedule())

    @app.get("/queue")
    def queue_redirect():
        return RedirectResponse("/inbox", status_code=301)

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

    @app.post("/items/{item_id}/retemplate")
    def retemplate(request: Request, item_id: str, template: str = Form(""),
                   who: str = Depends(user), _=Depends(csrf)):
        from instapost.render import templates as looks
        with session() as s:
            item = s.get(Item, item_id)
            if item is None or item.format == approvals.WEEKLY_VIDEO:
                raise HTTPException(404)
            fmt = item.format
        look = _look_for(fmt, template)
        jobs.enqueue("retemplate", f"new look for {item_id}", item_id=item_id, template=look,
                     actor=f"web:{who}")
        name = looks.label(look, looks.kind_of(fmt)) or "next"
        return back(request, f"Making it again in the {name} look (same text, no new writing "
                    "cost). The new version replaces this one.")

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

        from instapost.render import templates as looks
        from instapost.stats import costs
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
                          history=history, now_local=load_schedule().local_now(),
                          cost=costs.item_cost(item_id), look=_look(folder),
                          looks=looks.BY_KIND[looks.kind_of(item.format)]
                          if item.format != approvals.WEEKLY_VIDEO else {})

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request, days: int = 14, start: str = "",
                 who: str = Depends(user)):
        """Only the requested range is loaded: 7, 14 or 30 days from `start` (default today)."""
        from datetime import time as dtime
        days = days if days in (7, 14, 30) else 14
        sched = load_schedule()
        today = sched.local_now().date()
        try:
            first = date.fromisoformat(start) if start else today
        except ValueError:
            first = today
        last = first + timedelta(days=days - 1)
        lo = to_utc_naive(datetime.combine(first, dtime.min, tzinfo=sched.tz))
        hi = to_utc_naive(datetime.combine(last + timedelta(days=1), dtime.min, tzinfo=sched.tz))
        available = publisher.configured_platforms()
        with session() as s:
            scheduled = list(s.scalars(select(Item).where(
                Item.scheduled_at >= lo, Item.scheduled_at < hi,
                Item.status.in_(approvals.TAKEN + (ItemStatus.FAILED,)))
                .order_by(Item.scheduled_at)))
            waiting = list(s.scalars(select(Item).where(
                Item.status == ItemStatus.NEEDS_REVIEW, Item.for_date >= first.isoformat(),
                Item.for_date <= last.isoformat())))
            entries = list(s.scalars(select(PlanEntry).where(
                PlanEntry.status == "planned", PlanEntry.day >= first.isoformat(),
                PlanEntry.day <= last.isoformat())
                .order_by(PlanEntry.slot, PlanEntry.position)))
            weekly_days = {i.for_date for i in s.scalars(select(Item).where(
                Item.format == approvals.WEEKLY_VIDEO, Item.status != ItemStatus.REJECTED,
                Item.for_date >= first.isoformat(), Item.for_date <= last.isoformat()))}
            cfg = sched.weekly_video
            day_list = []
            totals = {"formats": {}, "platforms": {}, "posts": 0}

            def count(fmt: str, platforms: list[str]) -> None:
                totals["posts"] += 1
                totals["formats"][fmt] = totals["formats"].get(fmt, 0) + 1
                for pl in platforms:
                    totals["platforms"][pl] = totals["platforms"].get(pl, 0) + 1

            for n in range(days):
                d = first + timedelta(days=n)
                posts = [i for i in scheduled if to_local(i.scheduled_at, sched).date() == d]
                wait = [i for i in waiting if i.for_date == d.isoformat()]
                plan = [e for e in entries if e.day == d.isoformat()]
                weekly_planned = (cfg.enabled and WEEKDAYS[d.weekday()] == cfg.day
                                  and d >= today and d.isoformat() not in weekly_days)
                for i in posts + wait:
                    count(i.format, [p for p, st in publisher.destinations(i, available)
                                     if st not in ("off", "skipped")])
                for e in plan:
                    count(e.format, [p for p in publisher.planned_platforms(e.format)
                                     if p in available])
                if weekly_planned:
                    count(approvals.WEEKLY_VIDEO, ["youtube"] if "youtube" in available else [])
                # One list per day, in time order: (time, kind, post or plan entry).
                rows = [(to_local(i.scheduled_at, sched).strftime("%H:%M"), "post", i)
                        for i in posts]
                rows += [(i.preferred_slot or "", "post", i) for i in wait]
                rows += [(e.slot or "", "plan", e) for e in plan]
                if weekly_planned:
                    rows.append((cfg.publish_at, "weekly", None))
                rows.sort(key=lambda r: r[0] or "99")
                day_list.append({"date": d, "label": d.strftime("%a %d %b"),
                                 "today": d == today, "past": d < today, "rows": rows})
            return render(request, "calendar.html", days=day_list, range_days=days,
                          first=first, last=last, today=today, totals=totals,
                          prev=(first - timedelta(days=days)).isoformat(),
                          next=(first + timedelta(days=days)).isoformat(),
                          lead=first.weekday(), available=available,
                          weekly_at=cfg.publish_at)

    @app.get("/library", response_class=HTMLResponse)
    def library(request: Request, status: str = "", format: str = "", page: int = 1,
                who: str = Depends(user)):
        from sqlalchemy import func
        with session() as s:
            q = select(Item)
            if status:
                q = q.where(Item.status == status)
            if format:
                q = q.where(Item.format == format)
            total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
            pages = max(1, -(-total // LIBRARY_PAGE))
            page = min(max(page, 1), pages)
            items = list(s.scalars(q.order_by(Item.created_at.desc())
                                   .offset((page - 1) * LIBRARY_PAGE).limit(LIBRARY_PAGE)))
            counts = dict(s.execute(select(Item.status, func.count()).group_by(Item.status)).all())
            counts[""] = sum(counts.values())
            return render(request, "library.html", items=items, status_filter=status,
                          fmt=format, counts=counts, page=page, pages=pages, total=total)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, who: str = Depends(user)):
        s = get_settings()
        sched = load_schedule()
        ig = tokens.info("ig_access_token")
        from instapost.render import music
        from instapost.studio.images import chain_names
        with session() as db:
            links = dashboard.platform_links(db)
        return render(request, "settings.html", s=s, sched=sched,
                      steps=setup_status.checklist(),
                      platforms=sorted(publisher.configured_platforms()),
                      ig_token=ig, youtube_ok=youtube.is_configured(), links=links,
                      image_chain=chain_names(), music_tracks=len(music.tracks()),
                      spend_day=budget.spent("day"), spend_month=budget.spent("month"))

    @app.get("/costs", response_class=HTMLResponse)
    def costs_page(request: Request, days: int = 30, who: str = Depends(user)):
        from instapost.stats import costs
        days = days if days in (7, 30, 90) else 30
        st = get_settings()
        with session() as s:
            c = costs.summary(s, days)
            chart = charts.stacked_columns(
                c["day_list"], c["per_day"], "Spend per day", unit="spent",
                fmt=lambda v: f"${v:.2f}" if v >= 0.1 or v == 0 else f"${v:.3f}",
                slots=costs.GROUP_SLOTS)
            return render(request, "costs.html", c=c, day_chart=chart,
                          cap=st.monthly_budget_usd, daily_cap=st.daily_budget_usd,
                          models={"writer": st.writer_model, "qa": st.qa_model,
                                  "story": st.story_writer_model
                                  if st.story_writer_model != st.writer_model else ""})

    def _look_for(fmt: str, value: str) -> str | None:
        """A look id from a form ("reel:night", or a bare id) if it fits this format."""
        from instapost.render import templates as looks
        kind, _, look = value.rpartition(":")
        own = looks.kind_of(fmt)
        return look if (not kind or kind == own) and look in looks.BY_KIND[own] else None

    def _look(folder: Path) -> str:
        f = folder / "template.txt"
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""

    # Scene reels (Shiv and Parvati talking)
    def _scene(scene_id: str) -> dict:
        from instapost.studio import scenes
        try:
            return scenes.get(scene_id)
        except KeyError:
            raise HTTPException(404) from None

    @app.get("/scenes", response_class=HTMLResponse)
    def scenes_page(request: Request, who: str = Depends(user)):
        from instapost.studio import scenes, voice
        items = scenes.all_scenes()
        return render(request, "scenes.html", items=items[:60], voices=voice.available(),
                      gemini=bool(get_settings().gemini_api_key))

    @app.post("/scenes/new")
    def scene_new(request: Request, topic: str = Form(""), who: str = Depends(user),
                  _=Depends(csrf)):
        topic = topic.strip()[:200] or None
        jobs.enqueue("scene_write", f"scene script: {topic or 'any theme'}", topic=topic,
                     actor=f"web:{who}")
        return back(request, "Claude is writing a scene (about $0.02). It appears here in a "
                    "minute; you also get a Telegram message.", "/scenes")

    @app.post("/scenes/sample")
    def scene_sample(request: Request, who: str = Depends(user), _=Depends(csrf)):
        from instapost.studio import scenes
        entry = scenes.new(scenes.SAMPLE, topic="sample")
        return back(request, "Sample script added (free). Read it, then approve to make the "
                    "reel.", f"/scenes/{entry['id']}")

    @app.get("/scenes/{scene_id}", response_class=HTMLResponse)
    def scene_page(request: Request, scene_id: str, who: str = Depends(user)):
        from instapost.scriptures import load_verses
        from instapost.studio import scenes, voice
        entry = _scene(scene_id)
        script = scenes.script_of(entry)
        verses = [(v.id, v.label) for v in load_verses().values() if not v.rejected]
        return render(request, "scene.html", entry=entry, script=script,
                      problems=scenes.problems(script), characters=scenes.CHARACTERS,
                      verses=verses, words=scenes.words(script), voices=voice.available(),
                      max_words=scenes.SPOKEN_WORDS_MAX,
                      est=len(script.pictures) * 0.039 + 0.01)

    async def _scene_form(request: Request, entry: dict):
        from instapost.studio import scenes
        form = await request.form()
        old = scenes.script_of(entry)
        get = lambda k, d="": str(form.get(k, d)).strip()  # noqa: E731
        pictures = [scenes.ScenePicture(prompt=get(f"picture_{i}"))
                    for i in range(4) if get(f"picture_{i}")]
        lines = []
        for i in range(6):
            text = get(f"text_{i}")
            if not text:
                continue
            try:
                pic = int(get(f"pic_{i}", "1")) - 1
            except ValueError:
                pic = 0
            lines.append(scenes.SceneLine(speaker=get(f"speaker_{i}", "narrator"),
                                          text=text, picture=pic))
        tags = [t for t in get("hashtags").replace(",", " ").split() if t]
        return old.model_copy(update=dict(
            title=get("title") or old.title, source=get("source") or old.source,
            pictures=pictures, lines=lines, reference_id=get("reference_id") or None,
            question=get("question") or old.question, caption_hi=get("caption_hi"),
            hashtags=tags))

    @app.post("/scenes/{scene_id}")
    async def scene_save(request: Request, scene_id: str, who: str = Depends(user),
                         _=Depends(csrf)):
        from instapost.studio import scenes
        entry = _scene(scene_id)
        form = await request.form()
        script = await _scene_form(request, entry)
        try:
            scenes.update_script(scene_id, script)
        except ValueError as e:
            return back(request, str(e), f"/scenes/{scene_id}")
        if bad := scenes.problems(script):
            return back(request, "Saved, but fix this before approving: " + "; ".join(bad),
                        f"/scenes/{scene_id}")
        if form.get("approve") == "1":
            scenes.set_status(scene_id, "approved", error=None)
            jobs.enqueue("scene_make", f"scene reel {scene_id}", scene_id=scene_id,
                         actor=f"web:{who}")
            return back(request, "Approved. The pictures, voices and video are being made "
                        "(about 3 minutes); the reel then waits in your inbox.", "/scenes")
        return back(request, "Saved.", f"/scenes/{scene_id}")

    @app.post("/scenes/{scene_id}/reject")
    def scene_reject(request: Request, scene_id: str, who: str = Depends(user),
                     _=Depends(csrf)):
        from instapost.studio import scenes
        if _scene(scene_id)["status"] in ("draft", "approved"):
            scenes.set_status(scene_id, "rejected")
        return back(request, "Scene discarded.", "/scenes")

    # Reel looks (templates)
    @app.get("/templates", response_class=HTMLResponse)
    def templates_page(request: Request, who: str = Depends(user)):
        from instapost.learn import assets
        from instapost.render import templates as looks
        from instapost.studio import plates
        from instapost.studio.free_images import _load_usage
        with session() as s:
            rows = queries.post_rows(s, since=utcnow() - timedelta(days=assets.HISTORY_DAYS))
        scores = {r["option"]: r for r in assets.table("template", rows=rows)}
        usage = _load_usage()
        kinds = [(kind, looks.KIND_NAMES[kind], list(by_id.values()), set(looks.enabled(kind)))
                 for kind, by_id in looks.BY_KIND.items()]
        return render(request, "templates.html", kinds=kinds, scores=scores, usage=usage,
                      key=looks.key, plate_style=get_settings().reel_style == "plate",
                      ready_photos=len(plates.ready()))

    @app.get("/templates/sample/{kind}/{template_id}")
    def template_sample(kind: str, template_id: str, who: str = Depends(user)):
        from instapost.render import templates as looks
        if template_id not in looks.BY_KIND.get(kind, {}):
            raise HTTPException(404)
        return FileResponse(looks.sample(template_id, kind),
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/templates")
    async def templates_save(request: Request, who: str = Depends(user), _=Depends(csrf)):
        from instapost.render import templates as looks
        form = await request.form()
        chosen = [str(v).partition(":") for v in form.getlist("on")]
        saved = []
        with session() as s:
            for kind in looks.BY_KIND:
                ids = looks.set_enabled(s, [t for k, _, t in chosen if k == kind], f"web:{who}",
                                        kind)
                saved.append(f"{looks.KIND_NAMES[kind]}: "
                             + ", ".join(looks.label(t, kind) for t in ids))
        return back(request, "Saved. " + " · ".join(saved), "/templates")

    # Ink drawings for the page
    def _drawing(drawing_id: str):
        from instapost.studio import drawings
        for d in drawings.library(include_disabled=True):
            if d.id == drawing_id:
                return d
        raise HTTPException(404)

    @app.get("/drawings", response_class=HTMLResponse)
    def drawings_page(request: Request, who: str = Depends(user)):
        from instapost.studio import drawings
        from instapost.studio.free_images import _load_usage
        usage = _load_usage()
        off = drawings.disabled()
        items = drawings.library(include_disabled=True)
        rank = {"own": 0, "made": 1, "library": 2, "starter": 3}
        items.sort(key=lambda d: (d.id in off, rank.get(d.source, 4), d.id))
        st = get_settings()
        pack = drawings.pack_status()
        return render(request, "drawings.html", items=items, off=off,
                      last_used={d.id: usage.get(f"drawing:{d.id}") for d in items},
                      pack=pack, missing=sum(1 for _, _, have in pack if not have),
                      style=drawings.INK_STYLE, gemini=bool(st.gemini_api_key),
                      generate=st.drawings_generate)

    @app.get("/drawings/preview")
    def drawing_preview(id: str, who: str = Depends(user)):
        from instapost.studio import drawings
        return FileResponse(drawings.preview(_drawing(id)),
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/drawings/toggle")
    def drawing_toggle(request: Request, id: str = Form(...), on: str = Form(...),
                       who: str = Depends(user), _=Depends(csrf)):
        from instapost.studio import drawings
        _drawing(id)
        drawings.set_enabled(id, on == "1")
        return back(request, "Saved.")

    @app.post("/drawings/upload")
    async def drawing_upload(request: Request, who: str = Depends(user), _=Depends(csrf)):
        from instapost.studio import drawings
        form = await request.form()
        tags = [w.strip().lower() for w in str(form.get("tags") or "").replace(",", " ").split()]
        saved, problems = 0, []
        for f in form.getlist("files")[:20]:
            if not getattr(f, "filename", ""):
                continue
            d, why = drawings.add_upload(f.filename, await f.read(15 << 20), tags)
            if d:
                saved += 1
            else:
                problems.append(f"{f.filename}: {why}")
        msg = f"Added {saved} drawing(s)." + (" " + "; ".join(problems[:3]) if problems else "")
        return back(request, msg, "/drawings")

    @app.post("/drawings/pack")
    def drawing_pack(request: Request, limit: int = Form(10), who: str = Depends(user),
                     _=Depends(csrf)):
        limit = max(1, min(limit, 40))
        jobs.enqueue("drawings_pack", f"make {limit} drawings with Gemini", limit=limit,
                     actor=f"web:{who}")
        return back(request, f"Making up to {limit} drawings with Gemini (about "
                    f"${limit * 0.039:.2f}). They appear here as they are made.")

    # Book photos for reels
    def _plate_entry(plate_id: str) -> dict:
        from instapost.studio import plates
        entry = plates.load_index()["plates"].get(plate_id)
        if entry is None:
            raise HTTPException(404)
        return entry

    @app.get("/backgrounds", response_class=HTMLResponse)
    def backgrounds(request: Request, who: str = Depends(user)):
        from instapost.studio import plates
        from instapost.studio.free_images import _load_usage
        data = plates.sync_library()
        usage = _load_usage()
        rank = {"review": 0, "on": 1, "off": 2}
        rows = sorted(data["plates"].items(),
                      key=lambda kv: (rank.get(kv[1].get("status"), 3), kv[0]))
        counts = {s: sum(1 for _, e in rows if e.get("status") == s) for s in rank}
        from instapost.learn import assets
        scores = {r["option"]: r for r in assets.table("photo")}
        return render(request, "backgrounds.html", rows=rows, counts=counts, scores=scores,
                      last_used={k: usage.get(f"plate:{k}") for k, _ in rows},
                      style=get_settings().reel_style,
                      unsplash=bool(get_settings().unsplash_access_key),
                      last_fetch=data["meta"].get("last_fetch"))

    @app.get("/backgrounds/{plate_id}/{kind}.jpg")
    def background_image(plate_id: str, kind: str, who: str = Depends(user)):
        from instapost.studio import plates
        entry = _plate_entry(plate_id)
        if kind == "preview":
            path = plates.preview(plate_id)
        elif kind == "sample":
            path = plates.sample(plate_id)
        elif kind == "photo":
            path = Path(entry["file"])
        else:
            raise HTTPException(404)
        if path is None or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})

    @app.post("/backgrounds/{plate_id}/status")
    def background_status(request: Request, plate_id: str, status: str = Form(...),
                          who: str = Depends(user), _=Depends(csrf)):
        from instapost.studio import plates
        _plate_entry(plate_id)
        if status not in ("on", "off"):
            raise HTTPException(400)
        plates.set_status(plate_id, status)
        return back(request, "Saved." if status == "on" else "This photo will not be used.")

    @app.get("/backgrounds/{plate_id}/edit", response_class=HTMLResponse)
    def background_edit(request: Request, plate_id: str, who: str = Depends(user)):
        return render(request, "background_edit.html", plate_id=plate_id,
                      entry=_plate_entry(plate_id))

    @app.post("/backgrounds/{plate_id}/corners")
    def background_corners(request: Request, plate_id: str, quad: str = Form(...),
                           who: str = Depends(user), _=Depends(csrf)):
        import json as _json

        from instapost.studio import plates
        entry = _plate_entry(plate_id)
        try:
            pts = [[float(x), float(y)] for x, y in _json.loads(quad)]
        except (ValueError, TypeError):
            raise HTTPException(400, "four corners are needed") from None
        w, h = entry.get("size", [10 ** 5, 10 ** 5])
        if len(pts) != 4 or not all(0 <= x <= w and 0 <= y <= h for x, y in pts):
            raise HTTPException(400, "four corners inside the photo are needed")
        plates.set_status(plate_id, "on", pts)
        return back(request, "Corners saved; the photo is in use.", "/backgrounds")

    @app.post("/backgrounds/fetch")
    def backgrounds_fetch(request: Request, who: str = Depends(user), _=Depends(csrf)):
        jobs.enqueue("fetch_plates", "find book photos on Unsplash", actor=f"web:{who}")
        return back(request, "Searching Unsplash for book photos. New ones appear here in a "
                    "minute.")

    @app.post("/backgrounds/upload")
    async def backgrounds_upload(request: Request, who: str = Depends(user), _=Depends(csrf)):
        from instapost.render import music
        from instapost.studio import plates
        form = await request.form()
        target = get_settings().state_dir / "plates" / "own"
        target.mkdir(parents=True, exist_ok=True)
        saved = 0
        for f in form.getlist("files")[:20]:
            name = music.safe_name(getattr(f, "filename", "") or "")
            if Path(name).suffix.lower() not in plates.IMAGE_EXTENSIONS:
                continue
            data = await f.read(25 << 20)
            try:
                from io import BytesIO

                from PIL import Image
                Image.open(BytesIO(data)).verify()  # a real picture, not something else
            except Exception:  # noqa: BLE001
                continue
            (target / name).write_bytes(data)
            saved += 1
        plates.sync_library()
        return back(request, f"Added {saved} photo(s).", "/backgrounds")

    # Music library
    @app.get("/music", response_class=HTMLResponse)
    def music_page(request: Request, pillar: str = "", who: str = Depends(user)):
        from instapost.render import music
        with session() as s:
            used = list(s.scalars(select(Item).where(Item.music.is_not(None))
                                  .order_by(Item.created_at.desc()).limit(500)))
            by_track: dict[str, list[Item]] = {}
            for i in used:
                by_track.setdefault(i.music, []).append(i)
            rows = []
            for p in music.tracks():
                name = music.rel(p)
                posts = by_track.get(name, [])
                stamp = music.last_used(p)
                rows.append({
                    "rel": name, "name": p.name, "folder": name.rpartition("/")[0],
                    "pillars": [x.value for x in music.pillars_of(p)],
                    "duration": music.duration(p), "offset": music.start_offset(p),
                    "uses": len(posts), "last_post": posts[0] if posts else None,
                    "last_used_at": datetime.fromisoformat(stamp).replace(tzinfo=None)
                    if stamp else None})
            coverage = {p.value: sum(1 for r in rows if p.value in r["pillars"]) for p in Pillar}
            stamped = [r for r in rows if r["last_used_at"]]
            newest = next((r for r in rows if used and r["rel"] == used[0].music), None)
            last = newest or (max(stamped, key=lambda r: r["last_used_at"]) if stamped else None)
            folders = sorted({r["folder"] for r in rows if r["folder"]}
                             | {"shiv", "krishna", "kabir", "jeevan", "life"})
            hint = {f: ", ".join(PILLAR_LABEL[p.value] for p, words in
                                 music.FOLDER_PILLARS.items() if f.lower() in words)
                    for f in folders}
            mode = get_settings().music_mode
            return render(request, "music.html", rows=rows, pillar=pillar,
                          shown=[r for r in rows if not pillar or pillar in r["pillars"]],
                          coverage=coverage, last=last, recent=used[:8], folders=folders,
                          folder_hint=hint, pillar_label=PILLAR_LABEL,
                          trending=music.trending_list(), max_mb=MAX_TRACK_MB,
                          mode_note="in use for reels" if mode == "library"
                          else f"MUSIC_MODE={mode}: not used")

    @app.get("/music/file/{name:path}")
    def music_file(name: str, who: str = Depends(user)):
        from instapost.render import music
        path = music.resolve(name)
        if path is None:
            raise HTTPException(404)
        return FileResponse(path)

    @app.post("/music/upload")
    async def music_upload(request: Request, who: str = Depends(user), _=Depends(csrf)):
        from instapost.render import music
        form = await request.form()
        raw = str(form.get("new_folder") or form.get("folder") or "").strip().lower()
        folder = music.safe_name(raw) if raw else ""
        target = music.library_dir() / folder
        saved, skipped = [], []
        for f in form.getlist("files")[:30]:
            if not getattr(f, "filename", ""):
                continue
            name = music.safe_name(f.filename)
            if Path(name).suffix.lower() not in music.AUDIO_EXTENSIONS:
                skipped.append(f"{name} (not audio)")
                continue
            target.mkdir(parents=True, exist_ok=True)
            dest = target / name
            n = 2
            while dest.exists():
                dest = target / f"{Path(name).stem}-{n}{Path(name).suffix}"
                n += 1
            size, too_big = 0, False
            with dest.open("wb") as out:
                while chunk := await f.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_TRACK_MB << 20:
                        too_big = True
                        break
                    out.write(chunk)
            if too_big:
                dest.unlink()
                skipped.append(f"{name} (over {MAX_TRACK_MB} MB)")
                continue
            saved.append(music.rel(dest))
        with session() as s:
            if saved:
                audit(s, f"web:{who}", "music_added", tracks=saved)
        msg = (f"Added {len(saved)} track(s) to {folder or 'data/music'}." if saved
               else "No track added.")
        return back(request, msg + (" Skipped: " + ", ".join(skipped) if skipped else ""),
                    "/music")

    @app.post("/music/remove")
    def music_remove(request: Request, name: str = Form(...), who: str = Depends(user),
                     _=Depends(csrf)):
        from instapost.render import music
        path = music.resolve(name)
        if path is None:
            raise HTTPException(404)
        dest = music.library_dir() / ".removed" / music.rel(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        path.replace(dest)
        with session() as s:
            audit(s, f"web:{who}", "music_removed", track=name)
        return back(request, f"Removed {name} (kept in data/music/.removed).", "/music")

    # Analytics
    @app.get("/stats")
    def stats_redirect():
        return RedirectResponse("/analytics", status_code=301)

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics(request: Request, days: int = 30, platform: str = "", sort: str = "date",
                  who: str = Depends(user)):
        days = days if days in (7, 30, 90) else 30
        platform = platform if platform in queries.PLATFORMS else ""
        with session() as s:
            ov = dashboard.overview(s, days=days)
            rows = ov["rows"]
            if platform:
                rows = [r for r in rows if platform in r.per_platform or platform in r.permalinks]

            def metric(r, key):
                m = r.per_platform.get(platform, {}) if platform else r.combined
                if key == "score":
                    sc = scoring.score(m) if platform else m.get("score")
                    return sc if sc is not None else -1
                if key == "engagement":
                    return (m.get("shares") or 0) + (m.get("saves") or 0)
                return m.get(key) or 0

            keyfn = {"score": lambda r: metric(r, "score"), "views": lambda r: metric(r, "views"),
                     "engagement": lambda r: metric(r, "engagement")
                     }.get(sort, lambda r: r.published_at)
            rows = sorted(rows, key=keyfn, reverse=True)
            sched = load_schedule()
            slot_hours = sorted({int(t.split(":")[0]) for times in sched.slots.values()
                                 for t in times})
            day_list, per_platform = dashboard.daily_views(
                ov["rows"], days, (platform,) if platform else queries.PLATFORMS)
            followers = queries.followers_series(s, days=days)
            if platform:
                followers = {platform: followers.get(platform, [])}
            scope = ov["platforms"][platform] if platform else ov["total"]
            return render(
                request, "analytics.html", ov=ov, scope=scope, rows=rows, days=days,
                platform=platform, sort=sort, score_of=scoring.score,
                scope_label=PLATFORM_LABEL.get(platform, "All platforms"),
                views_chart=charts.stacked_columns(day_list, per_platform, "Views per day"),
                followers_chart=charts.line_chart(followers, "Followers"),
                pillar_chart=charts.bar_chart(
                    [(PILLAR_LABEL.get(p, p), v, n) for p, v, n in queries.by_pillar(rows)],
                    "Average score by pillar"),
                format_chart=charts.bar_chart(
                    [(FORMAT_LABEL.get(f, f), v, n) for f, v, n in _by_format(rows)],
                    "Average score by format"),
                heat=charts.heatmap(queries.slot_heatmap(rows), slot_hours,
                                    "Average score by weekday and posting hour"))

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request, who: str = Depends(user)):
        with session() as s:
            return render(request, "reports.html", reports=report.latest(s))

    # Learning & plan (Phase 4)
    @app.get("/plan", response_class=HTMLResponse)
    def plan_page(request: Request, week: str = "", who: str = Depends(user)):
        today = load_schedule().local_now().date()
        this_week = planner.week_start_of(today)
        ws = date.fromisoformat(week) if week else this_week + timedelta(days=7)
        ws = planner.week_start_of(ws)
        with session() as s:
            entries = planner.week_entries(s, ws)
            days: dict[str, list] = {}
            for e in entries:
                days.setdefault(e.day, []).append(e)
            return render(request, "plan.html", days=days, week=ws,
                          prev_week=ws - timedelta(days=7), next_week=ws + timedelta(days=7),
                          pillars=[p.value for p in Pillar],
                          hooks=[h.value for h in HookStyle],
                          scenes=load_brand().scene_presets, sched=load_schedule(),
                          lengths=LENGTHS, festivals=planner.festivals())

    @app.post("/plan/{entry_id}")
    def plan_edit(request: Request, entry_id: int, pillar: str = Form(...),
                  hook_style: str = Form(...), slot: str = Form(...), scene: int = Form(0),
                  length: str = Form(""), topic: str = Form(""), action: str = Form("save"),
                  who: str = Depends(user), _=Depends(csrf)):
        with session() as s:
            e = s.get(PlanEntry, entry_id)
            if e is None:
                raise HTTPException(404)
            if e.status != "planned":
                return back(request, "This entry was already used; it can't be changed.")
            if action == "skip":
                e.status, e.edited_by = "skipped", f"web:{who}"
                audit(s, f"web:{who}", "plan_skip", entry=entry_id)
                return back(request, "Skipped. Nothing will be made for this entry.")
            Pillar(pillar), HookStyle(hook_style)  # validate
            if slot not in load_schedule().slots.get(Format(e.format), []):
                raise HTTPException(400, "unknown slot")
            e.pillar, e.hook_style, e.slot = pillar, hook_style, slot
            e.scene = scene % max(len(load_brand().scene_presets), 1)
            e.length = length if length in LENGTHS else e.length
            e.topic = topic.strip() or None
            e.edited_by = f"web:{who}"
            audit(s, f"web:{who}", "plan_edit", entry=entry_id)
        return back(request, "Saved. Rebuilding the plan will keep your change.")

    @app.post("/plan-rebuild")
    def plan_rebuild(request: Request, week: str = Form(...), who: str = Depends(user),
                     _=Depends(csrf)):
        date.fromisoformat(week)
        jobs.enqueue("build_plan", f"plan for week of {week}", week_start=week,
                     actor=f"web:{who}")
        return back(request, "Rebuilding the plan (your edited entries stay).")

    @app.post("/weekly-review")
    def weekly_review(request: Request, week: str = Form(...), who: str = Depends(user),
                      _=Depends(csrf)):
        date.fromisoformat(week)
        jobs.enqueue("weekly_review", f"weekly review + plan for {week}", week_start=week,
                     actor=f"web:{who}")
        return back(request, "Queued: Claude reviews last week, then the plan is rebuilt "
                    "(about $0.10).")

    @app.get("/learning", response_class=HTMLResponse)
    def learning_page(request: Request, who: str = Depends(user)):
        with session() as s:
            rows = queries.post_rows(s, since=utcnow() - timedelta(days=planner.HISTORY_DAYS))
            learner = Learner(rows)
            beliefs = learner.beliefs()
            presets = load_brand().scene_presets
            sections = []
            for fmt in Format:
                dims = []
                for dim in dimensions(fmt):
                    arms = [dict(b, label=_option_label(dim, b["option"], presets))
                            for b in beliefs if b["format"] == fmt.value and b["dimension"] == dim]
                    dims.append((dim, learner.confidence(
                        fmt, dim, lambda o, d=dim: _option_label(d, o, presets)),
                                 charts.interval_chart(arms, f"{fmt.value} {dim}")))
                sections.append((fmt.value, dims))
            from instapost.learn import assets
            assets_charts = []
            for kind, title in (("template", "Looks"), ("photo", "Book photos"),
                                ("drawing", "Drawings")):
                rows_k = assets.table(kind, rows=rows)
                assets_charts.append((title, len(rows_k), charts.interval_chart(
                    [dict(r, label=r["option"].split(":")[-1].split("/")[-1][:18])
                     for r in rows_k[:12]], title) if rows_k else None))
            return render(request, "learning.html", sections=sections,
                          observations=learner.observations, reviews=review.latest(s),
                          explore=int(planner.EXPLORE_RATE * 100),
                          assets_charts=assets_charts)

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
