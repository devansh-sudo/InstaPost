"""Command line entry point.

Examples:
  instapost make reel --pillar gita --topic "गुस्सा" --vision-qa
  instapost make carousel --pillar kabir
  instapost make story --offline            # free test run with sample text and drawings
  instapost make reel --draft my_draft.json  # render a hand-written draft
  instapost budget
  instapost verses
  instapost admin-setup        # create dashboard login (password + authenticator app)
  instapost web                # dashboard on 127.0.0.1:8800
  instapost worker             # scheduler, publisher and Telegram bot
  instapost youtube-login      # one-time YouTube authorisation
  instapost check              # verify which platforms are connected
"""

import argparse
import sys
from pathlib import Path

from instapost import budget
from instapost.config import SetupError, get_settings
from instapost.models import Draft, Format, Pillar
from instapost.publish.tokens import redact
from instapost.scriptures import load_verses
from instapost.studio.images import PROVIDERS, QuotaError

FORMATS = {"reel": Format.REEL_BOOK_POV, "carousel": Format.CAROUSEL_SHLOK,
           "story": Format.STORY_LINE}


def _make(args) -> int:
    from instapost import pipeline

    draft = None
    if args.draft:
        draft = Draft.model_validate_json(Path(args.draft).read_text(encoding="utf-8"))
    result = pipeline.make(
        FORMATS[args.format], Pillar(args.pillar), args.topic, offline=args.offline,
        draft=draft, image_provider=args.image_provider, vision_qa=args.vision_qa,
    )
    print(f"{result.status.upper()}  {result.item_id}")
    print(f"  folder:     {result.folder}")
    print(f"  fact check: {result.fact.verdict.value}")
    for issue in result.fact.issues:
        print(f"    - {issue}")
    for check in result.qa.checks:
        mark = "ok  " if check.passed else "FAIL"
        print(f"  [{mark}] {check.name}: {check.detail}")
    return 0 if result.status != "blocked" else 2


def _budget(_args) -> int:
    s = get_settings()
    print(f"today:      ${budget.spent('day'):.3f} of ${s.daily_budget_usd:.2f}")
    print(f"this month: ${budget.spent('month'):.3f} of ${s.monthly_budget_usd:.2f}")
    return 0


REVIEW_SOURCES = {
    "श्रीमद्भगवद्गीता": "Gita Press Gorakhpur edition, or gitasupersite.iitk.ac.in (IIT Kanpur)",
    "संत कबीर": "a Kabir edition such as कबीर ग्रंथावली (नागरी प्रचारिणी सभा); wording varies "
                "between editions, so a common, well-attested version is fine",
    "शिव मंत्र": "a printed stotra/mantra book (e.g. Gita Press), and the named source "
                 "(ऋग्वेद 7.59.12 for the Mahamrityunjaya mantra)",
}


def _verses(args) -> int:
    from instapost import scriptures

    if args.action == "set":
        if not (args.id and args.status):
            print("usage: instapost verses set <id> <pending|reviewed|rejected>")
            return 1
        path = scriptures.set_status(args.id, args.status)
        print(f"{args.id}: {args.status} ({path.name})")
        return 0
    verses = list(load_verses().values())
    if args.action == "list":
        for v in verses:
            print(f"{v.review_status:9} {v.id:28} {v.label}")
        done = sum(v.reviewed for v in verses)
        print(f"\n{done}/{len(verses)} reviewed. Review them with: instapost verses review")
        return 0
    todo = [v for v in verses if v.review_status == "pending" or args.all]
    if not todo:
        print("Nothing pending. (Use --all to go through every verse again.)")
        return 0
    print(f"{len(todo)} to review. For each: compare the text, number and speaker with a "
          "printed edition.\nAnswer  c = correct   w = wrong (never cited until fixed)   "
          "s = skip   q = quit\n")
    for n, v in enumerate(todo, 1):
        path, line = scriptures.verse_file(v.id)
        print(f"── {n}/{len(todo)}  {v.id}  ({path.name}, line {line}) " + "─" * 20)
        print(f"  label:    {v.label}")
        print(f"  speaker:  {v.speaker or '-'}")
        print(f"  text:     {v.text}")
        print(f"  meaning:  {v.meaning}")
        print(f"  check with: {REVIEW_SOURCES.get(v.source, 'a trusted printed edition')}")
        print("  checks: every letter, matra, halant (्) and danda (। ॥) of the text; the "
              "number/source in the label; who speaks; the meaning says no more than the text")
        while True:
            answer = input("  c/w/s/q? ").strip().lower()
            if answer in ("c", "w", "s", "q"):
                break
        if answer == "q":
            break
        if answer == "s":
            continue
        status = "reviewed" if answer == "c" else "rejected"
        scriptures.set_status(v.id, status)
        print(f"  saved: {status}" + ("" if answer == "c" else
              f". To fix it: edit {path} line {line}, then "
              f"`instapost verses set {v.id} reviewed`"))
        print()
    verses = list(load_verses().values())
    print(f"{sum(v.reviewed for v in verses)}/{len(verses)} reviewed.")
    return 0


