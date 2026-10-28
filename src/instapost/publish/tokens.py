"""Tokens that the agent refreshes itself are kept in secrets/tokens.json (mode 600).

The value from .env is the starting point; once refreshed, the stored token wins.
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from instapost.config import get_settings


def _path() -> Path:
    d = get_settings().secrets_dir
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d / "tokens.json"


def _load() -> dict:
    p = _path()
    return json.loads(p.read_text()) if p.exists() else {}


def get(name: str) -> str | None:
    stored = _load().get(name, {}).get("value")
    return stored or getattr(get_settings(), name, None)


def info(name: str) -> dict:
    return _load().get(name, {})


def save(name: str, value: str, expires_in: int | None = None) -> None:
    data = _load()
    now = datetime.now(UTC)
    data[name] = {"value": value, "refreshed_at": now.isoformat(),
                  "expires_at": (datetime.fromtimestamp(now.timestamp() + expires_in, UTC)
                                 .isoformat() if expires_in else None)}
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


_SECRET_PATTERNS = [
    re.compile(r"(access_token=)[^&\s\"']+"),
    re.compile(r"(Bearer |OAuth )[A-Za-z0-9._\-|]+"),
    re.compile(r"(X-Amz-Signature=)[^&\s\"']+"),
    re.compile(r"(/bot)\d+:[A-Za-z0-9_\-]+"),  # Telegram puts the bot token in the URL path
    re.compile(r"(Client-ID )[A-Za-z0-9_\-]+"),
    re.compile(r"(key=)[A-Za-z0-9_\-.]{20,}"),
]


class RedactingFilter(logging.Filter):
    """Runs every log line through redact(), whichever library wrote it."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, ()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Logging for the worker and web processes: no request URLs, no secrets."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs every request URL at INFO; Telegram's contain the bot token.
    for noisy in ("httpx", "httpcore", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter())


def redact(text: str) -> str:
    """Remove tokens from error messages before they reach logs, the DB or Telegram."""
    for rx in _SECRET_PATTERNS:
        text = rx.sub(r"\1[redacted]", text)
    for name in ("ig_access_token", "fb_page_token", "telegram_bot_token"):
        value = get(name) if name != "telegram_bot_token" else get_settings().telegram_bot_token
        if value:
            text = text.replace(value, "[redacted]")
    return text
