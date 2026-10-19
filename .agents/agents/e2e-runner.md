# e2e-runner

Agente de verificación end-to-end de Nous.

## Entorno real de este host

El host NO tiene poetry/ruff/mypy — todo corre en contenedores
docker via WSL. Forma canónica:

```bash
docker run --rm --entrypoint bash -v "$PWD:/repo" -w /repo \
  nous-by-quintara-app:latest -c "<comando>"
```

- Suite: `pytest -q tests/unit tests/integration` (pytest ya va en la
  imagen; ruff/mypy se instalan con `pip install` en el efímero).
- `tests/integration` exige `nous_test` migrada: `alembic upgrade head`
  con `NOUS_DATABASE_URL` apuntando a `nous_test` primero.
- `tests/security/test_owasp.py` tiene 2 tests que necesitan el binario
  `git` — fallan en contenedor efímero por causa ambiental, no de código.
- Dashboard (yarn-only): `yarn vitest run && yarn tsc --noEmit && yarn eslint --max-warnings 0`.

## Secuencia

1. `pytest -q` — suite completa contra `nous_test` en contenedor.
2. `ruff check src/ scripts/ tests/` — sin errores nuevos.
3. `mypy src/` — strict limpio.
4. `python -m scripts.e2e_mcp` — E2E MCP real por streamable-http.
5. Browser: navegar al dashboard con chrome-devtools MCP y verificar
   consola sin errores (incluido el #418 de hidratación — solo aparece
   en el build/export, nunca en `next dev`).

## Reporte

Tabla con cada paso: PASS/FAIL + métrica (tests, latencias). Distingue
fallo ambiental (git ausente, red) de regresión real. No aprobar
con un solo FAIL no ambiental.
