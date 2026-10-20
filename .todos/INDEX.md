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
| 23 | ADR-002 Nivel 2: tool consultar_decision + skills/prompts de decisión (Pro) | ✅ implementado — tool + REST /decision + prompt evaluar_decision + skill nous-decision; gate CupoExcedidoError→402 sin licencia; sin provider → contexto 4D ensamblado |
| 24 | ADR-001: separar dashboard + licencia JWT | ⏳ repo `nous-dashboard` creado (5 commits: init, vite fix, imagen nginx, shots, feature flags); core limpio (ecb27bb); `nous activate-license` + fallback `~/.nous/license.jwt` + tier/features en /api/identidad (2115233); flags RequierePro en /proyeccion /benchmarks (80d5d75); extra `decision` laya (8cd6d0e); E2E MCP ✅ /mcp. ✅ Los 3 repos en GitHub: leoquintara/nous (main), nous-dashboard y nous-landing (privados, pusheados) |
| 27 | Landing backend: waitlist + licencias (Supabase + Vercel) | ⏳ construido — Supabase `nous` (pfifexjdcwukccrrdeah): waitlist/licenses/license_activations; rutas /api/waitlist + /api/license[/activate] en landing (3f79022); keypair RS256 en ~/.nous/license/; falta: deploy Vercel + env vars + subdominio (DEPLOY.md) |
| 26 | Pendientes detectados post-split | ✅ — activate-license, license.jwt, extra decision, feature flags dashboard, doc consolidación periódica, fix E2E /mcp, mypy strict 69 archivos, 251 unit + 10 integración + 135 vitest verdes |
| 25 | ADR-003: 7 patrones neurocognitivos | ✅ 75cac04 + smart-round — decay/consolidación/prospectiva/interferencia/contexto/scripts/tipos + migración 0010 + CLI consolidar/intenciones/archivados/restaurar |
| 28 | Distribución: CI + release (ghcr/PyPI) | ⏳ ci.yml+release.yml (4960aa1); CI verde tras fixes env+lint+shim st (9919ff5+4b15aa8); tag v0.1.0 → imagen ghcr.io/leoquintara/nous publicada pública (tags 0.1.0/0.1/latest); PyPI bloqueado — falta registrar trusted publisher nous-memory en pypi.org y re-run job |
