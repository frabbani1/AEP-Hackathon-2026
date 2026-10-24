import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import { AccessLogFormat } from 'aws-cdk-lib/aws-apigateway';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

/** Created by docs/aws-setup.md steps 3, 9 and 10. This stack reads them; it does not create or change them. */
export const API_ROLE_NAME = 'aep-hack-api-role';
export const TABLE_NAME = 'aep-premise-risk';

export interface ApiProps {
  /** Browser origins allowed to call the API (the Amplify site, and local dev). */
  readonly allowedOrigins: string[];
}

/**
 * Three Python Lambdas behind one API Gateway HTTP API.
 *
 *   GET  /risk         risk function         implemented (wraps the deterministic risk engine)
 *   GET  /health       risk function         implemented
 *   POST /restoration  restoration function  placeholder, returns 501 until the restoration engine exists
 *   POST /messages     messages function     placeholder, returns 501 until the narration layer exists
 *
 * All three run as the existing least-privilege role: read-only on serving/ and curated/ in the private data bucket
 * and GetItem on the premise table. The role cannot touch the raw bucket, write anything, or read the token salt.
 * It has no Bedrock permission. Add one, scoped to a single model ARN, when the messages function needs it.
 *
 * The API has no authentication. It serves anonymized, whitelisted engine output keyed by an unguessable token, and
 * is protected by CORS, request throttling and a monthly budget alarm. Add an authorizer before real customer use.
 */
export class Api extends Construct {
  readonly httpApi: apigw.HttpApi;
  readonly stage: apigw.HttpStage;
  readonly functions: Record<'risk' | 'restoration' | 'messages', lambda.Function>;

  constructor(scope: Construct, id: string, props: ApiProps) {
    super(scope, id);
    const stack = cdk.Stack.of(this);

    const role = iam.Role.fromRoleName(this, 'ApiRole', API_ROLE_NAME, { mutable: false });

    // Only the engine package ships. Tests, the curation code (needs pyarrow) and bytecode stay out of the zip.
    // boto3 is already in the Lambda runtime. The risk engine itself uses only the standard library.
    const code = lambda.Code.fromAsset(path.join(__dirname, '..', '..', 'backend'), {
      exclude: ['tests', 'concierge/curate', '**/__pycache__', '**/*.pyc', '*.egg-info', '.pytest_cache'],
    });

    const make = (key: 'risk' | 'restoration' | 'messages', handler: string, description: string,
                  memorySize: number, environment?: Record<string, string>) => {
      const functionName = `aep-hack-${key}`;
      return new lambda.Function(this, `${key[0].toUpperCase()}${key.slice(1)}Fn`, {
        functionName,
        description,
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler,
        code,
        role,
        memorySize,
        timeout: cdk.Duration.seconds(10),
        environment,
        logGroup: new logs.LogGroup(this, `${key[0].toUpperCase()}${key.slice(1)}Logs`, {
          logGroupName: `/aws/lambda/${functionName}`,
          retention: logs.RetentionDays.ONE_WEEK,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
        }),
      });
    };

    this.functions = {
      risk: make('risk', 'concierge.api.risk_handler.handler', 'Outage risk band, confidence and factors', 512, {
        TABLE_NAME,
        DATA_BUCKET: `aep-hack-data-${stack.account}`,
      }),
      restoration: make('restoration', 'concierge.api.restoration_handler.handler', 'Restoration range (placeholder)', 128),
      messages: make('messages', 'concierge.api.messages_handler.handler', 'Customer messages (placeholder)', 128),
    };

    this.httpApi = new apigw.HttpApi(this, 'HttpApi', {
      apiName: 'aep-hack-api',
      description: 'AI Outage Concierge API',
      createDefaultStage: false,
      corsPreflight: {
        allowOrigins: props.allowedOrigins,
        allowMethods: [apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.OPTIONS],
        allowHeaders: ['content-type'],
        maxAge: cdk.Duration.days(1),
      },
    });

    // Access logs record route and outcome only. They omit source IP and query strings (which carry premise tokens).
    const accessLogs = new logs.LogGroup(this, 'AccessLogs', {
      logGroupName: '/aws/apigateway/aep-hack-api',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.stage = new apigw.HttpStage(this, 'Stage', {
      httpApi: this.httpApi,
      autoDeploy: true,
      throttle: { rateLimit: 25, burstLimit: 50 },
      accessLogSettings: {
        destination: new apigw.LogGroupLogDestination(accessLogs),
        format: AccessLogFormat.custom(JSON.stringify({
          requestId: '$context.requestId',
          route: '$context.routeKey',
          status: '$context.status',
          latencyMs: '$context.responseLatency',
          integrationStatus: '$context.integrationStatus',
        })),
      },
    });

    const risk = new HttpLambdaIntegration('RiskIntegration', this.functions.risk);
    this.httpApi.addRoutes({ path: '/risk', methods: [apigw.HttpMethod.GET], integration: risk });
    this.httpApi.addRoutes({ path: '/health', methods: [apigw.HttpMethod.GET], integration: risk });
    this.httpApi.addRoutes({
      path: '/restoration', methods: [apigw.HttpMethod.POST],
      integration: new HttpLambdaIntegration('RestorationIntegration', this.functions.restoration),
    });
    this.httpApi.addRoutes({
      path: '/messages', methods: [apigw.HttpMethod.POST],
      integration: new HttpLambdaIntegration('MessagesIntegration', this.functions.messages),
    });
  }

  /** Base URL without a trailing slash. */
  get url(): string {
    return this.stage.url.replace(/\/$/, '');
  }
}
