#!/usr/bin/env bash
# Build the React app and deploy it to Amplify Hosting as a manual deployment (no Git provider, no access tokens).
#
# Dry run by default: reads the stack outputs, builds the site and zips it, but uploads nothing.
# Set APPLY=1 to upload and publish.
#
#   export AWS_PROFILE=aep-hack AWS_REGION=us-east-1
#   scripts/amplify_deploy.sh                     # build + zip only
#   APPLY=1 scripts/amplify_deploy.sh             # upload, publish, wait for the deployment to finish
#
# The API and CDN URLs are read from the AepConcierge stack outputs and baked into the build (VITE_API_URL,
# VITE_ASSETS_URL). Needs: the stack deployed (docs/aws-setup.md step 14), Node 20.19+ or 22.12+, aws, curl, zip.
set -euo pipefail

: "${AWS_PROFILE:?set AWS_PROFILE (e.g. aep-hack)}"
STACK="${STACK:-AepConcierge}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"

stack_output() {  # $1 = output key
  local v
  v="$(aws cloudformation describe-stacks --stack-name "$STACK" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text)"
  [[ -n "$v" && "$v" != "None" ]] || { echo "stack $STACK has no output $1; deploy it first (docs/aws-setup.md step 14)" >&2; exit 1; }
  printf '%s' "$v"
}

APP_ID="$(stack_output AmplifyAppId)"
BRANCH="$(stack_output AmplifyBranch)"
SITE_URL="$(stack_output AmplifyUrl)"
export VITE_API_URL="$(stack_output ApiUrl)"
export VITE_ASSETS_URL="$(stack_output AssetsUrl)"

echo "account: $(aws sts get-caller-identity --query Account --output text)   app: $APP_ID ($BRANCH)"
echo "API:    $VITE_API_URL"
echo "assets: $VITE_ASSETS_URL"

(cd "$FRONTEND" && npm ci --no-audit --no-fund && npm run build)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ZIP="$WORK/site.zip"
(cd "$FRONTEND/dist" && zip -qr "$ZIP" .)
echo "built $(unzip -Z1 "$ZIP" | grep -vc '/$') files, $(du -h "$ZIP" | cut -f1) zipped"

if [[ "${APPLY:-0}" != "1" ]]; then
  echo "DRY RUN (nothing uploaded). Re-run with APPLY=1 to deploy to $SITE_URL"
  exit 0
fi

read -r JOB_ID UPLOAD_URL < <(aws amplify create-deployment --app-id "$APP_ID" --branch-name "$BRANCH" \
  --query '[jobId, zipUploadUrl]' --output text)
curl --fail --silent --show-error -T "$ZIP" "$UPLOAD_URL"
aws amplify start-deployment --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" > /dev/null

status=""
for _ in $(seq 1 60); do
  status="$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" \
              --query job.summary.status --output text)"
  case "$status" in
    SUCCEED) echo "deployed: $SITE_URL"; exit 0 ;;
    FAILED|CANCELLED) echo "deployment $JOB_ID ended with status $status; see the Amplify console" >&2; exit 1 ;;
  esac
  sleep 5
done
echo "deployment $JOB_ID still $status after 5 minutes; check the Amplify console" >&2
exit 1
