#!/usr/bin/env python3
"""Serve the Lambda handler on localhost so the frontend can be developed without AWS.

    <py> scripts/dev_api.py [--port 8787] [--artifacts artifacts] [--assistant-model]

Reads the files built by scripts/curate.py and scripts/build_risk_artifacts.py (premise_risk.jsonl, calibration.json,
restoration_tree.json, zones.json, labor_standards.json, and backtest.json when present). Nothing here talks to AWS:
alert sign-ups live in memory, and the assistant writes from templates unless you pass --assistant-model, which needs
AWS credentials and BEDROCK_MODEL_ID.

Real emails ("email me this alert") need SMTP settings. Put SMTP_HOST, SMTP_PORT, SMTP_USER and SMTP_PASSWORD in
`.env.local` at the repo root (git-ignored) or export them. Without them the button shows the email and says nothing was sent.

Binds to 127.0.0.1 only and handles one request at a time. The API has no authentication, so do not expose this
port. The frontend dev server proxies /api to it (frontend/vite.config.ts, VITE_API_PROXY).
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


RISK_FILES = ("premise_risk.jsonl", "calibration.json", "restoration_tree.json", "zones.json")


def first_with(candidates: list[Path], files: tuple[str, ...]) -> Path:
    """The first folder that holds every file in `files` (so risk rows and zones always come from the same build).
    When none is complete, the first candidate is returned and main() reports what is missing."""
    return next((c for c in candidates if all((c / f).exists() for f in files)), candidates[0])


def set_defaults(artifacts: Path) -> None:
    """Pick the artifact files. The full local build (`artifacts/local/`) wins over `artifacts/`, because
    `artifacts/risk/` is often only premise_risk.jsonl and calibration.json. The backtest report is committed under
    `artifacts/backtest/`, so it is found from either root. Any variable already set in the environment is kept."""
    roots = [artifacts / "local", artifacts] if artifacts.name != "local" else [artifacts, artifacts.parent]
    risk = first_with([r / "risk" for r in roots], RISK_FILES)
    curated = first_with([r / "curated" for r in roots], ("labor_standards.json",))
    backtest = first_with([r / "backtest" for r in roots], ("backtest.json",))
    for name, path in (("PREMISE_FILE", risk / "premise_risk.jsonl"), ("CALIBRATION_FILE", risk / "calibration.json"),
                       ("TREE_FILE", risk / "restoration_tree.json"), ("ZONES_FILE", risk / "zones.json"),
                       ("LABOR_FILE", curated / "labor_standards.json"), ("EVIDENCE_FILE", backtest / "backtest.json")):
        os.environ.setdefault(name, str(path))


def load_env_file(path: Path) -> list[str]:
    """Read KEY=VALUE lines from a local settings file (`.env.local`, git-ignored) into the environment. Variables already
    set in the shell win. Returns the names it set, never the values, so nothing secret is printed."""
    if not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            names.append(key)
    return names


def trust_system_certs() -> None:
    """python.org builds of Python on macOS ship without CA certificates, so the weather call to Open-Meteo fails with
    CERTIFICATE_VERIFY_FAILED and every answer silently falls back to the default storm. Use the system bundle (or
    certifi's) for this local server only. A bundle you set yourself is kept."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    import ssl
    if ssl.get_default_verify_paths().cafile and Path(ssl.get_default_verify_paths().cafile).exists():
        return
    candidates = [Path("/etc/ssl/cert.pem"), Path("/opt/homebrew/etc/ca-certificates/cert.pem")]
    try:
        import certifi
        candidates.insert(0, Path(certifi.where()))
    except ImportError:
        pass
    for path in candidates:
        if path.exists():
            os.environ["SSL_CERT_FILE"] = str(path)
            return


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
    load_env_file(ROOT / ".env.local")
    set_defaults(args.artifacts)
    trust_system_certs()
    if not args.assistant_model:
        os.environ.pop("BEDROCK_MODEL_ID", None)
    missing = [os.environ[k] for k in ("PREMISE_FILE", "CALIBRATION_FILE", "TREE_FILE", "ZONES_FILE", "LABOR_FILE")
               if not Path(os.environ[k]).exists()]
    if missing:
        print("missing artifacts (run scripts/curate.py and scripts/build_risk_artifacts.py first):", *missing, sep="\n  ")
        return 1
    from concierge.api.handler import lambda_handler, service
    service()                                    # load everything now, so the first request is not slow
    try:
        server = HTTPServer(("127.0.0.1", args.port), make_handler(lambda_handler))
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        print(f"\nPort {args.port} is already in use, so the API is probably already running (an earlier `make dev`).\n"
              f"  - To use it, just open http://localhost:5173\n"
              f"  - To restart it, stop the old one first:\n"
              f"      lsof -nP -iTCP:{args.port} -sTCP:LISTEN     (note the PID)\n"
              f"      kill <PID>\n"
              f"  - Or run on another port: scripts/dev_api.py --port 8790 (and set VITE_API_PROXY=http://127.0.0.1:8790)",
              file=sys.stderr)
        return 1
    print(f"zones: {os.environ['ZONES_FILE']}\nevidence: {os.environ['EVIDENCE_FILE']}"
          f"{'' if Path(os.environ['EVIDENCE_FILE']).exists() else '  (missing: /evidence will be unavailable)'}")
    print("email alerts: " + ("ON (SMTP settings found)" if service().mailer is not None else
                              "off (add SMTP_HOST, SMTP_USER, SMTP_PASSWORD to .env.local to send real emails)"))
    print(f"API on http://127.0.0.1:{args.port}  (try: curl -s -X POST localhost:{args.port}/zones -d '{{}}')")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
