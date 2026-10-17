"""Live job tracking: every long task writes its current step so the dashboard can show it."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select

from instapost.db import Job, session, utcnow


class JobHandle:
    def __init__(self, job_id: int):
        self.id = job_id

    def step(self, label: str) -> None:
        with session() as s:
            s.get(Job, self.id).step = label


@contextmanager
def track(kind: str, label: str) -> Iterator[JobHandle]:
    with session() as s:
        job = Job(kind=kind, label=label, step="starting")
        s.add(job)
        s.flush()
        job_id = job.id
    handle = JobHandle(job_id)
    try:
        yield handle
    except Exception as e:
        with session() as s:
            job = s.get(Job, job_id)
            job.status, job.error, job.finished_at = "failed", str(e)[:2000], utcnow()
        raise
    with session() as s:
        job = s.get(Job, job_id)
        job.status, job.step, job.finished_at = "done", "done", utcnow()


def enqueue(kind: str, label: str, **payload) -> int:
    """Ask the worker to do something (the web app never generates or publishes itself)."""
    with session() as s:
        job = Job(kind=kind, label=label, status="queued", step="waiting for worker",
                  payload=payload)
        s.add(job)
        s.flush()
        return job.id


def take_queued() -> Job | None:
    with session() as s:
        job = s.scalars(select(Job).where(Job.status == "queued").order_by(Job.id)).first()
        if job:
            job.status, job.started_at = "running", utcnow()
        return job


def finish(job_id: int, error: str | None = None) -> None:
    with session() as s:
        job = s.get(Job, job_id)
        job.status = "failed" if error else "done"
        job.step, job.error, job.finished_at = ("failed" if error else "done"), error, utcnow()
