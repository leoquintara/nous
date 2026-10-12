# AGENTS.md — Nous
> Memoria persistente 4D para agentes de IA. Un producto de Quintara AI.

🎯 Qué es este proyecto

Nous es un servidor de memoria persistente para agentes de IA, implementado como servidor MCP (Model Context Protocol). Permite a cualquier agente (Claude, Cursor, Windsurf, Cline, etc.) almacenar, consultar y analizar memorias en tres dimensiones:

Cognitiva: pensamientos, ideas, reflexiones, decisiones
Somática: estados corporales, emociones, niveles de estrés
Conductual: acciones, hábitos, commits, interacciones

Además, Nous mantiene una línea de universo 4D: histórico temporal completo, detección de trayectorias (patrones), y proyección de futuros probables.

Filosofía central: Local-first. Los datos del usuario NUNCA salen de su máquina sin consentimiento explícito.

🏗️ Arquitectura del Proyecto

nous/
├── src/
│   ├── init.py
│   ├── main.py                 # Entry point del servidor MCP
│   ├── config.py               # Configuración (pydantic-settings)
│   │
│   ├── mcp/                    # Capa de protocolo MCP
│   │   ├── init.py
│   │   ├── server.py           # Servidor MCP (transporte stdio + HTTP/SSE)
│   │   ├── tools.py            # Tools MCP (recordar, consultar, proyectar)
│   │   ├── resources.py        # Resources MCP (resúmenes, patrones)
│   │   └── prompts.py          # Prompts MCP (reflexión, análisis)
│   │
│   ├── core/                   # Lógica de negocio
│   │   ├── init.py
│   │   ├── clasificador.py     # Clasifica eventos en 3 dimensiones
│   │   ├── embeddings.py       # Generación y gestión de embeddings
│   │   ├── busqueda.py         # Búsqueda híbrida (BM25 + vectorial + RRF)
│   │   ├── temporal.py         # Línea de universo y trayectorias
│   │   ├── prediccion.py       # Extrapolación y proyección futura
│   │   └── modelos.py          # Dataclasses y enums del dominio
│   │
│   ├── storage/                # Capa de persistencia
│   │   ├── init.py
│   │   ├── database.py         # Conexión async a PostgreSQL
│   │   ├── models.py           # Modelos SQLAlchemy 2.0
│   │   ├── repositories.py     # Repositorios (patrón Repository)
│   │   └── migrations/         # Migraciones Alembic
│   │       ├── env.py
│   │       └── versions/
│   │
│   ├── api/                    # API REST (para interfaz web)
│   │   ├── init.py
│   │   ├── routes.py           # Endpoints FastAPI
│   │   └── schemas.py          # Schemas Pydantic
│   │
│   └── web/                    # Interfaz web (FastAPI + templates)
│       ├── static/             # JS, CSS, Three.js para brain 3D
│       └── templates/          # Jinja2 templates
│
├── tests/                      # Tests (espejo de src/)
│   ├── unit/
│   │   ├── test_clasificador.py
│   │   ├── test_busqueda.py
│   │   └── test_temporal.py
│   ├── integration/
│   │   ├── testmcptools.py
│   │   └── test_repositories.py
│   └── conftest.py             # Fixtures compartidos
│
├── docs/                       # Documentación técnica
│   ├── architecture.md
│   ├── api.md
│   └── deployment.md
│
├── scripts/                    # Scripts de utilidad
│   ├── seed_db.py              # Poblar DB con datos de ejemplo
│   └── benchmark_search.py     # Benchmark de búsqueda híbrida
│
├── docker-compose.yml          # PostgreSQL 18 + pgvector + app
├── Dockerfile                  # Imagen de la app
├── pyproject.toml              # Dependencias (Poetry)
├── alembic.ini                 # Configuración de Alembic
├── .env.example                # Variables de entorno de ejemplo
├── LICENSE                     # AGPL 3.0
└── AGENTS.md                   # Este archivo

📋 Convenciones de Código Python

Tipado Estricto
TODO el código debe tener type hints. Sin excepciones.
Usar mypy --strict en CI. Cero errores permitidos.
Preferir X | None sobre Optional[X] (Python 3.10+).
Usar TypeAlias, TypeVar, Protocol cuando aplique.
python

✅ CORRECTO
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

❌ INCORRECTO
def buscareventos(query, dimension=None, topk=10):  # sin type hints
    ...

