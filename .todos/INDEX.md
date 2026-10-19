# .todos — Seguimiento de tareas Nous

| # | Tarea | Estado |
|---|-------|--------|
| 1 | E2E navegador | ✅ Chrome instalado está roto (SxS); E2E real con Playwright-core + Edge headless: login→home→4 páginas→tema, 0 errores consola (`scripts/e2e_browser.js`) |
| 2 | Diagnosticar/arreglar memlord | ✅ vivo en :8477, MCP responde |
| 3 | Benchmark Nous vs gbrain/memlord | ✅ `scripts/benchmark_vs.py` — Nous gana en capacidades 4D; memlord en p95 de query; gbrain en writes |
| 4 | Nous como MCP en Devin | ✅ stdio en mcp_config.json — activo en sesión |
| 5 | .todos, .agents/{skills,agents,directives} | ✅ completo |
| 6 | Dashboard Next 16 + TS hexagonal + liquid glass | ✅ build OK, E2E visual OK |
| 7 | Migrar DSH Memory (cerebro 3D) a TS + Iconoir | ✅ tipos/geometria/fisica/CerebroCanvas + iconoir |
| 8 | Fix consultar_memoria colgada en stdio | ✅ commit 636a630 (HF offline + warm-up main thread) |
| 9 | Signal por feature a 5615625539 | ⏳ al final |
| 10 | CLI completa de Nous | ✅ commit 3f81b20 — serve/version/doctor/db/seed/stats/export/import/benchmark + entry `nous` (argparse, sin deps) |
| 11 | Upgrade de versiones estilo gbrain | ✅ commit 3f81b20 — check-update/upgrade con detección fuente/paquete + alembic post-upgrade |
| 12 | Tests CLI + upgrade (Python) | ✅ 37 tests nuevos (unit + integración vs nous_test) — 116 total |
| 13 | Tests dashboard (vitest) | ✅ commit e62a891 — 28 tests adaptador API + cerebro (tipos/física/geometría) |
| 14 | Auditoría OWASP + tests | ✅ commit 2a7aee5 — 50 tests (1/regla) + fixes: rate limit login, headers, bounds schema, validación JSONL, sin short-circuit |
| 15 | R1: cache LRU de embeddings | ✅ a018a2c — memoria de trabajo, hit ~0ms, NOUS_CACHE_EMBEDDINGS_MAX |
| 16 | R2: consolidación por evocación | ✅ 05864c3 — refuerzos (migración 0003) frenan el olvido; requiere `alembic upgrade head` |
| 17 | R3: separación de patrones | ✅ fe111d1 — dedup semántico ≥0.95 en ventana 1h + piso de coseno 0.25 |
| 18 | R4: priming / activación difusa | ✅ 822d84a — hits+vecinos activados 300s, boost 0.15 |
| 19 | R5: episodios + poda sináptica | ✅ a60fbce — tool obtener_episodios + `nous prune` (dry-run, --aplicar) |
| 20 | Docs neurociencia | ✅ 4cd3f7a — docs/neuroscience.md con referencias |

## Notas de entorno

- DB `postgres18` es contenedor Docker en **Debian WSL** (distro no-default):
  la VM se apaga sin procesos → mantener viva con `wsl -d Debian -- sleep infinity`.
  `NOUS_DATABASE_URL` usa la IP WSL `172.20.236.171` (localhost forwarding es inestable).
- `chrome.exe` del sistema no arranca (error SxS) → usar `channel: 'msedge'` o
  el Chromium bundled de Playwright.
| 21 | Cerebro 3D adaptado a Nous + membrana viva | ✅ 3e7a883 — refuerzos→consolidated, shader respira, onda cortical, marcapaso talámico |
| 22 | ADR-002 Nivel 1: src/core/decision/ + provider augmenta clasificador | ✅ 75cac04 — decision/ autocontenido (choice/score/noul), factory auto con probe Ollama, fallback léxico |
| 23 | ADR-002 Nivel 2: tool consultar_decision + skills/prompts de decisión (Pro) | ⏳ pendiente — tras Nivel 1; tier Pro, módulo decision/ desacoplado |
| 24 | ADR-001: separar dashboard a repo nous-dashboard + licencia JWT | ⏳ aprobado 2026-10-19, pendiente ejecución |
| 25 | ADR-003: 7 patrones neurocognitivos | ✅ 75cac04 + smart-round — decay/consolidación/prospectiva/interferencia/contexto/scripts/tipos + migración 0010 + CLI consolidar/intenciones/archivados/restaurar |
