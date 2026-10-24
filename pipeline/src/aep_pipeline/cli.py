"""Command line entry point: ``python -m aep_pipeline run --raw data --out curated``."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import __version__
from .errors import EXIT_OK, PipelineError
from .tokens import KEY_ENV_VAR, load_key

EXIT_CODE_HELP = """\
exit codes:
  0  success
  1  input does not match its contract
  2  link coverage below its minimum
  3  leak detected in a serving artifact
  4  missing key or configuration, or an unsafe path
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aep_pipeline",
        description="Curate the AEP challenge datasets into privacy-safe, linked artifacts.",
        epilog=EXIT_CODE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"aep_pipeline {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run",
        help="run the full pipeline",
        description=(
            "Read the raw data directory (never modified) and write curated artifacts "
            f"to the output directory. The tokenization key is read from {KEY_ENV_VAR}."
        ),
        epilog=EXIT_CODE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run.add_argument("--raw", required=True, type=Path, help="raw data directory (read-only)")
    run.add_argument("--out", required=True, type=Path, help="output directory for curated artifacts")
    run.add_argument(
        "--config",
        type=Path,
        default=None,
        help="configuration directory (defaults to pipeline/config next to the package)",
    )
    return parser


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    environ = os.environ if environ is None else environ
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            # The key is checked first, so a missing key stops the run before any raw file is opened.
            key = load_key(environ)
            from .pipeline import run_pipeline  # imported late: heavy dependencies

            run_pipeline(raw_dir=args.raw, out_dir=args.out, key=key, config_dir=args.config)
            return EXIT_OK
    except PipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    return EXIT_OK
