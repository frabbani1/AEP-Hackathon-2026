"""Wires the engines together. One class, no AWS calls except through the injected rows/clients."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..alerts import rules as alert_rules
from ..guidance import prepare
from ..messages import compose as msg
from ..restoration import model as rest
from ..risk.confidence import FALLBACK_MESSAGES, downgrade
from ..risk.engine import RiskEngine
from ..risk.schema import WhitelistError, _scan_strings
from ..risk.tokens import TOKEN_RE


class BadRequest(ValueError):
    """The caller sent something we won't accept. The message is safe to return."""


def _int(v: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
        raise BadRequest(f"{name} must be a whole number from {lo} to {hi}")
    return v


def _only(body: Mapping, allowed: set[str]) -> None:
    extra = set(body) - allowed
    if extra:
        raise BadRequest(f"unknown fields: {sorted(extra)}")


class Concierge:
    def __init__(self, rows, calibration: Mapping, labor: Mapping,
                 phraser: Callable[[Mapping], str] | None = None, model_id: str | None = None):
        self.rows, self.labor = rows, labor
        self.engine = RiskEngine(rows, calibration)
        self.phraser, self.model_id = phraser, model_id

    # ---- input parsing ----------------------------------------------------------------------------------------
    @staticmethod
    def token(body: Mapping) -> str:
        t = body.get("token")
        if not isinstance(t, str) or not TOKEN_RE.match(t):
            raise BadRequest("token must look like p_ followed by 16 lowercase hex characters")
        return t

    def severity(self, body: Mapping) -> Any:
        s = body.get("severity", "typical")
        try:
            self.engine.resolve_severity(s)
        except ValueError as e:
            raise BadRequest(str(e)) from None
        return s

    @staticmethod
    def needs(body: Mapping) -> dict[str, bool]:
        try:
            return prepare.clean_needs(body.get("needs"))
        except (ValueError, AttributeError, TypeError) as e:
            raise BadRequest(str(e)) from None

    @staticmethod
    def scenario(body: Mapping) -> dict:
        crews = _int(body.get("crews", rest.DEFAULT_CREWS), "crews", 1, rest.MAX_CREWS)
        access = body.get("access", "normal")
        if access not in rest.ACCESS_LEVELS:
            raise BadRequest(f"access must be one of {list(rest.ACCESS_LEVELS)}")
        damage = body.get("damage")
        items = None
        if damage is not None:
            if not isinstance(damage, list) or not (1 <= len(damage) <= 10):
                raise BadRequest("damage must be a list of 1 to 10 items")
            items = []
            for d in damage:
                if not isinstance(d, dict) or set(d) != {"asset_type", "qty"}:
                    raise BadRequest("each damage item needs exactly asset_type and qty")
                if d["asset_type"] not in rest.ASSET_LEVEL:
                    raise BadRequest(f"asset_type must be one of {sorted(rest.ASSET_LEVEL)}")
                items.append((d["asset_type"], _int(d["qty"], "qty", 1, rest.MAX_JOBS_PER_ASSET)))
        return {"crews": crews, "access": access, "damage": items}

    # ---- engines ----------------------------------------------------------------------------------------------
    def risk(self, token: str, severity: Any) -> dict:
        return self.engine.score(token, severity).to_dict()

    def restoration(self, token: str, severity: Any, scenario: Mapping) -> dict:
        sev = self.engine.resolve_severity(severity)
        sev_out = {"preset": sev["preset"], "multiplier": round(sev["multiplier"], 2), "extrapolated": sev["extrapolated"]}
        row = self.rows.get(token) if TOKEN_RE.match(token) else None
        fallback, level = None, None
        if row is None:
            fallback = {"reason_code": "unknown_premise", "message": FALLBACK_MESSAGES["unknown_premise"]}
        elif row["st"] != "ok":
            fallback = {"reason_code": row["why"], "message": FALLBACK_MESSAGES[row["why"]]}
        else:
            level = row["c"]
            if sev["extrapolated"]:
                level = downgrade(level)
        try:
            return rest.estimate(token=token if row else None, row=row, severity=sev_out, fallback=fallback,
                                 base_confidence=level, labor=self.labor, **scenario)
        except rest.RestorationError as e:
            raise BadRequest(str(e)) from None

    def guidance(self, risk: Mapping, restoration: Mapping, needs: Mapping) -> dict:
        est = restoration.get("estimate")
        return prepare.build(band=risk.get("band"), typical_hours=est["typical_hours"] if est else None,
                             estimate_display=est["display"] if est else None, needs=needs,
                             insufficient_data=risk["status"] != "ok")

    def alerts(self, risk: Mapping, restoration: Mapping, needs: Mapping, body: Mapping) -> dict:
        prior = body.get("prior_typical_hours")
        if prior is not None:
            prior = _int(prior, "prior_typical_hours", 1, 2000)
        channels = body.get("channels")
        if channels is not None and (not isinstance(channels, list) or not all(isinstance(c, str) for c in channels)):
            raise BadRequest("channels must be a list of text values")
        try:
            return alert_rules.evaluate(band=risk.get("band"), insufficient_data=risk["status"] != "ok",
                                        estimate=restoration.get("estimate"), needs=needs, channels=channels,
                                        prior_typical_hours=prior)
        except ValueError as e:
            raise BadRequest(str(e)) from None

    def message(self, token: str, severity: Any, body: Mapping, risk: Mapping, restoration: Mapping,
                needs: Mapping) -> dict:
        mtype, channel = body.get("message_type"), body.get("channel", "sms")
        use_llm = body.get("use_llm", True)
        if not isinstance(use_llm, bool):
            raise BadRequest("use_llm must be true or false")
        prior_display = None
        prior = body.get("prior_estimate")
        if prior is not None:
            if not isinstance(prior, dict) or set(prior) != {"low_hours", "high_hours"}:
                raise BadRequest("prior_estimate needs low_hours and high_hours")
            lo, hi = _int(prior["low_hours"], "low_hours", 1, 2000), _int(prior["high_hours"], "high_hours", 1, 2000)
            if lo > hi:
                raise BadRequest("prior_estimate low_hours cannot be above high_hours")
            prior_display = rest.display(lo, hi)
        sev = risk["severity"]
        try:
            card = msg.fact_card(
                message_type=mtype, channel=channel, band=risk.get("band"), scenario=sev["preset"],
                estimate=restoration.get("estimate"), prior_display=prior_display,
                confidence=(risk.get("confidence") or {}).get("level"), has_need=any(needs.values()),
                can_rate=risk["status"] == "ok")
        except ValueError as e:
            raise BadRequest(str(e)) from None
        return msg.compose(card, self.phraser, use_llm=use_llm, model_id=self.model_id)

    # ---- routes -----------------------------------------------------------------------------------------------
    def handle(self, route: str, body: Mapping) -> dict:
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        common = {"token", "severity"}
        if route == "risk":
            _only(body, common)
            out = self.risk(self.token(body), self.severity(body))
        elif route == "restoration":
            _only(body, common | {"crews", "access", "damage"})
            out = self.restoration(self.token(body), self.severity(body), self.scenario(body))
        elif route in ("guidance", "alerts", "summary", "messages"):
            allowed = common | {"crews", "access", "damage", "needs"}
            if route in ("alerts", "summary"):
                allowed |= {"channels", "prior_typical_hours"}
            if route == "messages":
                allowed |= {"message_type", "channel", "use_llm", "prior_estimate"}
            _only(body, allowed)
            token, severity, needs = self.token(body), self.severity(body), self.needs(body)
            risk = self.risk(token, severity)
            restoration = self.restoration(token, severity, self.scenario(body))
            if route == "guidance":
                out = {"risk_band": risk.get("band"), "guidance": self.guidance(risk, restoration, needs)}
            elif route == "alerts":
                out = self.alerts(risk, restoration, needs, body)
            elif route == "messages":
                out = self.message(token, severity, body, risk, restoration, needs)
            else:
                out = {"risk": risk, "restoration": restoration,
                       "guidance": self.guidance(risk, restoration, needs),
                       "alerts": self.alerts(risk, restoration, needs, body),
                       "disclosure": "Risk and restoration figures are model estimates from past storms and "
                                     "simulated damage. They are not weather forecasts or confirmed utility information."}
        else:
            raise KeyError(route)
        try:
            _scan_strings(out, "response")
        except WhitelistError:
            raise RuntimeError("response blocked: it looked like it contained an internal identifier") from None
        return out
