"""Three plots as plain SVG, written with the standard library only (design D7).

Points are anonymous and rounded; text is fixed wording plus numbers. Each file has a <title> and <desc> for screen readers.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from xml.sax.saxutils import escape

W, H = 640, 420
ML, MR, MT, MB = 64, 24, 48, 56
PW, PH = W - ML - MR, H - MT - MB
INK, GRID, MODEL, SIZE, ORACLE, SHADE = "#1f2933", "#d5dbe1", "#0b5cab", "#7b8794", "#c2570c", "#f3d9d9"


def _open(title: str, desc: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
        f'aria-labelledby="t d" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="{INK}">',
        f'<title id="t">{escape(title)}</title>', f'<desc id="d">{escape(desc)}</desc>',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{ML}" y="24" font-size="15" font-weight="bold">{escape(title)}</text>',
    ]


def _text(x: float, y: float, s: str, anchor: str = "start", size: int = 12, rotate: bool = False, fill: str = INK) -> str:
    tr = f' transform="rotate(-90 {x:.1f} {y:.1f})"' if rotate else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" fill="{fill}"{tr}>{escape(s)}</text>'


def _frame(y_label: str, x_label: str, y_ticks: Sequence[tuple[float, str]], ymap) -> list[str]:
    out = [f'<line x1="{ML}" y1="{MT}" x2="{ML}" y2="{MT + PH}" stroke="{INK}"/>',
           f'<line x1="{ML}" y1="{MT + PH}" x2="{ML + PW}" y2="{MT + PH}" stroke="{INK}"/>']
    for v, label in y_ticks:
        y = ymap(v)
        out.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{ML + PW}" y2="{y:.1f}" stroke="{GRID}"/>')
        out.append(_text(ML - 6, y + 4, label, "end"))
    out.append(_text(16, MT + PH / 2, y_label, "middle", rotate=True))
    out.append(_text(ML + PW / 2, H - 12, x_label, "middle"))
    return out


def _note(msg: str) -> list[str]:
    return [_text(ML + PW / 2, MT + PH / 2, msg, "middle", 14)]


def risk_bands_svg(risk: Mapping) -> str:
    """Hit rate per band with 95% ranges, the size baseline, and the base-rate line."""
    ymap = lambda v: MT + PH * (1 - v)  # noqa: E731
    out = _open("Share of homes on lines with significant damage, by risk band",
                "Hit rate for Low, Medium and High bands in the Feb 2025 storm, with 95% ranges, a circuit-size baseline "
                "and the base rate.")
    out += _frame("Share of homes on damaged lines", "Risk band (calibrated on Sep 2024)",
                  [(i / 5, f"{i * 20}%") for i in range(6)], ymap)
    base = risk["base_rate"]["premise_weighted"]
    out.append(f'<line x1="{ML}" y1="{ymap(base):.1f}" x2="{ML + PW}" y2="{ymap(base):.1f}" stroke="{INK}" stroke-dasharray="6 4"/>')
    out.append(_text(ML + PW - 4, ymap(base) - 5, f"base rate {round(100 * base)}%", "end"))
    slot = PW / 3
    for i, name in enumerate(("Low", "Medium", "High")):
        cx = ML + slot * (i + 0.5)
        out.append(_text(cx, MT + PH + 18, name, "middle"))
        for series, bands, dx, color, hollow in (("model", risk["bands"], -18, MODEL, False),
                                                  ("size", risk["size_baseline"]["bands"], 18, SIZE, True)):
            b = bands[name]
            x = cx + dx
            if b.get("suppressed") or b["hit_rate"] is None:
                out.append(_text(x, MT + PH - 8, "hidden", "middle", 10))
                continue
            lo, hi, p = b["ci95"][0], b["ci95"][1], b["hit_rate"]
            out.append(f'<line x1="{x:.1f}" y1="{ymap(lo):.1f}" x2="{x:.1f}" y2="{ymap(hi):.1f}" stroke="{color}" stroke-width="2"/>')
            for e in (lo, hi):
                out.append(f'<line x1="{x - 5:.1f}" y1="{ymap(e):.1f}" x2="{x + 5:.1f}" y2="{ymap(e):.1f}" stroke="{color}" stroke-width="2"/>')
            fill = "#ffffff" if hollow else color
            out.append(f'<circle cx="{x:.1f}" cy="{ymap(p):.1f}" r="6" fill="{fill}" stroke="{color}" stroke-width="2"/>')
    out.append(f'<circle cx="{ML + 12}" cy="{H - 30}" r="5" fill="{MODEL}"/>' + _text(ML + 22, H - 26, "risk band", size=11))
    out.append(f'<circle cx="{ML + 110}" cy="{H - 30}" r="5" fill="#ffffff" stroke="{SIZE}" stroke-width="2"/>'
               + _text(ML + 120, H - 26, "same groups ranked by circuit size", size=11))
    out.append("</svg>")
    return "\n".join(out)


def restoration_scatter_svg(runs: Mapping[str, Mapping] | None) -> str:
    """Predicted P50 days (x) against observed median days (y). Identity line and a plus or minus one day band."""
    out = _open("Predicted and observed repair time per circuit",
                "Predicted P50 repair days against observed median repair days for Feb 2025, one anonymous point per circuit, "
                "with the identity line and a band of plus or minus one day.")
    pts = {name: r["points"] for name, r in (runs or {}).items() if r and r.get("points")}
    if not pts:
        out += _note("Not run: the restoration engine was not available.")
        out.append("</svg>")
        return "\n".join(out)
    top = max([3.0] + [max(p["predicted_p50_days"], p["observed_median_days"]) for ps in pts.values() for p in ps])
    top = math.ceil(top + 0.5)
    xmap = lambda v: ML + PW * v / top  # noqa: E731
    ymap = lambda v: MT + PH * (1 - v / top)  # noqa: E731
    step = max(1, top // 6)
    out += _frame("Observed median repair days", "Predicted P50 days", [(v, str(v)) for v in range(0, top + 1, step)], ymap)
    band = (f"{xmap(0):.1f},{ymap(1):.1f} {xmap(top - 1):.1f},{ymap(top):.1f} {xmap(top):.1f},{ymap(top):.1f} "
            f"{xmap(top):.1f},{ymap(top - 1):.1f} {xmap(1):.1f},{ymap(0):.1f} {xmap(0):.1f},{ymap(0):.1f}")
    out.append(f'<polygon points="{band}" fill="#e8f1fb" stroke="none"/>')
    out.append(f'<line x1="{xmap(0):.1f}" y1="{ymap(0):.1f}" x2="{xmap(top):.1f}" y2="{ymap(top):.1f}" stroke="{INK}" stroke-dasharray="6 4"/>')
    for v in range(0, top + 1, step):
        out.append(_text(xmap(v), MT + PH + 18, str(v), "middle"))
    for name, color in (("forecast", MODEL), ("oracle", ORACLE)):
        for p in pts.get(name, []):
            r = 3 + 1.2 * math.sqrt(p["records"])
            out.append(f'<circle cx="{xmap(p["predicted_p50_days"]):.1f}" cy="{ymap(p["observed_median_days"]):.1f}" r="{r:.1f}" '
                       f'fill="{color}" fill-opacity="0.45" stroke="{color}"/>')
    out.append(f'<circle cx="{ML + 12}" cy="{H - 30}" r="5" fill="{MODEL}"/>' + _text(ML + 22, H - 26, "forecast damage", size=11))
    out.append(f'<circle cx="{ML + 130}" cy="{H - 30}" r="5" fill="{ORACLE}"/>' + _text(ML + 140, H - 26, "oracle damage (actual counts)", size=11))
    out.append(_text(ML + PW, H - 26, "marker size = records; shaded = within 1 day", "end", 11))
    out.append("</svg>")
    return "\n".join(out)


def repair_hist_svg(hist: Mapping | None) -> str:
    """Observed repair-day shares for Feb 2025, the backlog region shaded, and the predicted P50 to P90 band."""
    out = _open("How long Feb 2025 repairs took",
                "Share of repaired damage records by whole days to repair, with the backlog region shaded and the predicted "
                "P50 to P90 range.")
    bins = [b for b in (hist or {}).get("bins", [])]
    total = sum(b["count"] or 0 for b in bins)
    if not bins or total == 0:
        out += _note("No repair records to show.")
        out.append("</svg>")
        return "\n".join(out)
    shares = [(b["count"] or 0) / total for b in bins]
    ymax = min(1.0, math.ceil(max(shares) * 10) / 10 + 0.1)
    ymap = lambda v: MT + PH * (1 - v / ymax)  # noqa: E731
    out += _frame("Share of repaired records", "Days from report to repair",
                  [(i / 10, f"{i * 10}%") for i in range(0, int(round(ymax * 10)) + 1)], ymap)
    bw = PW / len(bins)
    start = (hist or {}).get("backlog_from_day", 10 ** 9)

    def xpos(day: float) -> float:
        for i, b in enumerate(bins):
            hi = b["to_day"]
            if day >= b["from_day"] and (hi is None or day <= hi + 1):
                width = (hi + 1 - b["from_day"]) if hi is not None else 1
                return ML + bw * (i + min(1.0, max(0.0, (day - b["from_day"]) / max(width, 1))))
        return ML + PW

    # Shade from the first bin that holds any backlog day (a merged bin that straddles the cutoff is shaded whole).
    first_backlog = next((i for i, b in enumerate(bins) if b["to_day"] is None or b["to_day"] >= start), None)
    if first_backlog is not None:
        x0 = ML + bw * first_backlog
        out.append(f'<rect x="{x0:.1f}" y="{MT}" width="{ML + PW - x0:.1f}" height="{PH}" fill="{SHADE}"/>')
        out.append(_text(x0 + 4, MT + 14, "backlog (not scored)", size=11))
    band = (hist or {}).get("predicted_p50_to_p90_days")
    if band:
        a, b = xpos(band[0]), xpos(band[1])
        out.append(f'<rect x="{a:.1f}" y="{MT}" width="{max(b - a, 2):.1f}" height="{PH}" fill="{MODEL}" fill-opacity="0.18" stroke="{MODEL}" stroke-dasharray="4 3"/>')
        label = "predicted P50 to P90 (beyond the chart)" if band[0] >= max(
            (b["from_day"] for b in bins if b["to_day"] is None), default=10 ** 9) else "predicted P50 to P90"
        if a > ML + PW * 0.7:                         # near the right edge: write the label to the left of the band
            out.append(_text(a - 4, MT + 30, label, "end", 11, fill=MODEL))
        else:
            out.append(_text(a + 4, MT + 30, label, size=11, fill=MODEL))
    for i, (b, s) in enumerate(zip(bins, shares)):
        x = ML + bw * i
        out.append(f'<rect x="{x + 2:.1f}" y="{ymap(s):.1f}" width="{bw - 4:.1f}" height="{MT + PH - ymap(s):.1f}" fill="{INK}" fill-opacity="0.75"/>')
        label = f"{b['from_day']}+" if b["to_day"] is None else (str(b["from_day"]) if b["to_day"] == b["from_day"]
                                                                 else f"{b['from_day']}-{b['to_day']}")
        out.append(_text(x + bw / 2, MT + PH + 18, label, "middle", 11))
    out.append("</svg>")
    return "\n".join(out)
