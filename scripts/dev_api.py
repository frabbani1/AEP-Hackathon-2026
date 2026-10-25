#!/usr/bin/env python3
"""Serve the Lambda handler on localhost so the frontend can be developed without AWS.

    <py> scripts/dev_api.py [--port 8787] [--artifacts artifacts] [--assistant-model]

Reads the files built by scripts/curate.py and scripts/build_risk_artifacts.py (premise_risk.jsonl, calibration.json,
restoration_tree.json, zones.json, labor_standards.json, and backtest.json when present). Nothing here talks to AWS:
alert sign-ups live in memory, and the assistant writes from templates unless you pass --assistant-model, which needs
AWS credentials and BEDROCK_MODEL_ID.

Binds to 127.0.0.1 only and handles one request at a time. The API has no authentication, so do not expose this
port. The frontend dev server proxies /api to it (frontend/vite.config.ts, VITE_API_PROXY).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def set_defaults(artifacts: Path) -> None:
    risk, curated, backtest = artifacts / "risk", artifacts / "curated", artifacts / "backtest"
    for name, path in (("PREMISE_FILE", risk / "premise_risk.jsonl"), ("CALIBRATION_FILE", risk / "calibration.json"),
                       ("TREE_FILE", risk / "restoration_tree.json"), ("ZONES_FILE", risk / "zones.json"),
                       ("LABOR_FILE", curated / "labor_standards.json"), ("EVIDENCE_FILE", backtest / "backtest.json")):
        os.environ.setdefault(name, str(path))


def make_handler(lambda_handler):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self, method: str) -> None:
            parts = urlsplit(self.path)
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(min(length, 1 << 20)).decode("utf-8", "replace") if length else None
            query = dict(q.split("=", 1) for q in parts.query.split("&") if "=" in q)
            event = {"requestContext": {"http": {"method": method}}, "rawPath": parts.path, "body": body,
                     "queryStringParameters": query or None}
            result = lambda_handler(event, None)
            payload = result["body"].encode()
            self.send_response(result["statusCode"])
            for key, value in result["headers"].items():
                self.send_header(key, value)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):   # noqa: N802 (http.server naming)
            self._serve("GET")

        def do_POST(self):  # noqa: N802
            self._serve("POST")

        def log_message(self, fmt, *args):   # request lines only; never the body
            sys.stderr.write("%s %s\n" % (self.command, urlsplit(self.path).path))

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--artifacts", type=Path, default=ROOT / "artifacts")
    ap.add_argument("--assistant-model", action="store_true", help="call Bedrock (needs credentials and BEDROCK_MODEL_ID)")
    args = ap.parse_args()
    set_defaults(args.artifacts)
    if not args.assistant_model:
        os.environ.pop("BEDROCK_MODEL_ID", None)
    missing = [os.environ[k] for k in ("PREMISE_FILE", "CALIBRATION_FILE", "TREE_FILE", "ZONES_FILE", "LABOR_FILE")
               if not Path(os.environ[k]).exists()]
    if missing:
        print("missing artifacts (run scripts/curate.py and scripts/build_risk_artifacts.py first):", *missing, sep="\n  ")
        return 1
    from concierge.api.handler import lambda_handler, service
    service()                                    # load everything now, so the first request is not slow
    server = HTTPServer(("127.0.0.1", args.port), make_handler(lambda_handler))
    print(f"API on http://127.0.0.1:{args.port}  (try: curl -s -X POST localhost:{args.port}/zones -d '{{}}')")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
