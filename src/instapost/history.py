"""Record of everything the studio produced; used for duplicate checks and writer context."""

import json
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

from instapost.config import get_settings


def _path() -> Path:
    path = get_settings().state_dir / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def entries() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def recent_hooks(limit: int = 30) -> list[str]:
    return [e["hook"] for e in entries()[-limit:]]


def most_similar(hook: str) -> tuple[float, str | None]:
    best, match = 0.0, None
    for e in entries():
        ratio = SequenceMatcher(None, hook, e["hook"]).ratio()
        if ratio > best:
            best, match = ratio, e["hook"]
    return best, match


def add(item_id: str, fmt: str, pillar: str, topic: str, hook: str) -> None:
    entry = {"ts": datetime.now(UTC).isoformat(), "id": item_id, "format": fmt,
             "pillar": pillar, "topic": topic, "hook": hook}
    with _path().open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
