"""Deterministic outage-risk engine (spec-02). No LLM, no network."""
from .engine import RiskEngine, SeverityError
from .schema import RiskResult

__all__ = ["RiskEngine", "RiskResult", "SeverityError"]
