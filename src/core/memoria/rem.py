"""Fase REM del sueño — asociaciones creativas (Neuro-3).

Si NREM destila episodios en gists (semanticización), REM es la fase
creativa: el cerebro dormido recombina recuerdos *distantes entre sí*
y descubre vínculos no obvios — ese "se me ocurrió dormido".

La implementación busca pares de eventos cuya similitud es *media*:
ni tan parecidos que son el mismo recuerdo (eso ya lo captura el dedup
y las aristas ``semantica``/``gist``), ni tan distantes que no hay
relación. Esa banda intermedia es donde viven las asociaciones
creativas: relacionados, pero nunca evocados juntos todavía.

El resultado son aristas ``rem`` — el "sueño" de Nous, auditable como
todo lo demás.
"""

from __future__ import annotations

from src.core.memoria.semanticizacion import EpisodioGist, _coseno

# Banda de similitud creativa: relacionados pero NO evidentes.
# Por debajo (0.55) no hay relación real; por encima (0.78) ya son
# casi el mismo recuerdo — la asociación sería trivial, no un sueño.
UMBRAL_REM_MIN = 0.55
UMBRAL_REM_MAX = 0.78
REM_MAX_POR_CORRIDA = 5


def pares_rem(
    episodios: list[EpisodioGist],
    minimo: float = UMBRAL_REM_MIN,
    maximo: float = UMBRAL_REM_MAX,
    max_pares: int = REM_MAX_POR_CORRIDA,
) -> list[tuple[str, str]]:
    """Encuentra pares soñables: similares pero distantes.

    Cada par cae en la banda [``minimo``, ``maximo``) de coseno.
    Greedy sobre los más similares primero; cada evento participa
    en a lo sumo un par (un sueño no repite al mismo protagonista
    en todas sus escenas).

    Args:
        episodios: Candidatos con embedding (los del corpus vivo).
        minimo: Coseno mínimo — bajo no hay relación real.
        maximo: Coseno máximo — más allá ya es un duplicado.
        max_pares: Tope de vínculos por corrida de sueño.

    Returns:
        Pares ``(id_a, id_b)`` con ``id_a < id_b`` (orden canónico).
    """
    elegibles = [e for e in episodios if e.embedding]
    candidatos: list[tuple[float, str, str]] = []
    for i, ev_a in enumerate(elegibles):
        for ev_b in elegibles[i + 1 :]:
            cos = _coseno(ev_a.embedding or (), ev_b.embedding or ())
            if minimo <= cos < maximo:
                lo, hi = sorted((ev_a.id, ev_b.id))
                candidatos.append((cos, lo, hi))
    candidatos.sort(reverse=True)
    pares: list[tuple[str, str]] = []
    usados: set[str] = set()
    for _, id_a, id_b in candidatos:
        if len(pares) >= max_pares:
            break
        if id_a in usados or id_b in usados:
            continue
        usados.update((id_a, id_b))
        pares.append((id_a, id_b))
    return pares
