#!/bin/sh
# Entrypoint del contenedor Nous: migra el esquema antes de servir.
# alembic es idempotente — en arranques posteriores es un no-op.
set -e

alembic upgrade head
exec "$@"
