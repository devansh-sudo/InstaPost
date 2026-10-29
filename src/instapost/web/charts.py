"""Server-rendered SVG charts (no chart library, no inline script, CSP-safe).

Conventions (from the data-viz method): colour follows the entity (Instagram is always
series 1, Facebook 2, YouTube 3); 2px lines, end dots with a surface ring, direct end labels
plus a legend for 2+ series; bars <= 24px with a rounded data end; hairline solid grid;
text never wears the series colour; every chart has a hover layer (data-tip, see charts.js)
and a table view.
"""

import math
from datetime import date

from markupsafe import Markup, escape

from instapost.stats.labels import PLATFORM_LABEL, compact

PLATFORM_SLOT = {"instagram": 1, "facebook": 2, "youtube": 3}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def nice_ticks(max_value: float, count: int = 4) -> list[float]:
    if max_value <= 0:
        return [0, 1]
    raw = max_value / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    top = step * math.ceil(max_value / step)
    return [round(i * step, 6) for i in range(int(round(top / step)) + 1)]


def _legend(names: list[str], slots: dict[str, int] | None = None) -> str:
    slots = slots or PLATFORM_SLOT
    items = "".join(
        f'<span class="key"><i class="sw s{slots.get(n, i + 1)}"></i>'
        f"{escape(PLATFORM_LABEL.get(n, n))}</span>" for i, n in enumerate(names))
    return f'<div class="legend">{items}</div>'


def line_chart(series: dict[str, list[tuple[date, int]]], title: str,
               width: int = 720, height: int = 260) -> Markup:
    """Follower growth: one line per platform on one shared axis (same unit)."""
    series = {k: v for k, v in series.items() if v}
    if not series:
        return Markup('<p class="muted">No follower data yet. The collector records it '
                      'every 6 hours once a platform is connected.</p>')
    pad_l, pad_r, pad_t, pad_b = 44, 110, 12, 28
    days = sorted({d for pts in series.values() for d, _ in pts})
    ticks = nice_ticks(max(v for pts in series.values() for _, v in pts) or 1)
    ymax = ticks[-1] or 1
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    span = max((days[-1] - days[0]).days, 1)

    def x(d: date) -> float:
        return pad_l + pw * ((d - days[0]).days / span if len(days) > 1 else 0.5)

    def y(v: float) -> float:
        return pad_t + ph * (1 - v / ymax)

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="{escape(title)}">']
    for t in ticks:
        parts.append(f'<line class="grid" x1="{pad_l}" x2="{pad_l + pw}" y1="{y(t):.1f}" '
                     f'y2="{y(t):.1f}"/><text class="tick" x="{pad_l - 6}" y="{y(t) + 4:.1f}" '
                     f'text-anchor="end">{compact(t)}</text>')
    for d in (days[0], days[-1]) if len(days) > 1 else (days[0],):
        parts.append(f'<text class="tick" x="{x(d):.1f}" y="{height - 8}" '
                     f'text-anchor="middle">{d.strftime("%d %b")}</text>')
    labels = []
    for name, pts in series.items():
        cls = f"s{PLATFORM_SLOT.get(name, 1)}"
        path = " ".join(f"{'M' if i == 0 else 'L'}{x(d):.1f},{y(v):.1f}"
                        for i, (d, v) in enumerate(pts))
        parts.append(f'<path class="line {cls}" d="{path}"/>')
        d_end, v_end = pts[-1]
        parts.append(f'<circle class="dot {cls}" cx="{x(d_end):.1f}" cy="{y(v_end):.1f}" r="4"/>')
        labels.append([y(v_end), f"{PLATFORM_LABEL.get(name, name)} {compact(v_end)}",
                       x(d_end)])
    # Direct end labels, nudged apart so they never overlap.
    labels.sort()
    for i in range(1, len(labels)):
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 15)
    for ly, text, lx in labels:
        parts.append(f'<text class="label" x="{lx + 9:.1f}" y="{ly + 4:.1f}">{escape(text)}</text>')
    # Hover columns: one invisible band per day, tooltip lists every platform.
    band = pw / max(len(days) - 1, 1)
    for d in days:
        vals = [f"{PLATFORM_LABEL.get(n, n)} {compact(v)}" for n, pts in series.items()
                for dd, v in pts if dd == d]
        tip = escape(f"{d.strftime('%a %d %b')}: " + " · ".join(vals))
        parts.append(f'<rect class="hit" data-x="{x(d):.1f}" x="{x(d) - band / 2:.1f}" '
                     f'y="{pad_t}" width="{band:.1f}" height="{ph}" data-tip="{tip}"/>')
    parts.append(f'<line class="crosshair" x1="0" x2="0" y1="{pad_t}" y2="{pad_t + ph}"/>')
    parts.append("</svg>")

    rows = "".join(
        f"<tr><td>{d.isoformat()}</td>" + "".join(
            f"<td>{compact(dict(series[n]).get(d))}</td>" for n in series) + "</tr>"
        for d in days)
    head = "".join(f"<th>{escape(PLATFORM_LABEL.get(n, n))}</th>" for n in series)
    table = (f'<details class="table-view"><summary>Table</summary><table><tr><th>Day</th>'
             f"{head}</tr>{rows}</table></details>")
    legend = _legend(list(series)) if len(series) > 1 else ""
    return Markup(f'<div class="viz">{legend}{"".join(parts)}{table}</div>')