Docstrings
Formato Google en todas las funciones públicas.
Incluir Args, Returns, Raises cuando aplique.
Primera línea: resumen en imperative mood ("Busca eventos...", no "Esta función busca...").
python
async def fusion_rrf(
    *listas_resultados: list[ResultadoBusqueda],
    k: int = 60,
    top_n: int = 10,
) -> list[ResultadoBusqueda]:
    """Fusiona múltiples listas de resultados usando Reciprocal Rank Fusion.

    RRF(d) = Σ 1 / (k + ranki(d)) donde ranki es el ranking
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

Estructura de Funciones y Clases
Funciones: máximo 30 líneas. Si excede, extraer.
Clases: máximo 200 líneas. Si excede, dividir responsabilidades.
Argumentos: máximo 4 por función. Si necesitas más, usa un dataclass.
Una responsabilidad por función. Si el nombre tiene "y" o "and", probablemente hace dos cosas.

Nomenclatura
Variables y funciones: snakecase, descriptivas. resultadosbusqueda, no res_busq.
Clases: PascalCase. BusquedaHibrida4D, no busquedahibrida4d.
Constantes: UPPERSNAKECASE. KRRFDEFAULT = 60.
Archivos: snakecase. busquedahibrida.py, no BusquedaHibrida.py.
Nunca: abreviaturas ambiguas (tmp, val, res), números en nombres (busqueda2), single letters excepto en loops (i, j, k).

Imports
Orden: stdlib → terceros → locales. Separados por línea en blanco.
Nunca import *.
Preferir imports absolutos: from src.core.busqueda import BusquedaHibrida4D.
python
✅ CORRECTO
import asyncio
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from pgvector.asyncpg import register_vector

from src.core.modelos import Dimension, Evento
from src.storage.database import get_session

❌ INCORRECTO
from src.core import *
import src.core.busqueda as b

Async/Await
Toda I/O debe ser async: database, HTTP, embeddings, MCP.
Nunca usar time.sleep() en código async. Usar asyncio.sleep().
Nunca bloquear el event loop. Si una librería es sync, usar asyncio.to_thread().

Manejo de Errores
Nunca usar except Exception genérico sin justificar.
Crear excepciones custom del dominio en src/core/exceptions.py.
Loggear errores con contexto, no solo el mensaje.
python
✅ CORRECTO
from src.core.exceptions import EventoNoEncontradoError

try:
    evento = await repo.obtenerevento(eventoid)
except EventoNoEncontradoError:
    logger.warning("Evento %s no encontrado en la línea de universo", evento_id)
    return None

❌ INCORRECTO
try:
    evento = await repo.obtenerevento(eventoid)
except Exception as e:
    print(f"Error: {e}")  # nunca print en producción

🔐 Privacidad y Seguridad (No Negociable)

NUNCA enviar datos del usuario a servicios externos sin consentimiento explícito.
Embeddings locales por defecto (sentence-transformers). Si el usuario configura un proveedor externo, mostrar warning explícito en la UI.
NUNCA loggear contenido de memorias. Solo IDs y metadata.
Cifrar datos sensibles con pgcrypto en PostgreSQL.
NUNCA hardcodear API keys, passwords, o secrets. Usar variables de entorno vía .env.
Sanitizar toda entrada del usuario antes de guardarla en PostgreSQL.
El usuario puede exportar/importar/borrar toda su memoria en cualquier momento.

🧪 Testing

Framework: pytest + pytest-asyncio + pytest-cov.
Cobertura mínima: 80% en src/core/, 70% en src/storage/, 60% en src/mcp/.
Tests unitarios: Sin I/O externa. Mockear database y embeddings.
Tests de integración: Usar PostgreSQL real (vía testcontainers o docker-compose de test).
Naming: test. Ejemplo: testbusquedahibridasinresultadosretornalistavacia.
Un test por comportamiento. Si el test name tiene "y" o "and", dividirlo.
python
tests/unit/test_busqueda.py

import pytest
from src.core.busqueda import fusion_rrf
from src.core.modelos import ResultadoBusqueda

@pytest.mark.asyncio
async def testfusionrrfcondoslistasordenaporscore():
    """Verifica que RRF fusiona correctamente dos listas de resultados."""
    listaa = [ResultadoBusqueda(eventoid="1", score_bm25=0.9, ...)]
    listab = [ResultadoBusqueda(eventoid="2", score_vectorial=0.8, ...)]
    
    resultado = fusionrrf(listaa, listab, k=60, topn=10)
    
    assert len(resultado) == 2
    assert resultado[0].scorerrf > resultado[1].scorerrf

🔀 Git Workflow

Convención: Conventional Commits.
  - feat: nueva funcionalidad
  - fix: corrección de bug
  - docs: documentación
  - refactor: refactorización sin cambio de comportamiento
  - test: agregar o modificar tests
  - chore: tareas de mantenimiento
Branches: feat/, fix/, docs/.
Commits: atómicos, un cambio lógico por commit. Mensaje en imperative mood.
NUNCA hacer push directo a main. Siempre PR con review.
NUNCA commitear: .env, pycache, *.pyc, datos de usuario, embeddings generados.

⚡ Performance

Búsqueda híbrida: objetivo < 200ms p95.
Índices obligatorios: en timestamp_unix, dimension, fuente de la tabla eventos.
Usar EXPLAIN ANALYZE antes de mergear cualquier query nueva.
N+1 queries: prohibido. Usar joinedload o selectinload en SQLAlchemy.
Connection pooling: usar asyncpg con pool de conexiones.
Cache: considerar Redis para resultados de búsqueda frecuentes (opcional, feature premium).
NUNCA modificar el esquema de PostgreSQL sin crear una migración Alembic.
NUNCA agregar dependencias pesadas (> 50MB) sin justificar en el PR.
NUNCA usar print() en código de producción. Usar logging con niveles.
NUNCA dejar código comentado ("por si acaso"). Para eso existe Git.
NUNCA ignorar errores con pass en un except.
NUNCA crear funciones con más de 4 argumentos posicionales.
NUNCA mezclar lógica de negocio en la capa de storage o MCP. La lógica vive en src/core/.
NUNCA asumir que el usuario tiene conexión a internet. Todo debe funcionar offline por defecto.

🚀 Cómo Ejecutar el Proyecto
bash
Clonar y setup
git clone https://github.com/quintara-ai/nous.git
cd nous
cp .env.example .env  # Editar con tus valores

Levantar infraestructura
docker-compose up -d  # PostgreSQL 18 + pgvector

Instalar dependencias
poetry install

Aplicar migraciones
poetry run alembic upgrade head

Ejecutar servidor MCP
poetry run python -m src.main

Ejecutar tests
poetry run pytest --cov=src --cov-report=term-missing

Linting y type checking
poetry run ruff check src/
poetry run mypy --strict src/

📐 Herramientas de Calidad

| Herramienta | Propósito | Configuración |
|-------------|-----------|---------------|
| ruff | Linter + formatter | pyproject.toml, line-length 100 |
| mypy | Type checking | --strict, sin # type: ignore |
| pytest | Testing | pytest-asyncio, pytest-cov |
| pre-commit | Hooks de Git | ruff + mypy antes de cada commit |
| Alembic | Migraciones DB | Autogenerate desde modelos SQLAlchemy |

📊 Métricas de Éxito del Proyecto

Tiempo de respuesta de búsqueda híbrida: < 200ms p95
Precisión de clasificación de dimensiones: > 85%
Disponibilidad del servidor MCP: 99.9% en entorno local
Cobertura de tests: > 80% en core
Cero errores de mypy --strict en CI

🤖 Reglas para Agentes de IA que Trabajan en Este Proyecto

Si eres un agente de IA (Claude, Copilot, Cursor, etc.) trabajando en este código:

Lee este archivo COMPLETO antes de hacer cualquier cambio.
Respeta la arquitectura de capas: MCP → Core → Storage. Nunca mezcles.
Escribe tests para TODO código nuevo. Sin test, no hay merge.
Sigue las convenciones de tipado estricto. Si mypy falla, corrígelo.
No agregues dependencias sin justificar. Pregunta antes.
Si no estás seguro de una decisión arquitectónica, NO la tomes. Pregunta al humano.
NUNCA comprometas la privacidad del usuario. Es el valor central de Nous.
Documenta lo complejo. Si una función necesita más de 3 líneas de comentario para explicarse, probablemente es demasiado compleja y debe refactorizarse.

📜 Licencia

AGPL 3.0 para uso open source.  
Licencia comercial disponible para empresas que requieran términos diferentes.  
Contacto: licensing@quintarai.com

Nous es un producto de Quintara AI. © 2025 Quintara AI. Todos los derechos reservados.

Papí, ahí está tu AGENTS.md completo, listo para copiar y pegar en el root de tu repo nous. 

Nous — la mente que ordena el caos. Nombre perfecto para lo que estás construyendo.

Ahora la pregunta es: ¿creas el repo hoy? 🚀