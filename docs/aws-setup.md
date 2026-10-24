# AWS setup, step by step

This sets up the **data layer** on AWS: two private S3 buckets, an encrypted key, a DynamoDB lookup table, a
least-privilege role for the future API, and a budget alarm. It does **not** create Lambda, API Gateway, CloudFront or
Amplify. Those come next, through CDK, after the data is in place and checked.

Every command here changes your AWS account, so run them yourself and read each one first. The two upload scripts
(`scripts/aws_publish.sh`, `scripts/load_dynamodb.py`) are dry runs unless you opt in.

Expected cost for this guide: well under $5 a month. S3 holds about 25 MB, DynamoDB on-demand holds about 117k small
items, and KMS is $1 a month per key. Bedrock calls are the only real variable cost, and they come later.

## What you will have at the end

```
s3://aep-hack-raw-<acct>/    private, KMS key, versioned     raw originals (optional, restricted)
s3://aep-hack-data-<acct>/   private, SSE-S3, versioned      curated/  serving/risk/
DynamoDB aep-premise-risk    on-demand, key = token           one item per premise
IAM role aep-hack-api-role   read-only on the data bucket + table, nothing on the raw bucket
SSM /aep-hack/token-salt     SecureString                     the token salt
Budget aep-hack-monthly      $100, emails at 50% and 80%
```

## Step 0. Before you start

- An AWS account you control. Do not work as the root user.
- Pick one region and use it everywhere. These steps use `us-east-1`. Check that your chosen Bedrock Claude model is
  offered in the region you pick.
- Run the local pipeline first (see `docs/data-cleaning.md`) so `artifacts/curated/` and `artifacts/risk/` exist.

## Step 1. Install the CLI and sign in

```bash
brew install awscli
aws --version                          # should say aws-cli/2.x
aws configure sso                      # prompts for start URL, SSO region, account, role; name the profile aep-hack
aws sso login --profile aep-hack       # opens a browser
export AWS_PROFILE=aep-hack AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity            # confirm the account id and role are the ones you expect
```

`aws configure sso` needs IAM Identity Center enabled in the account. If your organizer gave you an access portal URL,
use that as the start URL. If you only have an IAM user, create a profile with `aws configure --profile aep-hack` and
give that user only what the steps below need, not AdministratorAccess if you can avoid it.

## Step 2. Budget alarm (do this first)

A budget emails you, it does not stop spending. Set it before creating anything.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

