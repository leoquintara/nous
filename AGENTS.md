# AGENTS.md — Nous

> Memoria persistente 4D para agentes de IA. Un producto de Quintara AI.

## 🎯 Qué es este proyecto

Nous es un servidor de memoria persistente para agentes de IA, implementado como servidor MCP (Model Context Protocol). Permite a cualquier agente (Claude, Cursor, Windsurf, Cline, etc.) almacenar, consultar y analizar memorias en tres dimensiones:

- **Cognitiva**: pensamientos, ideas, reflexiones, decisiones
- **Somática**: estados corporales, emociones, niveles de estrés
- **Conductual**: acciones, hábitos, commits, interacciones

Además, Nous mantiene una **línea de universo 4D**: histórico temporal completo, detección de trayectorias (patrones) y proyección de futuros probables.

**Filosofía central: Local-first.** Los datos del usuario NUNCA salen de su máquina sin consentimiento explícito.

## 🏗️ Arquitectura del Proyecto

```
nous/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point del servidor MCP
│   ├── config.py               # Configuración (pydantic-settings, prefijo NOUS_)
│   │
│   ├── mcp/                    # Capa de protocolo MCP
│   │   ├── server.py           # Servidor MCP (transporte stdio + streamable-http)
│   │   ├── tools.py            # Tools MCP (recordar, consultar, proyectar, línea)
│   │   ├── resources.py        # Resources MCP (resúmenes, patrones, proyección)
│   │   └── prompts.py          # Prompts MCP (reflexión, análisis, decisión)
│   │
│   ├── core/                   # Lógica de negocio
│   │   ├── clasificador.py     # Clasifica eventos en 3 dimensiones (semántico+léxico)
│   │   ├── embeddings.py       # Embeddings locales (MiniLM-L12, 384 dims)
│   │   ├── reranker.py         # Reranker local (cross-encoder MiniLM-L12)
│   │   ├── busqueda.py         # Búsqueda híbrida (BM25 + vectorial + RRF + recencia)
│   │   ├── chunking.py         # Troceo markdown puro → evento_chunks (retrieval fino)
│   │   ├── temporal.py         # Línea de universo y trayectorias
│   │   ├── prediccion.py       # Fachada de proyección futura (re-export de temporal)
│   │   ├── modelos.py          # Dataclasses y enums del dominio
│   │   ├── exceptions.py       # Excepciones del dominio
│   │   ├── memoria/            # Patrones neurocognitivos (ADR-003, puros/testeables)
│   │   │   ├── decay.py        #   Decay adaptativo + spacing + primacía + olvido dirigido + SRS
│   │   │   ├── consolidacion.py#   Sueño: promueve/fortalece/archiva + reporte + salvados
│   │   │   ├── prospectiva.py  #   Intenciones time/event + triggers + snooze
│   │   │   ├── interferencia.py#   Detección de contradicciones (rel. 'contradice')
│   │   │   ├── contexto.py     #   Boost contexto/estado-dependiente en búsqueda
│   │   │   ├── scripts.py      #   Minería de secuencias recurrentes
│   │   │   ├── tipos.py        #   episódica/semántica/procedimental + valencia
│   │   │   ├── ritmos.py       #   Primacía de época + ventana circadiana de sueño
│   │   │   ├── retrieval.py    #   Hebbiana, extinción, metamemoria, ventana lábil
│   │   │   ├── novedad.py      #   Sorpresa → amplitud + frontera de época
│   │   │   ├── trabajo.py      #   Buffer de trabajo (Baddeley): TTL + ensayo
│   │   │   ├── semanticizacion.py# Sueño: episodios afines → gist semántico
│   │   │   ├── diversidad.py   #   Fan effect en resultados + tip-of-the-tongue
│   │   │   ├── rem.py          #   Sueño REM: pares soñables (rel. 'rem')
│   │   │   ├── bootstrap.py    #   Neuro-5: pack de arranque presupuestado + snapshot
│   │   │   ├── verificacion.py #   Neuro-5: veredicto de censores pre-acción
│   │   │   ├── calibracion.py  #   Neuro-5: Brier + curva de confiabilidad
│   │   │   ├── handoff.py      #   Neuro-5: documento de continuidad de sesión
│   │   │   └── epistemica.py   #   Neuro-6: lagunas — lo que la consulta NO cubrió
│   │   └── decision/           # Proveedores System One (ADR-002, autocontenido)
│   │       ├── provider.py     #   Protocolo /v1/systemone (choice/score/noul)
│   │       ├── adapters.py     #   Laya/Ollama/HTTP/Jev con guardia de privacidad + scrub
│   │       ├── scrub.py        #   Neuro-5: redacción de secretos salientes
│   │       ├── factory.py      #   auto → probe de socket, nunca stall 30s
│   │       └── engine.py       #   Orquestación (Nivel 2 = tool Pro)
│   │
│   ├── storage/                # Capa de persistencia
│   │   ├── database.py         # Conexión async a PostgreSQL
│   │   ├── models.py           # Modelos SQLAlchemy 2.0
│   │   ├── repositories.py     # Repositorios (patrón Repository)
│   │   ├── chunks.py           # Indexación de fragmentos (troceo + embeddings)
│   │   └── migrations/         # Migraciones Alembic
│   │
│   ├── api/                    # API REST (para interfaz web)
│   │   ├── routes.py           # Endpoints FastAPI
│   │   ├── schemas.py          # Schemas Pydantic
│   │   └── auth.py             # Bearer token (verificar_token + middleware ASGI)
│   │
│   └── web/                    # Interfaz web (FastAPI + templates)
│       ├── static/             # JS, CSS, Three.js para brain 3D
│       └── templates/          # Jinja2 templates
│
├── tests/                      # Tests (espejo de src/)
│   ├── unit/                   # Sin I/O externa (mockear DB y modelos)
│   ├── integration/            # PostgreSQL real (base nous_test aislada)
│   └── conftest.py             # Fixtures compartidos
│
├── docs/                       # Documentación técnica
│   ├── architecture.md
│   ├── api.md
│   └── deployment.md
│
├── scripts/                    # Scripts de utilidad
│   ├── seed_db.py              # Poblar DB con datos de ejemplo
│   └── benchmark_search.py     # Benchmark de búsqueda híbrida (persiste en DB)
│
├── context/                    # Material de contexto local (NO va a git)
├── docker-compose.yml          # PostgreSQL 18 + pgvector + app
├── Dockerfile                  # Imagen de la app
├── pyproject.toml              # Dependencias (Poetry)
├── alembic.ini                 # Configuración de Alembic
├── .env.example                # Variables de entorno de ejemplo
├── LICENSE                     # AGPL 3.0
└── AGENTS.md                   # Este archivo
```

