# API REST de Nous

FastAPI (`src/api/`). Sirve el MCP streamable-HTTP en `/mcp` y la API de
operación del dashboard (montada bajo `/api`, salvo `/health` y
`/login`). Todo endpoint requiere `Authorization: Bearer ns_<token>`
(salvo `/health`, `/`, `/login` y `/api/invitations/redeem`), salvo que
`NOUS_API_TOKEN` esté vacío (modo dev local).

**Credenciales**: `ns_<token>` estático = owner del workspace
`personal`; `ns_u_*` = key de usuario (resuelve su workspace default
y rol). Los GET solo exigen token válido; las escrituras exigen rol
`member`/`owner` en el workspace activo (403 a `viewer`). Los
endpoints aceptan `?workspace=<slug|uuid>` como override — exige
membresía en el destino, y `?project=<slug|uuid>` para acotar a un
project del workspace efectivo (exige membresía en el project). Las
escrituras de memoria aceptan `scope: "personal"|"workspace"`
(default `personal` — compartir es siempre explícito). Como
alternativa al header (clientes sin headers, p.ej. conectores
remotos) se acepta `?access_token=<token>` en la URL (RFC 6750) —
la URL lleva credencial, tratarla como privada.

**Contrato**: rutas, campos y enums viajan en inglés canónico
(`dimension: "cognitive"`, `kind: "commitment"`, `state: "archived"`);
el dominio interno y el storage conservan vocabulario español.

## Vida y acceso

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | Liveness — público |
| POST | `/login` | Usuario+contraseña → token de servicio (si `NOUS_DASHBOARD_USUARIO/PASSWORD` configurados) |

## Eventos y memoria

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/events` | Registra evento (`content`, `dimension`, `kind`, `tags`, `related_ids`, `branch`, `ttl`, `supersedes_id`, `with_whom`→loop, `scope`, `project`) |
| GET | `/events` | Lista eventos; soporta rango temporal (`desde`/`hasta`) y paginación |
| DELETE | `/events/{event_id}` | Olvido irreversible — borra el evento y sus vínculos |
| POST | `/events/{event_id}/forget` | Olvido dirigido — marca sin borrar (auditable) |
| POST | `/events/{event_id}/archive` · `/events/{event_id}/curate` | Ciclo de vida manual: archivar / promover a curada |
| GET | `/search` | Búsqueda híbrida (BM25+vector+RRF+recencia+reranker); params `q`, `dimension`, `branch` (default `main`, `"*"` = todas), `project` |
| GET | `/gaps` | Mapa de lagunas — lo que una consulta NO cubrió (Neuro-6) |
| GET | `/entity` | Ficha de entidad: card + grafo + span temporal |
| POST | `/consolidate` | Corrida de sueño bajo demanda (opcional `project`) |
| GET | `/consolidation/reports` | Reportes de consolidación |
| GET | `/archived` | Memorias archivadas |
| POST | `/scripts/mine` · GET `/scripts` | Minería de secuencias recurrentes |
| POST | `/drafts` · GET `/drafts` · POST `/drafts/{draft_id}/rehearse` · DELETE `/drafts/{draft_id}` | Buffer de trabajo (borradores con TTL y ensayo) |
| GET | `/reviews` | Revisiones SRS pendientes |
| GET | `/quarantine` | Memorias en cuarentena (auditoría inmune) |
| GET | `/approvals/pending` · POST `/approvals/{event_id}/approve` · DELETE `/approvals/{event_id}` · POST `/approvals/approve-all` | Cola de write-approval (Neuro-5) |

## Universo 4D

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/commitments` | Compromisos abiertos con edad |
| POST | `/commitments/{commitment_id}/close` | Cierra el loop (auditable) |
| POST | `/intentions` · GET `/intentions` · POST `/intentions/{intention_id}/cancel` · POST `/intentions/{intention_id}/snooze` | Intenciones prospectivas (time/event trigger, snooze) |
| GET | `/goals` | Lista objetivos |
| POST | `/goals` | Crea objetivo (`title`, `target_date`, `branch`) |
| PATCH | `/goals/{goal_id}` | Edita objetivo |
| POST | `/goals/{goal_id}/close` | Cierra con resolución |
| GET | `/bootstrap` | Pack de arranque de sesión (eventos recientes, loops, objetivos) — disparador de intenciones temporales |
| GET | `/relations` | Vínculos del grafo |
| POST | `/sessions` · POST `/sessions/close` · GET `/sessions` · GET `/sessions/handoff` | Sesiones y documento de continuidad |

