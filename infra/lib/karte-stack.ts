import * as path from 'path';
import { Construct } from 'constructs';
import {
  Duration, Size, RemovalPolicy, Stack, StackProps, CfnOutput, Tags,
  aws_apigateway as apigw, aws_dynamodb as ddb, aws_events as events, aws_events_targets as targets,
  aws_iam as iam, aws_kms as kms, aws_lambda as lambda, aws_logs as logs, aws_s3 as s3,
  aws_personalize as personalize, aws_stepfunctions as sfn, aws_stepfunctions_tasks as tasks,
} from 'aws-cdk-lib';

export class KarteStack extends Stack {
  constructor(scope: Construct, id: string, props: StackProps) {
    super(scope, id, props);
    const ctx = (k: string, d: string) => String(this.node.tryGetContext(k) ?? d);
    const modelId = ctx('modelId', 'jp.anthropic.claude-haiku-5-5');
    const fallbackModelId = ctx('fallbackModelId', 'jp.anthropic.claude-haiku-4-5-20251001-v1:0');
    const policyReference = ctx('policyReference', 'now'); // 'last_event' only for replaying historical open data
    const retentionYears = Number(ctx('retentionYears', '3')); // source history kept N years (S3 lifecycle + FULL cutoff)
    const weeklyFull = ctx('weeklyFull', 'false') === 'true';
    const allowedCidrs = ctx('allowedCidrs', '').split(',').map((s) => s.trim()).filter(Boolean);

    const key = new kms.Key(this, 'DataKey', {
      alias: 'alias/karte-data', enableKeyRotation: true, removalPolicy: RemovalPolicy.DESTROY,
      description: 'Karte data (S3 + DynamoDB)',
    });

    const bucket = new s3.Bucket(this, 'Inbound', {
      encryption: s3.BucketEncryption.KMS, encryptionKey: key, bucketKeyEnabled: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL, enforceSSL: true,
      eventBridgeEnabled: true, removalPolicy: RemovalPolicy.DESTROY, autoDeleteObjects: true,
      lifecycleRules: [
        { id: 'source-retention', prefix: 'inbound/', expiration: Duration.days(retentionYears * 365) },
        { id: 'reports-30d', prefix: 'reports/', expiration: Duration.days(30) },
        { id: 'personalize-work-14d', prefix: 'personalize/', expiration: Duration.days(14) },
        { id: 'abort-mpu', abortIncompleteMultipartUploadAfter: Duration.days(1) },
      ],
    });

    const table = (name: string, pk: string, sk?: string) => new ddb.TableV2(this, name, {
      partitionKey: { name: pk, type: ddb.AttributeType.STRING },
      sortKey: sk ? { name: sk, type: ddb.AttributeType.STRING } : undefined,
      billing: ddb.Billing.onDemand(),
      encryption: ddb.TableEncryptionV2.customerManagedKey(key),
      removalPolicy: RemovalPolicy.DESTROY,
      deletionProtection: false,
    });
    const karte = table('Karte', 'member_id');

    const env = {
      TABLE_KARTE: karte.tableName,
      BUCKET: bucket.bucketName, MODEL_ID: modelId, FALLBACK_MODEL_ID: fallbackModelId,
      POLICY_REFERENCE: policyReference, RETENTION_YEARS: String(retentionYears),
    };
    const code = lambda.Code.fromAsset(path.join(__dirname, '..', '..', 'lambdas'), { exclude: ['__pycache__', '*.pyc'] });
    const fn = (name: string, handler: string, o: Partial<lambda.FunctionProps> = {}) => {
      const f = new lambda.Function(this, name, {
        runtime: lambda.Runtime.PYTHON_3_12, architecture: lambda.Architecture.ARM_64, code, handler,
        environment: env, memorySize: 1024, timeout: Duration.seconds(30),
        logGroup: new logs.LogGroup(this, `${name}Logs`, { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: RemovalPolicy.DESTROY }),
        ...o,
      });
      key.grantEncryptDecrypt(f);
      return f;
    };

    const bedrockPolicy = new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:ap-northeast-1:${this.account}:inference-profile/jp.anthropic.*`,
        'arn:aws:bedrock:ap-northeast-1::foundation-model/anthropic.*',
        'arn:aws:bedrock:ap-northeast-3::foundation-model/anthropic.*',
      ],
    });

    const apiFn = fn('ApiFn', 'api.handler', { timeout: Duration.seconds(60) });
    karte.grantReadWriteData(apiFn);
    bucket.grantPut(apiFn, 'inbound/events/*'); // DELETE records a withdraw event so later FULL runs exclude the member
    apiFn.addToRolePolicy(bedrockPolicy);

    // ---- Personalize (User-Personalization-v2, batch inference only; no campaign) ----
    const pRole = new iam.Role(this, 'PersonalizeRole', { assumedBy: new iam.ServicePrincipal('personalize.amazonaws.com') });
    bucket.grantReadWrite(pRole, 'personalize/*');
    bucket.addToResourcePolicy(new iam.PolicyStatement({
      principals: [new iam.ServicePrincipal('personalize.amazonaws.com')],
      actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
      resources: [bucket.bucketArn, bucket.arnForObjects('personalize/*')],
    }));
    // Personalize validates the key *policy* itself (IAM delegation alone is rejected)
    key.addToResourcePolicy(new iam.PolicyStatement({
      principals: [pRole, new iam.ServicePrincipal('personalize.amazonaws.com')],
      actions: ['kms:Decrypt', 'kms:GenerateDataKey', 'kms:GenerateDataKeyWithoutPlaintext', 'kms:DescribeKey', 'kms:CreateGrant', 'kms:ListGrants'],
      resources: ['*'],
    }));
    key.grant(pRole, 'kms:Decrypt', 'kms:GenerateDataKey', 'kms:DescribeKey', 'kms:CreateGrant', 'kms:ListGrants');
    const dsg = new personalize.CfnDatasetGroup(this, 'DatasetGroup', { name: 'karte', kmsKeyArn: key.keyArn, roleArn: pRole.roleArn });
    dsg.node.addDependency(pRole); // role + its KMS/S3 policy must exist before Personalize validates key access
    const interSchema = new personalize.CfnSchema(this, 'InteractionsSchema', {
      name: 'karte-interactions',
      schema: JSON.stringify({ type: 'record', name: 'Interactions', namespace: 'com.amazonaws.personalize.schema', version: '1.0', fields: [
        { name: 'USER_ID', type: 'string' }, { name: 'ITEM_ID', type: 'string' }, { name: 'EVENT_TYPE', type: 'string' }, { name: 'TIMESTAMP', type: 'long' }] }),
    });
    const itemSchema = new personalize.CfnSchema(this, 'ItemsSchema', {
      name: 'karte-items',
      schema: JSON.stringify({ type: 'record', name: 'Items', namespace: 'com.amazonaws.personalize.schema', version: '1.0', fields: [
        { name: 'ITEM_ID', type: 'string' }, { name: 'CATEGORY', type: ['null', 'string'], categorical: true }, { name: 'PRICE', type: ['null', 'float'] }] }),
    });
    const interDs = new personalize.CfnDataset(this, 'InteractionsDataset', { name: 'karte-interactions', datasetGroupArn: dsg.attrDatasetGroupArn, datasetType: 'Interactions', schemaArn: interSchema.attrSchemaArn });
    const itemDs = new personalize.CfnDataset(this, 'ItemsDataset', { name: 'karte-items', datasetGroupArn: dsg.attrDatasetGroupArn, datasetType: 'Items', schemaArn: itemSchema.attrSchemaArn });

    const pipelineFn = fn('Pipeline', 'pipeline.handler', {
      timeout: Duration.minutes(15), memorySize: 3008, ephemeralStorageSize: Size.gibibytes(4),
      environment: { ...env, INTERACTIONS_DATASET_ARN: interDs.attrDatasetArn, ITEMS_DATASET_ARN: itemDs.attrDatasetArn,
        DATASET_GROUP_ARN: dsg.attrDatasetGroupArn, PERSONALIZE_ROLE_ARN: pRole.roleArn, KMS_KEY_ARN: key.keyArn },
    });
    bucket.grantReadWrite(pipelineFn);
    karte.grantReadWriteData(pipelineFn);
    pipelineFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['personalize:CreateDatasetImportJob', 'personalize:DescribeDatasetImportJob', 'personalize:CreateSolutionVersion', 'personalize:DescribeSolutionVersion',
        'personalize:CreateBatchInferenceJob', 'personalize:CreateSolution', 'personalize:ListSolutions', 'personalize:DescribeBatchInferenceJob', 'personalize:DescribeDataset', 'personalize:DescribeSolution'],
      resources: ['*'],
    }));
    pRole.grantPassRole(pipelineFn.grantPrincipal);

    const step = (action: string) => new tasks.LambdaInvoke(this, `S-${action}`, {
      lambdaFunction: pipelineFn, payload: sfn.TaskInput.fromObject({ action, 'state.$': '$' }),
      payloadResponseOnly: true, resultPath: '$', retryOnServiceExceptions: true,
    });
    const fail = new sfn.Fail(this, 'PipelineFailed', { cause: 'Personalize job failed; see failure field' });
    const poll = (name: string, startState: sfn.INextable & sfn.IChainable, check: string, wait: Duration, next: sfn.IChainable) => {
      const w = new sfn.Wait(this, `Wait-${name}`, { time: sfn.WaitTime.duration(wait) });
      const chk = step(check);
      const choice = new sfn.Choice(this, `Done-${name}`)
        .when(sfn.Condition.stringEquals('$.status', 'ACTIVE'), next)
        .when(sfn.Condition.stringEquals('$.status', 'FAILED'), fail)
        .otherwise(w);
      startState.next(w);
      w.next(chk);
      chk.next(choice);
    };
    const exportS = step('build');
    const startImports = step('start_imports');
    const startTrain = step('start_train');
    const ensureSol = step('ensure_solution');
    const startBatch = step('start_batch');
    const loadS = step('load');
    exportS.next(startImports);
    poll('imports', startImports, 'check_imports', Duration.minutes(1), ensureSol);
    ensureSol.next(startTrain);
    poll('train', startTrain, 'check_train', Duration.minutes(5), startBatch);
    poll('batch', startBatch, 'check_batch', Duration.minutes(2), loadS);
    const pipeline = new sfn.StateMachine(this, 'RecoPipeline', {
      stateMachineName: 'karte-reco-pipeline', definitionBody: sfn.DefinitionBody.fromChainable(exportS),
      timeout: Duration.hours(12),
    });

    new events.Rule(this, 'WeeklyFull', {
      schedule: events.Schedule.cron({ minute: '0', hour: '18', weekDay: 'SAT' }), // Sunday 03:00 JST
      enabled: weeklyFull,
      targets: [new targets.SfnStateMachine(pipeline)],
    });

    const policyStatements = [
      new iam.PolicyStatement({ effect: iam.Effect.ALLOW, principals: [new iam.AccountRootPrincipal()], actions: ['execute-api:Invoke'], resources: ['execute-api:/*'] }),
    ];
    if (allowedCidrs.length) {
      policyStatements.push(new iam.PolicyStatement({
        effect: iam.Effect.DENY, principals: [new iam.AnyPrincipal()], actions: ['execute-api:Invoke'], resources: ['execute-api:/*'],
        conditions: { NotIpAddress: { 'aws:SourceIp': allowedCidrs } },
      }));
    }
    const api = new apigw.RestApi(this, 'KarteApi', {
      restApiName: 'karte-api', endpointTypes: [apigw.EndpointType.REGIONAL], cloudWatchRole: false,
      policy: new iam.PolicyDocument({ statements: policyStatements }),
      defaultMethodOptions: { authorizationType: apigw.AuthorizationType.IAM },
      deployOptions: { stageName: 'v1', throttlingRateLimit: 50, throttlingBurstLimit: 100 },
    });
    const integ = new apigw.LambdaIntegration(apiFn);
    const member = api.root.addResource('karte').addResource('{memberId}');
    member.addMethod('GET', integ);
    member.addMethod('DELETE', integ);
    member.addResource('reco').addMethod('GET', integ);
    member.addResource('copy').addMethod('POST', integ);

    // Callers: the company's own servers. Attach these managed policies to their IAM role.
    const invokePolicy = new iam.ManagedPolicy(this, 'ApiInvokePolicy', {
      managedPolicyName: 'karte-api-invoke',
      statements: [new iam.PolicyStatement({ actions: ['execute-api:Invoke'], resources: [api.arnForExecuteApi('*', '/karte/*', 'v1')] })],
    });
    const uploadPolicy = new iam.ManagedPolicy(this, 'UploadPolicy', {
      managedPolicyName: 'karte-inbound-upload',
      statements: [
        new iam.PolicyStatement({ actions: ['s3:PutObject'], resources: [bucket.arnForObjects('inbound/*')] }),
        new iam.PolicyStatement({ actions: ['kms:GenerateDataKey', 'kms:Encrypt'], resources: [key.keyArn] }),
      ],
    });

    new CfnOutput(this, 'PipelineFunctionName', { value: pipelineFn.functionName });
    new CfnOutput(this, 'PipelineArn', { value: pipeline.stateMachineArn });
    new CfnOutput(this, 'ApiUrl', { value: api.url });
    new CfnOutput(this, 'BucketName', { value: bucket.bucketName });
    new CfnOutput(this, 'KarteTable', { value: karte.tableName });
    new CfnOutput(this, 'ApiInvokePolicyArn', { value: invokePolicy.managedPolicyArn });
    new CfnOutput(this, 'UploadPolicyArn', { value: uploadPolicy.managedPolicyArn });
    Tags.of(this).add('app', 'karte'); // do not change: Personalize schema tags are immutable (forces replacement)
  }
}
