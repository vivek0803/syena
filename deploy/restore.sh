#!/usr/bin/env bash
# Restore an encrypted backup:  ./deploy/restore.sh deploy/backups/syena-YYYYMMDD-HHMM.sql.gz.enc
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="$1"
COMPOSE="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"
read -r -p "This REPLACES the current database with $FILE. Type RESTORE to continue: " ok
[ "$ok" = "RESTORE" ] || exit 1
$COMPOSE stop api worker
set -a; . deploy/.env; set +a
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$FILE" | gunzip | \
  $COMPOSE exec -T db sh -c 'psql -U syena -d postgres -c "DROP DATABASE IF EXISTS syena WITH (FORCE);" -c "CREATE DATABASE syena;" && psql -U syena -d syena'
$COMPOSE up -d api worker
echo "Restored."
