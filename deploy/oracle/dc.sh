#!/usr/bin/env bash
# docker compose with the Oracle VM's files and env, from anywhere:
#
#   bash deploy/oracle/dc.sh logs -f backend
#   bash deploy/oracle/dc.sh restart backend
#   bash deploy/oracle/dc.sh ps
set -euo pipefail
cd "$(dirname "$0")/../.."
exec docker compose -f docker-compose.yml -f deploy/oracle/compose.yml \
  --env-file .env.production "$@"