## 📋 Convenciones de Código Python

### Tipado Estricto

- TODO el código debe tener type hints. Sin excepciones.
- Usar `mypy --strict` en CI. Cero errores permitidos.
- Preferir `X | None` sobre `Optional[X]` (Python 3.10+).
- Usar `TypeAlias`, `TypeVar`, `Protocol` cuando aplique.

```python
# ✅ CORRECTO
from dataclasses import dataclass
from enum import StrEnum

class Dimension(StrEnum):
    COGNITIVA = "cognitiva"
    SOMATICA = "somatica"
    CONDUCTUAL = "conductual"

@dataclass(frozen=True, slots=True)
class Evento:
    id: str
    timestamp_unix: int
    dimension: Dimension
    contenido: str
    amplitud: float = 0.5

async def buscar_eventos(
    query: str,
    dimension: Dimension | None = None,
    top_k: int = 10,
) -> list[Evento]:
    ...

# ❌ INCORRECTO
def buscar_eventos(query, dimension=None, topk=10):  # sin type hints
    ...
```

### Docstrings

- Formato Google en todas las funciones públicas.
- Incluir `Args`, `Returns`, `Raises` cuando aplique.
- Primera línea: resumen en imperative mood ("Busca eventos...", no "Esta función busca...").

```python
async def fusion_rrf(
    *listas_resultados: list[ResultadoBusqueda],
    k: int = 60,
    top_n: int = 10,
) -> list[ResultadoBusqueda]:
    """Fusiona múltiples listas de resultados usando Reciprocal Rank Fusion.

    RRF(d) = Σ 1 / (k + rank_i(d)) donde rank_i es el ranking
    del documento d en la lista i.

    Args:
        *listas_resultados: Listas de resultados a fusionar.
        k: Constante de suavizado (default 60, según paper original RRF).
        top_n: Número máximo de resultados a retornar.

    Returns:
        Lista fusionada ordenada por score RRF descendente.

    Raises:
        ValueError: Si alguna lista está vacía.
    """
    ...
```

