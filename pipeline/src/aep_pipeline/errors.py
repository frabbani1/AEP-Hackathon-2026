"""Pipeline errors and the exit codes they map to."""

from __future__ import annotations

EXIT_OK = 0
EXIT_CONTRACT = 1
EXIT_COVERAGE = 2
EXIT_LEAK = 3
EXIT_CONFIG = 4


class PipelineError(Exception):
    """Base class. Subclasses carry the process exit code for the failure."""

    exit_code = 1


class ContractError(PipelineError):
    """An input does not match its declared contract."""

    exit_code = EXIT_CONTRACT


class CoverageError(PipelineError):
    """A link coverage rate is below its declared minimum."""

    exit_code = EXIT_COVERAGE


class LeakError(PipelineError):
    """A serving artifact contains a forbidden field or raw identifier."""

    exit_code = EXIT_LEAK


class ConfigError(PipelineError):
    """Missing key, missing configuration, or an unsafe path."""

    exit_code = EXIT_CONFIG
