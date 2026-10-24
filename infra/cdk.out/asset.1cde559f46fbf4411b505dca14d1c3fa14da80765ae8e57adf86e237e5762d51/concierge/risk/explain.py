"""Top-factor attribution in log space with plain-language templates (design D8)."""
from __future__ import annotations

import math
from collections.abc import Mapping

from .config import DEFAULT_CONFIG, ROUND_RATE, ROUND_WEIGHT, RiskConfig

FACTOR_IDS = ("hist", "type", "dev", "hops", "sev")


def contributions(
    *, r_eff: float, service: str, protective: float, hops: float, multiplier: float,
    ref: Mapping[str, float], cfg: RiskConfig = DEFAULT_CONFIG,
) -> dict[str, float]:
    """Log-contribution of each factor against the reference premise.

    ln S = ln R_eff + ln T + ln E + ln m, so the five contributions sum to ln S - ln S_ref
    (when R_eff is above the log floor). The device/hop split of ln E is a 2-player Shapley value.
    """
    ref_p, ref_h = ref["protective"], ref["hops"]
    ln_e = lambda p, h: math.log(cfg.exposure(p, h))  # noqa: E731

    c_hist = math.log(max(r_eff, cfg.hist_eps) / max(ref["r_eff"], cfg.hist_eps))
    c_type = math.log(cfg.type_factor(service) / ref.get("type", 1.0))
    c_dev = 0.5 * (ln_e(protective, ref_h) - ln_e(ref_p, ref_h)) + 0.5 * (
        ln_e(protective, hops) - ln_e(ref_p, hops)
    )
    c_total_e = ln_e(protective, hops) - ln_e(ref_p, ref_h)
    c_hops = c_total_e - c_dev
    c_sev = math.log(multiplier / ref.get("multiplier", 1.0))
    return {"hist": c_hist, "type": c_type, "dev": c_dev, "hops": c_hops, "sev": c_sev}


def _device_phrase(p: int) -> str:
    return f"{p} protective {'device' if p == 1 else 'devices'}"


def factor_text(factor: str, direction: str, *, rate: float, protective: int) -> str:
    """Fixed sentence per factor. Numbers come only from the engine's own signals."""
    r = round(rate, ROUND_RATE)
    if factor == "hist":
        if direction == "lowers" and rate == 0:
            return "Your line had no storm damage reports in the past storms we use."
        if direction == "raises":
            return (f"Your line had more storm damage than most lines in past storms "
                    f"(about {r} reports per 1,000 homes).")
        return (f"Your line had less storm damage than most lines in past storms "
                f"(about {r} reports per 1,000 homes).")
    if factor == "type":
        if direction == "lowers":
            return "Your service is underground, which is better protected from wind and trees."
        return "Your service is overhead, which is more exposed to wind and trees."
    if factor == "dev":
        if direction == "raises":
            return (f"Power reaches you through {_device_phrase(protective)}; "
                    "more devices means more places a fault can cut service.")
        return (f"Power reaches you through only {_device_phrase(protective)}, "
                "fewer than most homes.")
    if factor == "hops":
        if direction == "raises":
            return "You are farther down the line from the power source than most homes."
        return "You are closer to the power source than most homes."
    if factor == "sev":
        if direction == "raises":
            return "The storm scenario you picked is stronger than a typical past storm."
        return "The storm scenario you picked is milder than a typical past storm."
    raise ValueError(f"unknown factor {factor}")


def top_factors(contribs: Mapping[str, float], *, rate: float, protective: int,
                cfg: RiskConfig = DEFAULT_CONFIG) -> list[dict]:
    """Rank by |contribution|, drop negligible ones, keep top_n. Weight = share of total absolute contribution."""
    total_abs = sum(abs(c) for c in contribs.values()) or 1.0
    ranked = sorted(
        ((f, c) for f, c in contribs.items() if abs(c) >= cfg.factor_min_abs),
        key=lambda x: (-abs(x[1]), FACTOR_IDS.index(x[0])),
    )[: cfg.top_n]
    out = []
    for f, c in ranked:
        direction = "raises" if c > 0 else "lowers"
        out.append({
            "id": f,
            "direction": direction,
            "weight": round(abs(c) / total_abs, ROUND_WEIGHT),
            "text": factor_text(f, direction, rate=rate, protective=protective),
        })
    return out
