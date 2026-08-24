#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${RUN_SERVICES_DOCKER:-}" == "1" || -f /.dockerenv ]]; then
  exec bash "$ROOT_DIR/scripts/run-services-docker.sh"
fi

exec bash "$ROOT_DIR/scripts/macos/start-services.sh"
