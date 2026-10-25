import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { aws_apigatewayv2 as apigw } from "aws-cdk-lib";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";

export interface ConciergeProps extends cdk.StackProps {
  cleanBucketName: string;
  apiRoleName: string;
  bedrockModelId: string;
}

// Paths of the clean bucket that CloudFront may serve. premise_risk.jsonl and the Parquet tables are never exposed.
const PUBLIC_STATIC_PATHS = ["curated/drawings/*", "curated/labor_standards.json", "serving/risk/calibration.json"];

export class ConciergeStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ConciergeProps) {
    super(scope, id, props);

    // ---- data ---------------------------------------------------------------------------------------------
    const table = new dynamodb.Table(this, "PremiseTable", {
      tableName: "aep-premise-risk",
      partitionKey: { name: "token", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY, // the table is rebuilt from the clean bucket by `make load-table`
    });

    // Demo-only sign-ups, mock outbox messages and the alert audit log (spec-05). Rows expire on their own (ttl).
    // Documents hold only fictional contacts and no message delivery happens anywhere in this stack.
    const alertsTable = new dynamodb.Table(this, "AlertsTable", {
      tableName: "aep-demo-alerts",
      partitionKey: { name: "token", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const cleanBucket = s3.Bucket.fromBucketName(this, "CleanBucket", props.cleanBucketName);

    // The role is created by scripts/setup_iam.sh (least privilege, read-only). CDK only uses it.
    const apiRole = iam.Role.fromRoleName(this, "ApiRole", props.apiRoleName, { mutable: false });

    // ---- API ----------------------------------------------------------------------------------------------
    const logGroup = new logs.LogGroup(this, "ApiLogs", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const fn = new lambda.Function(this, "ApiFunction", {
      functionName: "aep-concierge-api",
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "concierge.api.handler.lambda_handler",
      // Only the runtime engines are packaged. curate/ needs pyarrow and is not used by the API.
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "..", "backend"), {
        exclude: ["tests", "tests/**", "**/__pycache__", "**/*.pyc", "concierge/curate", "concierge/curate/**",
          "concierge/risk/build.py"],
      }),
      role: apiRole,
      memorySize: 512,
      timeout: cdk.Duration.seconds(20),
      logGroup,
      environment: {
        TABLE_NAME: table.tableName,
        ENROLL_TABLE: alertsTable.tableName,
        CLEAN_BUCKET: cleanBucket.bucketName,
        BEDROCK_MODEL_ID: props.bedrockModelId,
        POWERTOOLS_LOG_LEVEL: "INFO",
      },
    });

    const api = new apigw.HttpApi(this, "HttpApi", {
      apiName: "aep-concierge-api",
      description: "Outage risk, restoration, guidance, alerts and messages (demo, no authorizer, mock delivery only)",
      corsPreflight: {
        allowOrigins: ["*"],
        allowMethods: [apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.OPTIONS],
        allowHeaders: ["content-type"],
        maxAge: cdk.Duration.hours(1),
      },
    });
    const integration = new HttpLambdaIntegration("ApiIntegration", fn);
    // Every route is also served under /api so the same paths work through CloudFront (/api/* behavior) without
    // rewriting the URL. The handler strips the /api prefix.
    for (const prefix of ["", "/api"]) {
      api.addRoutes({ path: `${prefix}/health`, methods: [apigw.HttpMethod.GET], integration });
      for (const route of ["risk", "restoration", "guidance", "alerts", "messages", "summary", "assistant",
        "alerts/enroll", "alerts/unenroll", "alerts/simulate"]) {
        api.addRoutes({ path: `${prefix}/${route}`, methods: [apigw.HttpMethod.POST], integration });
      }
      api.addRoutes({ path: `${prefix}/alerts/audit`, methods: [apigw.HttpMethod.GET], integration });
      // Backtest verdicts and limits (spec-04). Read-only aggregates; answers "unavailable" until a reviewed
      // backtest.json is uploaded to serving/backtest/.
      api.addRoutes({ path: `${prefix}/evidence`, methods: [apigw.HttpMethod.GET], integration });
    }
    // Light throttle so a runaway client cannot run up Bedrock calls. Not authentication.
    const stage = api.defaultStage!.node.defaultChild as apigw.CfnStage;
    stage.defaultRouteSettings = { throttlingBurstLimit: 100, throttlingRateLimit: 50 };

    // ---- web hosting (frontend comes later) ---------------------------------------------------------------
    const webBucket = new s3.Bucket(this, "WebBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const apiHost = cdk.Fn.select(2, cdk.Fn.split("/", api.apiEndpoint));
    const cleanOrigin = origins.S3BucketOrigin.withOriginAccessControl(cleanBucket);
    const staticBehavior: cloudfront.BehaviorOptions = {
      origin: cleanOrigin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      compress: true,
    };

    const distribution = new cloudfront.Distribution(this, "Cdn", {
      comment: "AEP Outage Concierge",
      defaultRootObject: "index.html",
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(webBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        compress: true,
      },
      additionalBehaviors: {
        "/api/*": {
          origin: new origins.HttpOrigin(apiHost, { protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
        ...Object.fromEntries(PUBLIC_STATIC_PATHS.map((p) => [`/${p}`, staticBehavior])),
      },
    });

    // ---- outputs ------------------------------------------------------------------------------------------
    new cdk.CfnOutput(this, "ApiUrl", { value: api.apiEndpoint });
    new cdk.CfnOutput(this, "SiteUrl", { value: `https://${distribution.distributionDomainName}` });
    new cdk.CfnOutput(this, "DistributionId", { value: distribution.distributionId });
    new cdk.CfnOutput(this, "DistributionArn", {
      value: `arn:aws:cloudfront::${this.account}:distribution/${distribution.distributionId}`,
    });
    new cdk.CfnOutput(this, "TableName", { value: table.tableName });
    new cdk.CfnOutput(this, "AlertsTableName", { value: alertsTable.tableName });
    new cdk.CfnOutput(this, "WebBucketName", { value: webBucket.bucketName });
    new cdk.CfnOutput(this, "FunctionName", { value: fn.functionName });
  }
}