cat > /tmp/budget.json <<'EOF'
{"BudgetName":"aep-hack-monthly","BudgetLimit":{"Amount":"100","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}
EOF
cat > /tmp/notify.json <<'EOF'
[
 {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":50,"ThresholdType":"PERCENTAGE"},
  "Subscribers":[{"SubscriptionType":"EMAIL","Address":"YOU@example.com"}]},
 {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},
  "Subscribers":[{"SubscriptionType":"EMAIL","Address":"YOU@example.com"}]}
]
EOF
# edit YOU@example.com first
aws budgets create-budget --account-id "$ACCOUNT_ID" --budget file:///tmp/budget.json --notifications-with-subscribers file:///tmp/notify.json
```

## Step 3. Names

```bash
export RAW_BUCKET=aep-hack-raw-$ACCOUNT_ID
export DATA_BUCKET=aep-hack-data-$ACCOUNT_ID
export TABLE=aep-premise-risk
```

S3 bucket names are global, so the account id suffix keeps them unique.

## Step 4. Encryption key for the raw bucket

```bash
KEY_ARN=$(aws kms create-key --description "aep-hack raw data" --query KeyMetadata.Arn --output text)
aws kms create-alias --alias-name alias/aep-hack-raw --target-key-id "$KEY_ARN"
aws kms enable-key-rotation --key-id "$KEY_ARN"
echo "$KEY_ARN"
```

Only the raw bucket gets a customer key, so only people and roles you grant on that key can read raw data. The data
bucket uses S3's own encryption, which keeps later CloudFront access simple.

## Step 5. Create the two buckets

`us-east-1` takes no location constraint. In any other region add
`--create-bucket-configuration LocationConstraint=$AWS_REGION`.

```bash
for B in "$RAW_BUCKET" "$DATA_BUCKET"; do
  aws s3api create-bucket --bucket "$B" --region us-east-1
  aws s3api put-public-access-block --bucket "$B" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-versioning --bucket "$B" --versioning-configuration Status=Enabled
done

# raw bucket: encrypt with the KMS key by default
aws s3api put-bucket-encryption --bucket "$RAW_BUCKET" --server-side-encryption-configuration \
  "{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"$KEY_ARN\"},\"BucketKeyEnabled\":true}]}"

# data bucket: S3-managed encryption
aws s3api put-bucket-encryption --bucket "$DATA_BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# both: refuse any request that is not HTTPS
for B in "$RAW_BUCKET" "$DATA_BUCKET"; do
  aws s3api put-bucket-policy --bucket "$B" --policy "{\"Version\":\"2012-10-17\",\"Statement\":[{
    \"Sid\":\"DenyInsecureTransport\",\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":\"s3:*\",
    \"Resource\":[\"arn:aws:s3:::$B\",\"arn:aws:s3:::$B/*\"],
    \"Condition\":{\"Bool\":{\"aws:SecureTransport\":\"false\"}}}]}"
done

# both: drop old object versions after 30 days so versioning does not grow forever
cat > /tmp/lifecycle.json <<'EOF'
{"Rules":[{"ID":"expire-old-versions","Status":"Enabled","Filter":{},
 "NoncurrentVersionExpiration":{"NoncurrentDays":30},
 "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}
EOF
for B in "$RAW_BUCKET" "$DATA_BUCKET"; do
  aws s3api put-bucket-lifecycle-configuration --bucket "$B" --lifecycle-configuration file:///tmp/lifecycle.json
done
```

## Step 6. Raw files (optional)

Do you need the raw files in AWS? Only for reproducibility. The raw customer `.dbf` holds addresses and the
life-support flag, and the hospital layer holds names and addresses. If you skip this step, nothing sensitive is in
the cloud at all, and the curated tables are enough to run the app. Your teammates can rebuild from Git LFS.

If you do upload them:

```bash
aws s3 sync data/ "s3://$RAW_BUCKET/raw/data/" --sse aws:kms --sse-kms-key-id "$KEY_ARN" --dryrun   # preview
aws s3 sync data/ "s3://$RAW_BUCKET/raw/data/" --sse aws:kms --sse-kms-key-id "$KEY_ARN"            # upload
aws s3 cp docs/structure_drawings.pdf "s3://$RAW_BUCKET/raw/docs/" --sse aws:kms --sse-kms-key-id "$KEY_ARN"
```

Never grant the API role anything on this bucket.

## Step 7. Store the token salt

The salt makes premise tokens unguessable. Create it once and keep it. Losing it changes every token.

```bash
umask 077
openssl rand -hex 32 | tr -d "\n" > /tmp/salt.txt
aws ssm put-parameter --name /aep-hack/token-salt --type SecureString --value file:///tmp/salt.txt
export CONCIERGE_TOKEN_SALT="$(cat /tmp/salt.txt)"
rm -P /tmp/salt.txt 2>/dev/null || rm /tmp/salt.txt
```

On a teammate's machine, fetch it with
`export CONCIERGE_TOKEN_SALT=$(aws ssm get-parameter --name /aep-hack/token-salt --with-decryption --query Parameter.Value --output text)`.

## Step 8. Build, then publish the files

```bash
.venv/bin/python scripts/curate.py
.venv/bin/python scripts/build_risk_artifacts.py

scripts/aws_publish.sh                  # dry run: lists what would upload
APPLY=1 scripts/aws_publish.sh          # uploads, then checks that a second dry run lists nothing
```

It uploads `artifacts/curated/` (except `internal/`) to `s3://$DATA_BUCKET/curated/` and `artifacts/risk/` to
`s3://$DATA_BUCKET/serving/risk/`. Check:

```bash
aws s3 ls "s3://$DATA_BUCKET/" --recursive --human-readable --summarize
aws s3 ls "s3://$DATA_BUCKET/curated/internal/" || echo "good: internal/ was not uploaded"
```

## Step 9. DynamoDB lookup table

The premise picker looks a token up in single-digit milliseconds instead of loading an 18 MB file on every cold start.

```bash
aws dynamodb create-table --table-name "$TABLE" \
  --attribute-definitions AttributeName=token,AttributeType=S \
  --key-schema AttributeName=token,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
aws dynamodb wait table-exists --table-name "$TABLE"

.venv/bin/python -m pip install -e '.[aws]'        # boto3, only needed for the real load
.venv/bin/python scripts/load_dynamodb.py --table "$TABLE"            # dry run: counts the items
.venv/bin/python scripts/load_dynamodb.py --table "$TABLE" --apply    # writes 117,646 items

TOKEN=$(head -1 artifacts/risk/premise_risk.jsonl | .venv/bin/python -c "import sys,json;print(json.loads(sys.stdin.read())['t'])")
aws dynamodb get-item --table-name "$TABLE" --key "{\"token\":{\"S\":\"$TOKEN\"}}"
aws dynamodb describe-table --table-name "$TABLE" --query Table.ItemCount     # updates about every 6 hours
```

Writing 117k items on-demand costs roughly a dollar or less. Re-running the load overwrites by token, so it is safe
to repeat after a rebuild with the same salt.

## Step 10. A read-only role for the API

This role is for the Lambda you will add later. It can read the data bucket's `curated/` and `serving/` prefixes and
read the table. It cannot touch the raw bucket, write anything, or read the salt.

```bash
cat > /tmp/trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
aws iam create-role --role-name aep-hack-api-role --assume-role-policy-document file:///tmp/trust.json
aws iam attach-role-policy --role-name aep-hack-api-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

cat > /tmp/policy.json <<EOF
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"s3:GetObject","Resource":["arn:aws:s3:::$DATA_BUCKET/serving/*","arn:aws:s3:::$DATA_BUCKET/curated/*"]},
 {"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:BatchGetItem"],"Resource":"arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/$TABLE"}
]}
EOF
aws iam put-role-policy --role-name aep-hack-api-role --policy-name data-read-only --policy-document file:///tmp/policy.json
```

Bedrock permission is added later, scoped to the one model ARN you use (step 11).

## Step 11. Bedrock (for the explanations and messages)

1. Open the Bedrock console in your chosen region, go to the **Model catalog**, select the Anthropic model you plan to use,
   and submit the use-case form. This is once per account, and access is granted on submission.
2. List the exact model id available to you:
   ```bash
   aws bedrock list-foundation-models --by-provider anthropic --query "modelSummaries[].modelId" --output text
   ```
3. Test it from the CLI:
   ```bash
   aws bedrock-runtime converse --model-id <model-id-from-above> \
     --messages '[{"role":"user","content":[{"text":"Reply with the word ready."}]}]'
   ```
   Some models need a cross-region inference profile id instead of the plain model id. If the call asks for one, use
   `aws bedrock list-inference-profiles` and pass the profile id.
4. Keep the engine deterministic: the model only receives the whitelisted fact card, and the number guard falls back
   to a template if it returns a number the engine did not produce.

## Step 12. Verify

```bash
for B in "$RAW_BUCKET" "$DATA_BUCKET"; do
  aws s3api get-public-access-block --bucket "$B" --query PublicAccessBlockConfiguration
  aws s3api get-bucket-versioning --bucket "$B" --query Status
  aws s3api get-bucket-encryption --bucket "$B" --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm'
done
aws s3 ls "s3://$DATA_BUCKET/curated/tables/"
aws dynamodb get-item --table-name "$TABLE" --key "{\"token\":{\"S\":\"p_0000000000000000\"}}"   # expect empty: unknown token
```

All four public-access flags should be `true`, versioning `Enabled`, and encryption `aws:kms` for raw and `AES256`
for data. Then compare the uploaded files to the local `artifacts/curated/manifest.json` (sizes and sha256).

## What comes next

Not created by this guide, and best done with CDK so it is reviewable and repeatable:

- Lambda functions (score, restoration, messages) using `aep-hack-api-role`
- API Gateway HTTP API in front of them
- CloudFront with origin access control in front of the data bucket (tiles, device-tree JSON)
- Amplify hosting for the React app

## Teardown (destructive; read before running)

This deletes data. Raw files remain in Git LFS and can be rebuilt, but the salt is gone for good once the parameter is deleted.

```bash
aws dynamodb delete-table --table-name "$TABLE"
for B in "$RAW_BUCKET" "$DATA_BUCKET"; do
  aws s3api delete-objects --bucket "$B" --delete "$(aws s3api list-object-versions --bucket "$B" \
    --query '{Objects: [Versions,DeleteMarkers][][].{Key:Key,VersionId:VersionId}}' --output json)"   # repeat until empty
  aws s3api delete-bucket --bucket "$B"
done
aws iam delete-role-policy --role-name aep-hack-api-role --policy-name data-read-only
aws iam detach-role-policy --role-name aep-hack-api-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name aep-hack-api-role
aws ssm delete-parameter --name /aep-hack/token-salt
aws kms schedule-key-deletion --key-id "$KEY_ARN" --pending-window-in-days 7
aws budgets delete-budget --account-id "$ACCOUNT_ID" --budget-name aep-hack-monthly
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `ExpiredToken` or `Unable to locate credentials` | `aws sso login --profile aep-hack` and re-export `AWS_PROFILE` |
| `BucketAlreadyExists` | Names are global; check `ACCOUNT_ID` is set, or add a suffix |
| `IllegalLocationConstraintException` | Outside `us-east-1` add `--create-bucket-configuration LocationConstraint=$AWS_REGION` |
| `AccessDenied` on upload to raw | Your role needs `kms:GenerateDataKey` and `kms:Decrypt` on the raw key |
| Tokens in DynamoDB do not match the risk artifacts | Different salt between the curate and risk builds. Use the one in SSM for both |
| `get-item` returns nothing for a real token | Table still loading, or the table was loaded from artifacts built with another salt |
