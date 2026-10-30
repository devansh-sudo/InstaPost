"""What the dashboard's Settings page may change, how each value is checked, and saving.

A field is "settings:<name>" (an .env setting) or "schedule:<a.b.c>" (a schedule.yaml value).
Only differences from the files are saved (instapost.overrides), so the files stay the
reference and "reset" brings their value back.
"""

import re
from dataclasses import dataclass, field

from instapost import overrides
from instapost.config import Settings
from instapost.schedule import WEEKDAYS, Schedule, schedule_file_data

PLATFORMS = ("instagram", "facebook", "youtube")
PLATFORM_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}
FORMAT_NAMES = {"reel_book_pov": "Reels", "carousel_shlok": "Carousels",
                "story_line": "Stories"}
DAY_NAMES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri",
             "sat": "Sat", "sun": "Sun"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Field:
    key: str            # "settings:writer_effort" or "schedule:max_posts_per_day.youtube"
    label: str
    help: str = ""
    kind: str = "text"  # int, float, bool, choice, time, times, platforms
    choices: tuple = ()
    low: float = 0
    high: float = 0
    group: str = ""

    @property
    def name(self) -> str:
        return self.key.replace(":", "__").replace(".", "__")


def _fields() -> list[Field]:
    out = [
        *(Field(f"schedule:max_posts_per_day.{p}", PLATFORM_NAMES[p], kind="int", low=0, high=20,
                group="limits") for p in PLATFORMS),
        *(Field(f"schedule:targets.{f}", FORMAT_NAMES[f], kind="platforms", group="targets")
          for f in FORMAT_NAMES),
        *(Field(f"schedule:daily_plan.{d}.{f}", f"{DAY_NAMES[d]} {FORMAT_NAMES[f]}", kind="int",
                low=0, high=4, group="plan") for d in WEEKDAYS for f in FORMAT_NAMES),
        *(Field(f"schedule:slots.{f}", FORMAT_NAMES[f], "times, comma separated (HH:MM)",
                kind="times", group="times") for f in FORMAT_NAMES),
        Field("schedule:generate_at", "Make tomorrow's posts at", kind="time", group="times"),
        Field("schedule:report_at", "Daily report at", kind="time", group="times"),
        Field("schedule:plan_day", "Weekly plan on", kind="choice", choices=tuple(WEEKDAYS),
              group="times"),
        Field("schedule:plan_at", "Weekly plan at", kind="time", group="times"),
        Field("schedule:weekly_video.enabled", "Make the weekly YouTube video", kind="bool",
              group="weekly"),
        Field("schedule:weekly_video.day", "On", kind="choice", choices=tuple(WEEKDAYS),
              group="weekly"),
        Field("schedule:weekly_video.make_at", "Made at", kind="time", group="weekly"),
        Field("schedule:weekly_video.publish_at", "Published at (after approval)", kind="time",
              group="weekly"),
        Field("settings:instagram_reels", "Instagram reels",
              "auto = posted by the agent; app = sent to you to post with a trending sound",
              kind="choice", choices=("auto", "app"), group="publishing"),
        Field("settings:youtube_privacy", "YouTube uploads",
              "public once YouTube's API audit is approved", kind="choice",
              choices=("public", "unlisted", "private"), group="publishing"),
        Field("settings:music_mode", "Reel music", "library = your tracks; ambience = tanpura",
              kind="choice", choices=("library", "ambience"), group="publishing"),
        Field("settings:reel_style", "Book photos in reels",
              "plate = use approved book photos; classic = never", kind="choice",
              choices=("plate", "classic"), group="publishing"),
        Field("settings:writer_effort", "Writer effort (reels, carousels)",
              "low is about half the cost of medium", kind="choice",
              choices=("low", "medium", "high"), group="cost"),
        Field("settings:factcheck_first_model", "Cheap fact check first",
              "small model first, the careful one only when unsure", kind="choice",
              choices=("claude-haiku-5-5", ""), group="cost"),
        Field("settings:vision_check", "Image check before approval (~$0.001)", kind="bool",
              group="cost"),
        Field("settings:drawings_generate", "Paint new drawings with Gemini when none fits",
              "~$0.04 each, kept for reuse", kind="bool", group="cost"),
        Field("settings:scene_image_provider", "Scene photos come first from",
              kind="choice", choices=("unsplash", "library", "gemini"), group="cost"),
        Field("settings:scene_voices", "AI voices in Shiv–Parvati scenes", kind="bool",
              group="cost"),
        Field("settings:daily_budget_usd", "Daily spending cap ($)", kind="float", low=0.05,
              high=20, group="cost"),
        Field("settings:monthly_budget_usd", "Monthly spending cap ($)", kind="float", low=1,
              high=200, group="cost"),
    ]
    return out


