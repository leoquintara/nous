# Directivas de construcción con IA — Nous

## Orden de trabajo

1. Leer `AGENTS.md` completo antes de tocar código.
2. Capas: `mcp/` → `core/` → `storage/` nunca se mezclan. La lógica vive
   en `core/`; el dashboard consume por HTTP/MCP, nunca SQL directo.
3. Un commit atómico por feature (Conventional Commits, inglés técnico).
4. Antes de commit: `pytest -q` verde + `ruff` limpio + `mypy --strict` limpio.

## Privacidad (no negociable)

- Embeddings e inferencia siempre locales; sin llamadas externas por defecto.
- Nunca loggear contenido de memorias — solo IDs y metadata.
- Tokens: formato `ns_*`, en `.env`, jamás en código ni docs.

## Dashboard

- Arquitectura hexagonal: `domain/`, `application/`, `infrastructure/`, `ui/`.
- Liquid glass: `backdrop-filter` generoso, temas light/dark con la paleta
  Quintara (`#198BF8`, `#00D6D4`, `#101050`).
- Cada feature nueva documenta su decisión en `docs/decisions/`.
- Cada feature completada y verificada notifica por Signal al 5615625539.

## E2E

- Playwright MCP roto en este host (`spawn UNKNOWN`); usar chrome-devtools MCP.
- E2E de backend: `poetry run python -m scripts.e2e_mcp`.
