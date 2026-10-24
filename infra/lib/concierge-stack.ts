import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { Api } from './api';
import { PublicAssets } from './assets';
import { BRANCH_NAME, Frontend } from './frontend';

/**
 * Everything that sits on top of the data layer from docs/aws-setup.md:
 * Amplify (web app), Lambda + API Gateway (API), CloudFront + a public S3 bucket (static assets).
 *
 * Context options (cdk deploy -c key=value):
 *   extraCorsOrigins   comma-separated extra browser origins, default http://localhost:5173 (Vite dev server).
 *                      Pass an empty string to allow only the Amplify site.
 */
export class ConciergeStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const extra = String(this.node.tryGetContext('extraCorsOrigins') ?? 'http://localhost:5173')
      .split(',').map((o) => o.trim()).filter(Boolean);

    const frontend = new Frontend(this, 'Frontend');
    const allowedOrigins = [frontend.origin, ...extra];

    const assets = new PublicAssets(this, 'Assets', { allowedOrigins });
    const api = new Api(this, 'Api', { allowedOrigins });

    // scripts/amplify_deploy.sh and scripts/aws_publish_public.sh read these by name.
    const out = (name: string, value: string, description: string) =>
      new cdk.CfnOutput(this, name, { value, description });
    out('ApiUrl', api.url, 'API Gateway base URL (VITE_API_URL)');
    out('AssetsUrl', `https://${assets.distribution.distributionDomainName}`, 'CloudFront base URL for public assets (VITE_ASSETS_URL)');
    out('AssetsBucket', assets.bucket.bucketName, 'Public assets bucket, written only by scripts/aws_publish_public.sh');
    out('DistributionId', assets.distribution.distributionId, 'CloudFront distribution id, for cache invalidation');
    out('AmplifyAppId', frontend.app.attrAppId, 'Amplify app id');
    out('AmplifyBranch', BRANCH_NAME, 'Amplify branch that receives manual deployments');
    out('AmplifyUrl', frontend.origin, 'Public URL of the web app');
  }
}
