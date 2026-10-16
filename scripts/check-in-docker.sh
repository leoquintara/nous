#!/usr/bin/env bash
# Corre un script dentro de un contenedor efímero con el repo montado.
# Uso: bash scripts/check-in-docker.sh lint-in-docker.sh
#      bash scripts/check-in-docker.sh test-in-docker.sh tests/integration/test_scopes.py -x
set -euo pipefail
SCRIPT="$1"; shift || true
exec docker run --rm \
  -v "$(pwd):/app" \
  --add-host host.docker.internal:host-gateway \
  -w /app \
  --entrypoint bash \
  nous-by-quintara-app:latest \
  "scripts/$SCRIPT" "$@"
