import shutil

import pytest

from aep_pipeline.cli import main
from aep_pipeline.errors import ConfigError
from aep_pipeline.rawguard import RawDataChangedError, RawGuard, assert_output_outside_raw, checksum_tree

KEY = {"AEP_TOKEN_KEY": "unit-test-key-0123456789abcdef"}


def test_output_inside_raw_directory_is_refused(tmp_path, fixture_raw, test_config_dir, capsys):
    raw = tmp_path / "data"
    shutil.copytree(fixture_raw.raw_dir, raw)
    before = checksum_tree(raw)
    code = main(["run", "--raw", str(raw), "--out", str(raw / "curated"), "--config", str(test_config_dir)], environ=KEY)
    assert code == 4
    assert "raw data directory" in capsys.readouterr().err
    assert checksum_tree(raw) == before
    assert not (raw / "curated").exists()


def test_output_equal_to_raw_directory_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        assert_output_outside_raw(tmp_path, tmp_path)


def test_sibling_output_directory_is_allowed(tmp_path):
    (tmp_path / "data").mkdir()
    assert_output_outside_raw(tmp_path / "data", tmp_path / "curated")
    # a directory whose name merely starts with the raw directory's name is not inside it
    assert_output_outside_raw(tmp_path / "data", tmp_path / "data-curated")


def test_guard_passes_when_nothing_changes(fixture_raw):
    guard = RawGuard(fixture_raw.raw_dir)
    snapshot = guard.snapshot()
    assert snapshot
    assert guard.verify() == snapshot


def test_guard_detects_modified_added_and_removed_files(tmp_path, fixture_raw):
    raw = tmp_path / "data"
    shutil.copytree(fixture_raw.raw_dir, raw)

    guard = RawGuard(raw)
    guard.snapshot()
    (raw / "Install_Labor_HackOHIO.xlsx").write_bytes(b"tampered")
    with pytest.raises(RawDataChangedError) as modified:
        guard.verify()
    assert "Install_Labor_HackOHIO.xlsx" in str(modified.value)

    guard = RawGuard(raw)
    guard.snapshot()
    (raw / "new_file.txt").write_text("x")
    with pytest.raises(RawDataChangedError):
        guard.verify()

    (raw / "new_file.txt").unlink()
    guard = RawGuard(raw)
    guard.snapshot()
    (raw / "matched_upstream" / "matched_upstream.txt").unlink()
    with pytest.raises(RawDataChangedError):
        guard.verify()


def test_failed_run_leaves_raw_files_unchanged(tmp_path, fixture_raw, test_config_dir):
    raw = tmp_path / "data"
    shutil.copytree(fixture_raw.raw_dir, raw)
    before = checksum_tree(raw)
    # An invalid config directory makes the run fail after the raw snapshot is taken.
    code = main(["run", "--raw", str(raw), "--out", str(tmp_path / "out"), "--config", str(tmp_path / "nope")], environ=KEY)
    assert code != 0
    assert checksum_tree(raw) == before
