# Fuel-Finder Staging — Rebuild Checklist

If staging needs to be recreated, here's what existed and how to rebuild it.

## Region / Account
- **Region**: eu-north-1
- **Account**: 028597908565

## Resources that were torn down (April 2026)

### ECS
- **Cluster**: `fuel-finder` (shared with prod — NOT deleted)
- **Service**: `fuel-finder-staging` (deleted)
- **Task definition family**: `fuel-finder-staging`
- **Network**: awsvpc, `assignPublicIp=ENABLED`, security group `fuel-finder-ecs-sg`

### RDS
- **Instance**: `fuel-finder-staging` (db.t4g.micro, Postgres 16, 20 GB gp3)
- **DB name**: `fuelfinder`, **user**: `fuelfinder`
- **Subnet group**: `fuel-finder-db-subnets` (shared — NOT deleted)
- **Security group**: `fuel-finder-rds-sg` (shared — NOT deleted)

### Secrets Manager
- `fuel-finder-staging/DATABASE_URL`
- `fuel-finder-staging/API_KEY`

### S3 Bucket
- `fuel-finder-raw-028597908565` — shared with prod (NOT deleted)

### ALB
- **ALB name**: `fuel-finder-alb` (shared — NOT deleted)
- **Listener rule**: host-based rule routing `staging-fuel.hoy.la` to staging target group (deleted)
- **Target group**: `fuel-finder-staging-tg` (HTTP/8000, target type `ip`, health check `/health`) (deleted)

### Task Definition Config (deleted from repo)
- Was at `.aws/task-definition-staging.json`
- Container: `app`, port 8000, Fargate 256 CPU / 512 MB
- Env vars: ENVIRONMENT=staging, CORS_ORIGINS=`https://staging-fuel.hoy.la`
- Cognito: pool `eu-north-1_kpTRri6tv`, client `7orvb2g5ahtmsm08il0ebii1mr`
- Logs: `/ecs/fuel-finder` prefix `staging`
- Health check: `curl -sf http://localhost:8000/health || exit 1` (30s interval, 10s timeout, 3 retries, 60s start period)

### CI/CD
- GitHub Actions workflow had `deploy-staging` job (non-main branches)
- Used GitHub environment: `staging`
- ECR pushes happened on all branch pushes (not just main)

## To Rebuild
1. Re-create RDS instance (see `.aws/setup-infrastructure.sh` step 6)
2. Re-create Secrets Manager entries (DATABASE_URL, API_KEY)
3. Re-create ECS service + target group + ALB listener rule for `staging-fuel.hoy.la`
4. Re-create `.aws/task-definition-staging.json` (use prod as template, change family/env/secrets)
5. Re-add `deploy-staging` job to `.github/workflows/ci.yml`
6. Re-add `staging` environment in GitHub repo settings
7. Restore ECR push for all branches (not just main)
