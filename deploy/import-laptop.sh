#!/usr/bin/env bash
# Load the database exported on the laptop (windows\export-for-server.bat) into the server:
#   ./deploy/import-laptop.sh syena-laptop-export.sql.gz
# Your trades, decisions, algos, screens, lists, lessons and login come along. deploy/.env must have the SAME
# SYENA_ENCRYPTION_KEY as the laptop, or the login's 2FA and saved broker sessions can't be read (you'd re-create them).
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?usage: ./deploy/import-laptop.sh <export file>}"
COMPOSE="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"
read -r -p "This REPLACES the server's database with $FILE. Type IMPORT to continue: " ok
[ "$ok" = "IMPORT" ] || exit 1
$COMPOSE up -d db
$COMPOSE stop api worker 2>/dev/null || true
gunzip -c "$FILE" | $COMPOSE exec -T db sh -c \
  'psql -q -U syena -d postgres -c "DROP DATABASE IF EXISTS syena WITH (FORCE);" -c "CREATE DATABASE syena;" && psql -q -U syena -d syena'
$COMPOSE run --rm api alembic upgrade head
$COMPOSE up -d
echo "Imported. Trading mode is PAPER; your laptop copy keeps running until you stop it (stop it to avoid double trading)."
