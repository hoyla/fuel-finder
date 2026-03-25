#!/usr/bin/env bash
# ==========================================================================
# Fuel Finder — AWS Infrastructure Setup
#
# Run this script from the project root. It is idempotent — safe to re-run.
# Prerequisites: aws cli configured with appropriate permissions.
#
# What it creates:
#   1. ECR repository
#   2. S3 bucket (raw JSON backups)
#   3. CloudWatch log group
#   4. Security groups (ALB / ECS / RDS)
#   5. RDS PostgreSQL (staging + prod)
#   6. IAM roles (ECS execution, ECS task, GitHub Actions deploy)
#   7. GitHub OIDC provider
#   8. Secrets Manager entries (per-environment)
#   9. Application Load Balancer + target groups
#  10. ECS cluster + services (staging + prod)
#
# After running, add AWS_DEPLOY_ROLE_ARN to GitHub repo secrets, then
# push to deploy via GitHub Actions.
# ==========================================================================

set -euo pipefail

REGION="eu-north-1"
PROJECT="fuel-finder"
GITHUB_REPO="hoyla/fuel-finder"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "============================================"
echo "  Fuel Finder — AWS Setup"
echo "  Account: $ACCOUNT_ID  Region: $REGION"
echo "============================================"
echo ""

# ------------------------------------------------------------------
# 1. ECR Repository
# ------------------------------------------------------------------
echo ">>> 1/10  Creating ECR repository..."
aws ecr create-repository \
    --repository-name "$PROJECT" \
    --region "$REGION" \
    --image-scanning-configuration scanOnPush=true \
    --no-cli-pager 2>/dev/null || echo "    (already exists)"

ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$PROJECT"
echo "    ECR URI: $ECR_URI"
echo ""

# ------------------------------------------------------------------
# 2. S3 Bucket (raw JSON backups)
# ------------------------------------------------------------------
echo ">>> 2/10  Creating S3 bucket..."
S3_BUCKET="fuel-finder-raw-$ACCOUNT_ID"
aws s3api create-bucket \
    --bucket "$S3_BUCKET" \
    --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" \
    --no-cli-pager 2>/dev/null || echo "    (already exists)"

aws s3api put-public-access-block \
    --bucket "$S3_BUCKET" \
    --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
    --region "$REGION" --no-cli-pager

aws s3api put-bucket-versioning \
    --bucket "$S3_BUCKET" \
    --versioning-configuration Status=Enabled \
    --region "$REGION" --no-cli-pager

echo "    ✓ $S3_BUCKET"
echo ""

# ------------------------------------------------------------------
# 3. CloudWatch Log Group
# ------------------------------------------------------------------
echo ">>> 3/10  Creating CloudWatch log group..."
aws logs create-log-group \
    --log-group-name "/ecs/$PROJECT" \
    --region "$REGION" --no-cli-pager 2>/dev/null || echo "    (already exists)"

aws logs put-retention-policy \
    --log-group-name "/ecs/$PROJECT" \
    --retention-in-days 30 \
    --region "$REGION" --no-cli-pager

echo "    ✓ /ecs/$PROJECT (30 day retention)"
echo ""

# ------------------------------------------------------------------
# 4. VPC + Security Groups
# ------------------------------------------------------------------
echo ">>> 4/10  Looking up default VPC & creating security groups..."

VPC_ID=$(aws ec2 describe-vpcs \
    --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' --output text \
    --region "$REGION" --no-cli-pager)

if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
    echo "    ERROR: No default VPC found. Create one with: aws ec2 create-default-vpc"
    exit 1
fi

SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true \
    --query 'Subnets[*].SubnetId' --output text \
    --region "$REGION" --no-cli-pager)

SUBNET_1=$(echo "$SUBNET_IDS" | awk '{print $1}')
SUBNET_2=$(echo "$SUBNET_IDS" | awk '{print $2}')

echo "    VPC: $VPC_ID"
echo "    Subnets: $SUBNET_1, $SUBNET_2"

