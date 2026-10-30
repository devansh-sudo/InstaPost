"""India's festival calendar for the page: what is coming, and the posts made for it.

The list is data/calendar/festivals.yaml (Hindu festivals and vrats, Sant Kabir's day, and the
national days). Lunar dates move every year and the file's dates are estimates, so a festival
only shapes the plan once its date is confirmed against a panchang. On the dashboard
(Festivals) you confirm or move a date, skip a festival, add your own, or make its post right
away; those changes are saved in state/festivals.json (the data folder is read-only in
Docker).

On a confirmed festival day, the day's first reel takes the festival's theme and topic, and
the first story is the festival greeting.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import yaml

from instapost.config import get_settings

PILLARS = ("jeevan_sutra", "shiv_dhyan", "gita", "kabir", "life_lesson")
KINDS = {"festival": "Festival", "vrat": "Vrat / tithi", "national": "National day",
         "jayanti": "Jayanti"}


def _file_entries() -> list[dict]:
    path = get_settings().data_dir / "calendar" / "festivals.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return []
    return doc.get("festivals", [])


def _state_file() -> Path:
    d = get_settings().state_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / "festivals.json"


def _changes() -> dict:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("edits", {})
    data.setdefault("added", [])
    return data


def _save(data: dict) -> None:
    tmp = _state_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_state_file())


def fid(entry: dict) -> str:
    """A stable id: the name and the year it was first listed for."""
    slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-")
    return f"{slug}-{str(entry['date'])[:4]}"


def all_festivals() -> list[dict]:
    """Every festival with your changes applied, by date."""
    changes = _changes()
    out = []
    for raw in [*_file_entries(), *changes["added"]]:
        e = {"kind": "festival", "greeting": "", "confirmed": False, "skip": False,
             "name_hi": raw.get("name_hi") or raw["name"], **raw}
        e["id"] = raw.get("id") or fid(raw)
        e.update(changes["edits"].get(e["id"], {}))
        e["date"] = date.fromisoformat(str(e["date"]))
        e["mine"] = e["id"] in {a.get("id") for a in changes["added"]}
        out.append(e)
    return sorted(out, key=lambda e: e["date"])


def upcoming(today: date, days: int = 400) -> list[dict]:
    return [e for e in all_festivals() if today <= e["date"] <= today + timedelta(days=days)]


def confirmed_by_day() -> dict[str, dict]:
    """Confirmed, not skipped festivals by ISO date (what the planner uses)."""
    return {e["date"].isoformat(): e for e in all_festivals()
            if e["confirmed"] and not e["skip"]}


def get(festival_id: str) -> dict:
    for e in all_festivals():
        if e["id"] == festival_id:
            return e
    raise KeyError(festival_id)


def edit(festival_id: str, **fields) -> dict:
    get(festival_id)  # must exist
    allowed = {"date", "confirmed", "skip", "topic", "pillar", "greeting"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "date" in fields:
        fields["date"] = date.fromisoformat(str(fields["date"])).isoformat()
    if "pillar" in fields and fields["pillar"] not in PILLARS:
        raise ValueError("unknown theme")
    data = _changes()
    data["edits"].setdefault(festival_id, {}).update(fields)
    _save(data)
    return get(festival_id)


def add(name: str, day: date, pillar: str, topic: str, greeting: str = "",
        name_hi: str = "") -> dict:
    name = " ".join(name.split())[:60]
    if not name or pillar not in PILLARS or not topic.strip():
        raise ValueError("give a name, a theme and what the post should say")
    entry = {"name": name, "name_hi": name_hi.strip()[:60] or name, "date": day.isoformat(),
             "pillar": pillar, "topic": topic.strip()[:200], "greeting": greeting.strip()[:120],
             "kind": "festival", "confirmed": True}
    entry["id"] = fid(entry) + "-mine"
    data = _changes()
    if entry["id"] in {a["id"] for a in data["added"]}:
        raise ValueError("that festival is already in the list for this year")
    data["added"].append(entry)
    _save(data)
    return entry


def remove_mine(festival_id: str) -> None:
    data = _changes()
    data["added"] = [a for a in data["added"] if a["id"] != festival_id]
    data["edits"].pop(festival_id, None)
    _save(data)


def unconfirmed_soon(today: date, days: int = 21) -> list[dict]:
    """Festivals in the next weeks whose date you still have to check."""
    return [e for e in upcoming(today, days) if not e["confirmed"] and not e["skip"]]
