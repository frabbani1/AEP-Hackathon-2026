#!/usr/bin/env bash
# Build the frontend and publish it to the stack's web bucket, then clear the CloudFront cache.
#   scripts/publish_web.sh
# Reads the bucket and distribution from infra/cdk-outputs.json (written by `make deploy`). The app calls same-origin
# /api, which CloudFront routes to the API, so no API URL is baked into the build.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/infra/cdk-outputs.json"
[[ -f "$OUT" ]] || { echo "no $OUT; run make deploy first" >&2; exit 1; }
read_out() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["AepConciergeStack"][sys.argv[2]])' "$OUT" "$1"; }
BUCKET=$(read_out WebBucketName); DIST=$(read_out DistributionId); SITE=$(read_out SiteUrl)
cd "$ROOT/frontend"
npm ci --no-audit --no-fund
npm run build
# Hashed assets are cached for a year; index.html must always be re-fetched.
aws s3 sync dist "s3://$BUCKET" --delete --exclude index.html --cache-control "public,max-age=31536000,immutable"
aws s3 cp dist/index.html "s3://$BUCKET/index.html" --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/index.html" "/" "/map" >/dev/null
echo "published to $SITE"
