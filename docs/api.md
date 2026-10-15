# API REST de Nous

FastAPI (`src/api/`). Sirve el MCP streamable-HTTP en `/mcp` y la API de
operación del dashboard. Todo endpoint requiere `Authorization: Bearer
ns_<token>` (salvo `/health`, `/`, `/login` y
`/api/invitaciones/canjear`), salvo que `NOUS_API_TOKEN` esté vacío
(modo dev local).

**Credenciales**: `ns_<token>` estático = owner del workspace
`personal`; `ns_u_*` = key de usuario (resuelve su workspace default
y rol). Los GET solo exigen token válido; las escrituras exigen rol
`member`/`owner` en el workspace activo (403 a `viewer`). Los
endpoints aceptan `?workspace=<slug|uuid>` como override — exige
membresía en el destino. Las escrituras de memoria aceptan `scope:
"personal"|"workspace"` (default `personal` — compartir es siempre
explícito).

## Vida y acceso

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | Liveness — público |
| POST | `/login` | Usuario+contraseña → token de servicio (si `NOUS_DASHBOARD_USUARIO/PASSWORD` configurados) |

## Eventos y memoria

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/eventos` | Registra evento (`contenido`, `dimension`, `tipo`, `tags`, `relaciona_ids`, `rama`, `ttl_horas`, `reemplaza_id`, `con_quien`→loop) |
| GET | `/eventos` | Lista eventos; soporta rango temporal (`desde`/`hasta`) y paginación |
| DELETE | `/eventos/{id}` | Olvido irreversible — borra el evento y sus vínculos |
| GET | `/buscar` | Búsqueda híbrida (BM25+vector+RRF+recencia+reranker); params `q`, `dimension`, `rama` (default `main`, `"*"` = todas) |

## Universo 4D

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/loops` | Compromisos abiertos con edad |
| POST | `/loops/{compromiso_id}/cerrar` | Cierra el loop (auditable) |
| GET | `/objetivos` | Lista objetivos |
| POST | `/objetivos` | Crea objetivo (`titulo`, `fecha_objetivo`, `rama`) |
| PATCH | `/objetivos/{id}` | Edita objetivo |
| POST | `/objetivos/{id}/cerrar` | Cierra con resolución |
| GET | `/contexto-inicial` | Resumen de arranque de sesión (eventos recientes, loops, objetivos) |
| GET | `/relaciones` | Vínculos del grafo |

## Fuentes y ramas

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/fuentes` | Fuentes de memoria registradas |
| POST | `/fuentes` | Registra una fuente |
| PATCH | `/fuentes/{nombre}` | Edita fuente |
| DELETE | `/fuentes/{nombre}` | Elimina fuente |
| POST | `/fuentes/{nombre}/ramificar` | Crea rama alternativa de universo |
| GET | `/tags` | Todos los tags con conteo |

## Proyección e IA

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/proyeccion` | Proyección de trayectoria desde un evento |
| GET | `/proyeccion/actual` | Proyección desde el estado actual |
| GET | `/ia/proveedores` | Proveedores LLM (keys enmascaradas) |
| PUT | `/ia/proveedores/{pid}` | Crea/actualiza proveedor (key cifrada pgcrypto) |
| DELETE | `/ia/proveedores/{pid}` | Elimina proveedor |
| POST | `/ia/proveedores/{pid}/probar` | Prueba conexión sin persistir |
| POST | `/ia/predecir` | Futuros re-ponderados por LLM (primario → fallback) |

## Operación

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/actividad` | Actividad reciente para el cerebro 3D |
| GET | `/preferencias/{clave}` · PUT | Preferencias por clave (namespacadas) |
| GET | `/benchmarks` | Corridas de benchmark persistidas |
| GET | `/llamadas` · `/llamadas/stats` | Trazas de llamadas MCP/REST + agregados |
| POST | `/ping` | Latido de agente → feed de actividad |
| POST | `/importar` | Migración: `{fuente, ruta\|url, token, vista_previa}` — gbrain/memlord vía API MCP, archivos por `ruta` |
| GET | `/importar/historial` | Historial de migraciones |

## Identidad multi-tenant

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/identidad` | Whoami: usuario, workspace activo, workspaces con rol, estado de licencia |
| POST | `/workspaces` | Crea workspace (el llamante queda owner) |
| GET | `/workspaces/{ref}/miembros` | Roster del workspace (cualquier miembro) |
| PATCH | `/workspaces/{ref}/miembros/{uid}` | Cambia rol member↔viewer — solo owner |
| DELETE | `/workspaces/{ref}/miembros/{uid}` | Expulsa miembro — solo owner; último owner protegido |
| POST | `/workspaces/{ref}/invitaciones` | Emite `ns_inv_*` (rol, días) — solo owner; se ve una vez |
| POST | `/invitaciones/canjear` | **Público** (rate-limited): token + handle + nombre → key `ns_u_*` una vez |
| GET | `/llaves` | Keys del llamante (sin secretos) |
| POST | `/llaves` | Emite key propia — en claro una sola vez |
| DELETE | `/llaves/{ref}` | Revoca por UUID o prefijo — solo las propias |

## Errores

`401` token ausente/inválido · `402` límite de la edición libre sin
licencia · `403` origen no permitido, rol insuficiente o sin membresía
en el workspace destino · `404` recurso inexistente · `422` validación
de dominio · `502` instancia remota MCP inalcanzable (lista las tools
anunciadas en el mensaje para depuración).
