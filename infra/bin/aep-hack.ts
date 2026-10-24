#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { ConciergeStack } from '../lib/concierge-stack';

const app = new cdk.App();

new ConciergeStack(app, 'AepConcierge', {
  description: 'AI Outage Concierge: Lambda, API Gateway HTTP API, CloudFront, Amplify (HackOHI/O 2026)',
  // Account comes from the active AWS profile (AWS_PROFILE=aep-hack). Region defaults to the one used in docs/aws-setup.md.
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1' },
  tags: { project: 'aep-hack' },
});
