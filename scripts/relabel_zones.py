#!/usr/bin/env python3
"""Give the map areas in an existing zones.json readable names ("Salem", "East Roanoke") instead of "Area 7".

    <py> scripts/relabel_zones.py [path/to/zones.json ...]      default: every artifacts/**/risk/zones.json

A rebuild (`scripts/build_risk_artifacts.py`) already names areas this way, so this is only for a zones.json built
earlier. Ids, outlines, representative homes and everything else are left exactly as they were; only the display name
changes. Standard library only (no pyarrow or h3). Re-running it is harmless.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from concierge.zones.places import name_zones  # noqa: E402


def relabel(path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    zones = doc["zones"]
    names = name_zones(zones)
    for z in zones:
        key = "label" if "label" in z or "name" not in z else "name"   # keep the key the file already uses
        z[key] = names[z["id"]]
    path.write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return len(zones)


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or sorted((ROOT / "artifacts").glob("**/risk/zones.json"))
    if not paths:
        print("no zones.json found")
        return 1
    for path in paths:
        print(f"{path}: {relabel(path)} areas named")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