FIELDS = _fields()
GROUPS = {
    "limits": ("Posts per day, per platform",
               "A safety limit. Above it, a post waits until the next morning "
               "(\"daily_limit_reached\" in Activity). YouTube allows about 6 uploads a day "
               "through the API, so keep YouTube at 6 or below."),
    "targets": ("Where each kind of post goes", "Platforms that aren't connected are skipped."),
    "plan": ("How many posts are made each day", "Used by the weekly planner and the "
             "evening generation. The month plan file can still set its own topics."),
    "times": ("Times", "All in your time zone."),
    "weekly": ("Weekly YouTube video", ""),
    "publishing": ("Publishing", ""),
    "cost": ("Content and cost", ""),
}


def _get(data: dict, dotted: str):
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _set(data: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        data = data.setdefault(part, {})
    data[parts[-1]] = value


def _plain(value):
    return value.value if hasattr(value, "value") else value


def file_values() -> dict:
    """Each field's value in the files (.env and schedule.yaml), without dashboard changes."""
    base = Settings()
    sched = schedule_file_data()
    out = {}
    for f in FIELDS:
        where, dotted = f.key.split(":", 1)
        out[f.key] = getattr(base, dotted) if where == "settings" else _get(sched, dotted)
        if out[f.key] is None and f.kind == "int":
            out[f.key] = 0
    return out


def current_values() -> dict:
    """What the agent uses now (files plus dashboard changes)."""
    from instapost.config import get_settings
    from instapost.schedule import load_schedule

    st, sched = get_settings(), load_schedule().model_dump(mode="json")
    out = {}
    for f in FIELDS:
        where, dotted = f.key.split(":", 1)
        out[f.key] = getattr(st, dotted) if where == "settings" else _get(sched, dotted)
        if out[f.key] is None and f.kind == "int":
            out[f.key] = 0
    return out


def changed_keys() -> set[str]:
    data = overrides.load()
    keys = {f"settings:{k}" for k in data["settings"]}
    for f in FIELDS:
        where, dotted = f.key.split(":", 1)
        if where == "schedule" and _get(data["schedule"], dotted) is not None:
            keys.add(f.key)
    return keys


def parse(f: Field, raw: str | list[str] | None):
    """The value from the form, checked; raises ValueError with a plain message."""
    if f.kind == "bool":
        return raw == "1"
    if f.kind == "platforms":
        return [p for p in PLATFORMS if p in (raw or [])]
    raw = (raw or "").strip() if isinstance(raw, str) else ""
    if f.kind in ("int", "float"):
        try:
            v = int(raw) if f.kind == "int" else round(float(raw), 2)
        except ValueError:
            raise ValueError(f"{f.label}: a number, please") from None
        if not f.low <= v <= f.high:
            raise ValueError(f"{f.label}: between {f.low:g} and {f.high:g}")
        return v
    if f.kind == "choice":
        if raw not in f.choices:
            raise ValueError(f"{f.label}: choose one of the options")
        return raw
    if f.kind == "time":
        if not TIME.match(raw):
            raise ValueError(f"{f.label}: a time like 07:15")
        return raw
    if f.kind == "times":
        times = sorted({t.strip() for t in raw.replace(";", ",").split(",") if t.strip()})
        if not times or not all(TIME.match(t) for t in times):
            raise ValueError(f"{f.label}: times like 07:15, 20:45")
        return times
    return raw


@dataclass
class Result:
    changed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def apply(form, reset: bool = False) -> Result:
    """Save the form: values equal to the files are not stored; everything is validated as a
    whole before anything is saved."""
    res = Result()
    if reset:
        old = changed_keys()
        overrides.save({"settings": {}, "schedule": {}})
        res.changed = sorted(old)
        return res
    files = file_values()
    new = {"settings": {}, "schedule": {}}
    for f in FIELDS:
        raw = form.getlist(f.name) if f.kind == "platforms" else form.get(f.name)
        if raw is None and f.kind not in ("bool", "platforms"):
            continue
        try:
            value = parse(f, raw)
        except ValueError as e:
            res.errors.append(str(e))
            continue
        where, dotted = f.key.split(":", 1)
        file_value = _plain(files[f.key])
        if isinstance(file_value, list):
            file_value = [_plain(x) for x in file_value]
            same = sorted(file_value) == sorted(value)
        else:
            same = file_value == value
        if same:
            continue
        if where == "settings":
            new["settings"][dotted] = value
        else:
            _set(new["schedule"], dotted, value)
    if res.errors:
        return res
    try:
        Settings(**new["settings"])
        Schedule.model_validate(overrides.merge(schedule_file_data(), new["schedule"]))
    except Exception as e:  # noqa: BLE001 - shown to you, nothing is saved
        res.errors.append(f"not saved: {str(e)[:300]}")
        return res
    overrides.save(new)
    res.changed = sorted(changed_keys())  # every value that now differs from the files
    return res


def grouped(values: dict, files: dict, changed: set[str]):
    """Fields by group for the page: (group id, title, help, [(field, value, file value,
    changed?)])."""
    out = []
    for gid, (title, help_text) in GROUPS.items():
        rows = [(f, _plain_value(values[f.key]), _plain_value(files[f.key]), f.key in changed)
                for f in FIELDS if f.group == gid]
        out.append((gid, title, help_text, rows))
    return out


def _plain_value(v):
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return _plain(v)

