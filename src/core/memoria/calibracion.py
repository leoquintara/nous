"""Calibración de confianza — el Brier score de la memoria.

Una memoria que dice "estoy 90% segura" solo es confiable si el 90%
de las veces acierta. La calibración cierra el loop que la confianza
deja abierto: cada ``predicción`` declara su confianza ANTES del
resultado; al resolverse, el Brier score mide la distancia entre lo
declarado y lo ocurrido.

- **Brier score**: media de (confianza − resultado)². 0.0 = oráculo
  perfecto; 0.25 = decir siempre "50%" a un volado. Menor es mejor.
- **Curva de confiabilidad**: acierto empírico por decil de confianza
  declarada — si en el bucket 0.8–0.9 aciertas el 60%, estás
  sobreconfiado ahí.
- **Sesgo**: confianza media − acierto empírico global. Positivo =
  sobreconfianza (lo más común en LLMs y personas).

Lógica pura: la capa MCP/REST le entrega las predicciones resueltas y
él devuelve el reporte — sin I/O, sin reloj.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PrediccionResuelta:
    """Vista mínima de una predicción ya resuelta."""

    confianza: float
    acierto: bool


@dataclass(frozen=True, slots=True)
class BucketCalibracion:
    """Un decil de la curva de confiabilidad."""

    desde: float
    hasta: float
    n: int
    confianza_media: float
    acierto_empirico: float


@dataclass(slots=True)
class ReporteCalibracion:
    """Reporte agregado de calibración del workspace."""

    n_resueltas: int = 0
    brier: float | None = None
    acierto_empirico: float | None = None
    confianza_media: float | None = None
    sesgo: float | None = None
    curva: list[BucketCalibracion] = field(default_factory=list)


def brier_score(confianza: float, acierto: bool) -> float:
    """Puntúa una sola predicción: (confianza − resultado)².

    Args:
        confianza: Probabilidad declarada en [0.0, 1.0].
        acierto: Resultado real de la afirmación.

    Returns:
        Penalización cuadrática — 0.0 si acertó con certeza total.
    """
    return (confianza - (1.0 if acierto else 0.0)) ** 2


def reporte_calibracion(
    resueltas: list[PrediccionResuelta], n_pendientes: int = 0
) -> ReporteCalibracion:
    """Agrega predicciones resueltas en el reporte de calibración.

    Args:
        resueltas: Predicciones con resultado conocido.
        n_pendientes: Cuántas siguen abiertas (informativo — no afecta
            el score pero sí la lectura del reporte).

    Returns:
        ReporteCalibracion con Brier, curva por deciles y sesgo.
        ``brier`` es None si no hay resueltas — la honestidad también
        es admitir que sin datos no hay métrica.
    """
    if not resueltas:
        return ReporteCalibracion()
    brier = sum(
        brier_score(p.confianza, p.acierto) for p in resueltas
    ) / len(resueltas)
    empirico = sum(1 for p in resueltas if p.acierto) / len(resueltas)
    media = sum(p.confianza for p in resueltas) / len(resueltas)
    curva: list[BucketCalibracion] = []
    for decil in range(10):
        desde = decil / 10.0
        hasta = desde + 0.1
        en_bucket = [
            p
            for p in resueltas
            if desde <= p.confianza < hasta
            or (decil == 9 and p.confianza == 1.0)
        ]
        if not en_bucket:
            continue
        curva.append(
            BucketCalibracion(
                desde=round(desde, 1),
                hasta=round(hasta, 1),
                n=len(en_bucket),
                confianza_media=round(
                    sum(p.confianza for p in en_bucket) / len(en_bucket),
                    4,
                ),
                acierto_empirico=round(
                    sum(1 for p in en_bucket if p.acierto)
                    / len(en_bucket),
                    4,
                ),
            )
        )
    return ReporteCalibracion(
        n_resueltas=len(resueltas),
        brier=round(brier, 4),
        acierto_empirico=round(empirico, 4),
        confianza_media=round(media, 4),
        sesgo=round(media - empirico, 4),
        curva=curva,
    )
