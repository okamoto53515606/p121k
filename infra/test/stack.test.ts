import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { KarteStack } from '../lib/karte-stack';

const template = (ctx: Record<string, string> = {}) => {
  const app = new App({ context: ctx });
  return Template.fromStack(new KarteStack(app, 'T', { env: { account: '111111111111', region: 'ap-northeast-1' } }));
};

test('every API method requires IAM auth', () => {
  const t = template();
  const methods = t.findResources('AWS::ApiGateway::Method');
  expect(Object.keys(methods).length).toBe(5); // GET/DELETE karte, reco, copy, search
  for (const m of Object.values(methods)) {
    expect((m as any).Properties.AuthorizationType).toBe('AWS_IAM');
  }
});

test('bucket is private, KMS encrypted, TLS only', () => {
  const t = template();
  t.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true },
    BucketEncryption: { ServerSideEncryptionConfiguration: [Match.objectLike({ ServerSideEncryptionByDefault: { SSEAlgorithm: 'aws:kms', KMSMasterKeyID: Match.anyValue() } })] },
  });
  t.hasResourceProperties('AWS::S3::BucketPolicy', {
    PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } })]) },
  });
});

test('one DynamoDB table (karte) on the customer managed key; no tombstone/purge index', () => {
  const t = template();
  const tables = t.findResources('AWS::DynamoDB::GlobalTable');
  expect(Object.keys(tables).length).toBe(1);
  // the old withdraw/tombstone purge index must be gone (only search indexes remain)
  expect(JSON.stringify(tables)).not.toContain('purge-index');
  for (const x of Object.values(tables)) {
    expect((x as any).Properties.SSESpecification.SSEType).toBe('KMS');
  }
});

test('no public lambda url; no S3-triggered ingest', () => {
  const t = template();
  t.resourceCountIs('AWS::Lambda::Url', 0);
  expect(JSON.stringify(t.findResources('AWS::Events::Rule'))).not.toContain('Object Created');
});

test('source retention follows retentionYears (S3 lifecycle)', () => {
  const t = template({ retentionYears: '2' });
  t.hasResourceProperties('AWS::S3::Bucket', {
    LifecycleConfiguration: { Rules: Match.arrayWith([Match.objectLike({ Id: 'source-retention', Prefix: 'inbound/', ExpirationInDays: 730 })]) },
  });
});

test('weekly FULL schedule is opt-in', () => {
  expect(JSON.stringify(template().findResources('AWS::Events::Rule'))).toContain('"State":"DISABLED"');
  const on = template({ weeklyFull: 'true' }).findResources('AWS::Events::Rule');
  expect(JSON.stringify(on)).toContain('"State":"ENABLED"');
});

test('allowedCidrs adds an explicit deny', () => {
  const t = template({ allowedCidrs: '203.0.113.0/24' });
  t.hasResourceProperties('AWS::ApiGateway::RestApi', {
    Policy: { Statement: Match.arrayWith([Match.objectLike({ Effect: 'Deny', Condition: { NotIpAddress: { 'aws:SourceIp': ['203.0.113.0/24'] } } })]) },
  });
});

test('PITR is off so a withdrawn member cannot survive in backups', () => {
  const t = template();
  for (const x of Object.values(t.findResources('AWS::DynamoDB::GlobalTable'))) {
    expect(JSON.stringify(x)).toContain('"PointInTimeRecoverySpecification":{"PointInTimeRecoveryEnabled":false}');
  }
});

test('failures are announced (SNS topic + pipeline and API error alarms)', () => {
  const t = template();
  t.resourceCountIs('AWS::SNS::Topic', 1);
  t.resourceCountIs('AWS::CloudWatch::Alarm', 4); // pipeline errors, API errors, reject spike, skipped files
  const rules = t.findResources('AWS::Events::Rule');
  expect(JSON.stringify(rules)).toContain('Step Functions Execution Status Change');
  const alarms = JSON.stringify(t.findResources('AWS::CloudWatch::Alarm'));
  expect(alarms).toContain('RejectedRows');
  expect(alarms).toContain('SkippedFiles');
});

test('API access logs record the resource template, not the request path', () => {
  const t = template();
  t.hasResourceProperties('AWS::ApiGateway::Stage', {
    AccessLogSetting: Match.objectLike({
      Format: Match.stringLikeRegexp('resourcePath') }),
  });
  expect(JSON.stringify(t.findResources('AWS::ApiGateway::Stage'))).not.toContain('$context.path');
});

test('by default no CloudTrail trail is created; auditTrail=true adds object-level data events', () => {
  expect(Object.keys(template().findResources('AWS::CloudTrail::Trail')).length).toBe(0);
  const on = template({ auditTrail: 'true' });
  expect(Object.keys(on.findResources('AWS::CloudTrail::Trail')).length).toBe(1);
  expect(JSON.stringify(on.findResources('AWS::CloudTrail::Trail'))).toContain('inbound/');
});

test('the task role may invoke only the configured model', () => {
  const t = template();
  const json = JSON.stringify(t.findResources('AWS::IAM::Policy'));
  expect(json).toContain('inference-profile/jp.anthropic.claude-haiku-5-5');
  expect(json).not.toContain('inference-profile/jp.anthropic.*');
});

test('search indexes exist for the list queries (constant partition key)', () => {
  const t = template();
  const tables = Object.values(t.findResources('AWS::DynamoDB::GlobalTable')) as any[];
  const gsis = tables[0].Properties.GlobalSecondaryIndexes ?? [];
  const names = gsis.map((g: any) => g.IndexName).sort();
  expect(names).toEqual([
    'karte-first-purchase', 'karte-first-view', 'karte-last-purchase', 'karte-last-view',
    'karte-purchase-count-90d', 'karte-view-count-90d',
  ]);
  for (const g of gsis) {
    expect(g.KeySchema).toEqual(expect.arrayContaining([{ AttributeName: 'entity', KeyType: 'HASH' }]));
    expect(g.Projection.ProjectionType).toBe('KEYS_ONLY');
  }
});

test('one IAM user per purpose, each with only its own policy', () => {
  const t = template();
  const users = t.findResources('AWS::IAM::User');
  expect(Object.keys(users).length).toBe(2);
  const json = JSON.stringify(users);
  expect(json).toContain('p121k-upload');
  expect(json).toContain('p121k-api');
  t.hasResourceProperties('AWS::IAM::ManagedPolicy', { ManagedPolicyName: 'karte-inbound-upload' });
  t.hasResourceProperties('AWS::IAM::ManagedPolicy', { ManagedPolicyName: 'karte-api-invoke' });
});

test('API blocks requests from outside Japan by default (WAF geo match)', () => {
  const t = template();
  expect(Object.keys(t.findResources('AWS::WAFv2::WebACL')).length).toBe(1);
  expect(Object.keys(t.findResources('AWS::WAFv2::WebACLAssociation')).length).toBe(1);
  expect(JSON.stringify(t.findResources('AWS::WAFv2::WebACL'))).toContain('JP');
  expect(Object.keys(template({ geoBlock: 'false' }).findResources('AWS::WAFv2::WebACL')).length).toBe(0);
});

test('an S3 upload allow-list denies every other source but the API role', () => {
  const t = template({ uploadCidrs: '203.0.113.0/24' });
  const pol = JSON.stringify(t.findResources('AWS::S3::BucketPolicy'));
  expect(pol).toContain('203.0.113.0/24');
  expect(pol).toContain('ArnNotLike');
  expect(Object.keys(template().findResources('AWS::S3::BucketPolicy')).length).toBe(1); // only the TLS rule
});
