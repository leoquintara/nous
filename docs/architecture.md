# Arquitectura de Nous

Sistema local-first: toda la memoria vive en un PostgreSQL del usuario,
los modelos de embeddings y reranker corren en CPU local (ONNX
runtime / torch), y ningún dato sale de la máquina salvo llamadas
explícitas a proveedores IA configurados por el usuario.

## Capas (estrictas, unidireccionales)

```
MCP / REST / CLI  →  Core  →  Storage
```

- **`src/mcp/`** — servidor MCP (stdio + streamable-HTTP, 19 tools):
  `recordar_evento`, `consultar_memoria`, `contexto_inicial`,
  `obtener_linea_de_universo`, `obtener_episodios`,
  `proyectar_trayectoria`, `compromisos_abiertos`, `cerrar_compromiso`,
  `listar/crear/cerrar_objetivo`, `olvidar_evento`, `listar_tags`,
  `listar_relaciones`, `listar_fuentes`, `ramificar_fuente`,
  `leer/guardar_preferencia`. Solo orquesta; sin lógica de dominio.
- **`src/api/`** — FastAPI: REST para el dashboard + MCP HTTP + auth
  Bearer + CORS restringido a los orígenes configurados.
- **`src/cli/`** — argparse stdlib sobre `core`/`storage` (nunca duplica
  lógica): serve, doctor, db, stats, seed, export/import, skills,
  prune, benchmark, upgrade.
- **`src/core/`** — dominio: clasificador de dimensión/tipo, embeddings
  (local ONNX/torch), reranker cross-encoder, búsqueda híbrida
  (BM25 tsvector español + pgvector + RRF + decaimiento por recencia),
  dedup (umbral 0.95 en ventana 1h), temporal (línea/episodios/
  trayectorias/proyecciones), predicción IA con trazas.
- **`src/storage/`** — SQLAlchemy 2.0 async + asyncpg (pool,
  `pool_pre_ping`), pgvector, Alembic, repositorios por entidad.
  Secreto LLM cifrado con pgcrypto.

## Esquema (tablas principales)

`eventos` (contenido, dimensión, tipo, amplitud, frecuencia, tags[],
embedding vector, rama, ttl, reemplaza_id, tsvector) · `relaciones`
(origen, destino, peso, explícita) · `fuentes` · `objetivos` ·
`trayectorias` · `proyecciones` · `configuracion` (proveedores IA +
preferencias) · `llamadas` (trazas) · `benchmarks`.

## Flujos clave

- **Recordar**: contenido → clasifica dimensión+tipo → inserta →
  embedding en background → dedup: similares en la última hora se
  consolidan en vez de duplicarse. `con_quien` abre un compromiso.
- **Consultar**: query → vector + tsvector → RRF → decaimiento 30d →
  reranker. `rama="main"` por defecto; recuerdos expirados por TTL no
  son evocables pero permanecen en la línea temporal.
- **Proyectar**: rachas dimensionales + objetivos activos → futuros
  fantasma con probabilidad; opcionalmente re-ponderados por el
  proveedor LLM primario (con fallback). Solo se ejecuta a demanda —
  **ninguna acción de IA es automática**: cada llamada requiere un
  gesto explícito del usuario (regla de control de costo/privacidad).
- **Supersede**: `reemplaza_id` marca al anterior como corregido —
  historial auditable, nunca borrado.
- **Olvido**: `DELETE /eventos/{id}` borra evento + vínculos
  (irreversible, para el derecho al olvido).

## Dashboard

`dashboard/` — Next.js 16 App Router, TypeScript estricto, arquitectura
hexagonal (domain → application → infrastructure → ui), Three.js:
cerebro 3D con replay de actividad y selección screen-space; universo 4D
con cursor temporal, cauce recorrido luminoso, ramas y fantasmas.
Estado persistente en PostgreSQL vía API (no solo localStorage).

## Modelo de seguridad

Token `ns_*` de 64 hex para REST y MCP HTTP; stdio no usa token
(proceso local del usuario). Secrets de proveedores LLM con pgcrypto.
CORS acotado. Detalle: [security.md](security.md).
