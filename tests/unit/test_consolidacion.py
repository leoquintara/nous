"""Tests de consolidación por evocación (Ronda 2 — reconsolidación)."""

import uuid

from src.core.busqueda import _re_scored
from src.core.modelos import Evento, ResultadoBusqueda
from src.storage.models import EventoModel


def _modelo(edad_dias: float, refuerzos: int) -> EventoModel:
    """Fabrica un EventoModel en memoria con la antigüedad indicada."""
    ahora = 1_800_000_000
    return EventoModel(
        id=uuid.uuid4(),
        timestamp_unix=ahora - int(edad_dias * 86_400),
        dimension="cognitiva",
        fuente="test",
        contenido="evento",
        embedding=[1.0] + [0.0] * 383,
        amplitud=0.5,
        refuerzos=refuerzos,
    )


def test_re_scored_los_refuerzos_frenan_el_olvido() -> None:
    """Un evento viejo muy consultado supera en recencia a uno igual sin uso."""
    ahora = 1_800_000_000
    viejo = _modelo(edad_dias=60, refuerzos=50)
    fresco_sin_uso = _modelo(edad_dias=60, refuerzos=0)
    fusionados = [
        ResultadoBusqueda(evento_id=str(viejo.id), score_rrf=0.5),
        ResultadoBusqueda(evento_id=str(fresco_sin_uso.id), score_rrf=0.5),
    ]
    modelos = {str(viejo.id): viejo, str(fresco_sin_uso.id): fresco_sin_uso}

    resultados = _re_scored(
        fusionados, modelos, [1.0] + [0.0] * 383, ahora, vida_media_dias=30.0
    )

    por_id = {r.evento_id: r for r in resultados}
    assert (
        por_id[str(viejo.id)].score_final
        > por_id[str(fresco_sin_uso.id)].score_final
    )


def test_evento_desde_modelo_copia_refuerzos() -> None:
    """El dataclass de dominio conserva el contador de consolidación."""
    modelo = _modelo(edad_dias=1, refuerzos=7)
    evento = Evento.desde_modelo(modelo)
    assert evento.refuerzos == 7
