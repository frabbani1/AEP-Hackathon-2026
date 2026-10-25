"""AWS Lambda entry point for the API Gateway HTTP API (payload format 2.0).

Routes (POST unless noted; also reachable under /api/ when served through CloudFront):
  GET  /health
  POST /risk         {token, severity?}
  POST /restoration  {token, severity?, crews?, access?, damage? | damage_count?}
  POST /guidance     {token, severity?, needs?, crews?, access?, damage? | damage_count?}
  POST /alerts       {token, severity?, needs?, channels?, prior_typical_hours?, hours_until_storm?, ...}
  POST /alerts/enroll    {token, channels, needs?, message_types?}   demo sign-up, fictional contacts only
  POST /alerts/unenroll  {token}
  POST /alerts/simulate  {token, event, ...}                         mock delivery, nothing is sent
  GET  /alerts/audit?token=p_...                                     latest 50 decisions for a token
  GET  /evidence                                                     backtest verdicts, plot locations, limits
  POST /messages     {token, message_type, channel?, severity?, needs?, use_llm?, prior_estimate?}
  POST /assistant    {token, kind, channel?, stage?, severity?, needs?, use_llm?, prior_estimate?, ...}  eight kinds of text
  POST /summary      risk + restoration + guidance + alerts in one call
  POST /zones            {severity?}                     the outage map's areas with band and confidence
  POST /zones/assess     {zone_id, severity?, needs?, crews?, access?, damage? | damage_count?, use_llm?}
                                                         all four answers for one area (no premise token leaves the server)
  POST /weather          {zone_id, as_of?}               current weather and forecast for an area, or a past date

severity may be "weather" on /zones and /zones/assess: storm strength then comes from Open-Meteo wind gusts at the
area. as_of (YYYY-MM-DD, a past date) replays that date's weather instead of the live forecast.
"""
from __future__ import annotations

import base64
import json
import logging
import os

from .service import BadRequest, Concierge, Unavailable

log = logging.getLogger("concierge.api")
log.setLevel(logging.INFO)

MAX_BODY_BYTES = 8 * 1024
ROUTES = {"risk", "restoration", "guidance", "alerts", "messages", "summary", "assistant", "alerts/enroll",
          "alerts/unenroll", "alerts/simulate", "zones", "zones/assess", "weather"}
GET_ROUTES = {"alerts/audit"}
_service: Concierge | None = None


def _build() -> Concierge:
    from . import store
    from ..restoration import config as rest_config
    engine = rest_config.engine_from_env()
    tree = store.load_tree() if engine == "v2" else None
    phraser, model_id, assistant = None, os.environ.get("BEDROCK_MODEL_ID"), None
    if model_id:
        import boto3
        from botocore.config import Config

        from ..assistant.compose import Assistant
        from ..assistant.phrase import READ_TIMEOUT_SECONDS, BedrockPhraser
        from ..messages.compose import make_bedrock_phraser
        # Short timeouts and no SDK retries: the assistant makes its own single retry and then falls back.
        client = boto3.client("bedrock-runtime", config=Config(
            read_timeout=READ_TIMEOUT_SECONDS, connect_timeout=3, retries={"max_attempts": 1, "mode": "standard"}))
        phraser = make_bedrock_phraser(client, model_id)
        assistant = Assistant(BedrockPhraser(client, model_id), model_id)
    alert_store = None
    if os.environ.get("ENROLL_TABLE"):
        import boto3

        from ..alerts.stores import DynamoStore
        alert_store = DynamoStore(boto3.resource("dynamodb").Table(os.environ["ENROLL_TABLE"]))
    weather = None
    if os.environ.get("WEATHER_ENABLED", "1") != "0":
        from ..weather.openmeteo import OpenMeteo
        weather = OpenMeteo()
    return Concierge(store.load_rows(), store.load_calibration(), store.load_labor(), phraser, model_id,
                     alert_store=alert_store, evidence_loader=store.load_evidence, tree=tree,
                     restoration_engine=engine, assistant=assistant, zones=store.load_zones(), weather=weather)


def service() -> Concierge:
    global _service
    if _service is None:
        _service = _build()
    return _service


def _resp(status: int, payload: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(payload, separators=(",", ":"))}


def _error(status: int, code: str, message: str) -> dict:
    return _resp(status, {"error": {"code": code, "message": message}})


def lambda_handler(event, context=None):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", ""), event.get("rawPath", "")
    if path.startswith("/api/"):
        path = path[4:]
    route = path.strip("/")
    try:
        if route == "health" and method == "GET":
            svc = service()
            return _resp(200, {"status": "ok", "engine": svc.engine.config_hash[:19],
                               "messages_model": svc.model_id, "assistant_breaker": svc.assistant.breaker_state(),
                               "weather": "on" if svc.weather is not None else "off",
                               "zones": len(svc.zones) if svc.zones is not None else None})
        if route == "evidence":
            if method != "GET":
                return _error(405, "method_not_allowed", "Use GET.")
            return _resp(200, service().evidence())
        if route in GET_ROUTES:
            if method != "GET":
                return _error(405, "method_not_allowed", "Use GET with a token query parameter.")
            query = event.get("queryStringParameters") or {}
            return _resp(200, service().audit(query.get("token")))
        if route not in ROUTES:
            return _error(404, "not_found", "No such route.")
        if method != "POST":
            return _error(405, "method_not_allowed", "Use POST with a JSON body.")
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        if len(raw.encode()) > MAX_BODY_BYTES:
            return _error(413, "too_large", "Request body is too large.")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return _error(400, "bad_json", "Body must be valid JSON.")
        return _resp(200, service().handle(route, body))
    except BadRequest as e:
        return _error(400, "bad_request", str(e))
    except Unavailable as e:
        return _error(503, "unavailable", str(e))
    except Exception:  # noqa: BLE001 - never leak internals; the log has the stack trace
        log.exception("unhandled error on %s", route)
        return _error(500, "internal_error", "Something went wrong. Please try again.")
