# AWS Deployment Guide

This guide covers deploying the Fuel Finder scraper on AWS using Lambda + RDS PostgreSQL + S3.

## Architecture

```
EventBridge Scheduler (every 30 min)
    │
    ▼
Lambda Function (Python 3.11)
    ├──▶ Fuel Finder API (OAuth2 → fetch prices)
    ├──▶ RDS PostgreSQL (INSERT changed prices, UPSERT stations)
    └──▶ S3 Bucket (raw JSON backup)
```

## Components

| Service | Purpose | Notes |
|---|---|---|
| **Lambda** | Runs the scraper | 256MB RAM, 5-min timeout sufficient |
| **RDS PostgreSQL** | Stores stations, prices, brand lookups | `db.t4g.micro` for low cost; Aurora Serverless v2 if scaling needed |
| **S3** | Raw JSON backup per scrape | Lifecycle policy to move to Glacier after 90 days |
| **EventBridge Scheduler** | Triggers Lambda every 30 min | Two rules: one for incremental (30 min), one for full (daily) |
| **Secrets Manager** | API credentials + DB connection string | Referenced by Lambda env vars |
| **VPC** | Lambda + RDS in same VPC | Lambda needs NAT Gateway for outbound API calls |

## Step-by-step

### 1. Database (RDS)

Create a PostgreSQL 16 instance:

```
Engine:       PostgreSQL 16
Instance:     db.t4g.micro (or Aurora Serverless v2)
Storage:      20GB gp3 (auto-scaling)
VPC:          Your VPC with private subnets
DB name:      fuelfinder
Master user:  fuelfinder
```

After creation, connect and run the schema:

```bash
psql -h <rds-endpoint> -U fuelfinder -d fuelfinder -f schema.sql
psql -h <rds-endpoint> -U fuelfinder -d fuelfinder -f seed_brand_aliases.sql
```

### 2. S3 Bucket

```bash
aws s3 mb s3://fuel-finder-raw --region eu-west-1
```

Optional lifecycle policy (move to Glacier after 90 days):

```json
{
  "Rules": [
    {
      "ID": "archive-old-scrapes",
      "Status": "Enabled",
      "Transitions": [
        { "Days": 90, "StorageClass": "GLACIER" }
      ]
    }
  ]
}
```

### 3. Secrets Manager

Store API credentials:

```bash
aws secretsmanager create-secret \
  --name fuel-finder/api-credentials \
  --secret-string '{"FUEL_API_ID":"your-id","FUEL_API_SECRET":"your-secret"}'
```

Store database URL:

```bash
aws secretsmanager create-secret \
  --name fuel-finder/database-url \
  --secret-string '{"DATABASE_URL":"postgresql://fuelfinder:password@rds-endpoint:5432/fuelfinder"}'
```

### 4. Lambda Function

#### Package the code

```bash
mkdir -p package
pip install requests psycopg2-binary boto3 -t package/
cp api_client.py db.py scrape.py lambda_handler.py schema.sql package/
cd package && zip -r ../fuel-finder-scraper.zip . && cd ..
```

#### Create the function

```bash
aws lambda create-function \
  --function-name fuel-finder-scraper \
  --runtime python3.11 \
  --handler lambda_handler.handler \
  --zip-file fileb://fuel-finder-scraper.zip \
  --role arn:aws:iam::<account-id>:role/fuel-finder-lambda-role \
  --timeout 300 \
  --memory-size 256 \
  --environment "Variables={
    DATABASE_URL=<from-secrets-manager>,
    FUEL_API_ID=<from-secrets-manager>,
    FUEL_API_SECRET=<from-secrets-manager>,
    S3_BUCKET=fuel-finder-raw,
    AWS_REGION=eu-west-1,
    SKIP_S3=false
  }" \
  --vpc-config SubnetIds=<private-subnets>,SecurityGroupIds=<sg-id>
```

#### IAM Role permissions

The Lambda execution role needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::fuel-finder-raw/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:eu-west-1:<account-id>:secret:fuel-finder/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

### 5. EventBridge Scheduler

Two schedules:

**Incremental (every 30 minutes):**

```bash
aws scheduler create-schedule \
  --name fuel-finder-incremental \
  --schedule-expression "rate(30 minutes)" \
  --target '{
    "Arn": "arn:aws:lambda:eu-west-1:<account-id>:function:fuel-finder-scraper",
    "Input": "{\"mode\": \"incremental\"}",
    "RoleArn": "arn:aws:iam::<account-id>:role/fuel-finder-scheduler-role"
  }' \
  --flexible-time-window '{"Mode": "OFF"}'
```

**Full (daily at 03:00 UTC):**

```bash
aws scheduler create-schedule \
  --name fuel-finder-full-daily \
  --schedule-expression "cron(0 3 * * ? *)" \
  --target '{
    "Arn": "arn:aws:lambda:eu-west-1:<account-id>:function:fuel-finder-scraper",
    "Input": "{\"mode\": \"full\"}",
    "RoleArn": "arn:aws:iam::<account-id>:role/fuel-finder-scheduler-role"
  }' \
  --flexible-time-window '{"Mode": "OFF"}'
```

## Networking

Lambda needs **outbound internet access** to reach the Fuel Finder API. If your Lambda is in a VPC (required for RDS access), you need a NAT Gateway:

```
VPC
├── Public Subnet
│   └── NAT Gateway (with Elastic IP)
├── Private Subnet A
│   ├── Lambda ENI
│   └── RDS instance
└── Private Subnet B
    └── RDS instance (Multi-AZ standby)
```

Route table for private subnets: `0.0.0.0/0 → NAT Gateway`

## Monitoring

- **Lambda**: Check CloudWatch Logs for scrape output
- **Errors**: `scrape_runs` table tracks every run with status and error messages
- **CloudWatch Alarm**: Alert on Lambda errors > 0 in 1 hour

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name fuel-finder-scrape-failures \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --dimensions Name=FunctionName,Value=fuel-finder-scraper \
  --statistic Sum \
  --period 3600 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:eu-west-1:<account-id>:alerts
```

## Cost estimate (monthly)

| Service | Estimate |
|---|---|
| Lambda (48 invocations/day × 30s × 256MB) | ~$0.50 |
| RDS db.t4g.micro (on-demand) | ~$13 |
| S3 (raw JSON, ~500MB/month) | ~$0.01 |
| NAT Gateway (data processing) | ~$5 |
| EventBridge Scheduler | Free tier |
| **Total** | **~$19/month** |

Use RDS Reserved Instances for ~40% savings on long-term production.
