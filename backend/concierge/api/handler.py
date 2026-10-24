"""AWS Lambda entry point for the API Gateway HTTP API (payload format 2.0).

Routes (POST unless noted; also reachable under /api/ when served through CloudFront):
  GET  /health
  POST /risk         {token, severity?}
  POST /restoration  {token, severity?, crews?, access?, damage? | damage_count?}
  POST /guidance     {token, severity?, needs?, crews?, access?, damage? | damage_count?}
  POST /alerts       {token, severity?, needs?, channels?, prior_typical_hours?, ...}
  POST /messages     {token, message_type, channel?, severity?, needs?, use_llm?, prior_estimate?}
  POST /summary      risk + restoration + guidance + alerts in one call
"""
from __future__ import annotations

import base64
import json
import logging
import os

from .service import BadRequest, Concierge

log = logging.getLogger("concierge.api")
log.setLevel(logging.INFO)

MAX_BODY_BYTES = 8 * 1024
ROUTES = {"risk", "restoration", "guidance", "alerts", "messages", "summary"}
_service: Concierge | None = None


def _build() -> Concierge:
    from . import store
    from ..restoration import config as rest_config
    engine = rest_config.engine_from_env()
    tree = store.load_tree() if engine == "v2" else None
    phraser, model_id = None, os.environ.get("BEDROCK_MODEL_ID")
    if model_id:
        import boto3

        from ..messages.compose import make_bedrock_phraser
        phraser = make_bedrock_phraser(boto3.client("bedrock-runtime"), model_id)
    return Concierge(store.load_rows(), store.load_calibration(), store.load_labor(), phraser, model_id,
                     tree=tree, restoration_engine=engine)


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
                               "messages_model": svc.model_id})
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
    except Exception:  # noqa: BLE001 - never leak internals; the log has the stack trace
        log.exception("unhandled error on %s", route)
        return _error(500, "internal_error", "Something went wrong. Please try again.")
