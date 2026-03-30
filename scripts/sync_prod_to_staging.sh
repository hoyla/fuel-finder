#!/usr/bin/env bash
set -euo pipefail

# Sync production database to staging for regression testing.
#
# Requires:
#   - AWS CLI configured with credentials that can read Secrets Manager
#   - psql and pg_dump installed locally (e.g. via brew install libpq)
#   - Network access to both RDS instances (e.g. via SSM tunnel or VPN)
#
# Usage:
#   ./scripts/sync_prod_to_staging.sh
#
# What it does:
#   1. Reads DATABASE_URL for prod and staging from Secrets Manager
#   2. pg_dump from production
#   3. Drops and recreates the staging database
#   4. pg_restore into staging
#   5. Refreshes materialised views

REGION="${AWS_REGION:-eu-north-1}"
PROD_SECRET="fuel-finder-prod/DATABASE_URL"
STAGING_SECRET="fuel-finder-staging/DATABASE_URL"

echo "=== Fuel Finder: Sync production DB → staging ==="
echo ""
echo "This will REPLACE the entire staging database with production data."
echo ""
read -r -p "Are you sure? Type 'yes' to continue: " confirm
if [[ "$confirm" != "yes" ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "Fetching connection strings from Secrets Manager..."

PROD_URL=$(aws secretsmanager get-secret-value \
    --region "$REGION" \
    --secret-id "$PROD_SECRET" \
    --query 'SecretString' --output text)

STAGING_URL=$(aws secretsmanager get-secret-value \
    --region "$REGION" \
    --secret-id "$STAGING_SECRET" \
    --query 'SecretString' --output text)

# Sanity check: refuse to proceed if URLs look identical
if [[ "$PROD_URL" == "$STAGING_URL" ]]; then
    echo "ERROR: Production and staging URLs are identical. Aborting."
    exit 1
fi

DUMP_FILE=$(mktemp /tmp/fuel-finder-prod-dump.XXXXXX.sql)
trap 'rm -f "$DUMP_FILE"' EXIT

echo "Dumping production database..."
pg_dump "$PROD_URL" \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    --format=custom \
    --file="$DUMP_FILE"

echo "Dump complete ($(du -h "$DUMP_FILE" | cut -f1))."

echo "Restoring into staging database..."
pg_restore "$STAGING_URL" \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    --single-transaction \
    --dbname="$STAGING_URL" \
    "$DUMP_FILE"

echo "Refreshing materialised views..."
psql "$STAGING_URL" -c "REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices;"

echo ""
echo "Done. Staging database now mirrors production."
