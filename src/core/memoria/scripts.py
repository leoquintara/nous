"""Minería de scripts/esquemas (ADR-003, patrón 7).

Los scripts son secuencias de acciones que el usuario repite ("cada bug
crítico: reproduzco → leo logs → fix → test"). Detectarlos permite
predecir el próximo paso y sugerirlo.

Implementación: minería de bigramas de secuencia sobre clústeres
semánticos (cada evento se reduce a un token de tema — su primer
bigrama de stems léxicos — y las secuencias repetidas se cuentan).
Sin dependencias externas; para datasets grandes un PrefixSpan real es
el upgrade documentado.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventoSecuencia:
    """Vista mínima ordenable de un evento para minar scripts."""

    id: str
    timestamp_unix: int
    contenido: str
    dimension: str


@dataclass(frozen=True, slots=True)
class ScriptCandidato:
    """Secuencia recurrente detectada.

    Attributes:
        pasos: Tokens de tema en orden.
        soporte: Veces que la secuencia aparece.
        confianza: Proporción de continuaciones que siguen el patrón.
        eventos_ids: Ids de los eventos que la componen (muestra).
    """

    pasos: tuple[str, ...]
    soporte: int
    confianza: float
    eventos_ids: tuple[str, ...]


MIN_SOPORTE = 3
MIN_PASOS = 2
MAX_PASOS = 4
VENTANA_GAP_SEGUNDOS = 4 * 3600  # pasos separados >4h rompen la secuencia

_STOPWORDS = frozenset(
    "el la los las un una de del en y o a al con por para que me se lo "
    "le mi tu su es son fue fui hice tuvo esta esto eso muy mas más".split()
)


def _sin_acentos(palabra: str) -> str:
    """Colapsa variantes con/sin tilde al mismo token de tema."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", palabra)
        if not unicodedata.combining(c)
    )


def token_tema(contenido: str) -> str:
    """Reduce un contenido a un token de tema comparable entre eventos.

    Toma las dos palabras significativas más largas (sin acentos, para
    que "critico" y "crítico" cuenten como el mismo tema) — lo
    suficiente para distinguir "debug bug" de "push commit" sin NLP
    pesado.
    """
    palabras = [
        _sin_acentos(p)
        for p in re.findall(r"[a-záéíóúñü]+", contenido.lower())
        if p not in _STOPWORDS and len(p) > 3
    ]
    if not palabras:
        return "misc"
    palabras.sort(key=lambda p: (-len(p), p))
    return "+".join(sorted(palabras[:2]))


def _secuencias(eventos: list[EventoSecuencia]) -> list[list[EventoSecuencia]]:
    """Parte la línea en secuencias: mismo día lógico, gap < ventana."""
    ordenados = sorted(eventos, key=lambda e: e.timestamp_unix)
    if not ordenados:
        return []
    seqs: list[list[EventoSecuencia]] = [[ordenados[0]]]
    for prev, cur in zip(ordenados, ordenados[1:], strict=False):
        if cur.timestamp_unix - prev.timestamp_unix > VENTANA_GAP_SEGUNDOS:
            seqs.append([])
        seqs[-1].append(cur)
    return [s for s in seqs if len(s) >= MIN_PASOS]


def minar_scripts(
    eventos: list[EventoSecuencia],
    min_soporte: int = MIN_SOPORTE,
) -> list[ScriptCandidato]:
    """Detecta secuencias de temas recurrentes en la línea de universo.

    Cuenta n-gramas de tokens-tema dentro de cada secuencia temporal;
    retiene los que superan ``min_soporte`` y calcula confianza como
    soporte/apariciones_del_prefijo.

    Args:
        eventos: Eventos del workspace (cualquier orden).
        min_soporte: Apariciones mínimas para considerar un patrón.

    Returns:
        Scripts candidatos ordenados por soporte*confianza descendente.
    """
    conteos: Counter[tuple[str, ...]] = Counter()
    ejemplos: dict[tuple[str, ...], list[str]] = {}
    prefijos: Counter[tuple[str, ...]] = Counter()

    for seq in _secuencias(eventos):
        tokens = [token_tema(e.contenido) for e in seq]
        ids = [e.id for e in seq]
        for n in range(MIN_PASOS, MAX_PASOS + 1):
            for i in range(len(tokens) - n + 1):
                ngrama = tuple(tokens[i : i + n])
                conteos[ngrama] += 1
                ejemplos.setdefault(ngrama, []).extend(ids[i : i + n])
                prefijos[ngrama[:-1]] += 1

    scripts: list[ScriptCandidato] = []
    for ngrama, soporte in conteos.items():
        if soporte < min_soporte:
            continue
        prefijo = ngrama[:-1]
        # Confianza: P(siguiente | prefijo) — qué tan predecible es el final.
        confianza = soporte / max(prefijos.get(prefijo, soporte), 1)
        scripts.append(
            ScriptCandidato(
                pasos=ngrama,
                soporte=soporte,
                confianza=round(min(confianza, 1.0), 4),
                eventos_ids=tuple(ejemplos[ngrama][:12]),
            )
        )
    scripts.sort(key=lambda s: -(s.soporte * s.confianza))
    return scripts
