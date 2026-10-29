import pytest

from instapost import db, scriptures
from instapost.config import get_settings

ENV_TO_CLEAR = (
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN",
    "FB_PAGE_ID", "FB_PAGE_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID",
    "SESSION_SECRET", "ADMIN_PASSWORD_HASH", "ADMIN_TOTP_SECRET", "DEMO_MODE",
)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Each test gets its own database, state, output and secrets folders, and no keys."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'state' / 'test.db'}")
    for key in ENV_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    scriptures.load_verses.cache_clear()
    db.reset_for_tests()
    yield
    get_settings.cache_clear()
    scriptures.load_verses.cache_clear()
    db.reset_for_tests()
