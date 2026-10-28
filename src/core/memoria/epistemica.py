"""Capa epistémica — lo que NO se sabe, dicho explícitamente.

La metamemoria (:mod:`retrieval`) dice qué tan fiable es lo que la
memoria devuelve; este módulo cierra el otro lado: qué faltó. Una
respuesta honesta no es solo "esto es lo que sé con esta confianza" —
es "y esto es lo que no tengo".

Lógica pura: sin I/O, sin reloj implícito — los metadatos de los
resultados entran por parámetro y la laguna sale como dato.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Debajo de esta confianza media la evidencia recuperada se declara
# débil — existe, pero no sostiene una afirmación.
UMBRAL_EVIDENCIA_DEBIL = 0.5


@dataclass(frozen=True, slots=True)
class MapaLagunas:
    """Radiografía de lo que la consulta NO cubrió.

    Attributes:
        cobertura: Resultados recuperados / solicitados (0-1).
        confianza_media: Confianza media de lo recuperado; ``None``
            si no hubo nada.
        lagunas: Lista legible de lo faltante — cada entrada declara
            un hueco concreto (sin dimensión, evidencia débil, todo
            paramétrico…).
        es_laguna_total: True si no se recuperó nada.
        n_parametricas: Cuántos resultados son contenido paramétrico
            (sin corroboración fuera de los pesos del modelo).
    """

    cobertura: float
    confianza_media: float | None
    lagunas: list[str] = field(default_factory=list)
    es_laguna_total: bool = False
    n_parametricas: int = 0


def detectar_lagunas(
    n_resultados: int,
    top_n: int,
    dimensiones: list[str],
    confianzas: list[float],
    n_parametricas: int,
) -> MapaLagunas:
    """Detecta lo que la consulta dejó sin cubrir.

    Un resultado vacío no es silencio — es una laguna total. Un
    resultado parcial tiene lagunas específicas: dimensiones sin
    evidencia, confianza insuficiente o contenido que solo viene de
    los pesos del modelo.

    Args:
        n_resultados: Cuántos recuerdos recuperó la consulta.
        top_n: Cuántos pidió.
        dimensiones: Dimensiones presentes en los resultados.
        confianzas: Confianza metamemoria de cada resultado.
        n_parametricas: Resultados marcados como paramétricos.

    Returns:
        :class:`MapaLagunas` — cobertura, confianza media y la lista
        de huecos concretos en lenguaje legible.
    """
    todas = ("cognitiva", "somatica", "conductual")
    lagunas: list[str] = []
    cobertura = min(1.0, n_resultados / max(top_n, 1))
    confianza_media = (
        round(sum(confianzas) / len(confianzas), 3) if confianzas else None
    )

    if n_resultados == 0:
        return MapaLagunas(
            cobertura=0.0,
            confianza_media=None,
            lagunas=["sin evidencia alguna — laguna total"],
            es_laguna_total=True,
            n_parametricas=0,
        )

    presentes = set(dimensiones)
    for dim in todas:
        if dim not in presentes:
            lagunas.append(f"sin evidencia {dim}")
    if confianza_media is not None and confianza_media < UMBRAL_EVIDENCIA_DEBIL:
        lagunas.append(
            f"evidencia débil (confianza media {confianza_media:.2f} "
            f"< {UMBRAL_EVIDENCIA_DEBIL})"
        )
    if n_parametricas == n_resultados:
        lagunas.append(
            "todo lo recuperado es paramétrico — afirmado desde los "
            "pesos del modelo, sin corroboración"
        )
    elif n_parametricas > 0:
        lagunas.append(
            f"{n_parametricas} de {n_resultados} resultados son "
            "paramétricos (sin corroborar)"
        )
    if cobertura < 1.0:
        lagunas.append(
            f"cobertura parcial — {n_resultados} de {top_n} pedidos"
        )
    return MapaLagunas(
        cobertura=round(cobertura, 3),
        confianza_media=confianza_media,
        lagunas=lagunas,
        es_laguna_total=False,
        n_parametricas=n_parametricas,
    )
