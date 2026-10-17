"""The learner: what is working, and how sure are we?

Every scored post is an observation. For each format and each *dimension* (pillar, hook style,
background scene, posting slot, reel length) every option keeps a Beta belief about its
score: Beta(1 + sum of scores, 1 + sum of (1 - score)), with scores scaled to 0-1. Dimensions
are learned separately, which needs far fewer posts than learning every combination.

Choosing works by Thompson sampling: draw one plausible value from each option's belief and
take the highest. Options that do well are picked more often, and options with few posts
still get picked sometimes because their beliefs are wide. On top of that the planner turns
about 20% of pieces into explicit experiments (see planner.py).
"""

import random
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from instapost.brand import Brand, load_brand
from instapost.models import Format, HookStyle, Pillar
from instapost.schedule import load_schedule, to_local
from instapost.stats.queries import PostRow

MIN_AGE_HOURS = 24  # judge a post only after a day, when its numbers have settled
LENGTHS = ["short", "medium", "long"]


def dimensions(fmt: Format, brand: Brand | None = None) -> dict[str, list[str]]:
    """The options the learner chooses between, per format."""
    brand = brand or load_brand()
    slots = load_schedule().slots.get(fmt, [])
    pillars = [p.value for p in Pillar]
    hooks = [h.value for h in HookStyle]
    scenes = [str(i) for i in range(len(brand.scene_presets))]
    if fmt == Format.REEL_BOOK_POV:
        return {"pillar": pillars, "hook_style": hooks, "scene": scenes, "slot": slots,
                "length": LENGTHS}
    if fmt == Format.CAROUSEL_SHLOK:
        return {"pillar": pillars, "hook_style": hooks, "slot": slots}
    return {"pillar": pillars, "slot": slots}


def length_bucket(duration_s: float | None) -> str | None:
    if duration_s is None:
        return None
    return "short" if duration_s <= 8.5 else "medium" if duration_s <= 10.5 else "long"


def scene_index(scene_prompt: str, brand: Brand) -> int:
    """Which brand scene preset a free-text scene prompt is closest to."""
    ratios = [SequenceMatcher(None, scene_prompt, p).ratio() for p in brand.scene_presets]
    return max(range(len(ratios)), key=ratios.__getitem__) if ratios else 0


def nearest_slot(hhmm: str, slots: list[str]) -> str | None:
    if not slots:
        return None

    def minutes(t: str) -> int:
        h, m = map(int, t.split(":"))
        return h * 60 + m
    return min(slots, key=lambda sl: abs(minutes(sl) - minutes(hhmm)))


def item_features(draft: dict, pillar: str, duration_s: float | None,
                  scene: int | None = None, brand: Brand | None = None) -> dict:
    """Features stored on an item when it is created (the slot is added at publish time)."""
    brand = brand or load_brand()
    return {
        "pillar": pillar,
        "hook_style": draft.get("hook_style"),
        "scene": scene if scene is not None else scene_index(draft.get("scene_prompt", ""), brand),
        "length": length_bucket(duration_s),
    }


def observation(row: PostRow) -> tuple[Format, dict[str, str], float] | None:
    """(format, features, reward 0-1) for a scored post, or None if it can't teach yet."""
    from instapost.db import utcnow

    if row.score is None or (utcnow() - row.published_at).total_seconds() < MIN_AGE_HOURS * 3600:
        return None
    fmt = Format(row.item.format)
    feats = {k: str(v) for k, v in (row.item.features or {}).items() if v is not None}
    feats.setdefault("pillar", row.item.pillar)
    local = to_local(row.published_at, load_schedule()).strftime("%H:%M")
    slot = nearest_slot(local, load_schedule().slots.get(fmt, []))
    if slot:
        feats["slot"] = slot
    return fmt, feats, row.score / 100


@dataclass
class Arm:
    option: str
    n: int
    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def interval(self, rng: np.random.Generator, q: float = 0.9) -> tuple[float, float]:
        draws = rng.beta(self.alpha, self.beta, 4000)
        lo, hi = np.quantile(draws, [(1 - q) / 2, 1 - (1 - q) / 2])
        return float(lo), float(hi)


class Learner:
    def __init__(self, rows: list[PostRow], brand: Brand | None = None):
        self.brand = brand or load_brand()
        self.arms: dict[tuple[Format, str], dict[str, Arm]] = {}
        for fmt in Format:
            for dim, options in dimensions(fmt, self.brand).items():
                self.arms[(fmt, dim)] = {o: Arm(o, 0, 1.0, 1.0) for o in options}
        self.observations = 0
        for row in rows:
            obs = observation(row)
            if obs is None:
                continue
            fmt, feats, reward = obs
            self.observations += 1
            for dim, value in feats.items():
                arm = self.arms.get((fmt, dim), {}).get(value)
                if arm:
                    arm.n += 1
                    arm.alpha += reward
                    arm.beta += 1 - reward

    def sample(self, fmt: Format, dim: str, rng: random.Random,
               allowed: list[str] | None = None) -> str:
        """Thompson sampling: the option with the highest plausible score right now."""
        arms = [a for a in self.arms[(fmt, dim)].values() if allowed is None or a.option in allowed]
        return max(arms, key=lambda a: rng.betavariate(a.alpha, a.beta)).option

    def least_tried(self, fmt: Format, dim: str, rng: random.Random,
                    allowed: list[str] | None = None) -> str:
        arms = [a for a in self.arms[(fmt, dim)].values() if allowed is None or a.option in allowed]
        fewest = min(a.n for a in arms)
        return rng.choice([a.option for a in arms if a.n == fewest])

    def best(self, fmt: Format, dim: str) -> Arm | None:
        tried = [a for a in self.arms[(fmt, dim)].values() if a.n > 0]
        return max(tried, key=lambda a: a.mean) if tried else None

    def beliefs(self, seed: int = 0) -> list[dict]:
        """Everything the learner believes, for the dashboard and the weekly review."""
        rng = np.random.default_rng(seed)
        out = []
        for (fmt, dim), arms in self.arms.items():
            for arm in arms.values():
                lo, hi = arm.interval(rng)
                out.append({"format": fmt.value, "dimension": dim, "option": arm.option,
                            "posts": arm.n, "mean": round(100 * arm.mean, 1),
                            "low": round(100 * lo, 1), "high": round(100 * hi, 1)})
        return out

    def confidence(self, fmt: Format, dim: str, label=str) -> str:
        """Plain words for how much the data says so far."""
        arms = list(self.arms[(fmt, dim)].values())
        tried = [a for a in arms if a.n > 0]
        if sum(a.n for a in arms) < 10 or len(tried) < 2:
            return "learning: not enough posts yet"
        rng = np.random.default_rng(1)
        ranked = sorted(tried, key=lambda a: a.mean, reverse=True)
        lo_best, _ = ranked[0].interval(rng)
        _, hi_second = ranked[1].interval(rng)
        if lo_best > hi_second:
            return f"clear leader: {label(ranked[0].option)}"
        return f"leaning towards {label(ranked[0].option)}, not certain yet"
