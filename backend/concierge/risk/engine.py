"""Runtime scorer: score(token, severity) -> RiskResult (design D1, D3, D7, D8, D9).

Pure lookup plus a few multiplications. Everything heavy happens in build.py.
"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from pathlib import Path

from .calibrate import band_for
from .config import LABEL, ROUND_SCORE, RiskConfig
from .confidence import FALLBACK_MESSAGES, downgrade, reason_text
from .explain import contributions, top_factors
from .features import raw_score
from .schema import RiskResult
from .tokens import TOKEN_RE

audit_log = logging.getLogger("concierge.risk.audit")


class SeverityError(ValueError):
    """Severity preset unknown or multiplier outside the allowed range."""


class RiskEngine:
    def __init__(self, rows: Mapping[str, dict], calibration: Mapping, cfg: RiskConfig | None = None):
        self._rows = rows
        self._cal = calibration
        self.cfg = cfg or RiskConfig.from_dict(calibration["config"])
        self._presets: dict[str, float] = dict(calibration["presets"])
        self._max_observed: float = float(calibration["max_observed_multiplier"])
        self._n_storms: int = int(calibration["n_storms"])
        self.config_hash: str = calibration["config_hash"]

    @classmethod
    def from_artifacts(cls, directory: str | Path) -> "RiskEngine":
        d = Path(directory)
        calibration = json.loads((d / "calibration.json").read_text())
        rows: dict[str, dict] = {}
        with open(d / "premise_risk.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                rows[r.pop("t")] = r
        return cls(rows, calibration)

    # ---- severity -------------------------------------------------------------------------------------------
    def resolve_severity(self, severity: str | float | int) -> dict:
        if isinstance(severity, bool):
            raise SeverityError("severity must be a preset name or a number")
        if isinstance(severity, str):
            if severity not in self._presets:
                raise SeverityError(f"unknown severity preset; choose one of {sorted(self._presets)}")
            preset, m = severity, self._presets[severity]
        elif isinstance(severity, (int, float)):
            m = float(severity)
            if not math.isfinite(m) or not (self.cfg.severity_min <= m <= self.cfg.severity_max):
                raise SeverityError(
                    f"severity multiplier must be between {self.cfg.severity_min} and {self.cfg.severity_max}")
            preset = "custom"
        else:
            raise SeverityError("severity must be a preset name or a number")
        return {"preset": preset, "multiplier": m, "extrapolated": m > self._max_observed + 1e-9}

    # ---- scoring --------------------------------------------------------------------------------------------
    def score(self, token: str, severity: str | float | int = "typical") -> RiskResult:
        sev = self.resolve_severity(severity)  # validate first; no score on bad input
        sev_out = {"preset": sev["preset"], "multiplier": round(sev["multiplier"], 2),
                   "extrapolated": sev["extrapolated"]}

        row = self._rows.get(token) if isinstance(token, str) and TOKEN_RE.match(token) else None
        if row is None:
            return self._fallback(None, "unknown_premise", sev_out)
        if row["st"] != "ok":
            return self._fallback(token, row["why"], sev_out)
        return self._ok(token, row, sev)

    def _fallback(self, token: str | None, reason: str, sev_out: dict) -> RiskResult:
        result = RiskResult(
            config_hash=self.config_hash,
            status="insufficient_data",
            premise_token=token,
            severity=sev_out,
            fallback={"reason_code": reason, "message": FALLBACK_MESSAGES[reason]},
        )
        self._audit(result, [reason])
        return result

    def _ok(self, token: str, row: dict, sev: dict) -> RiskResult:
        cfg, m = self.cfg, sev["multiplier"]
        score = raw_score(row["Re"], row["svc"], row["P"], row["H"], m, cfg)
        band = band_for(score, self._cal["thresholds"])

        level = row["c"]
        assumptions = [
            "Storm strength is a scenario you choose, not a weather forecast.",
            f"Risk comes from damage reports on your line in {self._n_storms} past storms.",
            "How much your service type and place on the line matter is an assumption, not something we measured.",
        ]
        missing = [
            "No weather data is used.",
            "Damage reports show where repairs were needed, not which homes lost power.",
        ]
        if sev["extrapolated"]:
            level = downgrade(level)
            assumptions.append("This scenario is stronger than any storm in our history, so the rating is an extrapolation.")
        if row["svc"] == "Unknown":
            missing.append("Your service type (overhead or underground) is not on file, so we assumed overhead.")

        contribs = contributions(
            r_eff=row["Re"], service=row["svc"], protective=row["P"], hops=row["H"], multiplier=m,
            ref=self._cal["reference"], cfg=cfg)
        factors = top_factors(contribs, rate=row["R"], protective=row["P"], cfg=cfg)

        result = RiskResult(
            config_hash=self.config_hash,
            status="ok",
            premise_token=token,
            label=LABEL,
            severity={"preset": sev["preset"], "multiplier": round(m, 2), "extrapolated": sev["extrapolated"]},
            band=band,
            score=round(score, ROUND_SCORE),
            confidence={"level": level, "records": row["n"], "reason": reason_text(row["n"], self._n_storms)},
            band_evidence=self._cal["band_evidence"].get(band),
            top_factors=factors,
            signals={"circuit_reports_per_1k": round(row["R"], 1), "protective_devices": row["P"]},
            assumptions=assumptions,
            missing_data=missing,
            could_change=[
                "A storm that hits your area harder or softer than past storms.",
                "Tree trimming or line upgrades done since the last storm.",
                "New damage reports on your line as a storm unfolds.",
            ],
        )
        self._audit(result, [f["id"] for f in factors])
        return result

    def _audit(self, r: RiskResult, reasons: list[str]) -> None:
        audit_log.info(json.dumps({
            "token": r.premise_token, "status": r.status, "severity": (r.severity or {}).get("preset"),
            "multiplier": (r.severity or {}).get("multiplier"), "band": r.band,
            "confidence": (r.confidence or {}).get("level"), "reasons": reasons, "config_hash": r.config_hash,
        }, sort_keys=True))
