"""Tests E2E del clasificador con embeddings reales MiniLM-L12.

Incluye la métrica de éxito del AGENTS (precisión > 85%) y las
pruebas de estrés sobre entradas adversarias.
"""

import time

from src.core.clasificador import ClasificadorDimensiones
from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import Dimension

ETIQUETADOS: list[tuple[str, Dimension]] = [
    # cognitiva — pensamientos, decisiones, reflexiones
    ("estuve pensando en cómo mejorar la arquitectura", Dimension.COGNITIVA),
    ("tomé la decisión de migrar a TypeScript", Dimension.COGNITIVA),
    ("reflexión: la memoria es la base de la consciencia", Dimension.COGNITIVA),
    ("aprendí que pgvector soporta índices HNSW", Dimension.COGNITIVA),
    ("me cuestioné si realmente necesitaba esa librería", Dimension.COGNITIVA),
    ("concluí que el diseño local-first era el correcto", Dimension.COGNITIVA),
    ("idea: separar la capa MCP de la lógica de negocio", Dimension.COGNITIVA),
    ("entendí por qué el test fallaba intermitentemente", Dimension.COGNITIVA),
    ("me pregunté qué pasaría si duplicara los workers", Dimension.COGNITIVA),
    ("razoné que la privacidad vale más que la velocidad", Dimension.COGNITIVA),
    # somatica — estados corporales, emociones, energía
    ("me dolía mucho la cabeza esta mañana", Dimension.SOMATICA),
    ("sentí el pecho apretado por la ansiedad", Dimension.SOMATICA),
    ("estaba agotado después de correr", Dimension.SOMATICA),
    ("tenía los hombros tensos todo el día", Dimension.SOMATICA),
    ("me sentía mareado y con náuseas", Dimension.SOMATICA),
    ("dormí profundamente y desperté renovado", Dimension.SOMATICA),
    ("sentí un escalofrío recorrer la espalda", Dimension.SOMATICA),
    ("la migraña me dejó sin energía", Dimension.SOMATICA),
    ("corazón acelerado antes de la demo", Dimension.SOMATICA),
    ("relajación total después del masaje", Dimension.SOMATICA),
    # conductual — acciones, hábitos, actividades
    ("corrí cinco kilómetros por el parque", Dimension.CONDUCTUAL),
    ("escribí el informe final del proyecto", Dimension.CONDUCTUAL),
    ("hice push de todos los cambios pendientes", Dimension.CONDUCTUAL),
    ("comí sushi con los compañeros del trabajo", Dimension.CONDUCTUAL),
    ("instalé PostgreSQL en el servidor nuevo", Dimension.CONDUCTUAL),
    ("limpié toda la casa el domingo", Dimension.CONDUCTUAL),
    ("presenté el avance al equipo completo", Dimension.CONDUCTUAL),
    ("arreglé la bicicleta que estaba pinchada", Dimension.CONDUCTUAL),
    ("asistí a la reunión semanal de planificación", Dimension.CONDUCTUAL),
    ("grabé un video explicando el proyecto", Dimension.CONDUCTUAL),
    # casos difíciles — semántica debe desempatar hits léxicos cruzados
    ("pensé en comer algo saludable mañana", Dimension.COGNITIVA),
    ("me sentí realizado al terminar el maratón", Dimension.SOMATICA),
    ("medité diez minutos observando la respiración", Dimension.CONDUCTUAL),
    ("decidí que me siento mejor trabajando de noche", Dimension.COGNITIVA),
    ("el estrés me impidió concentrarme hoy", Dimension.SOMATICA),
    ("organicé mis notas de la lectura de ayer", Dimension.CONDUCTUAL),
]


async def test_precision_sobre_set_etiquetado_supera_85_porciento() -> None:
    """Verifica la métrica del AGENTS: precisión > 85% en set etiquetado."""
    clasificador = ClasificadorDimensiones(GeneradorEmbeddings())

    aciertos = 0
    fallos: list[str] = []
    for texto, esperada in ETIQUETADOS:
        resultado = await clasificador.clasificar(texto)
        if resultado.dimension == esperada:
            aciertos += 1
        else:
            fallos.append(f"{texto!r} -> {resultado.dimension} (esp {esperada})")

    precision = aciertos / len(ETIQUETADOS)
    assert precision > 0.85, f"precisión {precision:.0%}; fallos: {fallos}"


async def test_estres_texto_muy_largo_se_clasifica() -> None:
    """Verifica que un texto de miles de palabras no rompe el clasificador."""
    texto = "estuve pensando en la arquitectura del sistema. " * 500
    resultado = await ClasificadorDimensiones(
        GeneradorEmbeddings()
    ).clasificar(texto)

    assert resultado.dimension in set(Dimension)


async def test_estres_unicode_y_emoji_no_rompe() -> None:
    """Verifica que emojis y unicode se clasifican sin excepción."""
    resultado = await ClasificadorDimensiones(
        GeneradorEmbeddings()
    ).clasificar("😰 me sentía muy mal 💔 con el corazón roto 🥀")

    assert resultado.dimension in set(Dimension)


async def test_estres_texto_en_otro_idioma_no_rompe() -> None:
    """Verifica que texto no español retorna una dimensión válida."""
    resultado = await ClasificadorDimensiones(
        GeneradorEmbeddings()
    ).clasificar("I went running this morning before work")

    assert resultado.dimension in set(Dimension)


async def test_estres_frase_mixta_retorna_dimension_valida() -> None:
    """Verifica que contenido multi-dimensión sigue clasificándose."""
    resultado = await ClasificadorDimensiones(
        GeneradorEmbeddings()
    ).clasificar(
        "pensé en ir al gimnasio mientras me dolía mucho la cabeza"
    )

    assert resultado.dimension in set(Dimension)


async def test_estres_60_clasificaciones_en_tiempo_razonable() -> None:
    """Verifica que 50 clasificaciones secuenciales toman < 60 segundos."""
    clasificador = ClasificadorDimensiones(GeneradorEmbeddings())

    inicio = time.perf_counter()
    for texto, _ in ETIQUETADOS:
        await clasificador.clasificar(texto)
        await clasificador.clasificar(texto + " de nuevo")
    elapsed = time.perf_counter() - inicio

    assert elapsed < 60.0
