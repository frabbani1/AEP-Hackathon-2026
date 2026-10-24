#!/usr/bin/env bash
# Least-privilege roles for the two-bucket data layer. IAM roles are free. Safe to re-run (updates in place).
#
#   export RAW_BUCKET=... CLEAN_BUCKET=... AWS_REGION=us-east-2
#   scripts/setup_iam.sh
#
# aep-hack-etl-role  read raw/ + read/write staging/ in the raw bucket, read/write curated/ serving/ _meta/ in the
#                    clean bucket, read the token salt. Assumable by this account's ISB SSO users (role chaining).
# aep-hack-api-role  read-only on curated/ and serving/ in the clean bucket. Nothing on the raw bucket (explicit
#                    deny in both the role policy and the raw bucket policy). Assumable by Lambda only.
set -euo pipefail
: "${RAW_BUCKET:?}" "${CLEAN_BUCKET:?}" "${AWS_REGION:?}"
ACCT=$(aws sts get-caller-identity --query Account --output text)
TABLE="${TABLE:-aep-premise-risk}"
ALERTS_TABLE="${ALERTS_TABLE:-aep-demo-alerts}"                        # demo sign-ups, mock outbox, alert audit log
BEDROCK_PROFILE="${BEDROCK_PROFILE:-us.amazon.nova-2-lite-v1:0}"      # cross-region inference profile the API calls
BEDROCK_FOUNDATION="${BEDROCK_PROFILE#*.}"                              # underlying model id (profile minus "us.")
# CloudFront may read three public-safe paths of the clean bucket (set after `make deploy`; kept on re-runs).
CF_ARN="${CLOUDFRONT_DISTRIBUTION_ARN:-}"
if [[ -z "$CF_ARN" && -f "$(dirname "${BASH_SOURCE[0]}")/../infra/cdk-outputs.json" ]]; then
  CF_ARN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("AepConciergeStack",{}).get("DistributionArn",""))' \
    "$(dirname "${BASH_SOURCE[0]}")/../infra/cdk-outputs.json")
fi
SSO_PATTERN="arn:aws:iam::$ACCT:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_*"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

ensure_role() {  # name trust-file description
  if aws iam get-role --role-name "$1" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$1" --policy-document "file://$2"
  else
    aws iam create-role --role-name "$1" --assume-role-policy-document "file://$2" --description "$3" \
      --max-session-duration 3600 --tags Key=project,Value=aep-hack >/dev/null
  fi
  echo "role: $1"
}

# ---------- ETL role ----------
cat >"$TMP/etl-trust.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":"arn:aws:iam::$ACCT:root"},
 "Action":"sts:AssumeRole","Condition":{"ArnLike":{"aws:PrincipalArn":"$SSO_PATTERN"}}}]}
EOF
cat >"$TMP/etl-policy.json" <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"ListBoth","Effect":"Allow","Action":"s3:ListBucket",
  "Resource":["arn:aws:s3:::$RAW_BUCKET","arn:aws:s3:::$CLEAN_BUCKET"]},
 {"Sid":"ReadRawAndStaging","Effect":"Allow","Action":"s3:GetObject",
  "Resource":["arn:aws:s3:::$RAW_BUCKET/raw/*","arn:aws:s3:::$RAW_BUCKET/staging/*"]},
 {"Sid":"WriteStaging","Effect":"Allow","Action":["s3:PutObject","s3:DeleteObject"],
  "Resource":"arn:aws:s3:::$RAW_BUCKET/staging/*"},
 {"Sid":"ReadWriteClean","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],
  "Resource":["arn:aws:s3:::$CLEAN_BUCKET/curated/*","arn:aws:s3:::$CLEAN_BUCKET/serving/*",
              "arn:aws:s3:::$CLEAN_BUCKET/_meta/*"]},
 {"Sid":"ValidateBucketSettings","Effect":"Allow",
  "Action":["s3:GetBucketPublicAccessBlock","s3:GetBucketPolicyStatus","s3:GetEncryptionConfiguration",
            "s3:GetBucketVersioning"],
  "Resource":["arn:aws:s3:::$RAW_BUCKET","arn:aws:s3:::$CLEAN_BUCKET"]},
 {"Sid":"ReadSalt","Effect":"Allow","Action":"ssm:GetParameter",
  "Resource":"arn:aws:ssm:$AWS_REGION:$ACCT:parameter/aep-hack/token-salt"},
 {"Sid":"NeverTouchRawOriginals","Effect":"Deny","Action":["s3:PutObject","s3:DeleteObject","s3:DeleteObjectVersion"],
  "Resource":"arn:aws:s3:::$RAW_BUCKET/raw/*"}
]}
EOF
ensure_role aep-hack-etl-role "$TMP/etl-trust.json" "aep-hack ETL: raw bucket -> clean bucket"
aws iam put-role-policy --role-name aep-hack-etl-role --policy-name etl-data-access \
  --policy-document "file://$TMP/etl-policy.json"

