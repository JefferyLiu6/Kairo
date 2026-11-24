#!/usr/bin/env bash
# Pull the latest code and rebuild/restart the backend on the Oracle VM.
# User data lives in Docker volumes, so it is kept across every redeploy.
#
#   cd ~/kairo && bash deploy/oracle/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

git pull --ff-only
bash deploy/oracle/dc.sh up -d --build backend caddy
docker image prune -f >/dev/null
bash deploy/oracle/dc.sh ps
