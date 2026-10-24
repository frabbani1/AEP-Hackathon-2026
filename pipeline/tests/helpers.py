"""Shared helpers for tests that need the fixture layers as frames."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from aep_pipeline.config import load_thresholds
from aep_pipeline.contracts import load_layer_contracts, load_topology_contract
from aep_pipeline.layers import read_layer
from aep_pipeline.linking import snap_layers
from aep_pipeline.tokens import Tokenizer
from aep_pipeline.topology import build_graph, parse_topology

TEST_KEY = b"unit-test-key-0123456789abcdef"


def tokenizer() -> Tokenizer:
    return Tokenizer(TEST_KEY)


def read_all(raw_dir: Path, config_dir: Path | None = None) -> dict[str, gpd.GeoDataFrame]:
    contracts = load_layer_contracts(config_dir)
    return {name: read_layer(raw_dir, contract) for name, contract in contracts.items()}


def topology_and_graph(raw_dir: Path, config_dir: Path | None = None):
    contract = load_topology_contract(config_dir)
    topology = parse_topology(raw_dir / contract.path, contract)
    return topology, build_graph(topology)


def device_layers(frames: dict[str, gpd.GeoDataFrame]) -> dict[str, gpd.GeoDataFrame]:
    names = ("Transformer_Clip", "Fuse_Bank_Clip", "Recloser_Bank_Clip", "Switch_Clip")
    return {name: frames[name] for name in names}


def snap_asset_layers(frames, device_links, tok: Tokenizer) -> dict[str, gpd.GeoDataFrame]:
    """Asset layers with the tokens that damage records are linked to."""
    return snap_layers(frames, device_links, tok)


__all__ = [
    "read_all",
    "topology_and_graph",
    "device_layers",
    "snap_asset_layers",
    "tokenizer",
    "load_thresholds",
]
