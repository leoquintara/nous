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

## Notas de entorno

- DB `postgres18` es contenedor Docker en **Debian WSL** (distro no-default):
  la VM se apaga sin procesos → mantener viva con `wsl -d Debian -- sleep infinity`.
  `NOUS_DATABASE_URL` usa la IP WSL `172.20.236.171` (localhost forwarding es inestable).
- `chrome.exe` del sistema no arranca (error SxS) → usar `channel: 'msedge'` o
  el Chromium bundled de Playwright.
