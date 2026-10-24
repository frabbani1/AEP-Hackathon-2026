"""Protects the raw data directory: read-only use, verified by checksums."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import ConfigError


class RawDataChangedError(ConfigError):
    """A file in the raw data directory changed, appeared or disappeared during a run."""


def checksum_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


IGNORED_NAMES = frozenset({".DS_Store"})  # operating-system folder metadata, not data


def checksum_tree(root: Path) -> dict[str, str]:
    """SHA-256 of every data file under ``root``, keyed by relative path (sorted)."""
    result: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name not in IGNORED_NAMES):
        result[path.relative_to(root).as_posix()] = checksum_file(path)
    return result


def assert_output_outside_raw(raw_dir: Path, out_dir: Path) -> None:
    """Refuse an output location that is, or is inside, the raw data directory."""
    raw = Path(raw_dir).resolve()
    out = Path(out_dir).resolve()
    if out == raw or raw in out.parents:
        raise ConfigError(
            f"Refusing to write curated outputs inside the raw data directory ({raw_dir}). "
            "Choose an output directory outside it."
        )


class RawGuard:
    """Takes a checksum snapshot of the raw directory and verifies it is unchanged later."""

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = Path(raw_dir)
        self.before: dict[str, str] = {}

    def snapshot(self) -> dict[str, str]:
        self.before = checksum_tree(self.raw_dir)
        return self.before

    def verify(self) -> dict[str, str]:
        """Raise RawDataChangedError if anything differs from the snapshot."""
        after = checksum_tree(self.raw_dir)
        if after != self.before:
            changed = sorted(
                {k for k in self.before.keys() | after.keys() if self.before.get(k) != after.get(k)}
            )
            shown = ", ".join(changed[:5])
            raise RawDataChangedError(
                f"The raw data directory changed during the run ({len(changed)} file(s): {shown})."
            )
        return after
