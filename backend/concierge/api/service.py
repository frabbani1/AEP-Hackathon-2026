"""Wires the engines together. One class, no AWS calls except through the injected rows/clients."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from ..alerts import audit as alert_audit
from ..alerts import enroll as alert_enroll
from ..alerts import evaluate as alert_eval
from ..alerts.delivery import STATUS_NOT_SENT, MockDelivery
from ..alerts.stores import AlertStore, Clock, MemoryStore, utc_now
from ..alerts.table import load_table
from ..assistant import factcard
from ..assistant.compose import Assistant
from ..backtest import evidence as backtest_evidence
from ..guidance import prepare
from ..messages import compose as msg
from ..messages.templates import CHANNEL_LIMITS
from ..restoration import config as rest_config
from ..restoration import model as rest
from ..restoration.tree import RestorationTree
from ..risk.confidence import FALLBACK_MESSAGES, downgrade
from ..risk.engine import RiskEngine
from ..risk.schema import WhitelistError, _scan_strings
from ..risk.tokens import TOKEN_RE


log = logging.getLogger("concierge.api")

EVIDENCE_RETRY_SECONDS = 60   # how often a missing backtest report is looked for again


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
                 phraser: Callable[[Mapping], str] | None = None, model_id: str | None = None,
                 alert_store: AlertStore | None = None, clock: Clock = utc_now,
                 evidence_loader: Callable[[], Mapping | None] | None = None,
                 tree: RestorationTree | None = None, restoration_engine: str | None = None,
                 assistant: Assistant | None = None):
        self.rows, self.labor = rows, labor
        self.assistant = assistant if assistant is not None else Assistant()      # no model unless one is passed in
        self._evidence_loader = evidence_loader
        self._evidence: Mapping | None = None
        self._evidence_checked: float | None = None
        self.engine = RiskEngine(rows, calibration)
        self.phraser, self.model_id = phraser, model_id
        self.clock = clock
        self.alert_table = load_table()
        self.alert_store: AlertStore = alert_store if alert_store is not None else MemoryStore(clock)
        self.delivery = MockDelivery(self.alert_store, clock)
        self.tree = tree
        # "v1" or "v2". Read once, so a bad RESTORATION_ENGINE value fails at start-up instead of on a request.
        self.restoration_engine = restoration_engine or rest_config.engine_from_env()
        if self.restoration_engine not in rest_config.ENGINES:
            raise ValueError(f"restoration_engine must be one of {list(rest_config.ENGINES)}")

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
        damage, damage_count = body.get("damage"), body.get("damage_count")
        if damage is not None and damage_count is not None:
            raise BadRequest("send either damage_count or damage, not both")
        if damage_count is not None:
            damage_count = _int(damage_count, "damage_count", rest_config.DAMAGE_COUNT_MIN,
                                rest_config.DAMAGE_COUNT_MAX)
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
        return {"crews": crews, "access": access, "damage": items, "damage_count": damage_count}

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
                                 base_confidence=level, labor=self.labor, tree=self.tree,
                                 engine=self.restoration_engine, **scenario)
        except rest.RestorationError as e:
            raise BadRequest(str(e)) from None

    def guidance(self, risk: Mapping, restoration: Mapping, needs: Mapping) -> dict:
        est = restoration.get("estimate")
        return prepare.build(band=risk.get("band"), typical_hours=est["typical_hours"] if est else None,
                             estimate_display=est["display"] if est else None, needs=needs,
                             insufficient_data=risk["status"] != "ok")

    @staticmethod
    def _opt_int(body: Mapping, name: str, lo: int, hi: int) -> int | None:
        v = body.get(name)
        return None if v is None else _int(v, name, lo, hi)

    def _decide(self, token: str, risk: Mapping, restoration: Mapping, needs: Mapping, body: Mapping,
                enrollment: Mapping | None) -> alert_eval.Decision:
        channels = body.get("channels")
        if channels is not None and (not isinstance(channels, list) or not all(isinstance(c, str) for c in channels)):
            raise BadRequest("channels must be a list of text values")
        if channels is None:
            channels = alert_enroll.channel_types(enrollment)
        opt_in = body.get("opt_in", False)
        if not isinstance(opt_in, bool):
            raise BadRequest("opt_in must be true or false")
        try:
            return alert_eval.evaluate(
                self.alert_table, band=risk.get("band"), insufficient_data=risk["status"] != "ok",
                estimate=restoration.get("estimate"), needs=needs, channels=channels,
                prior_typical_hours=self._opt_int(body, "prior_typical_hours", 1, 2000),
                hours_until_storm=self._opt_int(body, "hours_until_storm", 0, alert_eval.MAX_HOURS_UNTIL_STORM),
                hours_since_last_update=self._opt_int(body, "hours_since_last_update", 0,
                                                      alert_eval.MAX_HOURS_SINCE_UPDATE),
                opt_in=opt_in or alert_enroll.opted_in_pre_storm(enrollment))
        except ValueError as e:
            raise BadRequest(str(e)) from None

    def _audit(self, token: str, route: str, decision: alert_eval.Decision, decision_id: str,
               extra_outputs: Mapping | None = None) -> None:
        """Write the audit entry. A failed write is logged but never blocks the customer's answer."""
        try:
            outputs = alert_audit.decision_outputs(decision.response)
            outputs.update(extra_outputs or {})
            entry = alert_audit.build_entry(
                decision_id=decision_id, route=route, token=token, engine_version=alert_eval.ENGINE_VERSION,
                table_hash=self.alert_table.hash, inputs=decision.inputs, rules_fired=decision.rules_fired,
                outputs=outputs, clock=self.clock)
            alert_audit.record(self.alert_store, entry)
        except Exception:  # noqa: BLE001
            log.exception("audit write failed")

    def alerts(self, token: str, risk: Mapping, restoration: Mapping, needs: Mapping, body: Mapping,
               route: str = "alerts") -> dict:
        enrollment = self.alert_store.get_enrollment(token)
        decision = self._decide(token, risk, restoration, needs, body, enrollment)
        decision_id = alert_audit.new_id("d")
        self._audit(token, route, decision, decision_id)
        return {**decision.response, "decision_id": decision_id, "enrolled": enrollment is not None}

    # ---- demo sign-up, mock delivery, audit ------------------------------------------------------------------
    def _known_token(self, body: Mapping) -> str:
        token = self.token(body)
        if self.rows.get(token) is None:
            raise BadRequest("We don't recognize that location.")
        return token

    def _audit_simple(self, token: str, route: str, inputs: Mapping, outputs: Mapping) -> None:
        try:
            entry = alert_audit.build_entry(
                decision_id=alert_audit.new_id("d"), route=route, token=token, engine_version=alert_eval.ENGINE_VERSION,
                table_hash=self.alert_table.hash, inputs=inputs, rules_fired=[], outputs=outputs, clock=self.clock)
            alert_audit.record(self.alert_store, entry)
        except Exception:  # noqa: BLE001
            log.exception("audit write failed")

    def enroll(self, body: Mapping) -> dict:
        token = self._known_token(body)
        needs = None
        if body.get("needs") is not None:
            needs = self.needs(body)
        try:
            record = alert_enroll.enroll(self.alert_store, token, body.get("channels"), needs,
                                         body.get("message_types"), self.clock)
        except alert_enroll.EnrollError as e:
            raise BadRequest(str(e)) from None
        needs_flags = record["needs"] or {}
        self._audit_simple(
            token, "enroll",
            {"channels_enrolled": alert_enroll.channel_types(record), "any_need": any(needs_flags.values()),
             "medical": bool(needs_flags.get("medical_equipment"))},
            {"action": "enroll", "message_types": record["message_types"]})
        return {"enrolled": True, "enrollment": alert_enroll.public_view(record),
                "demo_notice": alert_enroll.DEMO_NOTICE}

    def unenroll(self, body: Mapping) -> dict:
        token = self._known_token(body)
        result = alert_enroll.unenroll(self.alert_store, token)
        self._audit_simple(token, "unenroll", {"channels_enrolled": None, "any_need": False, "medical": False},
                           {"action": "unenroll", **result})
        return {**result, "message": "Your demo sign-up and any demo messages were deleted.",
                "demo_notice": alert_enroll.DEMO_NOTICE}

    def simulate(self, body: Mapping) -> dict:
        token = self._known_token(body)
        event = body.get("event")
        if event not in alert_eval.MESSAGE_TYPES:
            raise BadRequest(f"event must be one of {list(alert_eval.MESSAGE_TYPES)}")
        use_llm = body.get("use_llm", False)
        if not isinstance(use_llm, bool):
            raise BadRequest("use_llm must be true or false")
        severity = self.severity(body)
        scenario = self.scenario(body)
        enrollment = self.alert_store.get_enrollment(token)
        needs = self.needs(body) if "needs" in body else (
            enrollment["needs"] if enrollment and enrollment.get("needs") else prepare.clean_needs(None))
        risk = self.risk(token, severity)
        restoration = self.restoration(token, severity, scenario)
        decision = self._decide(token, risk, restoration, needs, body, enrollment)
        decision_id = alert_audit.new_id("d")
        elig = decision.response["eligibility"][event]
        base = {"decision_id": decision_id, "event": event, "demo_notice": alert_enroll.DEMO_NOTICE}

        def refuse(reason_code: str, message: str) -> dict:
            self._audit(token, "simulate", decision, decision_id,
                        {"simulated_event": event, "recorded": False, "reason": reason_code})
            return {**base, "recorded_in_outbox": False, "status": "not_recorded", "reason_code": reason_code,
                    "message": message, "eligibility": elig}

        if enrollment is None:
            return refuse("not_enrolled", "Sign up first. Nothing is recorded for a location that has not signed up.")
        if event not in enrollment["message_types"]:
            return refuse("message_type_not_chosen", "You chose not to receive this type of message.")
        if not elig["eligible"]:
            return refuse("not_eligible", elig["reason"])
        recommended = decision.response["recommended_channels"]
        channel = recommended[0] if recommended else "app"
        text_channel = "push" if channel == "app" else channel
        msg_body = {"message_type": event, "channel": text_channel, "use_llm": use_llm,
                    "prior_estimate": body.get("prior_estimate")}
        message = self.message(token, severity, msg_body, risk, restoration, needs)
        item = self.delivery.deliver(token, event, channel, message["text"])
        self._audit(token, "simulate", decision, decision_id,
                    {"simulated_event": event, "recorded": True, "channel": channel, "source": message["source"]})
        return {**base, "recorded_in_outbox": True, "status": STATUS_NOT_SENT, "channel": channel,
                "outbox_item": item, "message": {"source": message["source"], "guard": message["guard"],
                                                 "note": message["note"]},
                "eligibility": elig}

    def audit(self, token_value: Any) -> dict:
        if not isinstance(token_value, str) or not TOKEN_RE.match(token_value):
            raise BadRequest("token must look like p_ followed by 16 lowercase hex characters")
        entries = alert_audit.read(self.alert_store, token_value)
        out = {"entries": entries, "count": len(entries),
               "note": "Demo only. Entries expire after 7 days. This route has no authentication."}
        return self._finish(out)

    # ---- backtest evidence ------------------------------------------------------------------------------------
    def _load_evidence(self) -> Mapping | None:
        """The published backtest report. A missing report is looked for again after a minute, so an upload shows up
        without waiting for a cold start."""
        if self._evidence is None and self._evidence_loader is not None:
            now = time.monotonic()
            if self._evidence_checked is None or now - self._evidence_checked >= EVIDENCE_RETRY_SECONDS:
                self._evidence_checked = now
                try:
                    self._evidence = self._evidence_loader()
                except Exception:  # noqa: BLE001 - the route answers "unavailable"; the log has the stack trace
                    log.exception("could not load the backtest report")
        return self._evidence

    def evidence(self) -> dict:
        """Aggregate backtest verdicts, plot locations and the fixed limits list. No identifiers."""
        report = self._load_evidence()
        return self._finish(backtest_evidence.evidence_view(report) if report else backtest_evidence.unavailable_view())

    @staticmethod
    def _finish(out: dict) -> dict:
        try:
            _scan_strings(out, "response")
        except WhitelistError:
            raise RuntimeError("response blocked: it looked like it contained an internal identifier") from None
        return out

    @staticmethod
    def _prior_display(body: Mapping) -> str | None:
        prior = body.get("prior_estimate")
        if prior is None:
            return None
        if not isinstance(prior, dict) or set(prior) != {"low_hours", "high_hours"}:
            raise BadRequest("prior_estimate needs low_hours and high_hours")
        lo, hi = _int(prior["low_hours"], "low_hours", 1, 2000), _int(prior["high_hours"], "high_hours", 1, 2000)
        if lo > hi:
            raise BadRequest("prior_estimate low_hours cannot be above high_hours")
        return rest.display(lo, hi)

    # ---- assistant (spec-06) ----------------------------------------------------------------------------------
    def assist(self, body: Mapping) -> dict:
        """One of eight kinds of customer text. Every input is validated before the model can be called, and no
        customer string can reach a prompt: the card is built from engine output and closed enums only."""
        kind = body.get("kind")
        if not isinstance(kind, str) or kind not in factcard.KINDS:
            raise BadRequest(f"kind must be one of {list(factcard.KINDS)}")
        token, severity, needs, scenario = self.token(body), self.severity(body), self.needs(body), self.scenario(body)
        channel = body.get("channel", "sms")
        if not isinstance(channel, str) or channel not in CHANNEL_LIMITS:
            raise BadRequest(f"channel must be one of {list(CHANNEL_LIMITS)}")
        stage = body.get("stage")
        if stage is not None and (not isinstance(stage, str) or stage not in factcard.STAGES):
            raise BadRequest(f"stage must be one of {list(factcard.STAGES)}")
        use_llm = body.get("use_llm", True)
        if not isinstance(use_llm, bool):
            raise BadRequest("use_llm must be true or false")
        prior_display = self._prior_display(body)
        known = self.rows.get(token) is not None            # the allow-list: a well-formed unknown token never reaches the model
        risk = self.risk(token, severity)
        restoration = self.restoration(token, severity, scenario)
        guidance = self.guidance(risk, restoration, needs)
        est = restoration.get("estimate")
        card = factcard.build(kind=kind, risk=risk, restoration=restoration, channel=channel,
                              has_declared_need=any(needs.values()), prior_display=prior_display, stage=stage)
        typical = None if not est else est.get("p50_hours", est.get("typical_hours"))
        return self.assistant.respond(kind, card=card, guidance=guidance, typical_hours=typical, needs=needs,
                                      use_llm=use_llm, known=known)

    def message(self, token: str, severity: Any, body: Mapping, risk: Mapping, restoration: Mapping,
                needs: Mapping) -> dict:
        mtype, channel = body.get("message_type"), body.get("channel", "sms")
        use_llm = body.get("use_llm", True)
        if not isinstance(use_llm, bool):
            raise BadRequest("use_llm must be true or false")
        prior_display = self._prior_display(body)
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
            _only(body, common | {"crews", "access", "damage", "damage_count"})
            out = self.restoration(self.token(body), self.severity(body), self.scenario(body))
        elif route == "assistant":
            _only(body, common | {"kind", "channel", "stage", "crews", "access", "damage", "damage_count", "needs",
                                  "use_llm", "prior_estimate"})
            out = self.assist(body)
        elif route == "alerts/enroll":
            _only(body, {"token", "channels", "needs", "message_types"})
            out = self.enroll(body)
        elif route == "alerts/unenroll":
            _only(body, {"token"})
            out = self.unenroll(body)
        elif route == "alerts/simulate":
            _only(body, common | {"crews", "access", "damage", "needs", "event", "use_llm", "prior_typical_hours",
                                  "prior_estimate", "hours_until_storm", "hours_since_last_update", "channels",
                                  "opt_in"})
            out = self.simulate(body)
        elif route in ("guidance", "alerts", "summary", "messages"):
            allowed = common | {"crews", "access", "damage", "damage_count", "needs"}
            if route in ("alerts", "summary"):
                allowed |= {"channels", "prior_typical_hours", "hours_until_storm", "hours_since_last_update", "opt_in"}
            if route == "messages":
                allowed |= {"message_type", "channel", "use_llm", "prior_estimate"}
            _only(body, allowed)
            token, severity, needs = self.token(body), self.severity(body), self.needs(body)
            risk = self.risk(token, severity)
            restoration = self.restoration(token, severity, self.scenario(body))
            if route == "guidance":
                out = {"risk_band": risk.get("band"), "guidance": self.guidance(risk, restoration, needs)}
            elif route == "alerts":
                out = self.alerts(token, risk, restoration, needs, body, "alerts")
            elif route == "messages":
                out = self.message(token, severity, body, risk, restoration, needs)
            else:
                out = {"risk": risk, "restoration": restoration,
                       "guidance": self.guidance(risk, restoration, needs),
                       "alerts": self.alerts(token, risk, restoration, needs, body, "summary"),
                       "disclosure": "Risk and restoration figures are model estimates from past storms and "
                                     "simulated damage. They are not weather forecasts or confirmed utility information."}
        else:
            raise KeyError(route)
        return self._finish(out)
