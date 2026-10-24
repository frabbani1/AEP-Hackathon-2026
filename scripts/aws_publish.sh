#!/usr/bin/env bash
# Publish curated tables and risk artifacts to the data bucket.
#
# Dry run by default (aws s3 sync --dryrun). Set APPLY=1 to upload.
#
#   export AWS_PROFILE=aep-hack
#   export DATA_BUCKET=aep-hack-data-<account-id>
#   scripts/aws_publish.sh                        # preview only
#   APPLY=1 scripts/aws_publish.sh                # upload, then re-check that nothing is left to upload
#
# Uploaded:  artifacts/curated/ (tables, labor_standards.json, drawings, manifest.json) -> s3://$DATA_BUCKET/curated/
#            artifacts/risk/                                                              -> s3://$DATA_BUCKET/serving/risk/
# Never uploaded: artifacts/curated/internal/ (circuit crosswalk, quality report).
# The data bucket uses default SSE-S3 encryption, so no per-object encryption flags are needed here.
set -euo pipefail

: "${DATA_BUCKET:?set DATA_BUCKET (e.g. aep-hack-data-<account-id>)}"
: "${AWS_PROFILE:?set AWS_PROFILE (e.g. aep-hack)}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURATED="$ROOT/artifacts/curated"
RISK="$ROOT/artifacts/risk"

for f in "$CURATED/manifest.json" "$CURATED/tables/premise_dim.parquet" "$RISK/calibration.json"; do
  [[ -f "$f" ]] || { echo "missing $f; run scripts/curate.py and scripts/build_risk_artifacts.py first" >&2; exit 1; }
done

echo "account: $(aws sts get-caller-identity --query Account --output text)   bucket: $DATA_BUCKET"

sync_all() {  # $1 = extra flag ("--dryrun" or "")
  aws s3 sync "$CURATED" "s3://$DATA_BUCKET/curated/" --exclude "internal/*" --delete $1
  aws s3 sync "$RISK" "s3://$DATA_BUCKET/serving/risk/" --delete $1
}

if [[ "${APPLY:-0}" != "1" ]]; then
  echo "DRY RUN (nothing uploaded). Re-run with APPLY=1 to upload."
  sync_all "--dryrun"
  exit 0
fi

sync_all ""
echo "re-checking: a second dry run must list nothing to upload or delete ..."
leftover="$(sync_all "--dryrun" | grep -cE '^\(dryrun\) (upload|delete):' || true)"
[[ "$leftover" == "0" ]] && echo "OK: bucket matches local artifacts" || { echo "MISMATCH: $leftover file(s) differ" >&2; exit 1; }