### Estructura de Funciones y Clases

- Funciones: máximo 30 líneas. Si excede, extraer.
- Clases: máximo 200 líneas. Si excede, dividir responsabilidades.
- Argumentos: máximo 4 posicionales por función. Si necesitas más, usa un dataclass.
- Una responsabilidad por función. Si el nombre tiene "y" o "and", probablemente hace dos cosas.

### Nomenclatura

- Variables y funciones: `snake_case`, descriptivas. `resultados_busqueda`, no `res_busq`.
- Clases: `PascalCase`. `BusquedaHibrida4D`, no `busquedahibrida4d`.
- Constantes: `UPPER_SNAKE_CASE`. `K_RRF_DEFAULT = 60`.
- Archivos: `snake_case`. `busqueda_hibrida.py`, no `BusquedaHibrida.py`.
- Nunca: abreviaturas ambiguas (`tmp`, `val`, `res`), números en nombres (`busqueda2`), single letters excepto en loops (`i`, `j`, `k`).

### Imports

- Orden: stdlib → terceros → locales. Separados por línea en blanco.
- Nunca `import *`.
- Preferir imports absolutos: `from src.core.busqueda import BusquedaHibrida4D`.

```python
# ✅ CORRECTO
import asyncio
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from pgvector.asyncpg import register_vector

from src.core.modelos import Dimension, Evento
from src.storage.database import get_session

# ❌ INCORRECTO
from src.core import *
import src.core.busqueda as b
```

### Async/Await

- Toda I/O debe ser async: database, HTTP, embeddings, MCP.
- Nunca usar `time.sleep()` en código async. Usar `asyncio.sleep()`.
- Nunca bloquear el event loop. Si una librería es sync, usar `asyncio.to_thread()`.
- **SQLAlchemy `AsyncSession` NO soporta queries concurrentes** en la misma sesión: nada de `asyncio.gather` sobre una sola sesión; emitir las consultas secuencialmente o usar sesiones separadas.

### Manejo de Errores

- Nunca usar `except Exception` genérico sin justificar.
- Crear excepciones custom del dominio en `src/core/exceptions.py`.
- Loggear errores con contexto, no solo el mensaje.

```python
# ✅ CORRECTO
from src.core.exceptions import EventoNoEncontradoError

try:
    evento = await repo.obtener_evento(evento_id)
except EventoNoEncontradoError:
    logger.warning("Evento %s no encontrado en la línea de universo", evento_id)
    return None

# ❌ INCORRECTO
try:
    evento = await repo.obtener_evento(evento_id)
except Exception as e:
    print(f"Error: {e}")  # nunca print en producción
```

## 🔐 Privacidad y Seguridad (No Negociable)

