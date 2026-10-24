#!/usr/bin/env bash
# Publish browser-safe files to the PUBLIC bucket that CloudFront serves.
#
# Dry run by default (aws s3 sync --dryrun). Set APPLY=1 to upload and clear the CDN cache.
#
#   export AWS_PROFILE=aep-hack AWS_REGION=us-east-1
#   scripts/aws_publish_public.sh                 # preview only
#   APPLY=1 scripts/aws_publish_public.sh         # upload, re-check, invalidate CloudFront
#
# The bucket and distribution come from the AepConcierge stack outputs. Override with PUBLIC_BUCKET / DISTRIBUTION_ID.
#
# ALLOW-LIST. These are the only files that can reach the public bucket. Add to it deliberately and review each addition
# against the privacy rules in CLAUDE.md:
#   artifacts/curated/labor_standards.json    labor hours by unit, access adders, indirect labor multiplier
#   artifacts/curated/drawings/page-*.png     structure drawings, one image per PDF page
# Never published here: curated tables (tokens, circuits), risk artifacts, internal/. Those stay in the private data bucket.
# Anything in the public bucket that is not on the allow-list is deleted by the sync.
set -euo pipefail

: "${AWS_PROFILE:?set AWS_PROFILE (e.g. aep-hack)}"
STACK="${STACK:-AepConcierge}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURATED="$ROOT/artifacts/curated"

[[ -f "$CURATED/labor_standards.json" ]] || { echo "missing $CURATED/labor_standards.json; run scripts/curate.py first" >&2; exit 1; }

stack_output() {  # $1 = output key
  local v
  v="$(aws cloudformation describe-stacks --stack-name "$STACK" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text)"
  [[ -n "$v" && "$v" != "None" ]] || { echo "stack $STACK has no output $1; deploy it first (docs/aws-setup.md step 14)" >&2; exit 1; }
  printf '%s' "$v"
}

PUBLIC_BUCKET="${PUBLIC_BUCKET:-$(stack_output AssetsBucket)}"
DISTRIBUTION_ID="${DISTRIBUTION_ID:-$(stack_output DistributionId)}"

# Stage exactly the allow-list. cp -p keeps modification times, so an unchanged file is not re-uploaded on the next sync.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -p "$CURATED/labor_standards.json" "$STAGE/"
shopt -s nullglob
pages=("$CURATED"/drawings/page-*.png)
if ((${#pages[@]})); then
  mkdir "$STAGE/drawings"
  cp -p "${pages[@]}" "$STAGE/drawings/"
else
  echo "note: no drawings found (pdftoppm missing when curating?); publishing labor_standards.json only" >&2
fi

echo "account: $(aws sts get-caller-identity --query Account --output text)   public bucket: $PUBLIC_BUCKET"

sync_all() {  # $1 = extra flag ("--dryrun" or "")
  aws s3 sync "$STAGE" "s3://$PUBLIC_BUCKET/" --delete --cache-control "public, max-age=3600" $1
}

if [[ "${APPLY:-0}" != "1" ]]; then
  echo "DRY RUN (nothing uploaded). Re-run with APPLY=1 to upload."
  sync_all "--dryrun"
  exit 0
fi

sync_all ""
echo "re-checking: a second dry run must list nothing to upload or delete ..."
leftover="$(sync_all "--dryrun" | grep -cE '^\(dryrun\) (upload|delete):' || true)"
[[ "$leftover" == "0" ]] && echo "OK: bucket matches the allow-list" || { echo "MISMATCH: $leftover file(s) differ" >&2; exit 1; }

aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" --query Invalidation.Id --output text \
  | sed 's/^/invalidation started: /'
