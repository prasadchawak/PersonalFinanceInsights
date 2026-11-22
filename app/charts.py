"""Server-rendered SVG charts. No JavaScript, no external libraries, nothing loaded from outside.
Colours come from CSS classes (.s1 … .s8, .s-other) so light and dark mode are handled in style.css.
Every mark has an SVG <title>, which browsers show as a hover tooltip."""
import math
from html import escape

from markupsafe import Markup

MAX_SLICES = 8  # 7 named + "Other" — categorical colours are never cycled


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def money_short(cents: int) -> str:
    v = abs(cents) / 100
    sign = "-" if cents < 0 else ""
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.1f}M"
    if v >= 10_000:
        return f"{sign}${v / 1000:.0f}k"
    if v >= 1000:
        return f"{sign}${v / 1000:.1f}k"
    return f"{sign}${v:.0f}"


def fold(items: list[dict], limit: int = MAX_SLICES) -> list[dict]:
    """Keep the biggest limit-1 items; merge the rest into 'Other'. Assigns colour classes in fixed order."""
    items = sorted(items, key=lambda x: -x["value"])
    if len(items) > limit:
        rest = items[limit - 1:]
        items = items[:limit - 1] + [{"label": f"Other ({len(rest)})", "value": sum(i["value"] for i in rest),
                                      "other": True, "id": None}]
    for i, it in enumerate(items):
        it["cls"] = "s-other" if it.get("other") else f"s{i + 1}"
    return items


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, t = [], start
    while t <= hi + step * 0.001:
        ticks.append(round(t, 6))
        t += step
    if ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


