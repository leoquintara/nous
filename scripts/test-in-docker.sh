#!/usr/bin/env bash
# pytest + cobertura dentro de un contenedor efímero.
set -euo pipefail
cd /app
pip install -q 'pytest==9.0.2' 'pytest-asyncio==1.3.0' 'pytest-cov==7.0.0' 'sqlalchemy[asyncio]==2.0.50' 'asgi_lifespan==2.1.0' 'httpx==0.28.1'
export NOUS_DATABASE_URL="${NOUS_DATABASE_URL:-postgresql+asyncpg://nous:nous@host.docker.internal:5433/nous_test}"
export NOUS_LICENSE_KEY="${NOUS_LICENSE_KEY:-licencia-test}"
exec pytest -q "$@"
