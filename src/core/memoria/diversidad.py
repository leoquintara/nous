"""Diversidad y "tip of the tongue" — Neuro-3 (fan effect).

El fan effect (cue overload): cuando muchos recuerdos comparten la
misma clave de evocación, compiten entre sí y la accesibilidad de cada
uno cae — en la práctica, una búsqueda devuelve diez versiones casi
idénticas de lo mismo. La diversificación penaliza los
near-duplicados dentro del propio resultado para que cada slot muestre
algo distinto.

El tip-of-the-tongue: a veces la memoria está "en la punta de la
lengua" — hay candidatos, pero ninguno cruza el umbral de solidez.
Mejor declararlo ("casi recuerdo esto…") que fingir amnesia o fingir
certeza.

Todo aquí es puro: tokens y scores entran, penalizaciones salen.
"""

from __future__ import annotations

# Casi-duplicado por contenido: Jaccard de tokens por encima de esto
# cuenta como "el mismo recuerdo con distinto empaque".
UMBRAL_DUP_JACCARD = 0.6
# Penalización al score del casi-duplicado (el primero que entró gana).
PENAL_DUPLICADO = 0.85
# Por debajo de este score_final el recuerdo es "casi": existe señal
# pero no cruza el umbral de solidez — reportarlo honestamente.
UMBRAL_CASI = 0.30


def _tokens(texto: str) -> set[str]:
    """Tokens normalizados: minúsculas, alfanuméricos, largo ≥ 3."""
    return {
        t
        for t in texto.lower().split()
        if len(t) >= 3 and t.isalnum()
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    """Similitud de Jaccard entre dos bolsas de tokens."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def diversificar(
    items: list[tuple[str, str, float]],
    umbral: float = UMBRAL_DUP_JACCARD,
    penal: float = PENAL_DUPLICADO,
) -> list[tuple[str, str, float]]:
    """Penaliza near-duplicados por contenido dentro del resultado.

    Recorre los items en orden de score; cada item que se parece
    demasiado (Jaccard ≥ ``umbral``) a uno ya seleccionado pierde un
    factor de su score — el efecto fan: la clave compartida diluye
    la accesibilidad de cada competidor.

    Args:
        items: Tuplas ``(id, contenido, score)`` ordenadas por score.
        umbral: Jaccard mínimo para considerar dos textos duplicados.
        penal: Multiplicador de penalización del duplicado.

    Returns:
        Las mismas tuplas con scores ajustados, reordenadas.
    """
    seleccionados: list[tuple[str, set[str]]] = []
    salida: list[tuple[str, str, float]] = []
    for id_, contenido, score in items:
        tokens = _tokens(contenido)
        if any(
            _jaccard(tokens, tokens_previo) >= umbral
            for _, tokens_previo in seleccionados
        ):
            score *= penal
        else:
            seleccionados.append((id_, tokens))
        salida.append((id_, contenido, score))
    salida.sort(key=lambda t: t[2], reverse=True)
    return salida


def es_casi_recuerdo(score_final: float) -> bool:
    """True si el score indica "casi memoria" — señal bajo el umbral.

    El TOT no es amnesia ni certeza: hay candidatos pero ninguno
    cruza la solidez. La capa de resultado lo expone como
    ``casi_recuerdo`` para que el agente pondere honestamente.
    """
    return 0.0 < score_final < UMBRAL_CASI