def donut(items: list[dict], size: int = 280) -> Markup:
    """items: [{label, value(cents>0), cls}] — already folded."""
    total = sum(i["value"] for i in items)
    if total <= 0:
        return Markup("")
    cx = cy = size / 2
    r_out, r_in = size / 2 - 4, size / 2 * 0.58
    parts = [f'<svg class="chart" viewBox="0 0 {size} {size}" role="img" aria-label="Pie chart">']
    angle = -math.pi / 2
    for it in items:
        frac = it["value"] / total
        sweep = frac * 2 * math.pi
        title = f"{escape(it['label'])}: {money(it['value'])} ({frac:.1%})"
        if frac >= 0.9999:
            parts.append(f'<g><title>{title}</title><circle class="{it["cls"]} slice" cx="{cx}" cy="{cy}" '
                         f'r="{(r_out + r_in) / 2}" fill="none" stroke-width="{r_out - r_in}"/></g>')
            break
        a0, a1 = angle, angle + sweep
        large = 1 if sweep > math.pi else 0
        p = lambda r, a: f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}"
        d = (f"M{p(r_out, a0)} A{r_out},{r_out} 0 {large} 1 {p(r_out, a1)} "
             f"L{p(r_in, a1)} A{r_in},{r_in} 0 {large} 0 {p(r_in, a0)} Z")
        parts.append(f'<path class="{it["cls"]} slice" d="{d}"><title>{title}</title></path>')
        angle = a1
    parts.append(f'<text class="donut-total" x="{cx}" y="{cy - 4}" text-anchor="middle">{money_short(total)}</text>')
    parts.append(f'<text class="donut-sub" x="{cx}" y="{cy + 16}" text-anchor="middle">total</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


def hbars(items: list[dict], width: int = 1060, row: int = 30, label_w: int = 230, single_class: str | None = "s1") -> Markup:
    """Horizontal bars, one per item, sorted by caller. Single-series by default (one colour, the title names it)."""
    if not items:
        return Markup("")
    vmax = max(i["value"] for i in items) or 1
    val_w = 90
    bar_w = width - label_w - val_w - 16
    h = row * len(items) + 4
    out = [f'<svg class="chart" viewBox="0 0 {width} {h}" role="img" aria-label="Bar chart">']
    for n, it in enumerate(items):
        y = n * row + 4
        w = max(2, bar_w * it["value"] / vmax)
        cls = single_class or it.get("cls", "s1")
        label = escape(it["label"])
        short = label if len(label) <= 28 else label[:27] + "…"
        out.append(f'<g class="hit"><title>{label}: {money(it["value"])}</title>'
                   f'<rect class="hitbox" x="0" y="{y - 2}" width="{width}" height="{row}"/>'
                   f'<text class="lbl" x="{label_w - 8}" y="{y + row / 2 + 2}" text-anchor="end">{short}</text>'
                   f'<rect class="{cls} bar" x="{label_w}" y="{y + 5}" width="{w:.1f}" height="{row - 12}" rx="4"/>'
                   f'<text class="val" x="{label_w + w + 6:.1f}" y="{y + row / 2 + 2}">{money(it["value"])}</text></g>')
    out.append("</svg>")
    return Markup("".join(out))


def columns(labels: list[str], series: list[dict], width: int = 760, height: int = 300) -> Markup:
    """Grouped vertical bars. series: [{name, cls, values:[cents]}]. Values may be negative."""
    if not labels:
        return Markup("")
    left, right, top, bottom = 64, 12, 12, 34
    pw, ph = width - left - right, height - top - bottom
    vals = [v for s in series for v in s["values"]] + [0]
    ticks = _nice_ticks(min(vals) / 100, max(vals) / 100)
    lo, hi = ticks[0] * 100, ticks[-1] * 100
    y = lambda v: top + ph - (v - lo) / (hi - lo) * ph
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Column chart">']
    for t in ticks:
        ty = y(t * 100)
        out.append(f'<line class="{"axis0" if t == 0 else "grid"}" x1="{left}" x2="{width - right}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
                   f'<text class="tick" x="{left - 6}" y="{ty + 4:.1f}" text-anchor="end">{money_short(int(t * 100))}</text>')
    group = pw / len(labels)
    gap = 2
    bw = min(28, (group * 0.72 - gap * (len(series) - 1)) / len(series))
    every = max(1, math.ceil(len(labels) / 12))
    for i, lab in enumerate(labels):
        gx = left + i * group + (group - (bw * len(series) + gap * (len(series) - 1))) / 2
        for k, s in enumerate(series):
            v = s["values"][i]
            x = gx + k * (bw + gap)
            y0, y1 = y(0), y(v)
            top_y, hgt = min(y0, y1), max(1.5, abs(y1 - y0))
            out.append(f'<rect class="{s["cls"]} bar" x="{x:.1f}" y="{top_y:.1f}" width="{bw:.1f}" height="{hgt:.1f}" rx="3">'
                       f'<title>{escape(lab)} · {escape(s["name"])}: {money(v)}</title></rect>')
        if i % every == 0:
            out.append(f'<text class="tick" x="{left + i * group + group / 2:.1f}" y="{height - 12}" text-anchor="middle">{escape(lab)}</text>')
    out.append("</svg>")
    return Markup("".join(out))


def lines(labels: list[str], series: list[dict], width: int = 760, height: int = 300) -> Markup:
    """Line chart. series: [{name, cls, values:[cents]}]."""
    if not labels:
        return Markup("")
    left, right, top, bottom = 64, 16, 12, 34
    pw, ph = width - left - right, height - top - bottom
    vals = [v for s in series for v in s["values"]] + [0]
    ticks = _nice_ticks(min(vals) / 100, max(vals) / 100)
    lo, hi = ticks[0] * 100, ticks[-1] * 100
    y = lambda v: top + ph - (v - lo) / (hi - lo) * ph
    x = lambda i: left + (pw * i / (len(labels) - 1) if len(labels) > 1 else pw / 2)
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Line chart">']
    for t in ticks:
        ty = y(t * 100)
        out.append(f'<line class="{"axis0" if t == 0 else "grid"}" x1="{left}" x2="{width - right}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
                   f'<text class="tick" x="{left - 6}" y="{ty + 4:.1f}" text-anchor="end">{money_short(int(t * 100))}</text>')
    every = max(1, math.ceil(len(labels) / 12))
    for i, lab in enumerate(labels):
        if i % every == 0:
            out.append(f'<text class="tick" x="{x(i):.1f}" y="{height - 12}" text-anchor="middle">{escape(lab)}</text>')
    for s in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s["values"]))
        out.append(f'<polyline class="{s["cls"]} line" points="{pts}"/>')
        for i, v in enumerate(s["values"]):
            out.append(f'<circle class="{s["cls"]} dot" cx="{x(i):.1f}" cy="{y(v):.1f}" r="4">'
                       f'<title>{escape(labels[i])} · {escape(s["name"])}: {money(v)}</title></circle>')
    out.append("</svg>")
    return Markup("".join(out))


def progress(spent: int, budget: int, width: int = 220, height: int = 12) -> Markup:
    """Budget bar. Over budget is shown in the reserved 'critical' colour with the overage, never colour alone."""
    pct = spent / budget if budget else 0
    w = max(0, min(1, pct)) * width
    cls = "pb-over" if pct > 1 else ("pb-warn" if pct >= 0.9 else "pb-ok")
    return Markup(f'<svg class="progress" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
                  f'aria-label="{pct:.0%} of budget"><title>{money(spent)} of {money(budget)} ({pct:.0%})</title>'
                  f'<rect class="pb-track" x="0" y="0" width="{width}" height="{height}" rx="{height / 2}"/>'
                  f'<rect class="{cls}" x="0" y="0" width="{w:.1f}" height="{height}" rx="{height / 2}"/></svg>')
