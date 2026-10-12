# Nous

Memoria persistente 4D para agentes de IA. Servidor MCP local-first. Un producto de Quintara AI (quintarai.com).

Nous almacena, consulta y analiza memorias en tres dimensiones (cognitiva, somática, conductual) y mantiene una línea de universo 4D con detección de trayectorias y proyección de futuros probables.

## Quickstart

```bash
cp .env.example .env          # Editar con tus valores
docker-compose up -d          # PostgreSQL 18 + pgvector
poetry install
poetry run alembic upgrade head
poetry run python -m src.main
```

## Comandos

```bash
poetry run pytest --cov=src --cov-report=term-missing
poetry run ruff check src/
poetry run mypy --strict src/
```

Ver [AGENTS.md](AGENTS.md) para arquitectura, convenciones y reglas del proyecto.

## Licencia

AGPL 3.0 para uso open source — ver [LICENSE](LICENSE).
Licencia comercial disponible: licensing@quintarai.com
