"""Database models and session handling (SQLite by default, PostgreSQL via DATABASE_URL).

All timestamps are stored as naive UTC datetimes.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from instapost.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ItemStatus:
    GENERATING = "generating"
    BLOCKED = "blocked"            # failed QA; kept for inspection
    NEEDS_REVIEW = "needs_review"  # waiting for approval
    APPROVED = "approved"          # approved and scheduled
    PUBLISHING = "publishing"
    PUBLISHED = "published"        # every target succeeded
    PARTIAL = "partial"            # some targets failed
    FAILED = "failed"
    REJECTED = "rejected"


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    format: Mapped[str] = mapped_column(String(40))
    pillar: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    folder: Mapped[str] = mapped_column(Text)
    hook: Mapped[str] = mapped_column(Text, default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    fact_check: Mapped[str] = mapped_column(String(30), default="")
    qa_summary: Mapped[str] = mapped_column(Text, default="")
    for_date: Mapped[str | None] = mapped_column(String(10), index=True)  # local YYYY-MM-DD
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    approved_by: Mapped[str | None] = mapped_column(String(40))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)  # reels only, for watch ratio
    preferred_slot: Mapped[str | None] = mapped_column(String(5))  # "HH:MM" from the plan
    # What the learner tracks: pillar, hook_style, scene, length (slot comes from publishing).
    features: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    publications: Mapped[list["Publication"]] = relationship(
        back_populates="item", cascade="all, delete-orphan")


class Publication(Base):
    """One item on one platform. The unique constraint is what makes publishing idempotent."""

    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("item_id", "platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    platform: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/published/failed
    container_id: Mapped[str | None] = mapped_column(String(80))  # IG container, resumable
    remote_id: Mapped[str | None] = mapped_column(String(80))
    permalink: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)

    item: Mapped[Item] = relationship(back_populates="publications")


class Job(Base):
    """Long-running work (content generation, publishing) shown live on the dashboard."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(Text, default="")
    # queued (requested from the dashboard, waiting for the worker) / running / done / failed
    status: Mapped[str] = mapped_column(String(20), default="running")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    step: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class MetricSnapshot(Base):
    """Stats of one published post on one platform at one moment (Phase 3 collector).
    `metrics` holds normalised keys (views, reach, likes, comments, shares, saves,
    avg_watch_s, watch_ratio, follows, profile_visits, replies); `raw` keeps the API reply."""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(80), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    age_hours: Mapped[float] = mapped_column(Float)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class AccountSnapshot(Base):
    """Account-level numbers: kind "followers" (every few hours) or "daily" (one local day)."""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    day: Mapped[str] = mapped_column(String(10), index=True)  # local YYYY-MM-DD
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), unique=True)  # the local day it covers
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlanEntry(Base):
    """One planned piece in the weekly plan (Phase 4). The planner fills these; you can edit
    them on the Plan page; the evening generation turns them into items."""

    __tablename__ = "plan_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[str] = mapped_column(String(10), index=True)  # local Monday
    day: Mapped[str] = mapped_column(String(10), index=True)  # local YYYY-MM-DD
    position: Mapped[int] = mapped_column(Integer, default=0)
    format: Mapped[str] = mapped_column(String(40))
    pillar: Mapped[str] = mapped_column(String(40))
    hook_style: Mapped[str] = mapped_column(String(20))
    scene: Mapped[int] = mapped_column(Integer, default=0)  # index into brand scene_presets
    slot: Mapped[str] = mapped_column(String(5))  # "HH:MM"
    length: Mapped[str | None] = mapped_column(String(10))  # reels: short / medium / long
    topic: Mapped[str | None] = mapped_column(Text)
    experiment: Mapped[str | None] = mapped_column(Text)  # what is being tested, if anything
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned/used/skipped
    edited_by: Mapped[str | None] = mapped_column(String(40))
    item_id: Mapped[str | None] = mapped_column(String(80))


class WeeklyReview(Base):
    __tablename__ = "weekly_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[str] = mapped_column(String(10), unique=True)  # the week reviewed
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """Append-only record of every action, by the agent or by you."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(40))  # agent / web:admin / telegram
    action: Mapped[str] = mapped_column(String(60))
    item_id: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class KV(Base):
    """Small runtime settings changed from the dashboard or Telegram (autonomy, kill switch)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


@lru_cache
def engine():
    url = get_settings().database_url
    if url.startswith("sqlite:///"):
        from pathlib import Path

        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(conn, _record):
            # WAL lets the web app and the worker use the same file at the same time.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(eng)
    _add_missing_columns(eng)
    return eng


def _add_missing_columns(eng) -> None:
    """Tiny forward-only migration: add new nullable columns to existing tables, so a
    database created by an earlier phase keeps working after an upgrade."""
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing and col.nullable:
                    ddl = col.type.compile(eng.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))


@lru_cache
def _sessionmaker():
    return sessionmaker(engine(), expire_on_commit=False)


@contextmanager
def session() -> Iterator[Session]:
    s = _sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def audit(s: Session, actor: str, action: str, item_id: str | None = None, **detail) -> None:
    s.add(AuditLog(actor=actor, action=action, item_id=item_id, detail=detail))


def get_kv(s: Session, key: str, default: str) -> str:
    row = s.get(KV, key)
    return row.value if row else default


def set_kv(s: Session, key: str, value: str) -> None:
    row = s.get(KV, key)
    if row:
        row.value = value
    else:
        s.add(KV(key=key, value=value))


def reset_for_tests() -> None:
    engine.cache_clear()
    _sessionmaker.cache_clear()
