"""Write a small synthetic raw data directory and a matching test configuration.

    python -m tests.fixtures /tmp/aep-fixture

creates ``/tmp/aep-fixture/data`` and ``/tmp/aep-fixture/config`` so the pipeline can be
run end to end without the real data. Nothing here is real data.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

from .builder import build_raw_dir, make_test_config


def main(argv: list[str]) -> int:
    warnings.filterwarnings("ignore", message="Field .* create as date field")
    if len(argv) != 1:
        print("usage: python -m tests.fixtures <empty-output-directory>", file=sys.stderr)
        return 2
    base = Path(argv[0])
    config = make_test_config(base / "config")
    build_raw_dir(base / "data", config)
    print(f"Wrote {base / 'data'} and {config}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
