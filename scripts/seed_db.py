"""Siembra la base de datos con eventos de ejemplo realistas.

Genera eventos distribuidos en los últimos 30 días con proporción
50% cognitiva / 30% conductual / 20% somática, amplitudes variadas
(0.3-0.9) y tres rachas dimensionales forzadas para que la detección
de trayectorias tenga material con el que trabajar.

Uso:
    poetry run python -m scripts.seed_db [--eventos N]
"""

import argparse
import asyncio
import logging
import time

from scripts.benchmark_search import FRASES_SEMILLA
from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import Dimension, Evento
from src.core.temporal import SEGUNDOS_POR_DIA, detectar_trayectorias
from src.storage.database import crear_engine, get_session
from src.storage.models import EventoModel

logger = logging.getLogger("nous.scripts.seed_db")

EVENTOS_DEFAULT = 210
DIAS_HISTORIAL = 30
# Ciclo entrelazado: mantiene 50/30/20 sin rachas accidentales
# (máximo 2 eventos consecutivos de la misma dimensión).
CICLO_DIMENSIONES = (
    "cognitiva", "conductual", "cognitiva", "somatica", "cognitiva",
    "conductual", "cognitiva", "conductual", "somatica", "cognitiva",
)
RACHAS = ("conductual", "somatica", "cognitiva")
LONGITUD_RACHA = 4


def _dimensiones(total: int) -> list[str]:
    """Construye la secuencia de dimensiones con rachas insertadas.

    Mantiene la proporción 50/30/20 del ciclo base e inserta tres
    bloques consecutivos de LONGITUD_RACHA eventos de una misma
    dimensión, detectables como trayectorias.
    """
    secuencia: list[str] = []
    bloques = {total // 4, total // 2, (3 * total) // 4}
    indice_racha = 0
    while len(secuencia) < total:
        if len(secuencia) in bloques and indice_racha < len(RACHAS):
            secuencia.extend([RACHAS[indice_racha]] * LONGITUD_RACHA)
            indice_racha += 1
            continue
        secuencia.append(CICLO_DIMENSIONES[len(secuencia) % 10])
    return secuencia[:total]


def _posiciones_racha(total: int) -> dict[int, tuple[int, int]]:
    """Mapea índice de evento → (posición base del bloque, orden).

    Las rachas se compactan en ventanas cortas para que eventos de
    otras fuentes no se intercalen y las rompan en la detección.
    """
    posiciones: dict[int, tuple[int, int]] = {}
    for bloque in (total // 4, total // 2, (3 * total) // 4):
        for j in range(LONGITUD_RACHA):
            posiciones[bloque + j] = (bloque, j)
    return posiciones


async def sembrar(total: int) -> int:
    """Genera y persiste ``total`` eventos con embeddings reales."""
    ahora = int(time.time())
    inicio = ahora - DIAS_HISTORIAL * SEGUNDOS_POR_DIA
    dimensiones = _dimensiones(total)
    intervalo = (DIAS_HISTORIAL * SEGUNDOS_POR_DIA) // total
    rachas = _posiciones_racha(total)
    textos = [f"{FRASES_SEMILLA[i % len(FRASES_SEMILLA)]} #{i}" for i in range(total)]
    embeddings = GeneradorEmbeddings()
    vectores = await embeddings.generar_lote(textos)
    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            for i, (texto, vector) in enumerate(
                zip(textos, vectores, strict=True)
            ):
                ts = _timestamp_evento(i, inicio, intervalo, rachas)
                session.add(
                    EventoModel(
                        timestamp_unix=ts,
                        dimension=dimensiones[i],
                        fuente="seed",
                        contenido=texto,
                        embedding=vector,
                        amplitud=0.3 + 0.6 * ((i * 37) % 10) / 10,
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()
    return total


def _timestamp_evento(
    i: int,
    inicio: int,
    intervalo: int,
    rachas: dict[int, tuple[int, int]],
) -> int:
    """Calcula el timestamp del evento i, compactando rachas."""
    if i in rachas:
        base, orden = rachas[i]
        return inicio + base * intervalo + orden * 600
    return inicio + i * intervalo


def _contar_rachas(total: int) -> int:
    """Reporta cuántas trayectorias detecta el plan sembrado."""
    ahora = int(time.time())
    inicio = ahora - DIAS_HISTORIAL * SEGUNDOS_POR_DIA
    intervalo = (DIAS_HISTORIAL * SEGUNDOS_POR_DIA) // total
    dimensiones = _dimensiones(total)
    rachas = _posiciones_racha(total)
    eventos = [
        Evento(
            id=str(i),
            timestamp_unix=_timestamp_evento(i, inicio, intervalo, rachas),
            dimension=Dimension(dimensiones[i]),
            contenido="",
        )
        for i in range(total)
    ]
    return len(detectar_trayectorias(eventos))


async def main() -> None:
    """Siembra eventos y reporta la distribución resultante."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eventos", type=int, default=EVENTOS_DEFAULT)
    args = parser.parse_args()
    sembrados = await sembrar(args.eventos)
    logger.info(
        "Eventos sembrados: %d (rachas detectables: %d)",
        sembrados,
        _contar_rachas(args.eventos),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