def bar_chart(rows: list[tuple[str, float, int]], title: str, max_value: float = 100,
              width: int = 420) -> Markup:
    """Horizontal bars for one measure (average score), value at the tip."""
    if not rows:
        return Markup('<p class="muted">No scored posts yet. Posts are scored once they '
                      'reach 20 people.</p>')
    label_w, pad_r, bar_h, gap = 120, 70, 22, 14
    height = len(rows) * (bar_h + gap) + gap
    pw = width - label_w - pad_r
    parts = [f'<svg class="chart fixed" width="{width}" viewBox="0 0 {width} {height}" '
             f'role="img" aria-label="{escape(title)}">']
    parts.append(f'<line class="grid" x1="{label_w}" x2="{label_w}" y1="0" y2="{height}"/>')
    for i, (label, value, n) in enumerate(rows):
        y0 = gap + i * (bar_h + gap)
        w = max(pw * min(value, max_value) / max_value, 2)
        r = min(4, w)
        # Square at the baseline, 4px rounded data end.
        path = (f"M{label_w},{y0} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{bar_h - 2 * r} "
                f"a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z")
        tip = escape(f"{label}: average score {value} from {n} post{'s' if n != 1 else ''}")
        parts.append(f'<text class="label" x="{label_w - 8}" y="{y0 + 15}" '
                     f'text-anchor="end">{escape(label)}</text>')
        parts.append(f'<path class="bar s1" d="{path}" data-tip="{tip}"/>')
        parts.append(f'<text class="label" x="{label_w + w + 6:.1f}" y="{y0 + 15}">'
                     f'{value:g} <tspan class="tick">({n})</tspan></text>')
    parts.append("</svg>")
    table = ("<details class='table-view'><summary>Table</summary><table><tr><th>Group</th>"
             "<th>Avg score</th><th>Posts</th></tr>" + "".join(
                 f"<tr><td>{escape(lbl)}</td><td>{v:g}</td><td>{n}</td></tr>"
                 for lbl, v, n in rows) + "</table></details>")
    return Markup(f'<div class="viz">{"".join(parts)}{table}</div>')


