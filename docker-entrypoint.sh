#!/bin/sh
# Entrypoint del contenedor Nous: migra el esquema antes de servir.
# alembic es idempotente — en arranques posteriores es un no-op.
set -e

# Atajo para instalar skills/agentes sin checkout ni CLI local:
#   docker run --rm -v ~/.agents:/agents <imagen> skills [install|list]
# Escribe en el bind-mount /agents (o NOUS_SKILLS_TARGET). Sin DB.
if [ "$1" = "skills" ]; then
    shift
    [ $# -eq 0 ] && set -- install
    exec python -m src.cli skills "$@" --target "${NOUS_SKILLS_TARGET:-/agents}"
fi

alembic upgrade head
exec "$@"
