import subprocess
import sys

from aep_pipeline import errors
from aep_pipeline.cli import main


def test_help_prints_usage_and_exit_codes():
    result = subprocess.run(
        [sys.executable, "-m", "aep_pipeline", "run", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--raw" in result.stdout and "--out" in result.stdout
    assert "AEP_TOKEN_KEY" in result.stdout
    for code in ("0  success", "1  input", "2  link coverage", "3  leak", "4  missing key"):
        assert code in result.stdout


def test_exit_code_constants_match_the_documented_values():
    assert (errors.EXIT_OK, errors.EXIT_CONTRACT, errors.EXIT_COVERAGE, errors.EXIT_LEAK, errors.EXIT_CONFIG) == (
        0,
        1,
        2,
        3,
        4,
    )
    assert errors.ContractError.exit_code == 1
    assert errors.CoverageError.exit_code == 2
    assert errors.LeakError.exit_code == 3
    assert errors.ConfigError.exit_code == 4


def test_missing_key_exits_4_before_touching_raw_data(tmp_path, capsys):
    missing_raw = tmp_path / "does-not-exist"
    code = main(["run", "--raw", str(missing_raw), "--out", str(tmp_path / "out")], environ={})
    captured = capsys.readouterr()
    assert code == 4
    assert "AEP_TOKEN_KEY" in captured.err
    # The raw path was never inspected: the message is about the key, not the path.
    assert "does-not-exist" not in captured.err
    assert not (tmp_path / "out").exists()


def test_short_key_exits_4(tmp_path, capsys):
    code = main(
        ["run", "--raw", str(tmp_path), "--out", str(tmp_path / "out")],
        environ={"AEP_TOKEN_KEY": "short"},
    )
    assert code == 4
    assert "too short" in capsys.readouterr().err
