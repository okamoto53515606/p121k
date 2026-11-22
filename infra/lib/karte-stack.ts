import * as path from 'path';
import { Construct } from 'constructs';
import {
  Duration, Size, RemovalPolicy, Stack, StackProps, CfnOutput, Tags,
  aws_apigateway as apigw, aws_cloudtrail as cloudtrail, aws_cloudwatch as cw, aws_cloudwatch_actions as cw_actions,
  aws_dynamodb as ddb, aws_events as events, aws_events_targets as targets,
  aws_iam as iam, aws_kms as kms, aws_lambda as lambda, aws_logs as logs, aws_s3 as s3,
  aws_personalize as personalize, aws_sns as sns, aws_sns_subscriptions as subs,
  aws_stepfunctions as sfn, aws_stepfunctions_tasks as tasks, aws_wafv2 as wafv2,
} from 'aws-cdk-lib';

export class KarteStack extends Stack {
  constructor(scope: Construct, id: string, props: StackProps) {
    super(scope, id, props);
    const ctx = (k: string, d: string) => String(this.node.tryGetContext(k) ?? d);
    const modelId = ctx('modelId', 'jp.anthropic.claude-haiku-5-5');
    const policyReference = ctx('policyReference', 'now'); // 'last_event' only for replaying historical open data
    const retentionYears = Number(ctx('retentionYears', '3')); // source history kept N years (S3 lifecycle + FULL cutoff)
    const weeklyFull = ctx('weeklyFull', 'false') === 'true';
    const alertEmail = ctx('alertEmail', ''); // optional: where pipeline/API failures are mailed
    const auditTrail = ctx('auditTrail', 'false') === 'true'; // CloudTrail data events (billable)
    const geoBlock = ctx('geoBlock', 'true') === 'true'; // API: block requests originating outside Japan
    const uploadCidrs = ctx('uploadCidrs', '').split(',').map((x) => x.trim()).filter(Boolean);
    const callerPrincipalArns = ctx('callerPrincipalArns', '').split(',').map((x) => x.trim()).filter(Boolean);
    // 原則2: 呼び出しは IAM ロール。長期キーは明示的に opt-in したときだけ作る（非推奨）。
    const accessKeys = ctx('accessKeys', 'false') === 'true';
    const accessKeyMaxAgeDays = ctx('accessKeyMaxAgeDays', '180');
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
      // No PITR: a deleted (withdrawn) member must not survive in 35-day backups.
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: false },
      removalPolicy: RemovalPolicy.DESTROY,
      deletionProtection: false,
    });
    // New logical id: DynamoDB allows only one GSI creation per update, so the table
    // with all six search indexes is created fresh. Data is rebuilt from S3 by the next FULL.
    const karte = table('KarteV2', 'member_id');
    // List search: one index per sortable field, fixed partition key `entity`.
    // KEYS_ONLY keeps them small: member_id + the sort value are all the API returns.
    for (const attr of ['first_purchase_ts', 'last_purchase_ts', 'first_view_ts', 'last_view_ts',
                        'purchase_count_90d', 'view_count_90d']) {
      karte.addGlobalSecondaryIndex({
        indexName: `karte-${attr.replace(/_ts$/, '').replace(/_/g, '-')}`,
        partitionKey: { name: 'entity', type: ddb.AttributeType.STRING },
        sortKey: { name: attr, type: ddb.AttributeType.NUMBER },
        projectionType: ddb.ProjectionType.KEYS_ONLY,
      });
    }

    const env = {
      TABLE_KARTE: karte.tableName,
      BUCKET: bucket.bucketName, MODEL_ID: modelId,
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

    const fmId = modelId.replace(/^(jp|global)\./, '');
    const bedrockPolicy = new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${modelId}`,
        `arn:aws:bedrock:${this.region}::foundation-model/${fmId}`,
        `arn:aws:bedrock:ap-northeast-3::foundation-model/${fmId}`,
      ],
    });

    // Alerts carry no member data: only resource names and counts.
    const alerts = new sns.Topic(this, 'Alerts', { topicName: 'karte-alerts' });
    if (alertEmail) alerts.addSubscription(new subs.EmailSubscription(alertEmail));

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
        'personalize:CreateBatchInferenceJob', 'personalize:CreateSolution', 'personalize:ListSolutions', 'personalize:DescribeBatchInferenceJob', 'personalize:DescribeDataset', 'personalize:DescribeSolution',
        'personalize:ListSolutionVersions', 'personalize:DeleteSolutionVersion'],
      resources: ['*'],
    }));
    pRole.grantPassRole(pipelineFn.grantPrincipal);
    pipelineFn.addToRolePolicy(new iam.PolicyStatement({ actions: ['cloudwatch:PutMetricData'], resources: ['*'] }));

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
    const pruneS = step('prune_versions');
    exportS.next(startImports);
    poll('imports', startImports, 'check_imports', Duration.minutes(1), ensureSol);
    ensureSol.next(startTrain);
    poll('train', startTrain, 'check_train', Duration.minutes(5), startBatch);
    poll('batch', startBatch, 'check_batch', Duration.minutes(2), loadS);
    loadS.next(pruneS);
    const pipeline = new sfn.StateMachine(this, 'RecoPipeline', {
      stateMachineName: 'karte-reco-pipeline', definitionBody: sfn.DefinitionBody.fromChainable(exportS),
      timeout: Duration.hours(12),
    });

    new events.Rule(this, 'WeeklyFull', {
      schedule: events.Schedule.cron({ minute: '0', hour: '18', weekDay: 'SAT' }), // Sunday 03:00 JST
      enabled: weeklyFull,
      targets: [new targets.SfnStateMachine(pipeline)],
    });

    // Failure notifications: a silent pipeline failure means recommendations quietly go stale.
    new events.Rule(this, 'PipelineFailedAlert', {
      eventPattern: {
        source: ['aws.states'], detailType: ['Step Functions Execution Status Change'],
        detail: { status: ['FAILED', 'TIMED_OUT', 'ABORTED'], stateMachineArn: [pipeline.stateMachineArn] },
      },
      targets: [new targets.SnsTopic(alerts)],
    });
    const alarm = (id: string, metric: cw.IMetric, desc: string) => {
      const a = new cw.Alarm(this, id, { metric, threshold: 1, evaluationPeriods: 1, comparisonOperator: cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD, alarmDescription: desc });
      a.addAlarmAction(new cw_actions.SnsAction(alerts));
      return a;
    };
    alarm('PipelineErrors', pipelineFn.metricErrors({ period: Duration.minutes(5) }), 'FULL pipeline step failed (rejects/schema/data problem)');
    alarm('ApiErrors', apiFn.metricErrors({ period: Duration.minutes(5) }), 'Karte API returned a server error');

    // Data-quality alarms: a PUT-side format change shows up as a jump in rejects, not as a failure.
    const buildMetric = (name: string) => new cw.Metric({ namespace: 'P121K', metricName: name, statistic: cw.Stats.MAXIMUM, period: Duration.days(1) });
    const qualityAlarm = (id: string, name: string, threshold: number, desc: string) => {
      const a = new cw.Alarm(this, id, {
        metric: buildMetric(name), threshold, evaluationPeriods: 1,
        comparisonOperator: cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cw.TreatMissingData.NOT_BREACHING, alarmDescription: desc,
      });
      a.addAlarmAction(new cw_actions.SnsAction(alerts));
      return a;
    };
    qualityAlarm('RejectsSpike', 'RejectedRows', 50, 'FULL build rejected an unusual number of rows (data contract change?)');
    qualityAlarm('SkippedFiles', 'SkippedFiles', 1, 'FULL build skipped a whole file (columns / encoding changed?)');

    const policyStatements = [
      new iam.PolicyStatement({ effect: iam.Effect.ALLOW, principals: [new iam.AccountRootPrincipal()], actions: ['execute-api:Invoke'], resources: ['execute-api:/*'] }),
    ];
    if (allowedCidrs.length) {
      policyStatements.push(new iam.PolicyStatement({
        effect: iam.Effect.DENY, principals: [new iam.AnyPrincipal()], actions: ['execute-api:Invoke'], resources: ['execute-api:/*'],
        conditions: { NotIpAddress: { 'aws:SourceIp': allowedCidrs } },
      }));
    }
    if (auditTrail) {
      const trailBucket = new s3.Bucket(this, 'AuditTrailBucket', {
        encryption: s3.BucketEncryption.KMS, encryptionKey: key, bucketKeyEnabled: true,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL, enforceSSL: true,
        removalPolicy: RemovalPolicy.DESTROY, autoDeleteObjects: true,
        lifecycleRules: [{ id: 'trail-90d', expiration: Duration.days(90) }],
      });
      const trail = new cloudtrail.Trail(this, 'AuditTrail', { bucket: trailBucket, isMultiRegionTrail: false, sendToCloudWatchLogs: false });
      // Who read/uploaded source history: object-level data events on the inbound prefixes only.
      trail.addS3EventSelector([{ bucket, objectPrefix: 'inbound/' }], { readWriteType: cloudtrail.ReadWriteType.ALL });
    }

    const accessLogs = new logs.LogGroup(this, 'ApiAccessLogs', {
      retention: logs.RetentionDays.THREE_MONTHS, removalPolicy: RemovalPolicy.DESTROY,
    });
    const api = new apigw.RestApi(this, 'KarteApi', {
      restApiName: 'karte-api', endpointTypes: [apigw.EndpointType.REGIONAL],
      policy: new iam.PolicyDocument({ statements: policyStatements }),
      defaultMethodOptions: { authorizationType: apigw.AuthorizationType.IAM },
      deployOptions: {
        stageName: 'v1', throttlingRateLimit: 50, throttlingBurstLimit: 100,
        accessLogDestination: new apigw.LogGroupLogDestination(accessLogs),
        // resourcePath (the template) is logged instead of path, so member IDs never enter the logs.
        accessLogFormat: apigw.AccessLogFormat.custom(JSON.stringify({
          requestId: '$context.requestId', resourcePath: '$context.resourcePath', status: '$context.status',
          ip: '$context.identity.sourceIp', userAgent: '$context.identity.userAgent', integrationError: '$context.integrationErrorMessage',
        })),
      },
    });
    const integ = new apigw.LambdaIntegration(apiFn);
    const karteRes = api.root.addResource('karte');
    karteRes.addResource('search').addMethod('GET', integ);
    const member = karteRes.addResource('{memberId}');
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
    // 呼び出し側が引き受ける（assume する）ロール。用途ごとに分け、付けるポリシーは1つだけ。
    // EC2 のインスタンスプロファイル / ECS タスクロール / EKS（IRSA）/ OIDC /
    // Roles Anywhere のいずれでも、このロールを引き受けさせれば長期キーは不要。
    const assumeRole = (id: string, roleName: string, policy: iam.IManagedPolicy, description: string) => {
      const role = new iam.Role(this, id, { roleName, assumedBy: new iam.AccountRootPrincipal(), managedPolicies: [policy], description });
      if (callerPrincipalArns.length) {
        role.assumeRolePolicy!.addStatements(new iam.PolicyStatement({
          actions: ['sts:AssumeRole'], principals: callerPrincipalArns.map((a) => new iam.ArnPrincipal(a)),
        }));
      }
      return role;
    };
    const uploadRole = assumeRole('UploadRole', 'p121k-upload', uploadPolicy, 'S3 PUT of inbound TSV (company servers)');
    const apiRole = assumeRole('ApiRole', 'p121k-api', invokePolicy, 'Karte API (company servers)');

    // 非推奨: 長期アクセスキーで呼ぶ場合だけ、IAM ユーザーと失効ジョブを作る。
    if (accessKeys) {
      const uploadUser = new iam.User(this, 'UploadUser', { userName: 'p121k-upload-key', managedPolicies: [uploadPolicy] });
      const apiUser = new iam.User(this, 'ApiUser', { userName: 'p121k-api-key', managedPolicies: [invokePolicy] });
      for (const u of [uploadUser, apiUser]) Tags.of(u).add('p121k-managed', 'true');
      const keyguard = fn('Keyguard', 'keyguard.handler', {
        timeout: Duration.minutes(5),
        environment: { ...env, KEY_USERS: `${uploadUser.userName},${apiUser.userName}`, ACCESS_KEY_MAX_AGE_DAYS: accessKeyMaxAgeDays },
      });
      keyguard.addToRolePolicy(new iam.PolicyStatement({
        actions: ['iam:ListAccessKeys', 'iam:UpdateAccessKey', 'iam:DeleteAccessKey', 'iam:ListUsers'],
        resources: [uploadUser.userArn, apiUser.userArn],
      }));
      new events.Rule(this, 'KeyguardDaily', {
        schedule: events.Schedule.cron({ minute: '15', hour: '19' }),
        targets: [new targets.LambdaFunction(keyguard)],
      });
      new CfnOutput(this, 'UploadUserName', { value: uploadUser.userName });
      new CfnOutput(this, 'ApiUserName', { value: apiUser.userName });
    }

    // 海外IPからのAPI呼び出しを遮断（国単位）。WAF は API Gateway のステージに紐づける。
    if (geoBlock) {
      const acl = new wafv2.CfnWebACL(this, 'ApiGeoAcl', {
        name: 'karte-api-jp-only', scope: 'REGIONAL',
        defaultAction: { allow: {} },
        visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: 'karte-api-geo', sampledRequestsEnabled: true },
        rules: [{
          name: 'block-outside-japan', priority: 0, action: { block: {} },
          statement: { notStatement: { statement: { geoMatchStatement: { countryCodes: ['JP'] } } } },
          visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: 'karte-api-block-non-jp', sampledRequestsEnabled: true },
        }],
      });
      new wafv2.CfnWebACLAssociation(this, 'ApiGeoAclAssoc', {
        resourceArn: api.deploymentStage.stageArn, webAclArn: acl.attrArn,
      });
    }

    // S3 も同じ考え方で絞る。ただし S3 に「国」の概念は無いので、
    // 自社の固定グローバルIP（CIDR）を指定した場合だけ許可リストになる。
    if (uploadCidrs.length) {
      bucket.addToResourcePolicy(new iam.PolicyStatement({
        effect: iam.Effect.DENY, principals: [new iam.AnyPrincipal()], actions: ['s3:PutObject'],
        resources: [bucket.arnForObjects('inbound/*')],
        conditions: {
          NotIpAddress: { 'aws:SourceIp': uploadCidrs },
          // the API Lambda writes the withdraw row itself; it has no fixed egress IP
          ArnNotLike: { 'aws:PrincipalArn': [apiFn.role!.roleArn] },
        },
      }));
    }

    new CfnOutput(this, 'AlertsTopicArn', { value: alerts.topicArn });
    new CfnOutput(this, 'UploadRoleArn', { value: uploadRole.roleArn });
    new CfnOutput(this, 'ApiRoleArn', { value: apiRole.roleArn });
    new CfnOutput(this, 'PipelineArn', { value: pipeline.stateMachineArn });
    new CfnOutput(this, 'ApiUrl', { value: api.url });
    new CfnOutput(this, 'ApiFunctionName', { value: apiFn.functionName });
    new CfnOutput(this, 'ApiLogGroupName', { value: apiFn.logGroup.logGroupName });
    new CfnOutput(this, 'ApiAccessLogGroupName', { value: accessLogs.logGroupName });
    new CfnOutput(this, 'BucketName', { value: bucket.bucketName });
    new CfnOutput(this, 'KarteTable', { value: karte.tableName });
    new CfnOutput(this, 'ApiInvokePolicyArn', { value: invokePolicy.managedPolicyArn });
    new CfnOutput(this, 'UploadPolicyArn', { value: uploadPolicy.managedPolicyArn });
    Tags.of(this).add('app', 'karte'); // do not change: Personalize schema tags are immutable (forces replacement)
  }
}