# ALB security group — HTTP from anywhere
ALB_SG=$(aws ec2 create-security-group \
    --group-name "$PROJECT-alb-sg" \
    --description "Fuel Finder ALB - HTTP inbound" \
    --vpc-id "$VPC_ID" --region "$REGION" \
    --query 'GroupId' --output text --no-cli-pager 2>/dev/null || \
    aws ec2 describe-security-groups \
        --filters Name=group-name,Values="$PROJECT-alb-sg" Name=vpc-id,Values="$VPC_ID" \
        --query 'SecurityGroups[0].GroupId' --output text \
        --region "$REGION" --no-cli-pager)

aws ec2 authorize-security-group-ingress \
    --group-id "$ALB_SG" --protocol tcp --port 80 --cidr 0.0.0.0/0 \
    --region "$REGION" --no-cli-pager 2>/dev/null || true

echo "    ALB SG: $ALB_SG"

# ECS security group — traffic from ALB only
ECS_SG=$(aws ec2 create-security-group \
    --group-name "$PROJECT-ecs-sg" \
    --description "Fuel Finder ECS - ALB inbound only" \
    --vpc-id "$VPC_ID" --region "$REGION" \
    --query 'GroupId' --output text --no-cli-pager 2>/dev/null || \
    aws ec2 describe-security-groups \
        --filters Name=group-name,Values="$PROJECT-ecs-sg" Name=vpc-id,Values="$VPC_ID" \
        --query 'SecurityGroups[0].GroupId' --output text \
        --region "$REGION" --no-cli-pager)

aws ec2 authorize-security-group-ingress \
    --group-id "$ECS_SG" --protocol tcp --port 8000 --source-group "$ALB_SG" \
    --region "$REGION" --no-cli-pager 2>/dev/null || true

echo "    ECS SG: $ECS_SG"

# RDS security group — Postgres from ECS only
RDS_SG=$(aws ec2 create-security-group \
    --group-name "$PROJECT-rds-sg" \
    --description "Fuel Finder RDS - ECS inbound only" \
    --vpc-id "$VPC_ID" --region "$REGION" \
    --query 'GroupId' --output text --no-cli-pager 2>/dev/null || \
    aws ec2 describe-security-groups \
        --filters Name=group-name,Values="$PROJECT-rds-sg" Name=vpc-id,Values="$VPC_ID" \
        --query 'SecurityGroups[0].GroupId' --output text \
        --region "$REGION" --no-cli-pager)

aws ec2 authorize-security-group-ingress \
    --group-id "$RDS_SG" --protocol tcp --port 5432 --source-group "$ECS_SG" \
    --region "$REGION" --no-cli-pager 2>/dev/null || true

echo "    RDS SG: $RDS_SG"
echo ""

# ------------------------------------------------------------------
# 5. RDS PostgreSQL (staging + prod)
# ------------------------------------------------------------------
echo ">>> 5/10  Creating RDS PostgreSQL instances..."

DB_PASSWORD_STAGING=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
DB_PASSWORD_PROD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)

aws rds create-db-subnet-group \
    --db-subnet-group-name "$PROJECT-db-subnets" \
    --db-subnet-group-description "Fuel Finder DB subnets" \
    --subnet-ids "$SUBNET_1" "$SUBNET_2" \
    --region "$REGION" --no-cli-pager 2>/dev/null || echo "    (subnet group exists)"

for ENV in staging prod; do
    if [ "$ENV" = "prod" ]; then
        DB_PASSWORD="$DB_PASSWORD_PROD"
    else
        DB_PASSWORD="$DB_PASSWORD_STAGING"
    fi

    aws rds create-db-instance \
        --db-instance-identifier "$PROJECT-${ENV}" \
        --engine postgres --engine-version 16 \
        --db-instance-class db.t4g.micro \
        --allocated-storage 20 --storage-type gp3 \
        --master-username fuelfinder \
        --master-user-password "$DB_PASSWORD" \
        --db-name fuelfinder \
        --vpc-security-group-ids "$RDS_SG" \
        --db-subnet-group-name "$PROJECT-db-subnets" \
        --backup-retention-period 7 \
        --no-publicly-accessible --no-multi-az \
        --storage-encrypted \
        --region "$REGION" --no-cli-pager 2>/dev/null || echo "    $PROJECT-${ENV} (instance exists)"

    echo "    ✓ $PROJECT-${ENV} (db.t4g.micro, Postgres 16)"
done
echo ""

# ------------------------------------------------------------------
# 6. IAM Roles
# ------------------------------------------------------------------
echo ">>> 6/10  Creating IAM roles..."

