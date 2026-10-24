"""Leak gate: a final, independent scan of everything the pipeline wrote for serving.

It checks column and key names against the forbidden list, string values against raw
identifier formats, and browser-safe (``public/``) files for geometry and coordinates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

from .errors import LeakError
from .privacy import PrivacyRules

SERVING_DIRS = ("server", "public")
MAX_REPORTED = 10


@dataclass(frozen=True)
class Finding:
    artifact: str
    location: str
    detail: str

    def __str__(self) -> str:
        return f"{self.artifact} [{self.location}]: {self.detail}"


def _text_patterns(rules: PrivacyRules) -> list[re.Pattern[str]]:
    patterns = []
    for pattern in rules.raw_identifier_patterns:
        core = pattern.pattern.lstrip("^").rstrip("$")
        patterns.append(re.compile(rf"(?<![\w,.]){core}(?![\w,.])"))
    return patterns


def _scan_parquet(path: Path, artifact: str, public: bool, rules: PrivacyRules) -> list[Finding]:
    findings: list[Finding] = []
    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow
    metadata = schema.metadata or {}
    if public and b"geo" in metadata:
        findings.append(Finding(artifact, "schema", "geometry is not allowed in browser-safe artifacts"))
    for name in schema.names:
        reason = rules.forbidden_column_reason(name, public=public)
        if reason:
            findings.append(Finding(artifact, f"column {name}", reason))
    table = parquet.read()
    for field in schema:
        if str(field.type) not in ("string", "large_string"):
            continue
        column = table[field.name]
        for pattern in rules.raw_identifier_patterns:
            matches = pc.match_substring_regex(column, pattern.pattern)
            if pc.any(matches).as_py():
                findings.append(
                    Finding(artifact, f"column {field.name}", f"value matches raw identifier format {pattern.pattern}")
                )
                break
    return findings


def _walk_json(node: object, artifact: str, trail: str, public: bool, rules: PrivacyRules, out: list[Finding]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            reason = rules.forbidden_column_reason(str(key), public=False)
            if reason:
                out.append(Finding(artifact, f"key {trail}{key}", reason))
            _walk_json(value, artifact, f"{trail}{key}.", public, rules, out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk_json(value, artifact, f"{trail}{index}.", public, rules, out)
    elif isinstance(node, str):
        if rules.raw_identifier_match(node):
            out.append(Finding(artifact, f"value {trail.rstrip('.')}", "value matches a raw identifier format"))


def _scan_json(path: Path, artifact: str, public: bool, rules: PrivacyRules) -> list[Finding]:
    findings: list[Finding] = []
    _walk_json(json.loads(path.read_text(encoding="utf-8")), artifact, "", public, rules, findings)
    return findings


def _scan_text(path: Path, artifact: str, rules: PrivacyRules) -> list[Finding]:
    text = path.read_text(encoding="utf-8")
    findings = []
    for pattern in _text_patterns(rules):
        if pattern.search(text):
            findings.append(Finding(artifact, "text", "contains a value in a raw identifier format"))
            break
    return findings


def scan_tree(root: Path, rules: PrivacyRules) -> tuple[list[Finding], int]:
    """Scan ``root`` (which holds server/, public/ and reports/). Returns findings and files scanned."""
    findings: list[Finding] = []
    scanned = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        artifact = path.relative_to(root).as_posix()
        top = artifact.split("/", 1)[0]
        public = top == "public"
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            findings += _scan_parquet(path, artifact, public, rules)
        elif suffix == ".json":
            findings += _scan_json(path, artifact, public, rules)
        elif suffix in (".md", ".txt"):
            findings += _scan_text(path, artifact, rules)
        else:
            findings.append(Finding(artifact, "file", f"unexpected file type '{suffix}' in curated output"))
        scanned += 1
    return findings, scanned


def run_leak_gate(root: Path, rules: PrivacyRules) -> dict:
    """Raise LeakError when anything leaks; otherwise return a summary for the manifest."""
    findings, scanned = scan_tree(root, rules)
    if findings:
        shown = "; ".join(str(f) for f in findings[:MAX_REPORTED])
        extra = f" (+{len(findings) - MAX_REPORTED} more)" if len(findings) > MAX_REPORTED else ""
        raise LeakError(f"Leak gate failed with {len(findings)} finding(s): {shown}{extra}")
    return {"passed": True, "files_scanned": scanned}