def _admin_setup(args) -> int:
    import getpass
    import secrets

    import pyotp

    from instapost.web.auth import hash_password

    if args.password_stdin:  # used by deploy/docker.sh, which asks for the password itself
        pw = sys.stdin.readline().rstrip("\n")
        if len(pw) < 12:
            print("Password must be at least 12 characters.", file=sys.stderr)
            return 1
    else:
        pw = getpass.getpass("New dashboard password (min 12 characters): ")
        if len(pw) < 12 or pw != getpass.getpass("Repeat: "):
            print("Passwords must match and be at least 12 characters.")
            return 1
    totp = pyotp.random_base32()
    uri = pyotp.TOTP(totp).provisioning_uri(name="admin", issuer_name="InstaPost")
    lines = [f"SESSION_SECRET={secrets.token_urlsafe(48)}",
             f"ADMIN_PASSWORD_HASH={hash_password(pw)}",
             f"ADMIN_TOTP_SECRET={totp}"]
    if args.password_stdin:
        print("\n".join(lines))
        print(f"TOTP_URI={uri}")
        return 0
    print("\nAdd these lines to .env (keep them secret):\n")
    print("\n".join(lines))
    print("\nIn Google Authenticator / Authy: add account -> enter setup key:")
    print(f"  {totp}   (or open this link on the phone: {uri})")
    return 0


def _new(args) -> int:
    from instapost import quick
    from instapost.telegram_bot import FORMAT_LABEL

    fmt, pillar, topic = quick.parse(args.words)
    job = quick.request_new(fmt, pillar, topic, "cli")
    print(f"Queued job {job}: {FORMAT_LABEL.get(fmt.value, fmt.value)} · {pillar.value}"
          + (f" · {topic}" if topic else ""))
    print("The worker makes it now (2–4 minutes) and sends it to Telegram.\n"
          "Tap 🚀 Post now there to publish to all platforms at once, or ✅ Approve to post "
          "at the next time slot.")
    return 0


def _weekly_video(args) -> int:
    from datetime import date

    from instapost import jobs

    end = args.end or None
    if end:
        date.fromisoformat(end)  # validate
    jobs.enqueue("weekly_video", "weekly video (command line)", end_day=end, actor="cli")
    print("Queued: the worker makes the weekly YouTube video from the published posts of the "
          "7 days up to " + (end or "today") + ". It arrives in Telegram and the Inbox in a "
          "few minutes.")
    return 0


def _plan_month(args) -> int:
    from instapost.db import session
    from instapost.learn import planner

    with session() as s:
        summary = planner.load_month(s, args.file)
    print(f"Loaded {summary['entries']} planned posts over {summary['days']} days.")
    for w in summary["warnings"]:
        print("  note:", w)
    print("Each evening at the schedule's generate_at time, the next day's posts are created "
          "and sent to you for approval. See and edit them on the dashboard's Plan page.")
    return 0


def _web(_args) -> int:
    import uvicorn

    from instapost.web.app import create_app

    s = get_settings()
    uvicorn.run(create_app(), host=s.web_host, port=s.web_port, proxy_headers=True,
                forwarded_allow_ips=s.forwarded_allow_ips, log_level="info")
    return 0


def _worker(_args) -> int:
    from instapost.publish.tokens import setup_logging
    from instapost.worker import Worker

    setup_logging()
    Worker().run_forever()
    return 0


def _youtube_login(args) -> int:
    from instapost.publish import youtube

    youtube.login(args.port, args.bind, paste=not args.tunnel)
    try:
        print("YouTube connected:", youtube.channel())
    except Exception as e:  # noqa: BLE001 - the login itself is saved; report what blocks use
        print("Login saved, but the channel could not be read yet:\n  "
              + redact(str(e))[:400] + "\nFix that (e.g. enable the API it names), then run "
              "`instapost check`; no need to log in again.")
        return 1
    return 0


