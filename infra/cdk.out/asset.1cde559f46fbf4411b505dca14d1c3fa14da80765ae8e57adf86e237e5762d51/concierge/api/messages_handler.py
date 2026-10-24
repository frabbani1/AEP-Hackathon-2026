"""Lambda for POST /messages.

Placeholder until the narration layer exists. When it does, the rule from CLAUDE.md applies: Bedrock only explains a
whitelisted fact card built by the engines, and any number in its output that is not in the fact card makes the
handler fall back to a template. This function has no Bedrock permission yet (see infra/lib/api.ts).
It reads nothing from the request and returns no numbers.
"""
from __future__ import annotations

from .http import error


def handler(event: dict, context=None) -> dict:
    return error(501, "not_implemented", "Customer messages are not available yet.")