## Fuentes y ramas

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/sources` | Fuentes de memoria registradas (conteo real) |
| POST | `/sources` | Registra una fuente |
| PATCH | `/sources/{name}` | Edita fuente |
| DELETE | `/sources/{name}` | Elimina fuente |
| POST | `/sources/{name}/branch` | Crea rama alternativa de universo |
| POST | `/sources/{name}/trust` | Marca fuente confiable/no confiable |
| GET | `/tags` | Todos los tags con conteo |

## Proyección e IA

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/projection` | Proyección de trayectoria desde un evento |
| GET | `/projection/current` | Proyección desde el estado actual |
| POST | `/decision` · POST `/ai/predict` | Decisión System One / futuros re-ponderados por LLM |
| GET | `/ai/providers` | Proveedores LLM (keys enmascaradas) |
| PUT | `/ai/providers/{pid}` | Crea/actualiza proveedor (key cifrada pgcrypto) |
| DELETE | `/ai/providers/{pid}` | Elimina proveedor |
| POST | `/ai/providers/{pid}/test` | Prueba conexión sin persistir |
| POST | `/predictions` · GET `/predictions` · POST `/predictions/{prediction_id}/resolve` | Predicciones calibrables |
| GET | `/calibration` | Brier score + curva de confiabilidad |
| POST | `/verify` · GET `/censors` · POST `/censors` | Veredicto de censores pre-acción |
| POST | `/license/activate` · DELETE `/license` | Licencia Pro/Enterprise |

## Operación

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/activity` | Actividad reciente para el cerebro 3D (`src` = cliente MCP que originó la traza) |
| GET | `/preferences/{key}` · PUT `/preferences/{key}` | Preferencias por clave (namespacadas) |
| GET | `/benchmarks` | Corridas de benchmark persistidas |
| GET | `/calls` · `/calls/stats` | Trazas de llamadas MCP/REST + agregados |
| POST | `/ping` | Latido de agente → feed de actividad |
| POST | `/import` | Migración: `{format, path\|url, token, source, branch, preview}` — gbrain/memlord vía API MCP, archivos por `path` |
| GET | `/import/history` | Historial de migraciones |

## Identidad multi-tenant

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/identity` | Whoami: usuario, workspace activo, workspaces con rol, estado de licencia |
| POST | `/workspaces` | Crea workspace (el llamante queda owner) |
| GET | `/workspaces/{ref}/members` | Roster del workspace (cualquier miembro) |
| PATCH | `/workspaces/{ref}/members/{uid}` | Cambia rol member↔viewer — solo owner |
| DELETE | `/workspaces/{ref}/members/{uid}` | Expulsa miembro — solo owner; último owner protegido |
| POST | `/workspaces/{ref}/invitations` | Emite `ns_inv_*` (rol, días) — solo owner; se ve una vez |
| POST | `/invitations/redeem` | **Público** (rate-limited): token + handle + nombre → key `ns_u_*` una vez |
| GET | `/keys` | Keys del llamante (sin secretos) |
| POST | `/keys` | Emite key propia — en claro una sola vez |
| DELETE | `/keys/{ref}` | Revoca por UUID o prefijo — solo las propias |

## Projects (subdivisión del workspace)

Un project agrupa memoria dentro de un workspace con membresía
propia (`owner`/`member`/`viewer`). `kind: "regular"|"vault"` — el
vault es minable por el sueño pero inmutable. `state` viaja en
inglés (`active`/`archived`); archivado = read-only. Borrar un
project no destruye memoria: los eventos caen a `project_id NULL`
(nivel workspace).

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/workspaces/{ref}/projects` | Roster visible del llamante (owner ve todos) |
| POST | `/workspaces/{ref}/projects` | Crea project (rol member+ del workspace; cupo 5 sin licencia) |
| GET | `/projects/{ref}` | Detalle por slug o UUID |
| PATCH | `/projects/{ref}` | Renombra, descripción o `state` (archivar/reactivar) |
| DELETE | `/projects/{ref}` | Elimina — memoria cae a nivel workspace (SET NULL) |
| GET | `/projects/{ref}/members` | Miembros del project |
| POST | `/projects/{ref}/members` | Asigna miembro del workspace (project owner o workspace owner) |
| DELETE | `/projects/{ref}/members/{handle}` | Quita miembro |

Los endpoints de memoria aceptan `?project=<slug|uuid>` como
restricción dentro del workspace efectivo (`/events`, búsqueda,
línea de universo, lagunas). Un `recall` sin `project` cubre los
projects accesibles más el nivel workspace.

## Errores

`401` token ausente/inválido · `402` límite de la edición libre sin
licencia · `403` origen no permitido, rol insuficiente o sin membresía
en el workspace destino · `404` recurso inexistente · `422` validación
de dominio · `502` instancia remota MCP inalcanzable (lista las tools
anunciadas en el mensaje para depuración).
