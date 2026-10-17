#!/usr/bin/env bash
# Deploy / redeploy Syena on the VPS:  ./deploy/deploy.sh
# git pull -> CI gate (tests + parser evals) -> build -> migrate -> restart. Every restart drops to PAPER.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"

[ -f deploy/.env ] || { echo "deploy/.env missing (copy deploy/.env.example)"; exit 1; }
chmod 600 deploy/.env

echo "==> pulling"
git pull --ff-only

echo "==> building images"
$COMPOSE build

echo "==> CI gate: deterministic tests + parser evals (deploy aborts on failure)"
docker run --rm -e SYENA_ENV=test -v "$PWD/backend/tests:/app/backend/tests:ro" --entrypoint sh syena-backend:latest -c \
  "pip install --user -q pytest hypothesis httpx >/dev/null && python -m pytest -q -p no:cacheprovider tests && syena eval-gate"

echo "==> starting database"
$COMPOSE up -d db
echo "==> migrating"
$COMPOSE run --rm api alembic upgrade head

echo "==> restarting services"
$COMPOSE up -d --remove-orphans
$COMPOSE ps

echo "==> health"
sleep 5
curl -fsS "https://$(grep '^SYENA_DOMAIN=' deploy/.env | cut -d= -f2 | awk '{print $1}')/health" && echo
echo "Done. Mode is PAPER after every deploy; switch to LIVE from Settings if you want it."
