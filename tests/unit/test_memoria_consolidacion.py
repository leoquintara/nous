"""Tests unitarios de la consolidación-sueño (ADR-003)."""

from src.core.memoria.consolidacion import (
    EventoConsolidable,
    decidir_consolidacion,
    resumen_legible,
)
from src.core.memoria.tipos import TipoMemoria
from src.core.modelos import Dimension

_AHORA = 1_800_000_000


def _evento(
    id_: str,
    *,
    dias: float = 30.0,
    amplitud: float = 0.5,
    refuerzos: int = 0,
    weight: float = 1.0,
    tipo: str = "evento",
    tipo_memoria: TipoMemoria | None = TipoMemoria.EPISODICA,
    ultimo_acceso: int | None = None,
    archivado: bool = False,
    curado: bool = False,
) -> EventoConsolidable:
    """Fabrica un candidato con la antigüedad indicada."""
    return EventoConsolidable(
        id=id_,
        timestamp_unix=int(_AHORA - dias * 86400),
        dimension=Dimension.COGNITIVA,
        tipo=tipo,
        amplitud=amplitud,
        refuerzos=refuerzos,
        weight=weight,
        tipo_memoria=tipo_memoria,
        ultimo_acceso=ultimo_acceso,
        archivado=archivado,
        curado=curado,
    )


def test_evento_saliente_se_promueve_a_curado() -> None:
    """Amplitud muy alta → promoción al tier que nunca decae."""
    plan = decidir_consolidacion(
        [_evento("a", amplitud=0.9)], ahora_unix=_AHORA
    )
    assert "a" in plan.promover
    assert not plan.archivar


def test_evento_fuerte_se_fortalece() -> None:
    """Amplitud alta (sin llegar a promoción) → weight sube."""
    plan = decidir_consolidacion(
        [_evento("a", amplitud=0.75)], ahora_unix=_AHORA
    )
    assert "a" in plan.fortalecer
    assert plan.fortalecer["a"] > 1.0


def test_evento_debil_y_viejo_se_archiva() -> None:
    """Retención bajo el umbral tras suficiente edad → archivo."""
    viejo = _evento("a", dias=60, amplitud=0.1, refuerzos=0)
    plan = decidir_consolidacion([viejo], ahora_unix=_AHORA)
    assert "a" in plan.archivar


def test_evento_joven_no_se_archiva() -> None:
    """La ventana mínima protege a los recién llegados."""
    plan = decidir_consolidacion(
        [_evento("a", dias=2, amplitud=0.1)], ahora_unix=_AHORA
    )
    assert "a" not in plan.archivar


def test_compromiso_nunca_se_archiva() -> None:
    """Los accionables (compromiso/preferencia) están protegidos."""
    viejo = _evento("a", dias=90, amplitud=0.05, tipo="compromiso")
    plan = decidir_consolidacion([viejo], ahora_unix=_AHORA)
    assert "a" not in plan.archivar


def test_curado_no_se_toca() -> None:
    """El tier curado es inmune a la consolidación."""
    plan = decidir_consolidacion(
        [_evento("a", dias=365, curado=True)], ahora_unix=_AHORA
    )
    assert not plan.archivar and not plan.fortalecer


def test_resumen_legible_cuenta_acciones() -> None:
    """El resumen del reporte lista las tres acciones."""
    plan = decidir_consolidacion(
        [
            _evento("p", amplitud=0.95),
            _evento("f", amplitud=0.75),
            _evento("a", dias=120, amplitud=0.05),
        ],
        ahora_unix=_AHORA,
    )
    resumen = resumen_legible(plan)
    assert "promovidos" in resumen and "fortalecidos" in resumen
    assert "archivados" in resumen


def test_acceso_reciente_salva_al_viejo() -> None:
    """Un acceso reciente reinicia el reloj — no se archiva."""
    viejo_pero_visto = _evento(
        "a", dias=120, amplitud=0.05, ultimo_acceso=_AHORA - 86400
    )
    plan = decidir_consolidacion([viejo_pero_visto], ahora_unix=_AHORA)
    assert "a" not in plan.archivar
