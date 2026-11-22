#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { KarteStack } from '../lib/karte-stack';

const app = new cdk.App();
new KarteStack(app, 'KarteStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'ap-northeast-1' },
  description: 'In-house one-to-one karte (IAM-only API, Tokyo)',
});
