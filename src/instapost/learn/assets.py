"""Which book photos and drawings do well.

Every published post records the photo and drawing it used (item features "photo" and
"drawing"). Once its numbers have settled, its score teaches a Beta belief per photo and per
drawing, as the main learner does for pillars and hook styles. Picking is Thompson sampling,
so good performers come up more often and new ones still get their turn; the ones used most
recently go last, so the feed never repeats the same photo two posts in a row.

Kept apart from the weekly planner on purpose: which photos exist changes all the time.
"""

import random
from datetime import timedelta

import numpy as np

from instapost.learn.bandit import Arm, observation
from instapost.studio.free_images import _load_usage

KINDS = ("photo", "drawing")
RECENT = 3          # the last few used go to the back of the queue
HISTORY_DAYS = 120


def _rows():
    from instapost.db import session, utcnow
    from instapost.stats import queries

    with session() as s:
        return queries.post_rows(s, since=utcnow() - timedelta(days=HISTORY_DAYS))


def arms(kind: str, rows=None) -> dict[str, Arm]:
    """Belief per photo or drawing id, from scored posts."""
    out: dict[str, Arm] = {}
    for row in _rows() if rows is None else rows:
        obs = observation(row)
        if obs is None:
            continue
        _, feats, reward = obs
        key = feats.get(kind)
        if not key:
            continue
        arm = out.setdefault(key, Arm(key, 0, 1.0, 1.0))
        arm.n += 1
        arm.alpha += reward
        arm.beta += 1 - reward
    return out


def recent(kind: str, ids: list[str], n: int = RECENT) -> set[str]:
    """The `n` ids (of these) used most recently."""
    prefix = {"photo": "plate:", "drawing": "drawing:"}[kind]
    usage = _load_usage()
    used = sorted((usage[prefix + i], i) for i in ids if prefix + i in usage)
    return {i for _, i in used[-n:]}


def order(kind: str, ids: list[str], rng: random.Random | None = None,
          beliefs: dict[str, Arm] | None = None) -> list[str]:
    """Best first by Thompson sampling, the most recently used last."""
    if len(ids) <= 1:
        return list(ids)
    rng = rng or random.Random()
    beliefs = arms(kind) if beliefs is None else beliefs
    latest = recent(kind, ids, min(RECENT, len(ids) - 1))

    def draw(i: str) -> float:
        a = beliefs.get(i)
        return rng.betavariate(a.alpha, a.beta) if a else rng.betavariate(1, 1)

    fresh = sorted((i for i in ids if i not in latest), key=draw, reverse=True)
    usage = _load_usage()
    prefix = {"photo": "plate:", "drawing": "drawing:"}[kind]
    tail = sorted(latest, key=lambda i: usage.get(prefix + i, ""))
    return fresh + tail


def table(kind: str, labels: dict[str, str] | None = None, rows=None,
          seed: int = 0) -> list[dict]:
    """For the dashboard: each photo/drawing with posts, average score and its range."""
    rng = np.random.default_rng(seed)
    out = []
    for key, arm in arms(kind, rows).items():
        lo, hi = arm.interval(rng)
        out.append({"option": key, "label": (labels or {}).get(key, key), "posts": arm.n,
                    "mean": round(100 * arm.mean, 1), "low": round(100 * lo, 1),
                    "high": round(100 * hi, 1)})
    return sorted(out, key=lambda r: (r["posts"] == 0, -r["mean"]))