def stacked_columns(days: list[date], series: dict[str, list[float]], title: str,
                    width: int = 720, height: int = 240, fmt=compact, unit: str = "views",
                    slots: dict[str, int] | None = None) -> Markup:
    """Views per day, one stacked column per day, one segment per platform (colour =
    platform). 2px surface gap between segments; only the top segment gets a rounded end.
    `fmt`, `unit` and `slots` (series name -> colour 1..3) reuse it for other measures."""
    series = {k: v for k, v in series.items() if any(v)}
    if not series or not days:
        return Markup(f'<p class="muted">No {unit} yet in this period.</p>')
    slots = slots or PLATFORM_SLOT
    pad_l, pad_r, pad_t, pad_b = 44, 12, 12, 28
    totals = [sum(v[i] for v in series.values()) for i in range(len(days))]
    ticks = nice_ticks(max(totals) or 1)
    ymax = ticks[-1] or 1
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    slot = pw / len(days)
    bw = min(24, slot * 0.6)

    def y(v: float) -> float:
        return pad_t + ph * (1 - v / ymax)

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="{escape(title)}">']
    for t in ticks:
        parts.append(f'<line class="grid" x1="{pad_l}" x2="{pad_l + pw}" y1="{y(t):.1f}" '
                     f'y2="{y(t):.1f}"/><text class="tick" x="{pad_l - 6}" y="{y(t) + 4:.1f}" '
                     f'text-anchor="end">{fmt(t)}</text>')
    step = max(1, len(days) // 7)
    for i, d in enumerate(days):
        cx = pad_l + slot * (i + 0.5)
        if i % step == 0 or i == len(days) - 1:
            parts.append(f'<text class="tick" x="{cx:.1f}" y="{height - 8}" '
                         f'text-anchor="middle">{d.strftime("%d %b")}</text>')
        # Hover target bigger than the mark (the whole day column), under the segments so
        # each segment still shows its own tooltip.
        tip_all = escape(f"{d.strftime('%a %d %b')}: {fmt(totals[i])} {unit}")
        parts.append(f'<rect class="hit" x="{cx - slot / 2:.1f}" y="{pad_t}" width="{slot:.1f}"'
                     f' height="{ph}" data-tip="{tip_all}"/>')
        base = 0
        segs = [(n, v[i]) for n, v in series.items() if v[i]]
        for j, (name, v) in enumerate(segs):
            top, bottom = y(base + v), y(base)
            h = max(bottom - top - (2 if j < len(segs) - 1 else 0), 1)  # 2px surface gap
            x0 = cx - bw / 2
            cls = f"s{slots.get(name, 1)}"
            tip = escape(f"{d.strftime('%a %d %b')} · {PLATFORM_LABEL.get(name, name)}: "
                         f"{fmt(v)} {unit} (day total {fmt(totals[i])})")
            if j == len(segs) - 1:  # rounded data end on the top segment only
                r = min(4, h, bw / 2)
                path = (f"M{x0:.1f},{bottom:.1f} v-{h - r:.1f} "
                        f"a{r},{r} 0 0 1 {r},-{r} h{bw - 2 * r:.1f} a{r},{r} 0 0 1 {r},{r} "
                        f"v{h - r:.1f} z")
                parts.append(f'<path class="bar {cls}" d="{path}" data-tip="{tip}"/>')
            else:
                parts.append(f'<rect class="bar {cls}" x="{x0:.1f}" y="{bottom - h:.1f}" '
                             f'width="{bw:.1f}" height="{h:.1f}" data-tip="{tip}"/>')
            base += v
    parts.append("</svg>")
    head = "".join(f"<th>{escape(PLATFORM_LABEL.get(n, n))}</th>" for n in series)
    rows = "".join(f"<tr><td>{d.isoformat()}</td>" + "".join(
        f"<td>{fmt(series[n][i])}</td>" for n in series) + f"<td>{fmt(totals[i])}</td></tr>"
        for i, d in enumerate(days))
    table = (f'<details class="table-view"><summary>Table</summary><table><tr><th>Day</th>'
             f"{head}<th>Total</th></tr>{rows}</table></details>")
    legend = _legend(list(series), slots) if len(series) > 1 else ""
    return Markup(f'<div class="viz">{legend}{"".join(parts)}{table}</div>')


SEQ_STEPS = 6  # seq-1 (light) .. seq-6 (dark), defined in app.css for light and dark


def heatmap(cells: dict[tuple[int, int], tuple[float, int]], hours: list[int],
            title: str) -> Markup:
    """Weekday x posting hour, cell shade = average score (one hue, light to dark)."""
    hours = sorted(set(hours) | {h for _, h in cells})
    if not hours:
        return Markup('<p class="muted">No scored posts yet.</p>')
    cw, ch, lw, th = 56, 30, 44, 22
    width, height = lw + cw * len(hours) + 2, th + ch * 7 + 2
    parts = [f'<svg class="chart heat" width="{width}" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="{escape(title)}">']
    for j, h in enumerate(hours):
        parts.append(f'<text class="tick" x="{lw + j * cw + cw / 2}" y="15" '
                     f'text-anchor="middle">{h:02d}:00</text>')
    for wd in range(7):
        yy = th + wd * ch
        parts.append(f'<text class="tick" x="{lw - 6}" y="{yy + 19}" '
                     f'text-anchor="end">{WEEKDAYS[wd]}</text>')
        for j, h in enumerate(hours):
            xx = lw + j * cw
            if (wd, h) in cells:
                value, n = cells[(wd, h)]
                step = min(SEQ_STEPS, 1 + int(value / (100 / SEQ_STEPS)))
                tip = escape(f"{WEEKDAYS[wd]} {h:02d}:00 · average score {value} ({n} posts)")
                ink = "on-dark" if step >= 4 else "on-light"
                parts.append(f'<rect class="cell seq-{step}" x="{xx + 1}" y="{yy + 1}" '
                             f'width="{cw - 2}" height="{ch - 2}" rx="3" data-tip="{tip}"/>'
                             f'<text class="cell-text {ink}" x="{xx + cw / 2}" y="{yy + 19}" '
                             f'text-anchor="middle">{value:g}</text>')
            else:
                parts.append(f'<rect class="cell empty" x="{xx + 1}" y="{yy + 1}" '
                             f'width="{cw - 2}" height="{ch - 2}" rx="3" '
                             f'data-tip="{WEEKDAYS[wd]} {h:02d}:00 · no posts yet"/>')
    parts.append("</svg>")
    scale = "".join(f'<i class="seq-{i}"></i>' for i in range(1, SEQ_STEPS + 1))
    legend = (f'<div class="legend"><span class="tick">score 0</span>'
              f'<span class="ramp">{scale}</span><span class="tick">100</span></div>')
    table = ("<details class='table-view'><summary>Table</summary><table><tr><th>Day</th>"
             "<th>Hour</th><th>Avg score</th><th>Posts</th></tr>" + "".join(
                 f"<tr><td>{WEEKDAYS[wd]}</td><td>{h:02d}:00</td><td>{v:g}</td><td>{n}</td></tr>"
                 for (wd, h), (v, n) in sorted(cells.items())) + "</table></details>")
    return Markup(f'<div class="viz">{legend}{"".join(parts)}{table}</div>')


def interval_chart(arms: list[dict], title: str, width: int = 380) -> Markup:
    """What the learner believes: one row per option, dot = average score, line = the range
    the true average probably lies in (90%). Wide line = not sure yet."""
    label_w, pad_r, row_h = 120, 62, 26
    rows = sorted(arms, key=lambda a: (a["posts"] == 0, -a["mean"]))
    height = len(rows) * row_h + 24
    pw = width - label_w - pad_r

    def x(v: float) -> float:
        return label_w + pw * v / 100

    parts = [f'<svg class="chart fixed" width="{width}" viewBox="0 0 {width} {height}" '
             f'role="img" aria-label="{escape(title)}">']
    for t in (0, 50, 100):
        parts.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="0" '
                     f'y2="{height - 20}"/><text class="tick" x="{x(t):.1f}" y="{height - 6}" '
                     f'text-anchor="middle">{t}</text>')
    for i, a in enumerate(rows):
        cy = 14 + i * row_h
        label = escape(str(a["label"]))
        parts.append(f'<text class="label" x="{label_w - 8}" y="{cy + 4}" '
                     f'text-anchor="end">{label}</text>')
        if a["posts"] == 0:
            parts.append(f'<text class="tick" x="{label_w + 4}" y="{cy + 4}">not tried yet</text>')
            continue
        tip = escape(f"{a['label']}: average {a['mean']} (probably {a['low']}–{a['high']}), "
                     f"{a['posts']} posts")
        parts.append(f'<line class="line s1" x1="{x(a["low"]):.1f}" x2="{x(a["high"]):.1f}" '
                     f'y1="{cy}" y2="{cy}"/>'
                     f'<circle class="dot s1" cx="{x(a["mean"]):.1f}" cy="{cy}" r="5" '
                     f'data-tip="{tip}"/>'
                     f'<rect class="hit" x="{label_w}" y="{cy - row_h / 2}" width="{pw}" '
                     f'height="{row_h}" data-tip="{tip}"/>'
                     f'<text class="label" x="{x(a["high"]) + 8:.1f}" y="{cy + 4}">'
                     f'{a["mean"]:g} <tspan class="tick">({a["posts"]})</tspan></text>')
    parts.append("</svg>")
    table = ("<details class='table-view'><summary>Table</summary><table><tr><th>Option</th>"
             "<th>Average</th><th>Probably between</th><th>Posts</th></tr>" + "".join(
                 f"<tr><td>{escape(str(a['label']))}</td><td>{a['mean']:g}</td>"
                 f"<td>{a['low']:g}–{a['high']:g}</td><td>{a['posts']}</td></tr>"
                 for a in rows) + "</table></details>")
    return Markup(f'<div class="viz">{"".join(parts)}{table}</div>')