cat > /tmp/ecs-trust.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ecs-tasks.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

# ECS task execution role (pulls images, reads secrets)
EXEC_ROLE_ARN=$(aws iam create-role \
    --role-name "$PROJECT-ecs-execution" \
    --assume-role-policy-document file:///tmp/ecs-trust.json \
    --query 'Role.Arn' --output text --no-cli-pager 2>/dev/null || \
    aws iam get-role --role-name "$PROJECT-ecs-execution" \
        --query 'Role.Arn' --output text --no-cli-pager)

aws iam attach-role-policy \
    --role-name "$PROJECT-ecs-execution" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy \
    --no-cli-pager 2>/dev/null || true

cat > /tmp/secrets-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["secretsmanager:GetSecretValue"],
    "Resource": [
      "arn:aws:secretsmanager:$REGION:$ACCOUNT_ID:secret:$PROJECT-staging/*",
      "arn:aws:secretsmanager:$REGION:$ACCOUNT_ID:secret:$PROJECT-prod/*"
    ]
  }]
}
EOF

aws iam put-role-policy \
    --role-name "$PROJECT-ecs-execution" \
    --policy-name "$PROJECT-read-secrets" \
    --policy-document file:///tmp/secrets-policy.json \
    --no-cli-pager

echo "    ✓ $PROJECT-ecs-execution: $EXEC_ROLE_ARN"

# ECS task role (app's own permissions — S3 for raw backups)
TASK_ROLE_ARN=$(aws iam create-role \
    --role-name "$PROJECT-ecs-task" \
    --assume-role-policy-document file:///tmp/ecs-trust.json \
    --query 'Role.Arn' --output text --no-cli-pager 2>/dev/null || \
    aws iam get-role --role-name "$PROJECT-ecs-task" \
        --query 'Role.Arn' --output text --no-cli-pager)

cat > /tmp/s3-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::$S3_BUCKET",
      "arn:aws:s3:::$S3_BUCKET/*"
    ]
  }]
}
EOF

aws iam put-role-policy \
    --role-name "$PROJECT-ecs-task" \
    --policy-name "$PROJECT-s3-access" \
    --policy-document file:///tmp/s3-policy.json \
    --no-cli-pager

echo "    ✓ $PROJECT-ecs-task: $TASK_ROLE_ARN"
echo ""

# ------------------------------------------------------------------
# 7. GitHub OIDC Provider + Deploy Role
# ------------------------------------------------------------------
echo ">>> 7/10  Setting up GitHub OIDC provider + deploy role..."

aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
    --no-cli-pager 2>/dev/null || echo "    (OIDC provider exists)"

cat > /tmp/github-trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:${GITHUB_REPO}:*"
      }
    }
  }]
}
EOF

DEPLOY_ROLE_ARN=$(aws iam create-role \
    --role-name "$PROJECT-github-deploy" \
    --assume-role-policy-document file:///tmp/github-trust.json \
    --query 'Role.Arn' --output text --no-cli-pager 2>/dev/null || \
    aws iam get-role --role-name "$PROJECT-github-deploy" \
        --query 'Role.Arn' --output text --no-cli-pager)

cat > /tmp/deploy-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ecr:GetAuthorizationToken"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage", "ecr:PutImage",
        "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload"
      ],
      "Resource": "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PROJECT"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecs:UpdateService", "ecs:DescribeServices",
        "ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition",
        "ecs:ListTasks", "ecs:DescribeTasks"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": ["$EXEC_ROLE_ARN", "$TASK_ROLE_ARN"]
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name "$PROJECT-github-deploy" \
    --policy-name "$PROJECT-deploy-permissions" \
    --policy-document file:///tmp/deploy-policy.json \
    --no-cli-pager

echo "    ✓ $PROJECT-github-deploy: $DEPLOY_ROLE_ARN"
echo ""

# ------------------------------------------------------------------
# 8. Secrets Manager (per-environment)
# ------------------------------------------------------------------
echo ">>> 8/10  Storing secrets..."

API_KEY_STAGING=$(openssl rand -hex 32)
API_KEY_PROD=$(openssl rand -hex 32)

