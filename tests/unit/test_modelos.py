"""Tests de los modelos del dominio."""

from src.core.modelos import Dimension, Evento


def test_dimension_tiene_tres_valores() -> None:
    """Verifica que Dimension expone las tres dimensiones de Nous."""
    assert set(Dimension) == {
        Dimension.COGNITIVA,
        Dimension.SOMATICA,
        Dimension.CONDUCTUAL,
    }


def test_evento_amplitud_default_es_media() -> None:
    """Verifica que Evento usa amplitud 0.5 por defecto."""
    evento = Evento(
        id="e1",
        timestamp_unix=1_700_000_000,
        dimension=Dimension.COGNITIVA,
        contenido="idea",
    )

    assert evento.amplitud == 0.5