- NUNCA enviar datos del usuario a servicios externos sin consentimiento explícito.
- Embeddings locales por defecto (sentence-transformers). Si el usuario configura un proveedor externo, mostrar warning explícito en la UI.
- NUNCA loggear contenido de memorias. Solo IDs y metadata.
- Cifrar datos sensibles con `pgcrypto` en PostgreSQL.
- NUNCA hardcodear API keys, passwords, o secrets. Usar variables de entorno vía `.env`.
- Sanitizar toda entrada del usuario antes de guardarla en PostgreSQL.
- El usuario puede exportar/importar/borrar toda su memoria en cualquier momento.

### Autenticación HTTP y multi-tenant

- Las superficies HTTP (transporte `streamable-http` del MCP y la API REST) se protegen con **Bearer token**. El estático `NOUS_API_TOKEN` (`ns_` + material aleatorio) equivale al **owner del workspace bootstrap `personal`**; las keys de usuario `ns_u_*` resuelven a `(usuario, workspace_default, rol)` — solo se persiste su hash SHA-256.
- Token ausente = modo desarrollo local (warning en logs). Nunca exponer a red sin token.
- `/health` y `/api/invitaciones/canjear` permanecen abiertos; el resto de rutas exige `Authorization: Bearer <token>` — las escrituras además exigen rol `member`/`owner` (403 a `viewer`).
- **Aislamiento por workspace**: toda entidad de dominio lleva `workspace_id`; eventos/objetivos además `scope` (`personal`|`workspace`) y `autor_id`. La visibilidad (workspace ∪ propios ∪ sin-atribuir) se filtra en los repositorios — nunca se bypassa con queries directas.
- **Override de workspace** (`workspace=` en tools MCP y REST): exige membresía en el destino, 403 si no. El rol resuelto en el destino manda.
- **Invitaciones** `ns_inv_*`: hash, un solo uso, con expiración. El canje crea usuario + membresía + primera key.
- **Feature gate**: sin `NOUS_LICENSE_KEY`, la edición libre permite 1 usuario y 3 workspaces (`CupoExcedidoError` → 402).
- El transporte `stdio` no requiere token: `NOUS_API_KEY` en el entorno del proceso decide el usuario; ausente → owner local.

## 🧪 Testing

- Framework: pytest + pytest-asyncio + pytest-cov.
- Cobertura mínima: 80% en `src/core/`, 70% en `src/storage/`, 60% en `src/mcp/`.
- Tests unitarios: sin I/O externa. Mockear database y embeddings.
- Tests de integración: PostgreSQL real contra la base **`nous_test`** (aislada de datos reales/benchmarks; ver `NOUS_TEST_DATABASE_URL`).
- Naming: `test_<comportamiento>`. Ejemplo: `test_busqueda_hibrida_sin_resultados_retorna_lista_vacia`.
- Un test por comportamiento. Si el nombre del test tiene "y" o "and", dividirlo.

```python
# tests/unit/test_busqueda.py
import pytest
from src.core.busqueda import fusion_rrf
from src.core.modelos import ResultadoBusqueda

@pytest.mark.asyncio
async def test_fusion_rrf_con_dos_listas_ordena_por_score():
    """Verifica que RRF fusiona correctamente dos listas de resultados."""
    lista_a = [ResultadoBusqueda(evento_id="1", score_bm25=0.9)]
    lista_b = [ResultadoBusqueda(evento_id="2", score_vectorial=0.8)]

    resultado = fusion_rrf(lista_a, lista_b, k=60, top_n=10)

    assert len(resultado) == 2
    assert resultado[0].score_rrf > resultado[1].score_rrf
```

## 🔀 Git Workflow

- Convención: **Conventional Commits**.
  - `feat:` nueva funcionalidad
  - `fix:` corrección de bug
  - `docs:` documentación
  - `refactor:` refactorización sin cambio de comportamiento
  - `test:` agregar o modificar tests
  - `chore:` tareas de mantenimiento
- Branches: `feat/`, `fix/`, `docs/`.
- Commits: atómicos, un cambio lógico por commit. Mensaje en imperative mood.
- NUNCA hacer push directo a main. Siempre PR con review.
- NUNCA commitear: `.env`, `__pycache__`, `*.pyc`, datos de usuario, embeddings generados, `context/`.

