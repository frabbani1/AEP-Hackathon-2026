"""Wires the engines together. One class, no AWS calls except through the injected rows/clients."""
from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import date
from statistics import median
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
from ..weather import outlook as wx_outlook
from ..weather import severity as wx_severity
from ..weather import summary as wx_summary
from ..weather.geocode import GeocodeError, Geocoder
from ..weather.openmeteo import OpenMeteo, WeatherError, snap_point
from ..zones import places
from ..zones import view as zone_view
from ..zones.index import MIN_HOMES, ZONE_ID_RE, Zone, ZoneIndex


log = logging.getLogger("concierge.api")

EVIDENCE_RETRY_SECONDS = 60   # how often a missing backtest report is looked for again
AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EARLIEST_AS_OF = date(1950, 1, 1)     # the Open-Meteo archive starts well before this
LAYER_CACHE_SIZE = 16         # map layers kept per storm scenario (a layer is a pure function of the scenario)


class BadRequest(ValueError):
    """The caller sent something we won't accept. The message is safe to return."""


class Unavailable(RuntimeError):
    """A published artifact this route needs is not there yet. The message is safe to return."""


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
                 assistant: Assistant | None = None, zones: ZoneIndex | None = None, weather: OpenMeteo | None = None,
                 weather_anchors: Mapping | None = None, geocoder: Geocoder | None = None):
        self.rows, self.labor = rows, labor
        self.zones = zones
        self.weather = weather
        self.geocoder = geocoder
        self._weather_anchors = weather_anchors
        self._layers: OrderedDict[Any, dict] = OrderedDict()
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
        if s == "weather":
            raise BadRequest("severity 'weather' needs a map area. Use /zones, /zones/assess or /weather.")
        try:
            self.engine.resolve_severity(s)
        except ValueError as e:
            raise BadRequest(str(e)) from None
        return s

    # ---- weather ----------------------------------------------------------------------------------------------
    def _as_of(self, body: Mapping) -> date | None:
        v = body.get("as_of")
        if v is None:
            return None
        if not isinstance(v, str) or not AS_OF_RE.match(v):
            raise BadRequest("as_of must be a date like 2025-02-16")
        try:
            d = date.fromisoformat(v)
        except ValueError:
            raise BadRequest("as_of must be a real calendar date like 2025-02-16") from None
        if d >= self.clock().date():
            raise BadRequest("as_of must be a past date. Leave it out to use the current weather and forecast.")
        if d < EARLIEST_AS_OF:
            raise BadRequest(f"as_of must be on or after {EARLIEST_AS_OF.isoformat()}")
        return d

    def _anchors(self) -> Mapping:
        if self._weather_anchors is None:
            self._weather_anchors = wx_severity.load_anchors()
        return self._weather_anchors

    @staticmethod
    def _zone_point(zone: Zone) -> tuple[float, float]:
        """Where to ask for weather: the middle of the area's outline, snapped to the 0.05 degree grid. Never a home."""
        lng = sum(p[0] for p in zone.ring) / len(zone.ring)
        lat = sum(p[1] for p in zone.ring) / len(zone.ring)
        return snap_point(lat, lng)

    def _series(self, points: list, day: date | None) -> dict:
        if self.weather is None:
            raise WeatherError("weather is turned off")
        return self.weather.historical(points, day) if day else self.weather.live(points)

    def _severity_block(self, gust: float) -> dict:
        return wx_severity.severity_from_gust(gust, self._anchors(), self.engine.presets,
                                              m_min=self.engine.cfg.severity_min, m_max=self.engine.cfg.severity_max)

    def _severity_from_summary(self, wx: Mapping) -> dict | None:
        gust = (wx.get("severity_window") or {}).get("peak_gust_mph")
        return None if gust is None else self._severity_block(gust)

    def _wx_unavailable(self, reason: str, area: str | None) -> dict:
        wx = wx_summary.unavailable(reason, area)
        wx["severity"] = {"source": "scenario_default", "preset": "typical",
                          "multiplier": round(self.engine.presets["typical"], 2)}
        wx["notice"] = ("Weather could not be loaded, so this answer uses the default typical-storm scenario "
                        "instead of the real weather.")
        return wx

    def weather_context(self, zone: Zone, day: date | None) -> dict:
        """The weather block for one area, with the storm strength it implies. Never raises: on any failure the block
        says `unavailable` and carries the default scenario, so the customer still gets an answer and knows why."""
        try:
            pt = self._zone_point(zone)
            wx = wx_summary.summarize(self._series([pt], day)[pt], zone.name)
            sev = self._severity_from_summary(wx)
        except (WeatherError, KeyError) as e:
            log.warning("weather unavailable: %s", type(e).__name__)
            return self._wx_unavailable("The weather service did not answer.", zone.name)
        if sev is None:
            return self._wx_unavailable("The weather data had no wind gust values.", zone.name)
        wx["severity"] = sev
        return wx

    def severity_ctx(self, body: Mapping, zone: Zone) -> tuple[Any, dict | None]:
        """(severity for the engines, weather block or None). `severity: "weather"` reads real weather for the area."""
        if body.get("severity", "typical") != "weather":
            if body.get("as_of") is not None:
                raise BadRequest("as_of only works with severity 'weather'")
            return self.severity(body), None
        wx = self.weather_context(zone, self._as_of(body))
        return ("typical", wx) if wx["status"] != "ok" else (wx["severity"]["multiplier"], wx)

    @staticmethod
    def _wx_compact(wx: Mapping) -> dict:
        """The weather block without the hour-by-hour list."""
        return {k: v for k, v in wx.items() if k != "hourly"}

    @staticmethod
    def _wx_apply(out: dict, wx: Mapping | None) -> dict:
        """Make an engine answer say where its storm strength came from. The engines keep their own wording for
        scenarios the customer picks; with real weather those sentences would be wrong."""
        if wx is None or wx.get("status") != "ok" or not out:
            return out
        if isinstance(out.get("severity"), dict) and out["severity"].get("preset") == "custom":
            out["severity"]["preset"] = "weather"
        swaps = {
            "Storm strength is a scenario you choose, not a weather forecast.":
                "Storm strength comes from the weather data for your area, placed on the scale of past storms. "
                "That step is an assumption, not a measured link.",
            "No weather data is used.":
                "Only wind gusts move the storm strength. Ice, snow, rain and lightning are shown but are not in the rating.",
            "No live crew locations, road conditions, or weather are used.":
                "No live crew locations or road conditions are used. Weather only sets the storm strength.",
            "Guidance is general and based on the scenario you chose. It is not a weather forecast.":
                "Guidance is general and based on the storm strength taken from weather data for your area. "
                "It is not a prediction that you will lose power.",
        }
        for key in ("assumptions", "missing_data", "notes"):
            if isinstance(out.get(key), list):
                out[key] = [swaps.get(x, x).replace("the storm scenario you chose",
                                                    "the storm strength taken from weather data")
                            if isinstance(x, str) else x for x in out[key]]
        for f in out.get("top_factors") or []:
            if f.get("id") == "sev":
                f["text"] = ("The weather for your area points to a storm stronger than a typical past storm."
                             if f.get("direction") == "raises" else
                             "The weather for your area points to a storm milder than a typical past storm.")
        return out

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
        return self._assist_run(kind, risk=risk, restoration=restoration, guidance=guidance, needs=needs,
                                channel=channel, stage=stage, use_llm=use_llm, prior_display=prior_display, known=known)

    def _assist_run(self, kind: str, *, risk: Mapping, restoration: Mapping, guidance: Mapping, needs: Mapping,
                    channel: str, stage: str | None, use_llm: bool, prior_display: str | None, known: bool) -> dict:
        est = restoration.get("estimate")
        card = factcard.build(kind=kind, risk=risk, restoration=restoration, channel=channel,
                              has_declared_need=any(needs.values()), prior_display=prior_display, stage=stage)
        typical = None if not est else est.get("p50_hours", est.get("typical_hours"))
        return self.assistant.respond(kind, card=card, guidance=guidance, typical_hours=typical, needs=needs,
                                      use_llm=use_llm, known=known)

    # ---- outage map (zones) -----------------------------------------------------------------------------------
    def _zone_index(self) -> ZoneIndex:
        if self.zones is None:
            raise Unavailable("The map areas have not been published yet.")
        return self.zones

    def zone_layer(self, body: Mapping) -> dict:
        """Every zone with its band and confidence for one storm scenario. No premise token leaves this method."""
        index = self._zone_index()
        if body.get("severity", "typical") == "weather":
            return self._weather_layer(index, body)
        if body.get("as_of") is not None:
            raise BadRequest("as_of only works with severity 'weather'")
        severity = self.severity(body)
        key = severity if isinstance(severity, str) else float(severity)
        if key in self._layers:
            self._layers.move_to_end(key)
            return self._layers[key]
        sev = self.engine.resolve_severity(severity)
        sev_out = {"preset": sev["preset"], "multiplier": round(sev["multiplier"], 2), "extrapolated": sev["extrapolated"]}
        ratings: dict[str, tuple[str, str] | None] = {}
        for zone in index:
            risk = self.risk(zone.rep, severity) if zone.rep else None
            ok = risk is not None and risk["status"] == "ok"
            ratings[zone.id] = (risk["band"], risk["confidence"]["level"]) if ok else None
        out = self._finish(zone_view.layer(
            [{"id": z.id, "name": z.name, "ring": z.ring} for z in index], ratings, sev_out, index.bounds, MIN_HOMES))
        self._layers[key] = out
        if len(self._layers) > LAYER_CACHE_SIZE:
            self._layers.popitem(last=False)
        return out

    def _weather_layer(self, index: ZoneIndex, body: Mapping) -> dict:
        """The map layer with each area's storm strength taken from the weather at its own location. Not cached: the
        weather client caches the weather itself (15 minutes live, for good for a past date)."""
        day = self._as_of(body)
        points = {z.id: self._zone_point(z) for z in index}
        try:
            series = self._series(sorted(set(points.values())), day)
        except WeatherError as e:
            log.warning("zone weather unavailable: %s", type(e).__name__)
            out = dict(self.zone_layer({"severity": "typical"}))          # a copy: the cached layer stays as it was
            out["weather"] = self._wx_unavailable("The weather service did not answer.", None)
            return out
        gust_at = {pt: (wx_summary.summarize(sr)["severity_window"] or {}).get("peak_gust_mph")
                   for pt, sr in series.items()}
        typical = self.engine.presets["typical"]
        mult: dict[str, float] = {}
        gust: dict[str, float | None] = {}
        for z in index:
            g = gust_at.get(points[z.id])
            gust[z.id] = g
            mult[z.id] = typical if g is None else self._severity_block(g)["multiplier"]
        ratings: dict[str, tuple[str, str] | None] = {}
        for z in index:
            risk = self.risk(z.rep, mult[z.id]) if z.rep else None
            ok = risk is not None and risk["status"] == "ok"
            ratings[z.id] = (risk["band"], risk["confidence"]["level"]) if ok else None
        ms = sorted(mult.values())
        sev_out = {"preset": "weather", "multiplier": round(median(ms), 2),
                   "extrapolated": any(self.engine.resolve_severity(m)["extrapolated"] for m in ms)}
        out = zone_view.layer([{"id": z.id, "name": z.name, "ring": z.ring} for z in index], ratings, sev_out,
                              index.bounds, MIN_HOMES)
        for item in out["zones"]:
            item["multiplier"] = round(mult[item["id"]], 3)
            item["peak_gust_mph"] = gust[item["id"]]
        gusts = [g for g in gust.values() if g is not None]
        ref = self._severity_block(max(gusts) if gusts else 0.0)
        out["weather"] = {
            "status": "ok", "source": "Open-Meteo", "attribution": wx_summary.ATTRIBUTION, "basis": wx_summary.BASIS,
            "kind": "historical" if day else "live", "as_of": day.isoformat() if day else None,
            "areas_total": len(index), "areas_without_weather": sum(1 for g in gust.values() if g is None),
            "peak_gust_mph_max": max(gusts) if gusts else None, "peak_gust_mph_min": min(gusts) if gusts else None,
            "severity": {"source": "weather", "multiplier_min": round(ms[0], 2), "multiplier_max": round(ms[-1], 2),
                         "method": ref["method"], "anchors": ref["anchors"]},
        }
        return self._finish(out)

    def weather_view(self, body: Mapping) -> dict:
        """Current weather and forecast (or a past date) for one map area, with the storm strength it implies."""
        zone = self._zone(body)
        try:
            pt = self._zone_point(zone)
            wx = wx_summary.summarize(self._series([pt], self._as_of(body))[pt], zone.name)
        except (WeatherError, KeyError):
            raise Unavailable("Weather could not be loaded right now. Try again in a minute.") from None
        sev = self._severity_from_summary(wx)
        if sev is not None:
            wx["severity"] = sev
        return self._finish(wx)

    @staticmethod
    def _place_query(body: Mapping) -> str:
        """The typed city or ZIP code, checked. Anything that looks like a street address is refused."""
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            raise BadRequest("Enter a city or a 5-digit ZIP code.")
        if len(query) > places.MAX_QUERY_LENGTH:
            raise BadRequest(f"Keep it under {places.MAX_QUERY_LENGTH} characters.")
        if re.search(r"\d", query) and not re.fullmatch(r"\s*\d{5}(-\d{4})?\s*", query):
            raise BadRequest("Enter a city or a 5-digit ZIP code. Please don't include a street address.")
        return query

    def outlook(self, body: Mapping) -> dict:
        """Forecast-only outlook for a US city or ZIP code, for places that have no outage data. It carries no risk
        band, no restoration time and no alert eligibility, and says so. The typed text goes to the place lookup
        (Open-Meteo geocoding) and nowhere else; it is not stored, logged or echoed. Weather is asked for at the
        place's coordinates rounded to the 0.05 degree grid."""
        query = self._place_query(body)
        if self.weather is None or self.geocoder is None:
            raise Unavailable("The forecast-only outlook is turned off.")
        try:
            place = self.geocoder.lookup(query)
        except GeocodeError as e:
            log.warning("%s", e)
            raise Unavailable("We couldn't look up that place right now. Try again in a minute.") from None
        if place is None:
            return self._finish({"status": "not_found", "source": wx_outlook.SOURCE})
        try:
            pt = snap_point(place.lat, place.lon)
            wx = wx_summary.summarize(self._series([pt], None)[pt])
        except (WeatherError, KeyError):
            raise Unavailable("Weather could not be loaded right now. Try again in a minute.") from None
        return self._finish({"status": "ok", **wx_outlook.build(place.name, wx, place.lat, place.lon)})

    def zone_find(self, body: Mapping) -> dict:
        """The map area for a typed city or ZIP code. The text is compared with public place and ZIP tables only; it is
        not stored, not logged and not echoed back, and no premise token is involved."""
        index = self._zone_index()
        query = self._place_query(body)
        triples = [(z.id, z.name, z.ring) for z in index]
        found = places.locate(query, triples)
        if found is None:
            return self._finish({"status": "not_covered", "covered_places": places.covered_places(triples)})
        return self._finish({"status": "found", **found})

    def _zone(self, body: Mapping) -> Zone:
        zid = body.get("zone_id")
        if not isinstance(zid, str) or not ZONE_ID_RE.match(zid):
            raise BadRequest("zone_id must look like z_ followed by 12 lowercase letters")
        zone = self._zone_index().get(zid)
        if zone is None:
            raise BadRequest("We don't recognize that area.")
        return zone

    def zone_assess(self, body: Mapping) -> dict:
        """All four answers for one zone, through its representative premise. The premise token stays on the server and
        nothing is written to the alerts audit log, because browsing the map is not an alert decision."""
        zone = self._zone(body)
        severity, wx = self.severity_ctx(body, zone)
        needs, scenario = self.needs(body), self.scenario(body)
        use_llm = body.get("use_llm", False)
        if not isinstance(use_llm, bool):
            raise BadRequest("use_llm must be true or false")
        rated = zone.rep is not None
        token = zone.rep or zone_view.UNRATED_TOKEN
        risk = self._wx_apply(self.risk(token, severity), wx)
        restoration = self._wx_apply(self.restoration(token, severity, scenario), wx)
        if not rated:                                    # say why this area has no rating, not "unknown premise"
            fallback = zone_view.unrated_fallback(zone.fallback)
            risk, restoration = {**risk, "fallback": fallback}, {**restoration, "fallback": fallback}
        guidance = self._wx_apply(self.guidance(risk, restoration, needs), wx)
        decision = self._decide(token, risk, restoration, needs, {}, None)

        def ask(kind: str, channel: str = "sms") -> dict:
            return self._assist_run(kind, risk=risk, restoration=restoration, guidance=guidance, needs=needs,
                                    channel=channel, stage=None, use_llm=use_llm, prior_display=None, known=rated)

        texts = {kind: ask(kind) for kind in ("explanation", "prep_before", "actions_during", "actions_after")}
        messages = []
        if rated:
            texts.update({kind: ask(kind, channel) for kind, _, channel in zone_view.MESSAGE_KINDS})
            messages = zone_view.messages_view(texts)
        sev_out = risk["severity"]
        out = {
            "source": zone_view.SOURCE, "zone_id": zone.id, "zone_name": zone.name,
            "status": "ok" if rated and risk["status"] == "ok" else "insufficient_data",
            "severity": sev_out, "critical_facility_nearby": zone.facility,
            "confirmed": {"available": False, "note": texts["explanation"]["sections"]["confirmed_note"]},
            "risk": zone_view.risk_view(risk, texts["explanation"]),
            "restoration": zone_view.restoration_view(restoration, sev_out),
            "preparation": zone_view.preparation_view(texts),
            "alerts": zone_view.alerts_view(decision.response),
            "messages": messages,
            "transparency": zone_view.transparency_view(risk, restoration, rated and risk["status"] == "ok"),
            "fallback": risk.get("fallback"),
        }
        if wx is not None:
            out["weather"] = wx                            # code-built from Open-Meteo; the model never saw it
        return self._finish(out)

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
        elif route == "zones":
            _only(body, {"severity", "as_of"})
            return self.zone_layer(body)
        elif route == "zones/find":
            _only(body, {"query"})
            return self.zone_find(body)
        elif route == "outlook":
            _only(body, {"query"})
            return self.outlook(body)
        elif route == "zones/assess":
            _only(body, {"zone_id", "severity", "as_of", "crews", "access", "damage", "damage_count", "needs",
                         "use_llm"})
            return self.zone_assess(body)
        elif route == "weather":
            _only(body, {"zone_id", "as_of"})
            return self.weather_view(body)
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
