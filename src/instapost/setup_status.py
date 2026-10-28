"""Setup checklist: what is still needed from the owner before the agent can run fully.

Shown on the dashboard Settings page, by `instapost check`, and by /setup in Telegram, so
the open items keep reminding you until they are done.
"""

import os
from dataclasses import dataclass

from instapost.brand import load_brand
from instapost.config import get_settings
from instapost.publish import publisher
from instapost.scriptures import load_verses


@dataclass
class Step:
    label: str
    done: bool
    hint: str
    required: bool = True


def checklist() -> list[Step]:
    from instapost.render import music

    s = get_settings()
    platforms = publisher.configured_platforms()
    verses = list(load_verses().values())
    reviewed = sum(v.reviewed for v in verses)
    from instapost.studio.free_images import LibraryProvider
    from instapost.studio.images import chain_names

    lib = LibraryProvider()
    usable = {"gemini": bool(s.gemini_api_key), "openai": bool(s.openai_api_key),
              "unsplash": bool(s.unsplash_access_key), "placeholder": True,
              "library": bool(lib.files(True)) and bool(lib.files(False))}
    from instapost.studio.images import recent_refusal

    chain = chain_names()
    # A source refused in the last day (no billing, bad key) does not count as ready.
    image_ok = [n for n in chain if usable.get(n) and not recent_refusal(n)]
    https = (s.public_base_url or "").startswith("https://")
    if s.media_host == "r2":
        media_ok = all((s.r2_account_id, s.r2_access_key_id, s.r2_secret_access_key,
                        s.r2_bucket))
    else:  # local: Instagram fetches the videos from this server's /media route
        media_ok = https
    return [
        Step("Claude API key", bool(s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")),
             "ANTHROPIC_API_KEY in .env (console.anthropic.com)"),
        Step(f"Image source ({', '.join(image_ok) or 'none ready'}; order: {' -> '.join(chain)})",
             bool(image_ok), "GEMINI_API_KEY with billing, UNSPLASH_ACCESS_KEY (free) or pictures "
             "in data/images/scenes + data/images/subjects (data/images/README.md)"),
        Step("Instagram handle in brand.yaml", load_brand().handle != "@your_handle",
             "edit `handle:` in brand.yaml"),
        Step(f"Verses reviewed ({reviewed}/{len(verses)})", reviewed == len(verses),
             "bash deploy/docker.sh verses review (docs/VERSES.md)"),
        Step("Dashboard login", bool(s.admin_password_hash and s.admin_totp_secret
                                     and s.session_secret), "run `instapost admin-setup`"),
        Step("Telegram bot", bool(s.telegram_bot_token and s.telegram_admin_chat_id),
             "TELEGRAM_BOT_TOKEN + TELEGRAM_ADMIN_CHAT_ID (docs/TELEGRAM.md)"),
        Step("Instagram connected", "instagram" in platforms,
             "IG_USER_ID + IG_ACCESS_TOKEN (docs/SETUP_META_YOUTUBE.md A3)"),
        Step("Facebook Page connected", "facebook" in platforms,
             "FB_PAGE_ID + FB_PAGE_TOKEN (docs/SETUP_META_YOUTUBE.md A4)", required=False),
        Step("YouTube connected", "youtube" in platforms,
             "client JSON in secrets/ + `instapost youtube-login`", required=False),
        Step(f"Video hosting ({s.media_host})", media_ok,
             "a domain with HTTPS, then MEDIA_HOST=local + PUBLIC_BASE_URL=https://... "
             "(docs/DOCKER.md, nginx-proxy-manager) or R2_* values (docs/DEPLOY.md 2.5)"),
        Step("Dashboard on HTTPS", https,
             "PUBLIC_BASE_URL=https://your-domain after the proxy is set up (docs/DOCKER.md)"),
        Step(f"Music library ({len(music.tracks())} tracks)", bool(music.tracks()),
             "royalty-free tracks in data/music/ (data/music/README.md); without them reels "
             "use the built-in tanpura ambience", required=False),
        Step("Demo mode off", not s.demo_mode, "DEMO_MODE=false before going live"),
        Step("Festival dates confirmed", _festivals_confirmed(),
             "check data/calendar/festivals.yaml against a panchang, set confirmed: true",
             required=False),
        Step("YouTube uploads public", s.youtube_privacy == "public",
             "after the YouTube API audit is approved, set YOUTUBE_PRIVACY=public",
             required=False),
    ]


def open_items() -> list[Step]:
    return [step for step in checklist() if not step.done]


def _festivals_confirmed() -> bool:
    import yaml

    path = get_settings().data_dir / "calendar" / "festivals.yaml"
    if not path.exists():
        return False
    entries = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("festivals", [])
    return bool(entries) and all(e.get("confirmed") for e in entries)
