"""Lambda for GET /risk and GET /health. Wraps the deterministic risk engine; adds no logic of its own.

GET /risk?token=p_<16 hex>&severity=<preset name | multiplier>   (severity defaults to "typical")
  200  RiskResult.to_dict(): status "ok" with band/score/confidence/factors, or status "insufficient_data"
       with a plain-language fallback (unknown or unscorable premise). Both are normal outcomes.
  400  token missing, or severity not a known preset / outside the allowed range
  500  anything unexpected (for example the premise table or calibration file could not be read);
       details go to CloudWatch, never to the caller
"""
from __future__ import annotations

import logging
import re

from concierge.risk import RiskEngine, SeverityError
from concierge.risk.config import ENGINE_VERSION

from . import store
from .http import error, query_param, response

logger = logging.getLogger(__name__)
# The engine writes one JSON audit line per score (token, band, confidence, reasons; no raw ids). Surface it in CloudWatch.
logging.getLogger("concierge.risk.audit").setLevel(logging.INFO)

_NUMBER = re.compile(r"\d+(\.\d+)?")
_engine: RiskEngine | None = None


def get_engine() -> RiskEngine:
    """One engine per warm container, built on first use."""
    global _engine
    if _engine is None:
        _engine = store.engine_from_env()
    return _engine


def parse_severity(raw: str | None) -> str | float:
    """'1.4' -> 1.4, 'feb-2025-like' -> unchanged. Range and preset checks belong to the engine."""
    if raw is None:
        return "typical"
    raw = raw.strip()
    return float(raw) if _NUMBER.fullmatch(raw) else raw


def handler(event: dict, context=None) -> dict:
    route = event.get("routeKey")
    try:
        if route == "GET /health":
            engine = get_engine()
            return response(200, {"status": "ok", "engine_version": ENGINE_VERSION, "config_hash": engine.config_hash})
        if route == "GET /risk":
            return _risk(event)
        return error(404, "not_found", "No such route.")
    except SeverityError as exc:
        return error(400, "invalid_severity", str(exc))
    except Exception:  # noqa: BLE001 - last line of defence; the traceback goes to CloudWatch, not to the caller
        logger.exception("risk request failed")
        return error(500, "internal_error", "Something went wrong on our side. Please try again in a moment.")


def _risk(event: dict) -> dict:
    token = query_param(event, "token")
    if token is None:
        return error(400, "missing_token", "A location token is required (query parameter: token).")
    result = get_engine().score(token, parse_severity(query_param(event, "severity")))
    return response(200, result.to_dict())