# ---------- API role ----------
cat >"$TMP/api-trust.json" <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},
 "Action":"sts:AssumeRole"}]}
EOF
cat >"$TMP/api-policy.json" <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadCleanServing","Effect":"Allow","Action":"s3:GetObject",
  "Resource":["arn:aws:s3:::$CLEAN_BUCKET/curated/*","arn:aws:s3:::$CLEAN_BUCKET/serving/*"]},
 {"Sid":"ListCleanServing","Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$CLEAN_BUCKET",
  "Condition":{"StringLike":{"s3:prefix":["curated/*","serving/*"]}}},
 {"Sid":"ReadPremiseTable","Effect":"Allow","Action":"dynamodb:GetItem",
  "Resource":"arn:aws:dynamodb:$AWS_REGION:$ACCT:table/$TABLE"},
 {"Sid":"DemoAlertsTable","Effect":"Allow",
  "Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem","dynamodb:Query"],
  "Resource":"arn:aws:dynamodb:$AWS_REGION:$ACCT:table/$ALERTS_TABLE"},
 {"Sid":"InvokeMessageModel","Effect":"Allow","Action":"bedrock:InvokeModel",
  "Resource":["arn:aws:bedrock:$AWS_REGION:$ACCT:inference-profile/$BEDROCK_PROFILE",
              "arn:aws:bedrock:*::foundation-model/$BEDROCK_FOUNDATION"]},
 {"Sid":"DenyRawBucket","Effect":"Deny","Action":"s3:*",
  "Resource":["arn:aws:s3:::$RAW_BUCKET","arn:aws:s3:::$RAW_BUCKET/*"]},
 {"Sid":"DenyAnyWrite","Effect":"Deny",
  "Action":["s3:PutObject","s3:DeleteObject","s3:DeleteObjectVersion","s3:PutBucketPolicy","s3:PutObjectAcl"],
  "Resource":"*"},
 {"Sid":"DenySalt","Effect":"Deny","Action":"ssm:*","Resource":"*"}
]}
EOF
ensure_role aep-hack-api-role "$TMP/api-trust.json" "aep-hack API: read-only on clean bucket"
aws iam put-role-policy --role-name aep-hack-api-role --policy-name clean-read-only \
  --policy-document "file://$TMP/api-policy.json"
aws iam attach-role-policy --role-name aep-hack-api-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

ETL_ARN=$(aws iam get-role --role-name aep-hack-etl-role --query Role.Arn --output text)
API_ARN=$(aws iam get-role --role-name aep-hack-api-role --query Role.Arn --output text)
sleep 10   # new role ARNs must propagate before a bucket policy can name them

# ---------- bucket policies (defense in depth) ----------
cat >"$TMP/raw-bucket.json" <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*",
  "Resource":["arn:aws:s3:::$RAW_BUCKET","arn:aws:s3:::$RAW_BUCKET/*"],"Condition":{"Bool":{"aws:SecureTransport":"false"}}},
 {"Sid":"DenyApiRole","Effect":"Deny","Principal":{"AWS":"$API_ARN"},"Action":"s3:*",
  "Resource":["arn:aws:s3:::$RAW_BUCKET","arn:aws:s3:::$RAW_BUCKET/*"]}
]}
EOF
CF_STATEMENT=""
if [[ -n "$CF_ARN" ]]; then
  CF_STATEMENT=",
 {\"Sid\":\"CloudFrontReadPublicSafePaths\",\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"cloudfront.amazonaws.com\"},
  \"Action\":\"s3:GetObject\",
  \"Resource\":[\"arn:aws:s3:::$CLEAN_BUCKET/curated/drawings/*\",\"arn:aws:s3:::$CLEAN_BUCKET/curated/labor_standards.json\",
              \"arn:aws:s3:::$CLEAN_BUCKET/serving/risk/calibration.json\"],
  \"Condition\":{\"StringEquals\":{\"AWS:SourceArn\":\"$CF_ARN\"}}}"
fi
cat >"$TMP/clean-bucket.json" <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*",
  "Resource":["arn:aws:s3:::$CLEAN_BUCKET","arn:aws:s3:::$CLEAN_BUCKET/*"],"Condition":{"Bool":{"aws:SecureTransport":"false"}}},
 {"Sid":"ApiRoleReadOnly","Effect":"Deny","Principal":{"AWS":"$API_ARN"},
  "NotAction":["s3:GetObject","s3:ListBucket"],
  "Resource":["arn:aws:s3:::$CLEAN_BUCKET","arn:aws:s3:::$CLEAN_BUCKET/*"]}$CF_STATEMENT
]}
EOF
aws s3api put-bucket-policy --bucket "$RAW_BUCKET" --policy "file://$TMP/raw-bucket.json"
aws s3api put-bucket-policy --bucket "$CLEAN_BUCKET" --policy "file://$TMP/clean-bucket.json"
echo "etl: $ETL_ARN"
echo "api: $API_ARN"
echo "bucket policies updated on $RAW_BUCKET and $CLEAN_BUCKET (CloudFront statement: ${CF_ARN:-not set yet})"
