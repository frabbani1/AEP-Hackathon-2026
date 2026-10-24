"""Privacy rules shared by the leak gate and tests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .contracts import default_config_dir
from .errors import ConfigError


@dataclass(frozen=True)
class PrivacyRules:
    forbidden_columns: frozenset[str]
    forbidden_substrings: tuple[str, ...]
    raw_identifier_patterns: tuple[re.Pattern[str], ...]
    public_forbidden_columns: frozenset[str]

    def forbidden_column_reason(self, name: str, *, public: bool = False) -> str | None:
        """Why a column or key name is forbidden, or None when it is allowed."""
        lowered = name.strip().lower()
        if lowered in self.forbidden_columns:
            return f"forbidden name '{name}'"
        for fragment in self.forbidden_substrings:
            if fragment in lowered:
                return f"name '{name}' contains forbidden fragment '{fragment}'"
        if public and lowered in self.public_forbidden_columns:
            return f"name '{name}' is not allowed in browser-safe artifacts"
        return None

    def raw_identifier_match(self, value: str) -> bool:
        return any(pattern.match(value) for pattern in self.raw_identifier_patterns)


def load_privacy_rules(config_dir: Path | None = None) -> PrivacyRules:
    path = (config_dir or default_config_dir()) / "privacy.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"Configuration file not found: {path}") from error
    return PrivacyRules(
        forbidden_columns=frozenset(c.lower() for c in data["forbidden_columns"]),
        forbidden_substrings=tuple(s.lower() for s in data["forbidden_substrings"]),
        raw_identifier_patterns=tuple(re.compile(p) for p in data["raw_identifier_patterns"]),
        public_forbidden_columns=frozenset(c.lower() for c in data["public_forbidden_columns"]),
    )
