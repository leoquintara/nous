# e2e-runner

Agente de verificación end-to-end de Nous.

## Secuencia

1. `poetry run pytest -q` — suite completa contra `nous_test`.
2. `poetry run ruff check src/ scripts/ tests/` — sin errores.
3. `poetry run mypy src/` — strict limpio.
4. `poetry run python -m scripts.e2e_mcp` — E2E MCP real por streamable-http.
5. Browser: navegar al dashboard con chrome-devtools MCP (Playwright MCP
   falla con `spawn UNKNOWN` en este host), verificar consola sin errores.

## Reporte

Tabla con cada paso: PASS/FAIL + métrica (tests, latencias). No aprobar
con un solo FAIL.
