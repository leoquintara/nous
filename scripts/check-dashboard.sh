#!/usr/bin/env bash
# Verificación del dashboard en un contenedor node efímero.
# Copia el repo (ro) a /work para no ensuciar el host, luego:
#   yarn install + vitest + tsc --noEmit + eslint
set -euo pipefail
cd /work/dashboard
corepack enable
yarn install --immutable
yarn vitest run
yarn tsc --noEmit
yarn eslint src/ --max-warnings 0
