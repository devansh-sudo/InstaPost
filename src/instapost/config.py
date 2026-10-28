"""Runtime settings, loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
IMAGE_KEYS = {"gemini": "gemini_api_key", "openai": "openai_api_key"}


class SetupError(RuntimeError):
    """A required setting is missing; the message says what to set and where."""


def require(*names: str) -> None:
    """Raise SetupError naming every missing setting (e.g. "anthropic_api_key")."""
    s = get_settings()
    missing = [n.upper() for n in names if not getattr(s, n)]
    if missing:
        them = "it" if len(missing) == 1 else "them"
        raise SetupError(f"{', '.join(missing)} not set. Add {them} to"
                         f" {ROOT / '.env'} (KEY=value, no spaces or quotes), then run again. "
                         "For a free test without keys use --offline.")


class Settings(BaseSettings):
    # Blank values in .env (e.g. "IG_USER_ID=") count as not set.
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_ignore_empty=True)

    # Read from .env too (the SDK itself only looks at the process environment).
    anthropic_api_key: str | None = None

    # Claude models. Writing and fact-check run on Sonnet, cheap checks on Haiku (PLAN §14).
    writer_model: str = "claude-sonnet-5-5"
    factcheck_model: str = "claude-sonnet-5-5"
    qa_model: str = "claude-haiku-5-5"

    image_provider: str = "gemini"
    gemini_api_key: str | None = None
    gemini_image_model: str = "gemini-2.5-flash-image"
    openai_api_key: str | None = None
    openai_image_model: str = "gpt-image-1"
    # Used in this order when the main source fails (quota, no key, nothing found).
    # unsplash needs UNSPLASH_ACCESS_KEY; library uses your pictures in data/images/; "none" = off.
    image_fallback: str = "unsplash,library"
    # Reel soundtrack: "library" = a track from data/music/ that fits the pillar (falls back to
    # the built-in tanpura ambience when the folder is empty), "ambience" = always the ambience.
    music_mode: str = "library"
    unsplash_access_key: str | None = None

    daily_budget_usd: float = 0.60
    monthly_budget_usd: float = 18.00

    # Demo mode: the worker uses sample text and placeholder images (no paid API calls),
    # so the whole system can be tried before any keys are set.
    demo_mode: bool = False

    # Phase 2: storage, schedule, publishing, approvals, dashboard.
    database_url: str = f"sqlite:///{ROOT / 'state' / 'instapost.db'}"
    schedule_file: Path = ROOT / "schedule.yaml"
    secrets_dir: Path = ROOT / "secrets"

    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    ig_user_id: str | None = None
    ig_access_token: str | None = None
    ig_api_base: str = "https://graph.instagram.com/v23.0"
    fb_page_id: str | None = None
    fb_page_token: str | None = None
    fb_api_base: str = "https://graph.facebook.com/v23.0"
    # "public" once the YouTube API audit is approved; until then YouTube forces private anyway.
    youtube_privacy: str = "private"

    # Public file hosting for Meta to fetch media: "r2" (recommended) or "local".
    media_host: str = "r2"
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    # For media_host="local": the public HTTPS base URL that reaches this server's /media route.
    public_base_url: str | None = None

    telegram_bot_token: str | None = None
    telegram_admin_chat_id: int | None = None

    session_secret: str | None = None
    admin_username: str = "admin"
    admin_password_hash: str | None = None
    admin_totp_secret: str | None = None
    web_host: str = "127.0.0.1"
    web_port: int = 8800
    # Proxies whose X-Forwarded-For header is trusted (for the real client IP in the login
    # rate limit). "*" is only safe when the port is not reachable from the internet.
    forwarded_allow_ips: str = "127.0.0.1"

    brand_file: Path = ROOT / "brand.yaml"
    data_dir: Path = ROOT / "data"
    assets_dir: Path = ROOT / "assets"
    output_dir: Path = ROOT / "output"
    state_dir: Path = ROOT / "state"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Names people often use for the keys InstaPost expects.
ALIASES = {"CLAUDE_API_KEY": "ANTHROPIC_API_KEY", "CLAUDE_KEY": "ANTHROPIC_API_KEY",
           "ANTHROPIC_KEY": "ANTHROPIC_API_KEY", "GOOGLE_API_KEY": "GEMINI_API_KEY",
           "GEMINI_KEY": "GEMINI_API_KEY", "GOOGLE_AI_API_KEY": "GEMINI_API_KEY"}


def env_file_problems(path: Path | None = None) -> list[str]:
    """Lines in .env that will not do what was meant. Names and line numbers only, never values."""
    import difflib
    import re

    path = path or ROOT / ".env"
    if not path.exists():
        return [f"{path} does not exist (copy .env.example to .env)"]
    known = {name.upper() for name in Settings.model_fields}
    seen: dict[str, int] = {}
    problems = []
    for no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            problems.append(f"line {no}: not KEY=value, ignored")
            continue
        key, value = m.group(1), m.group(2).strip()
        if key.upper() not in known:
            close = [ALIASES[key.upper()]] if key.upper() in ALIASES else \
                difflib.get_close_matches(key.upper(), known, n=1, cutoff=0.6)
            hint = f" (did you mean {close[0]}?)" if close else ""
            problems.append(f"line {no}: {key} is not a setting InstaPost uses{hint}")
        if key.upper() in seen:
            problems.append(f"line {no}: {key.upper()} also on line {seen[key.upper()]}; "
                            "the last one wins" + (" and it is empty" if not value else ""))
        seen[key.upper()] = no
        if value and value[0] in "\"'" and (len(value) < 2 or value[-1] != value[0]):
            problems.append(f"line {no}: {key} has an unclosed quote")
        elif (value and " " in value and value[0] not in "\"'"
              and not value.split()[1].startswith("#")):
            problems.append(f"line {no}: {key} contains a space; keys never do (check the paste)")
    return problems


# Gemini and Unsplash keys come in more than one format, so only these two are checked.
KEY_PREFIXES = {"ANTHROPIC_API_KEY": "sk-ant-", "OPENAI_API_KEY": "sk-"}


def key_report(name: str, path: Path | None = None) -> str:
    """Where a key comes from and whether it looks right. Never prints the key itself."""
    import os
    import re

    path = path or ROOT / ".env"
    found = None  # (line number, value) of the last line naming the key; the last one wins
    if path.exists():
        for no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            m = re.match(rf"\s*(?:export\s+)?{name}\s*=\s*(.*)$", raw, re.IGNORECASE)
            if m:
                found = (no, m.group(1).split(" #")[0].strip().strip("\"'"))
    env_value = os.environ.get(name, "").strip()
    if env_value and found and env_value == found[1]:
        env_value = ""  # Docker passes .env as environment; same value, so name the line
    if env_value:
        where, value = "your shell environment (overrides .env)", env_value
    elif found:
        where, value = f".env line {found[0]}", found[1]
    else:
        return f"not in {path}"
    if not value:
        return f"{where}: EMPTY - paste the key right after the = sign and save"
    prefix = KEY_PREFIXES.get(name)
    if prefix is None:
        return f"{where}: {len(value)} characters"
    right = value.startswith(prefix) and not (name != "ANTHROPIC_API_KEY"
                                              and value.startswith("sk-ant-"))
    shape = "looks right" if right else \
        f"does NOT start with {prefix!r}; check it is the right key"
    return f"{where}: {len(value)} characters, {shape}"
