"""Post score (PLAN §4): one number, 0-100, that says how well a post did on the signals
Instagram ranks on. Built only from ratios, so a small new account can be compared fairly
with itself over time.

Each component is a rate divided by a "good" target for a small spiritual page, capped at
1.5x the target. 100 means every signal reached 1.5x its target.

| Component    | Rate                      | Good target | Weight |
|--------------|---------------------------|-------------|--------|
| sends        | shares / reach            | 2.0 %       | 0.35   |
| saves        | saves / reach             | 3.0 %       | 0.25   |
| watch        | avg watch / video length  | 100 %       | 0.25   |
| comments     | comments / reach          | 1.0 %       | 0.10   |
| follows      | follows / reach           | 0.5 %       | 0.05   |

Components a platform does not report (e.g. watch time on a carousel) are left out and the
remaining weights are scaled up, so formats are comparable.
"""

COMPONENTS = {
    # name: (numerator key, target, weight)
    "sends": ("shares", 0.02, 0.35),
    "saves": ("saves", 0.03, 0.25),
    "watch": ("watch_ratio", 1.0, 0.25),
    "comments": ("comments", 0.01, 0.10),
    "follows": ("follows", 0.005, 0.05),
}
CAP = 1.5
MIN_REACH = 20  # below this, ratios are noise


def audience(metrics: dict) -> float | None:
    return metrics.get("reach") or metrics.get("views")


def components(metrics: dict) -> dict[str, float]:
    """Each available component as a 0-1 value."""
    reach = audience(metrics)
    out = {}
    for name, (key, target, _w) in COMPONENTS.items():
        value = metrics.get(key)
        if value is None:
            continue
        rate = value if key == "watch_ratio" else (value / reach if reach else None)
        if rate is None:
            continue
        out[name] = min(rate / target, CAP) / CAP
    return out


def score(metrics: dict) -> float | None:
    """0-100, or None when the post has too little reach to judge."""
    reach = audience(metrics)
    if not reach or reach < MIN_REACH:
        return None
    parts = components(metrics)
    if not parts:
        return None
    total_w = sum(COMPONENTS[n][2] for n in parts)
    return round(100 * sum(COMPONENTS[n][2] * v for n, v in parts.items()) / total_w, 1)


def combined(per_platform: dict[str, dict]) -> dict:
    """Sum counts across platforms; score = views-weighted mean of the platform scores."""
    keys = ("views", "reach", "likes", "comments", "shares", "saves", "follows")
    total = {k: sum(m.get(k) or 0 for m in per_platform.values()) for k in keys}
    weighted, weight = 0.0, 0.0
    for m in per_platform.values():
        sc = score(m)
        if sc is not None:
            w = audience(m) or 0
            weighted += sc * w
            weight += w
    total["score"] = round(weighted / weight, 1) if weight else None
    ratios = [m["watch_ratio"] for m in per_platform.values() if m.get("watch_ratio")]
    total["watch_ratio"] = round(max(ratios), 3) if ratios else None
    return total
