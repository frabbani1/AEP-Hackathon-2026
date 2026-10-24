"""Response helpers for API Gateway HTTP API payload format 2.0.

CORS headers are added by API Gateway (see infra/lib/api.ts), not here.
Nothing here logs the request: query strings carry premise tokens and must stay out of logs.
"""
from __future__ import annotations

import json
from typing import Any

_BASE_HEADERS = {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}


def response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": dict(_BASE_HEADERS), "body": json.dumps(body, separators=(",", ":"))}


def error(status: int, code: str, message: str) -> dict[str, Any]:
    """The message must be static text: never echo request input back."""
    return response(status, {"error": {"code": code, "message": message}})


def query_param(event: dict, name: str) -> str | None:
    """Single query-string value, or None when absent or empty. API Gateway joins repeated keys with commas."""
    value = (event.get("queryStringParameters") or {}).get(name)
    return value if value else None
