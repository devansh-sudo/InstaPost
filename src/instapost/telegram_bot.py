"""Telegram bot for approvals and alerts. It only ever responds to the admin chat ID.

Buttons on each new item: Approve / Reject / Regenerate, plus "approve next 3" and
"approve all of <day>". Commands: /help lists them.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import httpx

from instapost import approvals, budget, producer, quick
from instapost.config import get_settings
from instapost.db import Item, ItemStatus, get_kv, session, set_kv
from instapost.publish import publisher
from instapost.publish.instagram import PublishError
from instapost.publish.tokens import redact
from instapost.schedule import load_schedule

HELP = """Commands
/new reel gita गुस्सा – create one now (format, pillar and topic optional); arrives here
/postnow <id> – publish an approved or waiting item right away
/posted <id> <link> – link a reel you posted in the Instagram app
/queue – items waiting for approval
/approve_next 3 – approve the next N items
/approve_tomorrow – approve everything planned for tomorrow
/upcoming – what is scheduled
/status – kill switch, autonomy, budget
/autonomy L0|L1|L2 – how much is approved automatically
/kill – stop all publishing now
/resume – allow publishing again
/budget – spend against the caps
/report – yesterday's report now
/plan – the plan for the next 7 days
/setup – what is still missing in the setup"""

FORMAT_LABEL = {"reel_book_pov": "🎬 Reel", "carousel_shlok": "🖼 Carousel",
                "story_line": "📱 Story"}


class TelegramError(RuntimeError):
    pass


class Bot:
    def __init__(self, client: httpx.Client | None = None):
        s = get_settings()
        if not s.telegram_bot_token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self.base = f"https://api.telegram.org/bot{s.telegram_bot_token}"
        self.admin = s.telegram_admin_chat_id
        self.http = client or httpx.Client(timeout=70)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tg-generate")
        self._stop = threading.Event()

    # --- Bot API ---------------------------------------------------------------------------

    def call(self, method: str, files: dict | None = None, **params) -> dict:
        data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else str(v))
                for k, v in params.items() if v is not None}
        try:
            r = self.http.post(f"{self.base}/{method}", data=data, files=files)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise TelegramError(redact(f"{method}: {e}")) from e
        if not body.get("ok"):
            raise TelegramError(redact(f"{method}: {body.get('description')}"))
        return body["result"]

    def send(self, text: str, **kw) -> dict | None:
        if not self.admin:
            return None
        return self.call("sendMessage", chat_id=self.admin, text=text[:4000], **kw)

    # --- outgoing: items -------------------------------------------------------------------

    def item_text(self, item: Item) -> str:
        folder = Path(item.folder)
        fact = json.loads((folder / "fact_check.json").read_text(encoding="utf-8"))
        warn = ""
        if item.fact_check == "needs_human_review":
            warn = "\n⚠️ Please verify: " + "; ".join(fact.get("issues", []))[:300]
        elif item.fact_check == "fail":
            warn = "\n⛔ Fact check failed: " + "; ".join(fact.get("issues", []))[:300]
        status = {"approved": f"✅ Approved, posts {approvals.local_time_label(item)}",
                  "blocked": f"⛔ Blocked by QA: {item.qa_summary[:300]}"}.get(item.status, "")
        return (f"{FORMAT_LABEL.get(item.format, item.format)} · {item.pillar} · for "
                f"{item.for_date}\n\n{item.hook}\n\n{item.caption[:500]}{warn}\n\n{status}"
                f"\nid: {item.id}")

    def keyboard(self, item: Item) -> dict | None:
        if item.status != ItemStatus.NEEDS_REVIEW:
            return None
        return {"inline_keyboard": [
            [{"text": "✅ Approve", "callback_data": f"ap:{item.id}"},
             {"text": "🚀 Post now", "callback_data": f"pn:{item.id}"}],
            *([[{"text": "📲 Trending song (I post it on Instagram)",
                 "callback_data": f"ts:{item.id}"}]] if item.format == "reel_book_pov" else []),
            [{"text": "❌ Reject", "callback_data": f"rj:{item.id}"},
             {"text": "🔁 Regenerate", "callback_data": f"rg:{item.id}"}],
            [{"text": "⏭ Approve next 3", "callback_data": "an:3"},
             {"text": f"📅 Approve all {item.for_date}", "callback_data": f"ad:{item.for_date}"}],
        ]}

    def send_item(self, item_id: str) -> None:
        if not self.admin:
            return
        with session() as s:
            item = s.get(Item, item_id)
            folder = Path(item.folder)
            text, kb = self.item_text(item), self.keyboard(item)
            caption = text[:1000]
            if (folder / "reel.mp4").exists():
                with open(folder / "reel.mp4", "rb") as f:
                    msg = self.call("sendVideo", files={"video": f}, chat_id=self.admin,
                                    caption=caption, reply_markup=kb, supports_streaming="true")
            elif (folder / "story.jpg").exists():
                with open(folder / "story.jpg", "rb") as f:
                    msg = self.call("sendPhoto", files={"photo": f}, chat_id=self.admin,
                                    caption=caption, reply_markup=kb)
            elif slides := sorted(folder.glob("slide_*.jpg")):
                handles = {f"s{i}": open(p, "rb") for i, p in enumerate(slides[:10])}
                try:
                    media = [{"type": "photo", "media": f"attach://{k}"} for k in handles]
                    self.call("sendMediaGroup", files=handles, chat_id=self.admin, media=media)
                finally:
                    for h in handles.values():
                        h.close()
                msg = self.send(text, reply_markup=kb)
            else:
                msg = self.send(text, reply_markup=kb)
            if msg:
                item.telegram_message_id = msg["message_id"]

    # --- incoming --------------------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            cq = update["callback_query"]
            if cq["from"]["id"] != self.admin:
                return
            reply = self.on_button(cq["data"])
            self.call("answerCallbackQuery", callback_query_id=cq["id"], text=reply[:190])
            msg = cq.get("message", {})
            if msg:
                try:
                    self.call("editMessageReplyMarkup", chat_id=msg["chat"]["id"],
                              message_id=msg["message_id"], reply_markup={"inline_keyboard": []})
                except TelegramError:
                    pass
                self.send(reply)
            return
        msg = update.get("message") or {}
        sender = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if not self.admin:
            # Setup help: tells you the number to put in TELEGRAM_ADMIN_CHAT_ID.
            self.call("sendMessage", chat_id=msg["chat"]["id"],
                      text=f"Your chat ID is {sender}. Put it in TELEGRAM_ADMIN_CHAT_ID.")
            return
        if sender != self.admin:
            return
        self.send(self.on_command(text))

    def on_button(self, data: str) -> str:
        action, _, arg = data.partition(":")
        actor = "telegram"
        try:
            with session() as s:
                if action == "ts":
                    item = s.get(Item, arg)
                    if item is None:
                        return "That item no longer exists."
                    others = quick.trending(s, item, actor)
                    sounds = quick.sound_suggestions(s, item)
                    item_id, folder, caption = item.id, item.folder, item.caption
                    s.commit()
                    self.pool.submit(self._send_for_app, item_id, folder, caption, others,
                                     sounds)
                    return "📲 Sending you the video for the Instagram app…"
                if action in ("ap", "rj", "pn"):
                    item = s.get(Item, arg)
                    if item is None:
                        return "That item no longer exists."
                    if action == "pn":
                        targets = publisher.targets_for(item)
                        if not targets:
                            return ("Not posted: no platform for this format is connected "
                                    "(see /setup).")
                        quick.post_now(s, item, actor)
                        return (f"🚀 Posting now to {', '.join(targets)}. "
                                "The links arrive here in a few minutes.")
                    if action == "ap":
                        approvals.approve(s, item, actor)
                        return f"✅ Approved. Posts {approvals.local_time_label(item)}."
                    approvals.reject(s, item, actor)
                    return "❌ Rejected."
                if action == "an":
                    items = approvals.approve_next(s, int(arg), actor)
                    return self._approved_list(items)
                if action == "ad":
                    items = approvals.approve_day(s, date.fromisoformat(arg), actor)
                    return self._approved_list(items)
            if action == "rg":
                self.pool.submit(self._regenerate, arg)
                return "🔁 Regenerating… the new version will arrive here in a few minutes."
        except approvals.QueueError as e:
            return f"Not done: {e}"
        return "Unknown button."

    def _send_for_app(self, item_id: str, folder: str, caption: str,
                      others: list[str], sounds: list[str] | None = None) -> None:
        """The reel without background music + the caption, for posting in the app."""
        from pathlib import Path

        video = Path(folder) / "reel_for_app.mp4"
        if not video.exists():
            video = Path(folder) / "reel.mp4"
        try:
            with open(video, "rb") as f:
                self.call("sendDocument", files={"document": (f"{item_id}.mp4", f)},
                          chat_id=self.admin,
                          caption="Post this on Instagram with a trending song:")
            self.send(caption)
            self.send(
                "How to post it:\n"
                "1. Save the video above (⋮ → Save to Gallery).\n"
                "2. Instagram → + → Reel → choose it.\n"
                "3. Tap ♫ Audio → search one of the sounds below (prefer ones with the ↗ "
                "trending arrow) → set its start → Done.\n"
                "4. Volume: Original audio low (about 10–20%), the song about 70%.\n"
                "5. Paste the caption above → Share.\n"
                f"6. Then send me: /posted {item_id} <link of the reel>\n"
                + ("\nSounds for this reel:\n" + "\n".join(sounds) + "\n" if sounds else "")
                + (f"\nAlready posting automatically to: {', '.join(others)}." if others else ""))
        except (OSError, TelegramError) as e:
            self.send(f"⚠️ Could not send the video: {redact(str(e))[:200]}")

    def _regenerate(self, item_id: str) -> None:
        try:
            new_id = producer.regenerate(item_id, "telegram")
            self.send_item(new_id)
        except Exception as e:  # noqa: BLE001
            self.send(f"⚠️ Regenerate failed: {redact(str(e))[:300]}")

    @staticmethod
    def _approved_list(items: list[Item]) -> str:
        if not items:
            return "Nothing was waiting for approval."
        lines = [f"• {i.hook[:50]} → {approvals.local_time_label(i)}" for i in items]
        return f"✅ Approved {len(items)}:\n" + "\n".join(lines)

    def on_command(self, text: str) -> str:
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()
        actor = "telegram"
        with session() as s:
            if cmd in ("/start", "/help"):
                return HELP
            if cmd == "/new":
                fmt, pillar, topic = quick.parse(args)
                quick.request_new(fmt, pillar, topic, actor)
                return (f"🛠 Creating {FORMAT_LABEL.get(fmt.value, fmt.value)} · {pillar.value}"
                        + (f" · {topic}" if topic else "")
                        + ". It arrives here in 2–4 minutes; tap 🚀 Post now to publish it.\n"
                        + quick.USAGE)
            if cmd == "/posted":
                if len(args) < 2:
                    return "Use /posted <id> <instagram reel link>."
                item = s.get(Item, args[0])
                if item is None:
                    return "No item with that id."
                try:
                    return quick.record_manual_post(s, item, args[1], actor)
                except (approvals.QueueError, PublishError) as e:
                    return f"Not done: {redact(str(e))[:300]}"
            if cmd == "/postnow":
                if not args:
                    return "Use /postnow <id> (the id is at the bottom of each item message)."
                item = s.get(Item, args[0])
                if item is None:
                    return "No item with that id."
                if not publisher.targets_for(item):
                    return "Not posted: no platform for this format is connected (see /setup)."
                try:
                    quick.post_now(s, item, actor)
                except approvals.QueueError as e:
                    return f"Not done: {e}"
                return "🚀 Posting now. The links arrive here in a few minutes."
            if cmd == "/queue":
                items = approvals.pending_review(s)
                if not items:
                    return "Nothing waiting for approval."
                return "Waiting for approval:\n" + "\n".join(
                    f"• {i.for_date} {FORMAT_LABEL.get(i.format, i.format)}: {i.hook[:50]}"
                    for i in items)
            if cmd == "/approve_next":
                n = int(args[0]) if args and args[0].isdigit() else 1
                return self._approved_list(approvals.approve_next(s, n, actor))
            if cmd == "/approve_tomorrow":
                tomorrow = load_schedule().local_now().date() + timedelta(days=1)
                return self._approved_list(approvals.approve_day(s, tomorrow, actor))
            if cmd == "/upcoming":
                items = approvals.upcoming(s)
                if not items:
                    return "Nothing scheduled."
                return "\n".join(f"• {approvals.local_time_label(i)} "
                                 f"{FORMAT_LABEL.get(i.format, i.format)} [{i.status}] "
                                 f"{i.hook[:40]}" for i in items)
            if cmd == "/kill":
                approvals.set_kill_switch(s, True, actor)
                return "🛑 Kill switch ON. Nothing will be published or generated."
            if cmd == "/resume":
                approvals.set_kill_switch(s, False, actor)
                return "▶️ Kill switch OFF. Publishing resumes on schedule."
            if cmd == "/autonomy":
                if not args:
                    return f"Autonomy is {approvals.autonomy(s)}. Use /autonomy L0, L1 or L2."
                try:
                    approvals.set_autonomy(s, args[0].upper(), actor)
                except approvals.QueueError as e:
                    return str(e)
                return f"Autonomy set to {args[0].upper()}."
            if cmd == "/report":
                from instapost.stats import report
                day = load_schedule().local_now().date() - timedelta(days=1)
                text, _ = report.build(s, day)
                return text
            if cmd == "/plan":
                from sqlalchemy import select

                from instapost.db import PlanEntry
                today = load_schedule().local_now().date()
                entries = list(s.scalars(select(PlanEntry).where(
                    PlanEntry.day >= today.isoformat(),
                    PlanEntry.day <= (today + timedelta(days=6)).isoformat())
                    .order_by(PlanEntry.day, PlanEntry.slot)))
                if not entries:
                    return "No plan yet. It is built every week, or from the dashboard Plan page."
                return "Plan:\n" + "\n".join(
                    f"{e.day[5:]} {e.slot} {FORMAT_LABEL.get(e.format, e.format)} {e.pillar} "
                    f"· {e.hook_style}{' 🔬' if e.experiment else ''}"
                    f"{' · ' + e.topic[:30] if e.topic else ''}" for e in entries)
            if cmd == "/setup":
                from instapost.setup_status import checklist
                return "Setup checklist:\n" + "\n".join(
                    f"{'✅' if st.done else '⬜'} {st.label}"
                    + ("" if st.done else f" — {st.hint}") for st in checklist())
            if cmd in ("/status", "/budget"):
                st = get_settings()
                return (f"Kill switch: {'ON' if approvals.kill_switch_on(s) else 'off'}\n"
                        f"Autonomy: {approvals.autonomy(s)}\n"
                        f"Waiting for approval: {len(approvals.pending_review(s))}\n"
                        f"Spend today ${budget.spent('day'):.2f} / ${st.daily_budget_usd:.2f}, "
                        f"month ${budget.spent('month'):.2f} / ${st.monthly_budget_usd:.2f}")
        return "Unknown command. /help"

    # --- polling loop ----------------------------------------------------------------------

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                with session() as s:
                    offset = int(get_kv(s, "telegram_offset", "0"))
                updates = self.call("getUpdates", offset=offset, timeout=50,
                                    allowed_updates=["message", "callback_query"])
                for u in updates:
                    try:
                        self.handle_update(u)
                    except Exception as e:  # noqa: BLE001 - one bad update must not stop the bot
                        self.send(f"⚠️ Error: {redact(str(e))[:300]}")
                    with session() as s:
                        set_kv(s, "telegram_offset", str(u["update_id"] + 1))
            except TelegramError:
                time.sleep(10)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name="telegram", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
