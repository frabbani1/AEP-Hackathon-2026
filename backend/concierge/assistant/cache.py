"""Draft cache (design D4). Only guard-passed drafts, only in placeholder form, so a hit never carries another customer's number.

The key holds every field that changes the wording. It holds no token, no premise value and no text from a customer.
In-memory LRU per Lambda instance: a cold start empties it.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

MAX_ENTRIES = 512
TTL_SECONDS = 24 * 3600


def cache_key(*, kind: str, card: Mapping, selected_ids: Sequence[str], prompt_version: str, model_id: str | None,
              library_version: str | None) -> str:
    material = {
        "kind": kind, "stage": card["stage"], "band": card["band"],
        "range_bucket": (card.get("range") or {}).get("bucket"),
        "has_declared_need": card["has_declared_need"],
        "has_range": bool(card.get("range")), "has_prior_range": bool(card.get("prior_range")),
        "confidence": (card.get("confidence") or {}).get("level"),
        "scenario": card["scenario"]["preset"], "extrapolated": card["scenario"]["extrapolated"],
        "status": card["status"], "channel": card.get("channel"),
        "factors": sorted(f"{f['id']}:{f['direction']}" for f in card["top_factors"]),
        "items": list(selected_ids), "prompt_version": prompt_version, "model_id": model_id,
        "schema_version": card["schema_version"], "library_version": library_version,
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class DraftCache:
    def __init__(self, max_entries: int = MAX_ENTRIES, ttl_seconds: float = TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.max_entries, self.ttl, self.clock = max_entries, ttl_seconds, clock
        self._data: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        stored_at, draft = entry
        if self.clock() - stored_at >= self.ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return json.loads(json.dumps(draft))      # a copy, so a caller cannot change the stored draft

    def put(self, key: str, draft: Mapping) -> None:
        self._data[key] = (self.clock(), json.loads(json.dumps(draft)))
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def discard(self, key: str) -> None:
        self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)
