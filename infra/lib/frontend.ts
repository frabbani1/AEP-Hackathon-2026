import * as cdk from 'aws-cdk-lib';
import * as amplify from 'aws-cdk-lib/aws-amplify';
import { Construct } from 'constructs';

export const BRANCH_NAME = 'main';

/**
 * Amplify Hosting for the React app (frontend/).
 *
 * The app has no Git repository attached: `scripts/amplify_deploy.sh` builds the site locally and uploads a zip,
 * so no GitHub token is stored anywhere. Because the API and CDN URLs are baked in at build time by that script
 * (not through Amplify environment variables), this construct depends on nothing else in the stack. That lets the
 * API and CDN allow-list this site's origin for CORS without a circular dependency.
 */
export class Frontend extends Construct {
  readonly app: amplify.CfnApp;
  readonly branch: amplify.CfnBranch;
  /** https origin of the hosted site, for CORS allow-lists. */
  readonly origin: string;

  constructor(scope: Construct, id: string) {
    super(scope, id);
    const region = cdk.Stack.of(this).region;

    // connect-src lists the two places the app may call: API Gateway and CloudFront. Wildcards avoid a dependency on them.
    const csp = [
      "default-src 'self'",
      "script-src 'self'",
      "style-src 'self'",
      "img-src 'self' data: https://*.cloudfront.net",
      `connect-src 'self' https://*.execute-api.${region}.amazonaws.com https://*.cloudfront.net`,
      "base-uri 'self'",
      "form-action 'self'",
      "frame-ancestors 'none'",
    ].join('; ');

    const customHeaders = [
      'customHeaders:',
      "  - pattern: '**'",
      '    headers:',
      '      - key: Strict-Transport-Security',
      "        value: 'max-age=31536000; includeSubDomains'",
      '      - key: X-Content-Type-Options',
      '        value: nosniff',
      '      - key: X-Frame-Options',
      '        value: DENY',
      '      - key: Referrer-Policy',
      '        value: no-referrer',
      '      - key: Content-Security-Policy',
      `        value: "${csp}"`,
    ].join('\n');

    this.app = new amplify.CfnApp(this, 'App', {
      name: 'aep-outage-concierge',
      description: 'AI Outage Concierge web app (HackOHI/O 2026, AEP challenge)',
      platform: 'WEB',
      customHeaders,
    });

    this.branch = new amplify.CfnBranch(this, 'Branch', {
      appId: this.app.attrAppId,
      branchName: BRANCH_NAME,
      stage: 'PRODUCTION',
      enableAutoBuild: false,
      description: 'Manual deployments from scripts/amplify_deploy.sh',
    });

    this.origin = `https://${BRANCH_NAME}.${this.app.attrDefaultDomain}`;
  }
}
