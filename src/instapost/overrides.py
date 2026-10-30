"""Settings changed on the dashboard (Settings → Agent settings).

.env and schedule.yaml stay as they are (they are read-only inside Docker); changes made on
the dashboard are saved in state/overrides.json and win over the files. "Reset" removes a
change, so the file's value counts again. The worker and the dashboard both read this file,
and pick up a change within seconds, without a restart.

Only everyday options can be changed here, never keys, passwords or paths.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def path() -> Path:
    # Not from get_settings(): get_settings() itself reads this file.
    return Path(os.environ.get("STATE_DIR") or ROOT / "state") / "overrides.json"


def stamp() -> tuple[str, int]:
    p = path()
    try:
        return str(p), p.stat().st_mtime_ns
    except OSError:
        return str(p), 0


def load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("settings", {})
    data.setdefault("schedule", {})
    return data


def save(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def merge(base: dict, changes: dict) -> dict:
    """`changes` over `base`, one level of nested dicts deep (e.g. one weekday's counts)."""
    out = dict(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out
