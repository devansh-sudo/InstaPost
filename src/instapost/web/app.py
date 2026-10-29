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
from instapost.schedule import load_schedule, to_local, to_utc_naive
from instapost.stats import dashboard, queries, report
from instapost.stats import score as scoring
from instapost.stats.labels import PLATFORM_LABEL, compact
from instapost.web import auth, charts

HERE = Path(__file__).parent
FILE_NAME = re.compile(r"^(reel\.mp4|cover\.jpg|story\.jpg|slide_\d{2}\.jpg)$")
MAX_TRACK_MB = 25
FORMAT_LABEL = {"reel_book_pov": "Reel", "carousel_shlok": "Carousel", "story_line": "Story"}
PILLAR_LABEL = {"jeevan_sutra": "Jeevan sutra", "shiv_dhyan": "Shiv dhyan", "gita": "Gita",
                "kabir": "Kabir", "life_lesson": "Life lesson"}
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
        # same-origin: our own pages see where a form came from (to return there after an
        # action), other sites never see dashboard URLs.
        response.headers["Referrer-Policy"] = "same-origin"
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
        "shell": shell, "compact": compact, "labels": PLATFORM_LABEL, **ctx})


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
        for name in ("cover.jpg", "story.jpg", "slide_01.jpg"):
            if folder and (folder / name).exists():
                return FileResponse(folder / name,
                                    headers={"Cache-Control": "private, max-age=600"})
        # A neutral placeholder (blocked items have no image).
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 4">'
               '<rect width="3" height="4" fill="#8f877e" fill-opacity="0.22"/></svg>')
        return Response(svg, media_type="image/svg+xml")

    @app.get("/create", response_class=HTMLResponse)
    def create_page(request: Request, who: str = Depends(user)):
        today = load_schedule().local_now().date()
        return render(request, "create.html", formats=list(Format), pillars=list(Pillar),
                      pillar_labels=PILLAR_LABEL, today=today, tomorrow=today + timedelta(days=1),
                      sched=load_schedule())

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
                          cost=costs.item_cost(item_id))

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request, who: str = Depends(user)):
        sched = load_schedule()
        today = sched.local_now().date()
        with session() as s:
            items = approvals.upcoming(s, days=14)
            days = []
            for n in range(14):
                d = today + timedelta(days=n)
                days.append({
                    "label": d.strftime("%A %d %b"), "today": n == 0,
                    "posts": [i for i in items if i.scheduled_at
                              and to_local(i.scheduled_at, sched).date() == d],
                    "plan": planner.entries_for_day(s, d)})
            return render(request, "calendar.html", days=days)

    @app.get("/library", response_class=HTMLResponse)
    def library(request: Request, status: str = "", format: str = "",
                who: str = Depends(user)):
        from sqlalchemy import func
        with session() as s:
            q = select(Item).order_by(Item.created_at.desc()).limit(200)
            if status:
                q = q.where(Item.status == status)
            if format:
                q = q.where(Item.format == format)
            counts = dict(s.execute(select(Item.status, func.count()).group_by(Item.status)).all())
            counts[""] = sum(counts.values())
            return render(request, "library.html", items=list(s.scalars(q)),
                          status_filter=status, fmt=format, counts=counts)

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
            return render(request, "learning.html", sections=sections,
                          observations=learner.observations, reviews=review.latest(s),
                          explore=int(planner.EXPLORE_RATE * 100))

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
