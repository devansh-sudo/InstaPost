"""Verified-verse database loaded from data/scriptures/*.yaml.

review_status: pending (not checked yet; posts citing it wait for a human), reviewed (a human
compared it with a printed edition), rejected (wrong; never cited until fixed and reviewed).
"""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path

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

    @property
    def rejected(self) -> bool:
        return self.review_status == "rejected"


STATUSES = ("pending", "reviewed", "rejected")


def verse_file(verse_id: str) -> tuple[Path, int]:
    """The YAML file and 1-based line number where an entry starts."""
    for path in sorted((get_settings().data_dir / "scriptures").glob("*.yaml")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.fullmatch(rf"\s*-\s*id:\s*{re.escape(verse_id)}\s*", line):
                return path, no
    raise KeyError(verse_id)


def set_status(verse_id: str, status: str, on: date | None = None) -> Path:
    """Change one entry's review_status in place, keeping comments and layout."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    path, start = verse_file(verse_id)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    end = next((i for i in range(start, len(lines)) if re.match(r"\s*-\s*id:", lines[i])),
               len(lines))
    block = [ln for ln in lines[start - 1:end] if not ln.lstrip().startswith("reviewed_on:")]
    at = next(i for i, ln in enumerate(block) if ln.lstrip().startswith("review_status:"))
    indent = block[at][:len(block[at]) - len(block[at].lstrip())]
    block[at] = f"{indent}review_status: {status}\n"
    if status != "pending":
        day = (on or date.today()).isoformat()
        block.insert(at + 1, f'{indent}reviewed_on: "{day}"\n')
    lines[start - 1:end] = block
    path.write_text("".join(lines), encoding="utf-8")
    load_verses.cache_clear()
    return path


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
        if v.rejected:
            continue
        speaker = v.speaker or "-"
        lines.append(f"- id={v.id} | {v.label} | वक्ता: {speaker} | विषय: {', '.join(v.themes)}"
                     f" | भावार्थ: {v.meaning}")
    return "\n".join(lines)
