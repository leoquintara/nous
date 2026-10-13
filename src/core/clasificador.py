"""Clasificador de dimensiones zero-shot por prototipos semánticos.

Cada dimensión tiene frases de referencia en español; su prototipo es
el promedio de sus embeddings. Un evento se clasifica en la dimensión
cuyo prototipo le es más cercano en similitud coseno. Todo local —
reutiliza el mismo MiniLM-L12 de la búsqueda, sin modelo adicional.
"""

import math
import statistics

from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import Dimension, ResultadoClasificacion

MARGEN_MINIMO = 0.02
PESO_SEMANTICO = 0.65
PESO_LEXICO = 0.35
SATURACION_LEXICA = 0.5

PROTOTIPOS: dict[Dimension, tuple[str, ...]] = {
    Dimension.COGNITIVA: (
        "pensé en una idea nueva",
        "decidí cambiar de enfoque",
        "reflexioné sobre el problema",
        "aprendí algo importante hoy",
        "me hice una pregunta difícil",
        "llegué a una conclusión",
        "dudé sobre qué camino tomar",
        "recordé un concepto clave",
        "me di cuenta de algo",
        "analicé la situación con calma",
        "planeé la estrategia mentalmente",
        "imaginé una solución posible",
        "deduje la causa del error",
        "formulé una hipótesis",
    ),
    Dimension.SOMATICA: (
        "estoy muy triste",
        "me siento feliz y en paz",
        "tengo mucha ansiedad",
        "mi cuerpo está cansado",
        "me duele la espalda",
        "tengo sueño y cansancio",
        "estoy nervioso y agitado",
        "me siento enfermo",
        "mi corazón late acelerado",
        "tengo miedo e inquietud",
        "estoy relajado y tranquilo",
        "me siento con mucha energía",
        "sentí un malestar en el estómago",
        "estaba tenso en hombros y cuello",
    ),
    Dimension.CONDUCTUAL: (
        "hice ejercicio en el gimnasio",
        "escribí código durante horas",
        "completé la tarea pendiente",
        "llamé a un amigo por teléfono",
        "caminé hasta el parque",
        "comité cambios al repositorio",
        "tuve una reunión de trabajo",
        "cociné la cena para la familia",
        "trabajé toda la mañana",
        "escribí un documento importante",
        "presenté los resultados al equipo",
        "instalé el programa en la máquina",
        "grabé un video para el canal",
        "limpié y organicé mi espacio",
    ),
}

LEXICON: dict[Dimension, tuple[str, ...]] = {
    Dimension.COGNITIVA: (
        "pens", "decid", "decisi", "idea", "reflex", "aprend", "dud",
        "conclu", "razon", "analiz", "pregunt", "cuenta", "plane",
        "deduc", "imagin", "hipótes", "entend", "comprend", "record",
        "concept", "estudi", "supuest", "estrategi", "mental",
    ),
    Dimension.SOMATICA: (
        "ansie", "estrés", "dolor", "duel", "cansad", "energ", "sueñ",
        "dorm", "mare", "náuse", "corazón", "pecho", "espald", "hombr",
        "escalofr", "migrañ", "relaj", "tens", "agotad", "trist",
        "feliz", "mied", "nervi", "enferm", "malestar", "emoc", "sent",
        "alegr", "angusti", "agobi", "deshidrat", "hambre", "sed",
    ),
    Dimension.CONDUCTUAL: (
        "hice", "fui ", "corr", "escrib", "comí", "comi", "cocin",
        "limp", "organiz", "grab", "instal", "present", "asist",
        "llam", "camin", "conduj", "repar", "complet", "trabaj",
        "jug", "compr", "entren", "commit", "push", "reuni", "gimnas",
        "ejercic", "medit", "viaj", "pase", "bañ", "duch",
    ),
}


class ClasificadorDimensiones:
    """Clasifica texto de eventos en la dimensión más probable."""

    def __init__(self, embeddings: GeneradorEmbeddings) -> None:
        """Inicializa el clasificador con el generador de embeddings.

        Args:
            embeddings: Generador compartido; los prototipos se embeben
                perezosamente en la primera clasificación.
        """
        self._embeddings = embeddings
        self._prototipos: dict[Dimension, list[float]] | None = None

    async def clasificar(self, contenido: str) -> ResultadoClasificacion:
        """Clasifica un texto en la dimensión más cercana semánticamente.

        Args:
            contenido: Texto del evento a clasificar.

        Returns:
            ResultadoClasificacion con dimensión, confianza (margen
            top1-top2), scores por dimensión y flag dudoso.

        Raises:
            ValueError: Si el contenido está vacío o es solo espacios.
        """
        if not contenido.strip():
            raise ValueError("El contenido a clasificar está vacío")
        prototipos = await self._cargar_prototipos()
        vector = await self._embeddings.generar(contenido)
        contenido_lower = contenido.lower()
        scores = {
            dimension: _fusionar(
                _coseno(vector, prototipos[dimension]),
                _puntaje_lexico(contenido_lower, LEXICON[dimension]),
            )
            for dimension in prototipos
        }
        ordenadas = sorted(scores.values(), reverse=True)
        ganadora = max(scores, key=lambda d: scores[d])
        margen = ordenadas[0] - ordenadas[1]
        return ResultadoClasificacion(
            dimension=ganadora,
            confianza=round(margen, 4),
            scores=scores,
            dudoso=margen < MARGEN_MINIMO,
        )

    async def _cargar_prototipos(self) -> dict[Dimension, list[float]]:
        """Embebe las frases de referencia una sola vez por proceso."""
        if self._prototipos is None:
            self._prototipos = {}
            for dimension, frases in PROTOTIPOS.items():
                vectores = await self._embeddings.generar_lote(list(frases))
                self._prototipos[dimension] = _promedio(vectores)
        return self._prototipos


def _fusionar(semantico: float, lexico: float) -> float:
    """Combina evidencia semántica y léxica en un score único."""
    return PESO_SEMANTICO * semantico + PESO_LEXICO * lexico


def _puntaje_lexico(contenido_lower: str, stems: tuple[str, ...]) -> float:
    """Cuenta hits de stems léxicos; satura a 1.0 con dos coincidencias."""
    hits = sum(1 for stem in stems if stem in contenido_lower)
    return min(1.0, hits * SATURACION_LEXICA)


def _promedio(vectores: list[list[float]]) -> list[float]:
    """Promedia vectores componente a componente."""
    if not vectores:
        return []
    return [
        statistics.fmean(componentes)
        for componentes in zip(*vectores, strict=True)
    ]


def _coseno(a: list[float], b: list[float]) -> float:
    """Similitud coseno; los embeddings ya vienen normalizados."""
    producto = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    norma_a = math.sqrt(sum(float(x) ** 2 for x in a))
    norma_b = math.sqrt(sum(float(y) ** 2 for y in b))
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return producto / (norma_a * norma_b)
