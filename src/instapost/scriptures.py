"""Verified-verse database loaded from data/scriptures/*.yaml."""

from functools import lru_cache

import yaml
from pydantic import BaseModel

from instapost.config import get_settings


class Verse(BaseModel):
    id: str
    label: str
    speaker: str | None
    text: str
    meaning: str
    themes: list[str]
    review_status: str
    source: str

    @property
    def reviewed(self) -> bool:
        return self.review_status == "reviewed"


@lru_cache
def load_verses() -> dict[str, Verse]:
    verses: dict[str, Verse] = {}
    for path in sorted((get_settings().data_dir / "scriptures").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for entry in doc["entries"]:
            verse = Verse(source=doc["source"], **entry)
            if verse.id in verses:
                raise ValueError(f"duplicate verse id {verse.id} in {path.name}")
            verses[verse.id] = verse
    return verses


def catalogue_for_prompt() -> str:
    """Compact listing the writer chooses references from."""
    lines = []
    for v in load_verses().values():
        speaker = v.speaker or "-"
        lines.append(f"- id={v.id} | {v.label} | वक्ता: {speaker} | विषय: {', '.join(v.themes)}"
                     f" | भावार्थ: {v.meaning}")
    return "\n".join(lines)
