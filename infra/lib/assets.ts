import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export interface PublicAssetsProps {
  /** Browser origins allowed to fetch the assets (the Amplify site, and local dev). */
  readonly allowedOrigins: string[];
}

/**
 * Browser-safe static files behind CloudFront.
 *
 * This is deliberately a separate bucket from the private data bucket (docs/aws-setup.md step 5). The private bucket
 * holds curated tables with tokens and circuit ids and is read only by the API role. Nothing reaches the internet
 * unless scripts/aws_publish_public.sh copies it here from an explicit allow-list.
 */
export class PublicAssets extends Construct {
  readonly bucket: s3.Bucket;
  readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: PublicAssetsProps) {
    super(scope, id);

    this.bucket = new s3.Bucket(this, 'Bucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN, // published files can be rebuilt, but never delete a bucket by surprise
    });

    const headers = new cloudfront.ResponseHeadersPolicy(this, 'Headers', {
      comment: 'CORS for the Amplify site plus standard security headers',
      corsBehavior: {
        accessControlAllowOrigins: props.allowedOrigins,
        accessControlAllowMethods: ['GET', 'HEAD', 'OPTIONS'],
        accessControlAllowHeaders: ['*'],
        accessControlAllowCredentials: false,
        accessControlMaxAge: cdk.Duration.days(1),
        originOverride: true,
      },
      securityHeadersBehavior: {
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.NO_REFERRER, override: true },
        strictTransportSecurity: { accessControlMaxAge: cdk.Duration.days(365), includeSubdomains: true, override: true },
      },
    });

    this.distribution = new cloudfront.Distribution(this, 'Cdn', {
      comment: 'AI Outage Concierge public assets',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: headers,
        compress: true,
      },
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100, // US, Canada, Europe: cheapest, enough for a demo
    });
  }
}
