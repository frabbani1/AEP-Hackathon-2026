"""Finds the spec-03 circuit engine, if it has shipped.

Spec-03 (`restoration-v2`) must provide `concierge/restoration/circuit.py` with

    def load_engine(tables_dir) -> CircuitEngine          # see rest_eval.CircuitEngine
    engine_version: str                                    # attribute on the returned object, e.g. "restoration-v2"

`completion_hours(circuit, *, crews, damage, seed)` returns the pooled job-completion time (clock hours) of every job on the
circuit, one value per Monte Carlo trial, using the circuit's own device tree. `damage` is a `rest_eval.OracleDamage`
(actual asset counts) or a `rest_eval.ForecastDamage` (expected job count, Poisson draw per trial).
Until that module exists this returns None and the backtest marks the restoration model runs `not_run`.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from .rest_eval import CircuitEngine

HOOK_MODULE = "concierge.restoration.circuit"


def load_engine(tables_dir: str | Path) -> CircuitEngine | None:
    try:
        mod = importlib.import_module(HOOK_MODULE)
    except ModuleNotFoundError as e:
        if e.name != HOOK_MODULE:
            raise
        return None
    return mod.load_engine(tables_dir)