## ⚡ Performance

- Búsqueda híbrida: objetivo **< 200ms p95**.
- NUNCA agregar dependencias pesadas (> 50MB) sin justificar en el PR.
- NUNCA usar `print()` en código de producción. Usar `logging` con niveles.
- NUNCA dejar código comentado ("por si acaso"). Para eso existe Git.
- NUNCA ignorar errores con `pass` en un `except`.
- NUNCA crear funciones con más de 4 argumentos posicionales.
- NUNCA mezclar lógica de negocio en la capa de storage o MCP. La lógica vive en `src/core/`.
- NUNCA asumir que el usuario tiene conexión a internet. Todo debe funcionar offline por defecto.

### Reglas de base de datos

- NUNCA modificar el esquema sin una migración Alembic.
- Índices obligatorios en columnas de consulta frecuente (`timestamp_unix`, `dimension`, `fuente`).
- `EXPLAIN ANALYZE` obligatorio antes de mergear queries nuevas.
- Prohibido el patrón N+1: usar `selectinload`/`joinedload` o queries agregadas.
- Connection pooling con asyncpg (`pool_pre_ping` habilitado).

### Patrones de memoria (ADR-003)

- `src/core/memoria/` es **lógica pura** — sin I/O, sin SQLAlchemy, sin
  `time.time()` implícito (timestamps entran por parámetro). Las capas
  MCP/REST/CLI solo orquestan: leen con repos, llaman al core, persisten
  con repos batch (`aplicar_consolidacion` usa UPDATE/CASE, nunca loops).
- `src/core/decision/` es **autocontenido** — no puede importar
  `src.storage` ni `src.mcp`. Está diseñado para extraerse como paquete
  propio de Quintara (el ADR-002 Nivel 2, Pro, vive ahí).
- **Archivar ≠ borrar**: la consolidación marca `archivado=true`
  (reversible, restaurable); el DELETE real solo vive en `nous prune`.
  Compromisos y preferencias nunca se archivan — son accionables.
- **Censores** (`tipo='censor'`): accionables también — el sueño no
  los toca; una regla no se olvida sola. Se consultan con
  `verificar_accion` ANTES de actuar y viajan primeros en el bootstrap.
- El tier **curado** (`curado=true`) es inmune a decay y al archivo —
  es el equivalente del MEMORY.md curado de OpenClaw. Además tiene
  **presupuesto** (`NOUS_CURADO_MAX_CHARS`, default 8192): curar lo
  que no cabe rechaza la operación — la escasez obliga a consolidar.
- **Staged** (`staged=true`): propuesta pre-aprobación — invisible
  para TODO el recall (léxico, vector, bootstrap, sueño, SRS) hasta
  `aprobar_evento`. Con `NOUS_ESCRITURA_APROBACION=true` toda
  escritura llega así. Distinto de cuarentena (aislamiento por
  fuente no confiable) y de archivo (olvido reversible).
- **Grace floor**: una memoria nunca evocada (`refuerzos=0` y sin
  `ultimo_acceso`) no es elegible a archivo hasta 30 días — cero
  usos es ausencia de evidencia, no prueba de desechable.
- **Sesiones**: boundaries first-class — `iniciar_sesion` congela el
  snapshot del bootstrap; `cerrar_sesion` destila el handoff
  (documento de continuidad, no backup bruto).
- **Scrub saliente**: todo payload a proveedor externo pasa por
  `redactar_secretos` — el scrub es la red de seguridad tras el
  guard de bloqueo.
- **Capa epistémica** (Neuro-6): `parametrica` marca contenido
  afirmado desde los pesos del modelo sin corroboración — su
  confianza topea a 0.70 hasta 3 refuerzos o aprobación humana.
  `desglose_confianza` explica cada señal por separado.
  `mapa_lagunas` declara lo que la consulta NO cubrió.
  `vecinos_coactivos` cierra la vía hebbiana: las aristas coactivas
  ya se leen — los asociados asoman `via="coactiva"` con peso
  efectivo STP (media-vida ~6h) sobre el LTP duradero.
