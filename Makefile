# Two-bucket data pipeline. Needs AWS credentials in the environment (AWS_PROFILE or AWS_ACCESS_KEY_ID/...).
#   make ingest    upload originals to s3://$(RAW_BUCKET)/raw/<dataset>/<date>/ and verify counts + bytes
#   make etl       raw bucket -> staging -> sanitized clean bucket (curated/, serving/, _meta/) + data/curated/
#   make validate  fail on banned columns, unexpected row drops, groups < 10, unreadable files
#   make docs      regenerate docs/data_dictionary.md and docs/data_findings.md
#   make iam       create/update the ETL and API roles and bucket policies

ACCOUNT_ID   ?= 464604123481
AWS_REGION   ?= us-east-2
RAW_BUCKET   ?= aep-hack-raw-$(ACCOUNT_ID)
CLEAN_BUCKET ?= aep-hack-clean-$(ACCOUNT_ID)
PY           ?= .venv/bin/python

export AWS_REGION RAW_BUCKET CLEAN_BUCKET
export AWS_DEFAULT_REGION := $(AWS_REGION)

.PHONY: all ingest etl validate docs iam check-aws load-table deploy smoke

all: ingest etl validate docs

check-aws:
	@aws sts get-caller-identity --query Arn --output text >/dev/null || \
	  { echo "no AWS credentials: aws sso login, or export AWS_ACCESS_KEY_ID/..."; exit 1; }

ingest: check-aws
	bash scripts/ingest.sh

etl: check-aws
	$(PY) scripts/etl.py

validate: check-aws
	$(PY) scripts/validate.py

docs:
	$(PY) scripts/make_docs.py

iam: check-aws
	bash scripts/setup_iam.sh

# ---- API layer (DynamoDB + Lambda + HTTP API + CloudFront) -------------------------------------------------------
#   make load-table  load data/serving/risk/premise_risk.jsonl (from the last `make etl`) into DynamoDB
#   make deploy      cdk deploy, then attach the CloudFront read statement to the clean bucket
#   make smoke       call the deployed API
# Run `make etl validate` first. Re-run `make load-table` after every ETL, because tokens/rows come from that run.
load-table: check-aws
	$(PY) scripts/load_dynamodb.py --table aep-premise-risk --apply

deploy: check-aws
	cd infra && npm install --no-audit --no-fund && npx cdk deploy --require-approval never --outputs-file cdk-outputs.json
	bash scripts/setup_iam.sh

smoke:
	bash scripts/smoke.sh
