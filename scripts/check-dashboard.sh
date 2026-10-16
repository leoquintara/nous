#!/usr/bin/env bash
# Verificación del dashboard en un contenedor node efímero.
# Copia el repo (ro) a /work para no ensuciar el host, luego:
#   npm ci + vitest + tsc --noEmit + eslint
set -euo pipefail
cd /work/dashboard
npm ci --no-audit --no-fund
npx vitest run
npx tsc --noEmit
npx eslint src/ --max-warnings 0