- Toda corrida de consolidación persiste un `reportes_consolidacion`
  con ids y razones — el olvido siempre es auditable, nunca silencioso.
- Los providers de decisión degranan siempre: fallo de red/modelo →
  heurístico local, jamás excepción ni stall en el camino caliente.
- **Buffer de trabajo** (`borradores`): efímero por diseño — expira
  sin ensayo (el olvido es la función) y promueve a evento solo por
  repetición. No es la línea de universo ni se archiva: nace y muere.
- Aristas neurocognitivas: `coactiva` (evocados juntos — hebbiana,
  con peso tope), `reconsolida` (supersede dentro de la ventana
  lábil post-recall) y `gist` (episodio → semántica destilada en el
  sueño). Los efectos post-evocación corren en la cola de fondo,
  nunca en el camino caliente de `consultar_memoria`.

## 🚀 Cómo Ejecutar el Proyecto

```bash
# Clonar y setup
git clone https://github.com/leoquintara/nous.git
cd nous
cp .env.example .env   # Editar con tus valores

# Levantar infraestructura
docker-compose up -d   # PostgreSQL 18 + pgvector

# Con una PostgreSQL existente fuera del stack: define
# NOUS_DATABASE_URL_DOCKER en .env y omite el servicio `db`:
docker compose --profile app up -d --no-deps app

# Instalar dependencias
poetry install

# Aplicar migraciones
poetry run alembic upgrade head

# Ejecutar servidor MCP
poetry run python -m src.main

# Ejecutar tests
poetry run pytest --cov=src --cov-report=term-missing

# Linting y type checking
poetry run ruff check src/
poetry run mypy --strict src/
```

## 📐 Herramientas de Calidad

| Herramienta | Propósito | Configuración |
|-------------|-----------|---------------|
| ruff | Linter + formatter | pyproject.toml, line-length 100 |
| mypy | Type checking | `--strict`, sin `# type: ignore` injustificado |
| pytest | Testing | pytest-asyncio, pytest-cov |
| pre-commit | Hooks de Git | ruff + mypy antes de cada commit |
| Alembic | Migraciones DB | Autogenerate desde modelos SQLAlchemy |

## 📊 Métricas de Éxito del Proyecto

- Tiempo de respuesta de búsqueda híbrida: **< 200ms p95**
- Precisión de clasificación de dimensiones: **> 85%**
- Disponibilidad del servidor MCP: 99.9% en entorno local
- Cobertura de tests: > 80% en core
- Cero errores de `mypy --strict` en CI

## 🤖 Reglas para Agentes de IA que Trabajan en Este Proyecto

Si eres un agente de IA (Claude, Copilot, Cursor, etc.) trabajando en este código:

1. Lee este archivo COMPLETO antes de hacer cualquier cambio.
2. Respeta la arquitectura de capas: **MCP → Core → Storage**. Nunca mezcles.
3. Escribe tests para TODO código nuevo. Sin test, no hay merge.
4. Sigue las convenciones de tipado estricto. Si mypy falla, corrígelo.
5. No agregues dependencias sin justificar. Pregunta antes.
6. Si no estás seguro de una decisión arquitectónica, NO la tomes. Pregunta al humano.
7. NUNCA comprometas la privacidad del usuario. Es el valor central de Nous.
8. Documenta lo complejo. Si una función necesita más de 3 líneas de comentario para explicarse, probablemente es demasiado compleja y debe refactorizarse.

## 📜 Licencia

AGPL 3.0 para uso open source.
Licencia comercial disponible para empresas que requieran términos diferentes.
Contacto: licensing@quintarai.com

Nous es un producto de Quintara AI. © 2025 Quintara AI. Todos los derechos reservados.
