"""Runtime settings, loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


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
