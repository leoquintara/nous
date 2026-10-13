"""Tests de segmentación episódica (Ronda 5 — Zacks et al., 2007)."""

from src.core.episodios import segmentar
from src.core.modelos import Dimension, Evento


def _evento(ts: int, dim: Dimension = Dimension.COGNITIVA, amp: float = 0.5) -> Evento:
    return Evento(id=str(ts), timestamp_unix=ts, dimension=dim, contenido="c", amplitud=amp)


def test_segmentar_corta_en_el_hueco_temporal() -> None:
    """Un gap mayor que el máximo cierra el episodio y abre otro."""
    eventos = [
        _evento(1000), _evento(2000), _evento(3000),
        _evento(3000 + 20_000), _evento(3000 + 21_000),
    ]
    episodios = segmentar(eventos, gap_max_segundos=14_400)

    assert len(episodios) == 2
    assert len(episodios[0].eventos) == 3
    assert len(episodios[1].eventos) == 2
    assert episodios[0].fin == 3000
    assert episodios[1].inicio == 23_000


def test_segmentar_gap_exacto_no_corta() -> None:
    """Una separación igual al máximo sigue siendo el mismo episodio."""
    episodios = segmentar(
        [_evento(0), _evento(14_400)], gap_max_segundos=14_400
    )
    assert len(episodios) == 1


def test_segmentar_ordena_eventos_desordenados() -> None:
    """La entrada no necesita venir ordenada por timestamp."""
    episodios = segmentar([_evento(5000), _evento(1000), _evento(3000)])
    assert len(episodios) == 1
    assert episodios[0].inicio == 1000


def test_episodio_resume_dimension_dominante_y_amplitud() -> None:
    """El resumen reporta la dimensión modal y la amplitud media."""
    eventos = [
        _evento(1, Dimension.SOMATICA, 0.8),
        _evento(2, Dimension.SOMATICA, 0.4),
        _evento(3, Dimension.COGNITIVA, 0.6),
    ]
    (episodio,) = segmentar(eventos)

    assert episodio.dimension_dominante == Dimension.SOMATICA
    assert episodio.amplitud_media == 0.6


def test_segmentar_vacio_retorna_lista_vacia() -> None:
    assert segmentar([]) == []
