from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.builder import FixtureInfo, FixtureOptions, build_raw_dir, make_test_config

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DATA = REPO_ROOT / "data"
TEST_KEY = "unit-test-key-0123456789abcdef"


def _real_data_available() -> bool:
    probe = REAL_DATA / "Data1" / "Customer_Clip.dbf"
    if not probe.is_file():
        return False
    with probe.open("rb") as handle:
        return not handle.read(24).startswith(b"version https://git-lfs")


def pytest_collection_modifyitems(config, items):
    if _real_data_available():
        return
    skip = pytest.mark.skip(reason="real data under data/ is absent or only LFS pointers")
    for item in items:
        if "real_data" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def real_data_dir() -> Path:
    return REAL_DATA


@pytest.fixture(scope="session")
def test_config_dir(tmp_path_factory) -> Path:
    """Configuration copied from the real one, with record minimums relaxed for tiny fixtures."""
    return make_test_config(tmp_path_factory.mktemp("config") / "config")


@pytest.fixture(scope="session")
def fixture_raw(tmp_path_factory, test_config_dir) -> FixtureInfo:
    """A synthetic raw data directory shared (read-only) by tests."""
    return build_raw_dir(tmp_path_factory.mktemp("raw") / "data", test_config_dir)


@pytest.fixture()
def build_fixture(tmp_path, test_config_dir):
    """Build a fresh synthetic raw directory with custom options."""

    def _build(options: FixtureOptions | None = None) -> FixtureInfo:
        return build_raw_dir(tmp_path / "data", test_config_dir, options)

    return _build


@pytest.fixture()
def token_key() -> bytes:
    return TEST_KEY.encode()
