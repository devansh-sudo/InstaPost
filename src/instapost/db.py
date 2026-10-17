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
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
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
    return eng


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