def _check(_args) -> int:
    from instapost.publish import publisher

    s = get_settings()
    ok = publisher.configured_platforms()
    for p in ("instagram", "facebook", "youtube"):
        print(f"{p:10} {'connected' if p in ok else 'not set up'}")
    from instapost.publish.instagram import PublishError

    # "connected" above only means the settings are present; these calls test them for real.
    if "instagram" in ok:
        from instapost.publish.instagram import Instagram

        try:
            ig = Instagram()
            me = ig.me()
            print("  instagram account:", me)
            if me.get("user_id") and str(me["user_id"]) != str(s.ig_user_id):
                print(f"  ! IG_USER_ID in .env is {s.ig_user_id}, but this token belongs to "
                      f"user_id {me['user_id']}. Set IG_USER_ID={me['user_id']} and restart.")
            else:
                print("  instagram publishing limit:", ig.publishing_limit())
        except PublishError as e:
            print(f"  ! instagram test failed: {e}")
    if "youtube" in ok:
        from instapost.publish import youtube

        try:
            print("  youtube channel:", youtube.channel(), f"(uploads: {s.youtube_privacy})")
        except Exception as e:  # noqa: BLE001 - Google errors come in several types
            print(f"  ! youtube test failed: {redact(str(e))[:300]} "
                  "(run youtube-login again if the login was revoked or expired)")
    if "facebook" in ok:
        from instapost.publish.facebook import Facebook

        try:
            print("  facebook page:", Facebook().page())
        except PublishError as e:
            print(f"  ! facebook test failed: {e} (check FB_PAGE_ID and that FB_PAGE_TOKEN "
                  "is the Page token from me/accounts)")
    print(f"telegram   {'set' if s.telegram_bot_token else 'not set'}"
          f"{'' if s.telegram_admin_chat_id else ' (TELEGRAM_ADMIN_CHAT_ID missing)'}")
    print(f"dashboard  {'login set' if s.admin_password_hash else 'run admin-setup'}")
    print(f"media host {s.media_host}")

    from instapost.config import IMAGE_KEYS, ROOT, env_file_problems, key_report
    print(f"\nKeys (read from {ROOT / '.env'}):")
    from instapost.studio.free_images import LibraryProvider
    from instapost.studio.images import chain_names

    chain = chain_names()
    names = ["ANTHROPIC_API_KEY", *(IMAGE_KEYS[n].upper() for n in chain if n in IMAGE_KEYS)]
    if "unsplash" in chain:
        names.append("UNSPLASH_ACCESS_KEY")
    for name in names:
        print(f"  {name:19} {key_report(name)}")
    print(f"\nImages: tried in this order: {' -> '.join(chain)}")
    from instapost.studio.images import recent_refusal
    for n in chain:
        if reason := recent_refusal(n):
            print(f"  {n}: refused in the last 24 h: {reason}")
    if "library" in chain:
        lib = LibraryProvider()
        print(f"  library: {len(lib.files(True))} scenes, {len(lib.files(False))} subjects "
              f"in {lib.root}")
    if problems := env_file_problems():
        print("\nProblems in .env:")
        for line in problems:
            print(f"  ! {line}")

    from instapost.setup_status import checklist
    print("\nSetup checklist:")
    for st in checklist():
        mark = "done" if st.done else ("TODO" if st.required else "optional")
        print(f"  [{mark:8}] {st.label}" + ("" if st.done else f"  -> {st.hint}"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="instapost")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("make", help="create one reel, carousel or story")
    make.add_argument("format", choices=FORMATS)
    make.add_argument("--pillar", choices=[p.value for p in Pillar], default="jeevan_sutra")
    make.add_argument("--topic", help="optional topic hint, in Hindi or English")
    make.add_argument("--offline", action="store_true",
                      help="no API calls: sample text and placeholder images")
    make.add_argument("--draft", help="path to a Draft JSON to render instead of writing one")
    make.add_argument("--image-provider", choices=list(PROVIDERS),
                      help="main image source; IMAGE_FALLBACK sources are tried after it")
    make.add_argument("--vision-qa", action="store_true",
                      help="also check the final image with Claude (about $0.002)")
    make.set_defaults(func=_make)

    sub.add_parser("budget", help="show spend against the caps").set_defaults(func=_budget)
    vs = sub.add_parser("verses", help="list or review the verified-verse database")
    vs.add_argument("action", nargs="?", default="list", choices=["list", "review", "set"])
    vs.add_argument("id", nargs="?")
    vs.add_argument("status", nargs="?", choices=["pending", "reviewed", "rejected"])
    vs.add_argument("--all", action="store_true", help="review: also already-reviewed verses")
    vs.set_defaults(func=_verses)

    adm = sub.add_parser("admin-setup", help="create dashboard login")
    adm.add_argument("--password-stdin", action="store_true",
                     help="read the password from stdin and print only KEY=VALUE lines")
    adm.set_defaults(func=_admin_setup)
    new = sub.add_parser("new", help="create one piece now via the worker; it arrives in "
                         "Telegram with 🚀 Post now")
    new.add_argument("words", nargs="*", help="[reel|carousel|story] [pillar] [topic ...]")
    new.set_defaults(func=_new)
    wv = sub.add_parser("weekly-video", help="make the long YouTube video of the week's posts")
    wv.add_argument("--end", help="last day (YYYY-MM-DD), default today")
    wv.set_defaults(func=_weekly_video)
    pm = sub.add_parser("plan-month", help="load a hand-written plan file (data/plan/*.yaml)")
    pm.add_argument("file")
    pm.set_defaults(func=_plan_month)
    sub.add_parser("web", help="run the dashboard").set_defaults(func=_web)
    sub.add_parser("worker", help="run scheduler/publisher/bot").set_defaults(func=_worker)
    yt = sub.add_parser("youtube-login", help="connect YouTube (one time)")
    yt.add_argument("--port", type=int, default=8085)
    yt.add_argument("--bind", help="with --tunnel: address to listen on (0.0.0.0 in Docker)")
    yt.add_argument("--tunnel", action="store_true",
                    help="receive Google's redirect on --port (needs an SSH tunnel); by default "
                         "you paste the address from the browser instead")
    yt.set_defaults(func=_youtube_login)
    sub.add_parser("check", help="show connected platforms").set_defaults(func=_check)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (SetupError, QuotaError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