for ENV in staging prod; do
    echo "    --- ${ENV} ---"

    echo "    Waiting for RDS instance $PROJECT-${ENV} to become available..."
    echo "    (This may take 5-10 minutes)"
    aws rds wait db-instance-available \
        --db-instance-identifier "$PROJECT-${ENV}" \
        --region "$REGION" --no-cli-pager

    RDS_ENDPOINT=$(aws rds describe-db-instances \
        --db-instance-identifier "$PROJECT-${ENV}" \
        --query 'DBInstances[0].Endpoint.Address' --output text \
        --region "$REGION" --no-cli-pager)

    if [ "$ENV" = "prod" ]; then
        DB_PASSWORD="$DB_PASSWORD_PROD"
        API_KEY="$API_KEY_PROD"
    else
        DB_PASSWORD="$DB_PASSWORD_STAGING"
        API_KEY="$API_KEY_STAGING"
    fi

    DATABASE_URL="postgresql://fuelfinder:${DB_PASSWORD}@${RDS_ENDPOINT}:5432/fuelfinder"

    for SECRET_NAME in DATABASE_URL API_KEY; do
        SECRET_VALUE="${!SECRET_NAME}"
        aws secretsmanager create-secret \
            --name "$PROJECT-${ENV}/$SECRET_NAME" \
            --secret-string "$SECRET_VALUE" \
            --region "$REGION" --no-cli-pager 2>/dev/null || \
        aws secretsmanager put-secret-value \
            --secret-id "$PROJECT-${ENV}/$SECRET_NAME" \
            --secret-string "$SECRET_VALUE" \
            --region "$REGION" --no-cli-pager
        echo "    ✓ $PROJECT-${ENV}/$SECRET_NAME"
    done
done
echo ""

# ------------------------------------------------------------------
# 9. ALB + Target Groups
# ------------------------------------------------------------------
echo ">>> 9/10  Creating ALB and target groups..."

ALB_ARN=$(aws elbv2 create-load-balancer \
    --name "$PROJECT-alb" \
    --subnets "$SUBNET_1" "$SUBNET_2" \
    --security-groups "$ALB_SG" \
    --scheme internet-facing --type application \
    --region "$REGION" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text \
    --no-cli-pager 2>/dev/null || \
    aws elbv2 describe-load-balancers \
        --names "$PROJECT-alb" \
        --query 'LoadBalancers[0].LoadBalancerArn' --output text \
        --region "$REGION" --no-cli-pager)

ALB_DNS=$(aws elbv2 describe-load-balancers \
    --load-balancer-arns "$ALB_ARN" \
    --query 'LoadBalancers[0].DNSName' --output text \
    --region "$REGION" --no-cli-pager)

echo "    ALB: $ALB_DNS"

for ENV in staging prod; do
    TG_NAME="$PROJECT-${ENV}-tg"
    TG_ARN=$(aws elbv2 create-target-group \
        --name "$TG_NAME" \
        --protocol HTTP --port 8000 \
        --vpc-id "$VPC_ID" --target-type ip \
        --health-check-path /health \
        --health-check-interval-seconds 30 \
        --healthy-threshold-count 2 \
        --unhealthy-threshold-count 3 \
        --region "$REGION" \
        --query 'TargetGroups[0].TargetGroupArn' --output text \
        --no-cli-pager 2>/dev/null || \
        aws elbv2 describe-target-groups \
            --names "$TG_NAME" \
            --query 'TargetGroups[0].TargetGroupArn' --output text \
            --region "$REGION" --no-cli-pager)

    if [ "$ENV" = "staging" ]; then TG_ARN_STAGING="$TG_ARN"; fi
    if [ "$ENV" = "prod" ]; then TG_ARN_PROD="$TG_ARN"; fi
    echo "    ✓ Target group: $TG_NAME"
done

# HTTP listener — default action → prod
LISTENER_ARN=$(aws elbv2 describe-listeners \
    --load-balancer-arn "$ALB_ARN" \
    --query 'Listeners[0].ListenerArn' --output text \
    --region "$REGION" --no-cli-pager 2>/dev/null || echo "None")

