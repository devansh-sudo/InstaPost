"""Background worker: the only process that generates content and publishes.

Every 30 seconds it:
1. records a heartbeat (shown on the dashboard);
2. publishes approved items whose time has come (unless the kill switch is on);
3. at `generate_at` (schedule.yaml) creates the next day's content and sends it to Telegram;
4. once a day refreshes the Instagram token when it is more than 7 days old;
5. collects post and account stats (throttled to every 10 minutes);
6. at `report_at` sends the daily report for the previous day;
7. on `plan_day` at `plan_at`: weekly Claude review, then next week's plan;
8. on Sunday afternoon (schedule.yaml weekly_video): the week's posts as one YouTube video.
"""

import logging
import time
from datetime import UTC, date, datetime, timedelta

from instapost import approvals, jobs, producer, quick
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, audit, get_kv, session, set_kv, utcnow
from instapost.jobs import track
from instapost.learn import planner, review
from instapost.models import Format, Pillar
from instapost.publish import instagram, publisher, tokens
from instapost.schedule import WEEKDAYS, load_schedule
from instapost.stats import collect, report

log = logging.getLogger("instapost.worker")
TICK_SECONDS = 30


class Worker:
    def __init__(self):
        self.bot = None
        if get_settings().telegram_bot_token:
            from instapost.telegram_bot import Bot
            self.bot = Bot()

    def notify(self, text: str) -> None:
        log.info(text)
        if self.bot:
            try:
                self.bot.send(text)
            except Exception as e:  # noqa: BLE001
                log.warning("telegram send failed: %s", tokens.redact(str(e)))

    def send_item(self, item_id: str) -> None:
        if self.bot:
            try:
                self.bot.send_item(item_id)
            except Exception as e:  # noqa: BLE001
                log.warning("telegram send_item failed: %s", tokens.redact(str(e)))

    # --- duties ----------------------------------------------------------------------------

    def publish_due(self) -> None:
        with session() as s:
            if approvals.kill_switch_on(s):
                return
            due_ids = [i.id for i in approvals.due(s)]
        for item_id in due_ids:
            with track("publish", item_id) as job, session() as s:
                item = s.get(Item, item_id)
                if item.status != ItemStatus.APPROVED or approvals.kill_switch_on(s):
                    continue
                if "instagram" in (item.manual_platforms or []) \
                        and not quick.app_package_sent(s, item_id):
                    # You post this reel in the Instagram app (trending sound): send it now.
                    audit(s, "agent", "app_package_sent", item_id)
                    package = (item.id, item.folder, item.caption, publisher.targets_for(item),
                               quick.sound_suggestions(s, item))
                    s.commit()
                    if self.bot:
                        self.bot._send_for_app(*package)
                if not publisher.targets_for(item):
                    item.scheduled_at = None  # nothing automatic left: waits for /posted
                    continue
                job.step("publishing")
                try:
                    publisher.publish_item(s, item)
                except Exception as e:  # noqa: BLE001 - e.g. media hosting misconfigured
                    item.status = ItemStatus.FAILED
                    audit(s, "agent", "publish_failed", item_id, error=tokens.redact(str(e)))
                    self.notify(f"❗ Publishing failed for {item_id}: "
                                f"{tokens.redact(str(e))[:300]}")
                    continue
                links = [f"{p.platform}: {p.permalink or p.remote_id}" for p in item.publications
                         if p.status == "published"]
                errors = [f"{p.platform}: {p.error}" for p in item.publications if p.error]
                status = item.status
            if status == ItemStatus.PUBLISHED:
                msg = "🚀 Published: " + item_id + "\n" + "\n".join(links)
                if "youtube" in "".join(links) and get_settings().youtube_privacy == "private":
                    msg += "\n▶️ YouTube: make it public in YouTube Studio (API uploads are " \
                           "private until the API audit is approved)."
                self.notify(msg)
            elif status in (ItemStatus.FAILED, ItemStatus.PARTIAL):
                self.notify(f"❗ Publishing {status} for {item_id}\n" + "\n".join(errors))

    def maybe_generate(self) -> None:
        sched = load_schedule()
        now = sched.local_now()
        hh, mm = map(int, sched.generate_at.split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return
        tomorrow = now.date() + timedelta(days=1)
        created = producer.generate_day(tomorrow, self.notify, self.send_item)
        if created:
            self.notify(f"📝 {len(created)} items for {tomorrow} are ready. "
                        "Approve them above, or /approve_tomorrow.")

    def maybe_weekly_video(self) -> None:
        """Sunday afternoon: the week's posts as one long YouTube video, sent for approval."""
        from instapost import weekly

        with session() as s:
            if not weekly.due(s):
                return
            weekly.mark_tried(s, load_schedule().local_now().date())
        self.make_weekly_video(None, "agent")

    def make_weekly_video(self, end_day: date | None, actor: str) -> None:
        from instapost import weekly

        try:
            item_id = producer.make_weekly(actor, end_day)
        except weekly.NotEnoughPosts as e:
            self.notify(f"🎬 No weekly YouTube video this time: {e}.")
            return
        except Exception as e:  # noqa: BLE001
            self.notify(f"⚠️ Weekly video failed: {tokens.redact(str(e))[:300]}")
            return
        self.send_item(item_id)

    def maybe_refresh_token(self) -> None:
        if not tokens.get("ig_access_token"):
            return
        info = tokens.info("ig_access_token")
        last = info.get("refreshed_at")
        if last and datetime.fromisoformat(last) > datetime.now(UTC) - timedelta(days=7):
            return
        with session() as s:
            if get_kv(s, "ig_refresh_tried", "") == utcnow().date().isoformat():
                return
            set_kv(s, "ig_refresh_tried", utcnow().date().isoformat())
        try:
            left = instagram.refresh_token()
            with session() as s:
                audit(s, "agent", "ig_token_refreshed", days_left=left // 86400)
        except Exception as e:  # noqa: BLE001
            self.notify(f"⚠️ Instagram token refresh failed: {tokens.redact(str(e))[:300]}")

    def run_requests(self) -> None:
        """Requests queued from the dashboard: make one item, regenerate, or generate a day."""
        while job := jobs.take_queued():
            p = job.payload
            try:
                if job.kind == "regenerate":
                    self.send_item(producer.regenerate(p["item_id"], p.get("actor", "web")))
                elif job.kind == "make":
                    item_id = producer.make_one(Format(p["format"]), Pillar(p["pillar"]),
                                                date.fromisoformat(p["for_date"]),
                                                p.get("topic"), p.get("actor", "web"),
                                                template=p.get("template"))
                    self.send_item(item_id)
                elif job.kind == "scene_write":
                    entry = producer.write_scene(p.get("topic"), p.get("actor", "web"))
                    self.notify(f"🎭 New scene script to read: {entry['script']['title']}\n"
                                "Dashboard → Scenes: edit it if you like, then approve to make "
                                "the reel (about $0.15).")
                elif job.kind == "scene_make":
                    self.send_item(producer.make_scene(p["scene_id"], p.get("actor", "web")))
                elif job.kind == "retemplate":
                    self.send_item(producer.retemplate(p["item_id"], p.get("template"),
                                                       p.get("actor", "web")))
                elif job.kind == "build_plan":
                    with session() as s:
                        planner.build_week(s, date.fromisoformat(p["week_start"]),
                                           actor=p.get("actor", "web"))
                elif job.kind == "weekly_review":
                    ws = date.fromisoformat(p["week_start"])
                    self.weekly(ws - timedelta(days=7), ws, actor=p.get("actor", "web"))
                elif job.kind == "drawings_pack":
                    from instapost.studio import drawings
                    made, problems = drawings.make_pack(int(p.get("limit", 10)))
                    self.notify(f"🖋 Drawings: {made} new in the library."
                                + (f"\n{problems[0]}" if problems else ""))
                elif job.kind == "fetch_plates":
                    from instapost.studio import plates
                    added = plates.fetch_unsplash(force=True)
                    self.notify(f"📚 Book photos: {added} new from Unsplash. Check them on the "
                                "dashboard's Backgrounds page.")
                elif job.kind == "weekly_video":
                    self.make_weekly_video(date.fromisoformat(p["end_day"]) if p.get("end_day")
                                           else None, p.get("actor", "web"))
                elif job.kind == "generate_day":
                    producer.generate_day(date.fromisoformat(p["day"]), self.notify,
                                          self.send_item, force=True)
                jobs.finish(job.id)
            except Exception as e:  # noqa: BLE001
                jobs.finish(job.id, tokens.redact(str(e))[:1000])
                self.notify(f"⚠️ {job.kind} failed: {tokens.redact(str(e))[:300]}")

    def collect_stats(self) -> None:
        collect.run(self.notify)

    def maybe_report(self) -> None:
        sched = load_schedule()
        now = sched.local_now()
        hh, mm = map(int, sched.report_at.split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return
        day = now.date() - timedelta(days=1)
        with session() as s:
            if get_kv(s, "report_sent", "") == day.isoformat():
                return
            set_kv(s, "report_sent", day.isoformat())
        self.notify(report.create(day).text)

    def maybe_plan_week(self) -> None:
        """Weekly: review this week with Claude, then build next week's plan."""
        sched = load_schedule()
        now = sched.local_now()
        hh, mm = map(int, sched.plan_at.split(":"))
        if WEEKDAYS[now.weekday()] != sched.plan_day or (now.hour, now.minute) < (hh, mm):
            return
        this_week = planner.week_start_of(now.date())
        next_week = this_week + timedelta(days=7)
        with session() as s:
            if get_kv(s, "weekly_done", "") == next_week.isoformat():
                return
            set_kv(s, "weekly_done", next_week.isoformat())
        self.weekly(this_week, next_week)

    def weekly(self, review_week, plan_week, actor: str = "agent") -> None:
        if not get_settings().demo_mode:
            try:
                with session() as s:
                    text = review.create(s, review_week).text
                self.notify(text)
            except Exception as e:  # noqa: BLE001 - the plan is still built without new topics
                self.notify(f"⚠️ Weekly review skipped: {tokens.redact(str(e))[:300]}")
        with session() as s:
            entries = planner.build_week(s, plan_week, actor=actor)
            experiments = sum(1 for e in entries if e.experiment)
        self.notify(f"🗓 Plan for the week of {plan_week.strftime('%d %b')} is ready: "
                    f"{len(entries)} pieces, {experiments} experiments. Edit it on the "
                    "dashboard Plan page, or /plan to see it.")

    def tick(self) -> None:
        with session() as s:
            set_kv(s, "worker_heartbeat", utcnow().isoformat())
        for duty in (self.publish_due, self.run_requests, self.maybe_generate,
                     self.maybe_refresh_token, self.collect_stats, self.maybe_report,
                     self.maybe_plan_week, self.maybe_weekly_video):
            try:
                duty()
            except Exception as e:  # noqa: BLE001 - keep the worker alive, but tell the admin
                log.exception("duty %s failed", duty.__name__)
                self.notify(f"⚠️ {duty.__name__} failed: {tokens.redact(str(e))[:300]}")

    def recover(self) -> None:
        """Items left in "publishing" by a crash go back to the queue; their per-platform
        records make sure nothing that already succeeded is posted twice."""
        with session() as s:
            for item in s.query(Item).filter(Item.status == ItemStatus.PUBLISHING):
                item.status = ItemStatus.APPROVED
                audit(s, "agent", "recovered_after_restart", item.id)

    def run_forever(self) -> None:
        self.recover()
        if self.bot:
            self.bot.start_background()
            self.notify("👋 InstaPost worker started.")
        while True:
            self.tick()
            time.sleep(TICK_SECONDS)
