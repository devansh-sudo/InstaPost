"""Runtime settings, loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

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

    brand_file: Path = ROOT / "brand.yaml"
    data_dir: Path = ROOT / "data"
    assets_dir: Path = ROOT / "assets"
    output_dir: Path = ROOT / "output"
    state_dir: Path = ROOT / "state"


@lru_cache
def get_settings() -> Settings:
    return Settings()
