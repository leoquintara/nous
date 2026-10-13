# Análisis de seguridad — OWASP Top 10 (2021) + API Security

Auditoría de las superficies de Nous: REST (`/api/*`), MCP
streamable-http (`/mcp`), stdio, CLI e import/export JSONL.
Un test por categoría en `tests/security/test_owasp.py` +
`tests/integration/test_security_db.py` (**50 tests**).

## Matriz de hallazgos

| # | Categoría | Estado | Detalle |
|---|-----------|--------|---------|
| A01 | Broken Access Control | ✅ | Bearer en todo `/api`+`/mcp`; solo `/health`, `/api/login` y OPTIONS públicos. Esquema != Bearer rechazado. |
| A02 | Cryptographic Failures | ✅ | `hmac.compare_digest` en token y login (anti timing). Token `ns_`+64hex = 256 bits. El 401 nunca refleja el token. |
| A03 | Injection | ✅ corregido | SQL/tsquery parametrizados (verificado con payloads reales vs PostgreSQL). **Fix**: `fuente` ahora limitada a 50 chars — antes reventaba `String(50)` con DataError 500. |
| A04 | Insecure Design | ✅ corregido | Clamps existentes (limite≤500, top_n≤50, horizonte≤90, historial≤365). **Fix**: `contenido` 1-100KB, `frecuencia`>0, `timestamp`≥0, `amplitud`∈[0,1] en el schema. |
| A05 | Security Misconfiguration | ✅ corregido | CORS allowlist (solo :3000). `/api/docs`+`openapi.json` detrás de Bearer. **Fix**: headers de seguridad en toda respuesta (nosniff, DENY, no-referrer, CSP `default-src 'none'`), incluidos los 401. |
| A06 | Vulnerable Components | ⚠️ residual | `.env` fuera de git (test). Python: deps ancladas. Dashboard: 5 vulns **altas** en `eslint-config-next`→`fast-glob` — solo dev/lint, sin fix compatible con Next 16. |
| A07 | AuthN Failures | ✅ corregido | Prefijo `ns_` exigido. **Fix**: `/api/login` con rate limit 5 intentos/min por IP (429). **Fix**: comparación usuario+password sin short-circuit (evita enumerar usuarios por timing). |
| A08 | Software & Data Integrity | ✅ corregido | Upgrade: árbol limpio, `--ff-only`, comandos fijos, sin input de usuario en URLs. **Fix**: import JSONL valida dimensión/amplitud/timestamp/fuente/contenido — líneas malas cuentan como error, no revientan CHECK constraints. |
| A09 | Logging Failures | ✅ | Ningún `logger` toca `contenido` (verificado). Warnings de import no incluyen el texto del usuario. |
| A10 | SSRF | ✅ | Ninguna URL saliente deriva de input de usuario: `PYPI_JSON` fijo HTTPS, comandos de upgrade literales, sin fetch a hosts arbitrarios. |

## Decisiones tomadas

1. **Rate limit en memoria** (`LimitadorIntentos`, ventana deslizante):
   suficiente para el modelo single-proceso local-first. No persiste —
   muere con el proceso, que es el nivel de amenaza real (no compartido).
2. **Headers en el middleware más externo** (`SecurityHeadersMiddleware`
   envuelve a `BearerTokenMiddleware`): así los 401 también los llevan.
   CSP `default-src 'none'` es correcta porque la API solo sirve JSON.
3. **Validación en dos capas**: schema Pydantic (422 limpio en el borde)
   + `ErrorValidacion` en core (por si el caller es MCP, no REST).
4. **JSONL sin embeddings** ya era la decisión de diseño — la integridad
   del archivo la completa `_validar_fila` contra el contrato de tabla.
5. **`/api/docs` detrás de auth** en vez de deshabilitarlo: útil en
   self-hosted, protegido por el mismo Bearer que los datos.

## Residual / pendiente documentado

- **5 vulns altas** en la cadena `eslint-config-next` → `fast-glob`
  (npm audit). Son devDependencies — no se despliegan; sin fix que no
  rompa eslint-config-next 16.
- **Sin rate limit en el resto de endpoints**: `LimitadorIntentos` está
  listo para extenderse si el API se expone más allá de localhost.
- **`api_token` estático** con prefijo `ns_`: el modelo de gbrain/memlord.
  Rotación y scopes eran un item pendiente de la hoja de ruta.
- **Timing del Bearer**: `compare_digest` sobre el header — correcto;
  el IP rate limit solo aplica a `/api/login`, no al Bearer global.
