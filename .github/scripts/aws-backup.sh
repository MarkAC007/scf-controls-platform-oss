#!/bin/bash
# =============================================================================
# AWS Production Database Backup Script
# Runs on the EC2 instance via AWS SSM.
# Dumps the production PostgreSQL database and uploads to S3.
# =============================================================================

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
cd /opt/scf

echo "=== Getting database URL from running backend container ==="
DB_URL=$(docker compose exec -T backend printenv DATABASE_URL 2>/dev/null | tr -d '\r')
if [ -z "$DB_URL" ]; then
  echo "Container not running — reading from docker-compose.yml"
  DB_URL=$(grep 'DATABASE_URL' docker-compose.yml 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"')
fi

if [ -z "$DB_URL" ]; then
  echo "ERROR: Could not retrieve DATABASE_URL from container or docker-compose.yml"
  exit 1
fi

# Convert asyncpg URL to standard psql URL
# postgresql+asyncpg://user:pass@host/db → postgresql://user:pass@host/db
DB_URL="${DB_URL/postgresql+asyncpg/postgresql}"
echo "Database host: $(echo "$DB_URL" | sed 's|.*@||' | sed 's|/.*||')"

echo "=== Running pg_dump ==="
# Use postgres:15-alpine Docker image with host networking to reach private RDS
docker run --rm --network=host postgres:15-alpine \
  pg_dump "$DB_URL" \
    --no-owner \
    --no-privileges \
    --no-acl \
    --exclude-schema=rdsadmin \
    --schema=public \
    -F p \
  | gzip > /tmp/backup.sql.gz

DUMP_SIZE=$(du -sh /tmp/backup.sql.gz | cut -f1)
echo "Dump complete. Compressed size: $DUMP_SIZE"

echo "=== Uploading to S3 ==="
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
S3_BUCKET="scf-prod-${ACCOUNT_ID}-evidence"
S3_KEY="db-backups/azure-migration/backup-${TIMESTAMP}.sql.gz"

aws s3 cp /tmp/backup.sql.gz \
  "s3://${S3_BUCKET}/${S3_KEY}" \
  --region eu-west-1

echo "Upload complete: s3://${S3_BUCKET}/${S3_KEY}"

# Structured output for parsing by the GitHub Actions step
echo "BACKUP_ACCOUNT_ID=${ACCOUNT_ID}"
echo "BACKUP_S3_KEY=${S3_KEY}"

rm -f /tmp/backup.sql.gz
echo "=== AWS backup complete ==="
