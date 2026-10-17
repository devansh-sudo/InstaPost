"""Shared display helpers for numbers and platform names."""

PLATFORM_LABEL = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}


def compact(n: float | None) -> str:
    """1234 -> 1.2K, 2000000 -> 2M, None -> en dash."""
    if not isinstance(n, int | float):  # None, or a value the platform did not report
        return "–"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return f"{n:g}" if isinstance(n, float) else str(n)
