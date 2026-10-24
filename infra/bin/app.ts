#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ConciergeStack } from "../lib/stack";

const app = new cdk.App();
new ConciergeStack(app, "AepConciergeStack", {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: "us-east-2" },
  description: "AEP Outage Concierge: DynamoDB, Lambda API, API Gateway HTTP API, CloudFront",
  cleanBucketName: process.env.CLEAN_BUCKET ?? "aep-hack-clean-464604123481",
  apiRoleName: "aep-hack-api-role",
  // Amazon Nova 2 Lite via the US cross-region inference profile. Anthropic models are blocked in this sandbox
  // (they need AWS Marketplace permissions the sandbox role does not have). Change it with
  // `cdk deploy -c bedrockModelId=<profile id>` and re-run scripts/setup_iam.sh with BEDROCK_PROFILE set to match.
  // Deliberately not read from the shell environment, so a stray variable cannot change what gets deployed.
  bedrockModelId: app.node.tryGetContext("bedrockModelId") ?? "us.amazon.nova-2-lite-v1:0",
});
