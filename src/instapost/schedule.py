"""Posting schedule from schedule.yaml: slots, daily plan, pillar rotation, targets, limits."""

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel

from instapost.config import get_settings
from instapost.models import Format, Pillar

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class WeeklyVideo(BaseModel):
    """The Sunday YouTube video made from the week's posts (instapost.weekly)."""
    enabled: bool = True
    day: str = "sun"
    make_at: str = "16:30"     # built then and sent to you for approval
    publish_at: str = "19:15"  # goes out then once approved (or 10 minutes after approval)
    days: int = 7


class Schedule(BaseModel):
    timezone: str
    generate_at: str
    report_at: str = "09:00"
    plan_day: str = "sun"
    plan_at: str = "18:00"
    slots: dict[Format, list[str]]
    daily_plan: dict[str, dict[Format, int]]
    pillar_rotation: dict[Format, list[Pillar]]
    monday_pillar: Pillar | None = None
    targets: dict[Format, list[str]]
    max_posts_per_day: dict[str, int]
    weekly_video: WeeklyVideo = WeeklyVideo()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def local_now(self) -> datetime:
        return datetime.now(self.tz)

    def slot_times(self, fmt: Format) -> list[time]:
        return sorted(time.fromisoformat(t) for t in self.slots.get(fmt, []))

    def plan_for(self, day: date, offset: int = 0) -> list[tuple[Format, Pillar]]:
        """What to create for `day`. `offset` advances the rotation (e.g. days since start)."""
        counts = self.daily_plan[WEEKDAYS[day.weekday()]]
        plan = []
        for fmt, n in counts.items():
            rotation = self.pillar_rotation[fmt]
            for i in range(n):
                pillar = rotation[(offset * n + i) % len(rotation)]
                if day.weekday() == 0 and self.monday_pillar and fmt == Format.REEL_BOOK_POV \
                        and i == 0:
                    pillar = self.monday_pillar
                plan.append((fmt, pillar))
        return plan


def schedule_file_data() -> dict:
    """schedule.yaml as written, before any dashboard changes."""
    return yaml.safe_load(get_settings().schedule_file.read_text(encoding="utf-8"))


@lru_cache
def _load(_file: str, _mtime: int, _overrides) -> Schedule:
    from instapost import overrides

    # Changes made on the dashboard win over schedule.yaml (instapost.overrides).
    return Schedule.model_validate(overrides.merge(schedule_file_data(),
                                                   overrides.load()["schedule"]))


def load_schedule() -> Schedule:
    from instapost import overrides

    path = get_settings().schedule_file
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return _load(str(path), mtime, overrides.stamp())


load_schedule.cache_clear = _load.cache_clear  # type: ignore[attr-defined]


def to_utc_naive(local: datetime) -> datetime:
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def to_local(utc_naive: datetime, sched: Schedule) -> datetime:
    return utc_naive.replace(tzinfo=ZoneInfo("UTC")).astimezone(sched.tz)


def candidate_slots(sched: Schedule, fmt: Format, start_day: date,
                    days: int = 14) -> list[datetime]:
    """Local, tz-aware slot datetimes from `start_day` onward."""
    out = []
    for d in range(days):
        day = start_day + timedelta(days=d)
        out += [datetime.combine(day, t, tzinfo=sched.tz) for t in sched.slot_times(fmt)]
    return out
