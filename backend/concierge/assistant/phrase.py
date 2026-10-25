"""Bedrock Converse call with a timeout, one retry on throttling or timeout, and a circuit breaker (design D5).

The system prompt (instructions) and the card (data) go in separate Converse fields. Temperature is low, and the token cap
depends on the kind. This module returns raw model text; the guard decides whether it can be used.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("concierge.assistant")

MAX_TOKENS = {"explanation": 500, "prep_before": 700, "actions_during": 700, "actions_after": 700,
              "msg_pre_storm": 300, "msg_outage_confirmed": 300, "msg_estimate_updated": 300,
              "msg_restoration_complete": 200}
TEMPERATURE = 0.2
READ_TIMEOUT_SECONDS = 8      # set on the boto3 client (see api/handler.py); recorded here for the docs and tests
BREAKER_FAILURES = 3
BREAKER_WINDOW_SECONDS = 60
BREAKER_OPEN_SECONDS = 60
_RETRY_CODES = {"ThrottlingException", "TooManyRequestsException", "ModelTimeoutException",
                "ServiceUnavailableException", "ModelNotReadyException"}


class ModelUnavailable(RuntimeError):
    """The model could not be used (breaker open, or the call failed after its retry)."""


def _retryable(exc: BaseException) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
    name = type(exc).__name__
    return code in _RETRY_CODES or "Timeout" in name or "Throttl" in name


class Breaker:
    """Opens after `failures` consecutive failures inside `window` seconds; skips the model for `open_for` seconds;
    then lets one trial call through."""

    def __init__(self, failures: int = BREAKER_FAILURES, window: float = BREAKER_WINDOW_SECONDS,
                 open_for: float = BREAKER_OPEN_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.failures, self.window, self.open_for, self.clock = failures, window, open_for, clock
        self._fail_times: list[float] = []
        self._opened_at: float | None = None
        self._trial_out = False

    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self._opened_at >= self.open_for else "open"

    def allow(self) -> bool:
        s = self.state()
        if s == "closed":
            return True
        if s == "half_open" and not self._trial_out:
            self._trial_out = True       # one trial call
            return True
        return False

    def success(self) -> None:
        self._fail_times.clear()
        self._opened_at, self._trial_out = None, False

    def failure(self) -> None:
        now = self.clock()
        if self._opened_at is not None:      # the trial failed: open again for a full period
            self._opened_at, self._trial_out = now, False
            return
        self._fail_times = [t for t in self._fail_times if now - t < self.window] + [now]
        if len(self._fail_times) >= self.failures:
            self._opened_at, self._trial_out = now, False
            self._fail_times.clear()


class BedrockPhraser:
    def __init__(self, client: Any, model_id: str, breaker: Breaker | None = None):
        self.client, self.model_id = client, model_id
        self.breaker = breaker or Breaker()

    def __call__(self, kind: str, system: str, user: str) -> str:
        if not self.breaker.allow():
            raise ModelUnavailable("breaker open")
        last: BaseException | None = None
        for attempt in (1, 2):
            try:
                resp = self.client.converse(
                    modelId=self.model_id, system=[{"text": system}],
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig={"maxTokens": MAX_TOKENS[kind], "temperature": TEMPERATURE})
                text = resp["output"]["message"]["content"][0]["text"]
                self.breaker.success()
                return text
            except Exception as e:  # noqa: BLE001 - any client failure falls back to the template
                last = e
                if attempt == 1 and _retryable(e):
                    continue
                break
        self.breaker.failure()
        log.warning("model call failed: %s", type(last).__name__)
        raise ModelUnavailable(f"model unavailable: {type(last).__name__}")