if [ "$LISTENER_ARN" = "None" ] || [ -z "$LISTENER_ARN" ]; then
    LISTENER_ARN=$(aws elbv2 create-listener \
        --load-balancer-arn "$ALB_ARN" \
        --protocol HTTP --port 80 \
        --default-actions Type=forward,TargetGroupArn="$TG_ARN_PROD" \
        --region "$REGION" \
        --query 'Listeners[0].ListenerArn' --output text \
        --no-cli-pager)
    echo "    ✓ HTTP listener (default → prod)"
fi

echo ""

# ------------------------------------------------------------------
# 10. ECS Cluster + Services
# ------------------------------------------------------------------
echo ">>> 10/10  Creating ECS cluster and services..."

aws ecs create-cluster \
    --cluster-name "$PROJECT" \
    --region "$REGION" --no-cli-pager 2>/dev/null || echo "    (cluster exists)"

echo "    ✓ ECS cluster: $PROJECT"

# Patch task definitions with real account ID and register
for ENV in staging prod; do
    TASK_DEF_CONTENT=$(cat ".aws/task-definition-${ENV}.json" \
        | sed "s|ACCOUNT_ID|$ACCOUNT_ID|g" \
        | sed "s|PLACEHOLDER|$ECR_URI:latest|g")

    echo "$TASK_DEF_CONTENT" > "/tmp/task-def-${ENV}-resolved.json"

    TASK_DEF_ARN=$(aws ecs register-task-definition \
        --cli-input-json "file:///tmp/task-def-${ENV}-resolved.json" \
        --region "$REGION" \
        --query 'taskDefinition.taskDefinitionArn' --output text \
        --no-cli-pager)

    echo "    ✓ Task definition (${ENV}): $TASK_DEF_ARN"
done

for ENV in staging prod; do
    if [ "$ENV" = "staging" ]; then
        TG_ARN="$TG_ARN_STAGING"
    else
        TG_ARN="$TG_ARN_PROD"
    fi

    aws ecs create-service \
        --cluster "$PROJECT" \
        --service-name "$PROJECT-${ENV}" \
        --task-definition "$PROJECT-${ENV}" \
        --desired-count 1 \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_1,$SUBNET_2],securityGroups=[$ECS_SG],assignPublicIp=ENABLED}" \
        --load-balancers "targetGroupArn=$TG_ARN,containerName=app,containerPort=8000" \
        --region "$REGION" --no-cli-pager 2>/dev/null || echo "    $PROJECT-${ENV} (service exists)"

    echo "    ✓ ECS service: $PROJECT-${ENV}"
done
echo ""

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo "============================================"
echo "  ✅  AWS Infrastructure Ready!"
echo "============================================"
echo ""
echo "  ALB URL:    http://$ALB_DNS"
echo "  ECR URI:    $ECR_URI"
echo "  S3 Bucket:  $S3_BUCKET"
echo ""
echo "  Staging:"
echo "    RDS:      $PROJECT-staging"
echo "    Secrets:  $PROJECT-staging/{DATABASE_URL,API_KEY}"
echo ""
echo "  Production:"
echo "    RDS:      $PROJECT-prod"
echo "    Secrets:  $PROJECT-prod/{DATABASE_URL,API_KEY}"
echo ""
echo "  Deploy Role ARN (add to GitHub secrets as AWS_DEPLOY_ROLE_ARN):"
echo "    $DEPLOY_ROLE_ARN"
echo ""
echo "  API Keys:"
echo "    Staging: $API_KEY_STAGING"
echo "    Prod:    $API_KEY_PROD"
echo ""
echo "  Next steps:"
echo "    1. Add AWS_DEPLOY_ROLE_ARN secret to GitHub repo:"
echo "       gh secret set AWS_DEPLOY_ROLE_ARN --body \"$DEPLOY_ROLE_ARN\""
echo "    2. Create 'staging' environment in GitHub (no protection)"
echo "    3. Create 'production' environment in GitHub (add reviewers)"
echo "    4. Push to a branch → deploys to staging"
echo "       Merge to main → deploys to production"
echo "    5. (Optional) Add Cognito for JWT auth — see web/auth.py"
echo ""

# Clean up temp files
rm -f /tmp/ecs-trust.json /tmp/secrets-policy.json /tmp/s3-policy.json \
      /tmp/github-trust.json /tmp/deploy-policy.json \
      /tmp/task-def-staging-resolved.json /tmp/task-def-prod-resolved.json
