#!/usr/bin/env bash
# Call the deployed API and CloudFront. Reads URLs from infra/cdk-outputs.json (written by `make deploy`).
#   scripts/smoke.sh
# Written for macOS bash 3.2: request bodies are built with printf into variables, never with nested quotes
# inside $(...), which bash 3.2 mis-parses.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/infra/cdk-outputs.json"
[[ -f "$OUT" ]] || { echo "no $OUT; run make deploy first" >&2; exit 1; }
API=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["AepConciergeStack"]["ApiUrl"])' "$OUT")
CF=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["AepConciergeStack"]["SiteUrl"])' "$OUT")
TOK=$(python3 - "$ROOT/data/serving/risk/premise_risk.jsonl" <<'EOF'
import json, sys
for line in open(sys.argv[1]):
    r = json.loads(line)
    if r["st"] == "ok" and r["c"] == "High" and r["R"] > 5:
        print(r["t"]); break
EOF
)
BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
fail=0

# post LABEL EXPECTED URL JSON
post() {
  local status
  status=$(curl -s -m 30 -o "$BODY" -w '%{http_code}' -X POST -H 'content-type: application/json' "$3" -d "$4")
  report "$1" "$2" "$status"
}
# get LABEL EXPECTED URL
get() {
  local status
  status=$(curl -s -m 30 -o "$BODY" -w '%{http_code}' "$3")
  report "$1" "$2" "$status"
}
report() {
  if [[ "$2" == "$3" ]]; then echo "PASS  $1 ($3)"
  else echo "FAIL  $1 (expected $2, got $3) body: $(head -c 300 "$BODY")"; fail=1; fi
}

b_tok=$(printf '{"token":"%s"}' "$TOK")
b_crews=$(printf '{"token":"%s","crews":6}' "$TOK")
b_msg=$(printf '{"token":"%s","message_type":"outage_confirmed"}' "$TOK")
b_extra=$(printf '{"token":"%s","address":"x"}' "$TOK")

get  "GET /health"            200 "$API/health"
post "POST /risk"             200 "$API/risk" "$b_tok"
post "POST /restoration"      200 "$API/restoration" "$b_crews"
post "POST /guidance"         200 "$API/guidance" "$b_tok"
post "POST /alerts"           200 "$API/alerts" "$b_tok"
post "POST /messages"         200 "$API/messages" "$b_msg"
post "POST /summary"          200 "$API/summary" "$b_tok"
post "POST /zones"            200 "$API/zones" '{}'
post "unknown zone rejected"  400 "$API/zones/assess" '{"zone_id":"z_aaaaaaaaaaaa"}'
post "bad token rejected"     400 "$API/risk" '{"token":"nope"}'
post "unknown field rejected" 400 "$API/risk" "$b_extra"
post "invalid JSON rejected"  400 "$API/risk" '{bad'
get  "CloudFront /api/health" 200 "$CF/api/health"
post "CloudFront /api/risk"   200 "$CF/api/risk" "$b_tok"
get  "CloudFront labor json"  200 "$CF/curated/labor_standards.json"
get  "CloudFront calibration" 200 "$CF/serving/risk/calibration.json"
get  "premise table private"  403 "$CF/serving/risk/premise_risk.jsonl"
get  "restoration tree private" 403 "$CF/serving/risk/restoration_tree.json"
get  "map zones private"     403 "$CF/serving/risk/zones.json"
get  "parquet tables private" 403 "$CF/curated/tables/premise_dim.parquet"
get  "ETL reports private"    403 "$CF/_meta/etl_run_latest.json"
[[ $fail == 0 ]] && echo "smoke OK" || { echo "smoke FAILED" >&2; exit 1; }
