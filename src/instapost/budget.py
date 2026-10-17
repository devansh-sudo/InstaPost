"""Spend ledger with hard daily and monthly caps (PLAN §14).

Every paid call is checked with `ensure_room()` before it runs and recorded with `record()`
after. The ledger is an append-only JSONL file so it doubles as an audit trail.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from instapost.config import get_settings

# USD per million tokens: (input, output, cache_read). Keep in sync with Anthropic pricing.
CLAUDE_PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

# USD per generated image. VERIFY against the vendor price page before relying on these.
IMAGE_PRICES = {
    "gemini": 0.039,
    "openai": 0.042,
    "placeholder": 0.0,
}


class BudgetExceeded(RuntimeError):
    pass


def _ledger_path() -> Path:
    path = get_settings().state_dir / "spend.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _entries() -> list[dict]:
    path = _ledger_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def spent(period: str) -> float:
    """Total USD spent today ("day") or this calendar month ("month"), UTC."""
    now = datetime.now(UTC)
    prefix = now.strftime("%Y-%m-%d") if period == "day" else now.strftime("%Y-%m")
    return sum(e["usd"] for e in _entries() if e["ts"].startswith(prefix))


def ensure_room(estimated_usd: float) -> None:
    s = get_settings()
    if spent("day") + estimated_usd > s.daily_budget_usd:
        raise BudgetExceeded(f"daily cap ${s.daily_budget_usd:.2f} would be exceeded")
    if spent("month") + estimated_usd > s.monthly_budget_usd:
        raise BudgetExceeded(f"monthly cap ${s.monthly_budget_usd:.2f} would be exceeded")


def record(kind: str, model: str, usd: float, **details) -> None:
    entry = {"ts": datetime.now(UTC).isoformat(), "kind": kind, "model": model,
             "usd": round(usd, 6), **details}
    with _ledger_path().open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def claude_cost(model: str, usage) -> float:
    inp, out, cache_read = CLAUDE_PRICES[model]
    cache_write = inp * 1.25
    return (
        usage.input_tokens * inp
        + usage.output_tokens * out
        + (usage.cache_read_input_tokens or 0) * cache_read
        + (usage.cache_creation_input_tokens or 0) * cache_write
    ) / 1_000_000
