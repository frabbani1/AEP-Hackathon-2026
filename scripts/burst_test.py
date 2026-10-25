#!/usr/bin/env python3
"""Burst test for the deployed assistant (spec-06 task 8.3): N concurrent POST /assistant calls with the model on.

    python scripts/burst_test.py                    # 30 calls at the ApiUrl in infra/cdk-outputs.json
    python scripts/burst_test.py --calls 50 --url https://xxxx.execute-api.us-east-2.amazonaws.com

What it records: HTTP status counts, how many answers came from the model, the cache or the template fallback, the
guard's most common rejection reasons, p50/p95/max latency, and the circuit-breaker state before and after.

Cost and risk: each call can invoke Bedrock once (a few hundred tokens on Nova 2 Lite). The requests vary kind and
storm scenario so they do not all hit the draft cache. The API has no authentication and a light throttle (50 req/s),
so 30 calls will not trip it. Run it only against your own deployed stack.

Tokens come from data/serving/risk/premise_risk.jsonl (the last ETL run's file, same salt as the deployed table).
No token, draft or answer text is printed.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = ["explanation", "prep_before", "actions_during", "actions_after", "msg_pre_storm", "msg_outage_confirmed",
         "msg_estimate_updated", "msg_restoration_complete"]
SEVERITIES = ["sep-2024-like", "typical", "feb-2025-like", "beyond-observed"]


def api_url(arg: str | None) -> str:
    if arg:
        return arg.rstrip("/")
    out = ROOT / "infra" / "cdk-outputs.json"
    if not out.exists():
        raise SystemExit("no infra/cdk-outputs.json; run `make deploy` first or pass --url")
    return json.loads(out.read_text())["AepConciergeStack"]["ApiUrl"].rstrip("/")


def tokens(path: Path, n: int) -> list[str]:
    """A spread of rated premises: High, Medium and Low, so the cache keys differ."""
    by_band: dict[str, list[str]] = {"High": [], "Medium": [], "Low": []}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("st") == "ok" and len(by_band[r["c"]]) < n:
                by_band[r["c"]].append(r["t"])
            if all(len(v) >= n for v in by_band.values()):
                break
    return [t for group in zip(*by_band.values()) for t in group]


def call(url: str, body: dict) -> tuple[int, dict, float]:
    req = urllib.request.Request(f"{url}/assistant", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        try:
            payload = json.load(e)
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload, time.perf_counter() - t0
    except Exception:  # noqa: BLE001 - timeouts and resets count as failures
        return 0, {}, time.perf_counter() - t0


def health(url: str) -> dict:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=15) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calls", type=int, default=30)
    ap.add_argument("--url")
    ap.add_argument("--premises", type=Path, default=ROOT / "data/serving/risk/premise_risk.jsonl")
    args = ap.parse_args()
    if not 1 <= args.calls <= 200:
        raise SystemExit("--calls must be from 1 to 200")
    url = api_url(args.url)
    toks = tokens(args.premises, 10)
    if not toks:
        raise SystemExit(f"no rated premises in {args.premises}")

    before = health(url)
    if before.get("assistant_breaker") is None:
        raise SystemExit("/health shows no assistant breaker: the deployed Lambda is older than spec-06 or has no "
                         "BEDROCK_MODEL_ID. Run `make deploy` first.")
    print(f"before: breaker={before.get('assistant_breaker')} model={before.get('messages_model')}")
    bodies = [{"token": toks[i % len(toks)], "kind": KINDS[i % len(KINDS)], "severity": SEVERITIES[(i // 2) % 4],
               "use_llm": True} for i in range(args.calls)]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.calls) as pool:
        results = list(pool.map(lambda b: call(url, b), bodies))
    wall = time.perf_counter() - t0

    status = Counter(s for s, _, _ in results)
    source = Counter(d.get("source") for s, d, _ in results if s == 200)
    reasons = Counter(r for s, d, _ in results if s == 200 for r in (d.get("guard") or {}).get("reasons", []))
    lat = sorted(t for _, _, t in results)
    p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
    after = health(url)
    print(f"{args.calls} concurrent calls in {wall:.1f}s")
    print("http status:", dict(status))
    throttled = sum(1 for st, d, _ in results if st == 503 and "error" not in d)
    if throttled:
        print(f"  {throttled} calls got API Gateway's own 503 (no JSON error from our code). That is Lambda refusing a "
              "call because every execution slot is busy. This account allows 10 concurrent Lambda executions "
              "(aws lambda get-account-settings). Callers should retry a 503 after a short wait.")
    print("answers by source:", dict(source), f" (fallback rate {source.get('template', 0) / max(1, sum(source.values())):.0%})")
    print("top guard reasons:", reasons.most_common(5))
    print(f"latency s: p50={statistics.median(lat):.2f} p95={p95:.2f} max={lat[-1]:.2f}")
    print(f"after: breaker={after.get('assistant_breaker')}")
    ok = status.get(200, 0) == args.calls
    print("RESULT:", "every call returned a valid answer" if ok else "some calls failed (see http status)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
