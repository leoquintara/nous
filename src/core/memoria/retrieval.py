"""Efectos de la evocación (recall) — la memoria cambia al usarse.

Cuatro patrones neurocognitivos del lado de la lectura:

- **Co-activación hebbiana**: "neurons that fire together wire
  together" — los resultados devueltos juntos ganan una arista
  ``coactiva`` que se refuerza con cada co-evocación.
- **Extinción emocional**: una memoria negativa evocada en un estado
  neutro pierde valencia poco a poco (la exposición sin refuerzo
  extingue — nunca en un solo recall).
- **Ventana lábil / reconsolidación**: la memoria evocada queda
  lábil unas horas — una actualización dentro de la ventana cuenta
  como reconsolidación (arista ``reconsolida``), no solo supersesión.
- **Metamemoria**: ``confianza`` estima qué tan fiable es el
  recuerdo devuelto — el agente puede ponderar la respuesta en vez
  de tratar toda memoria como igualmente cierta.
"""

from __future__ import annotations

import itertools

# --- Co-activación hebbiana ---
COACTIVA_TOP_K = 6
COACTIVA_DELTA = 0.15
COACTIVA_PESO_MAX = 3.0

# --- Extinción emocional ---
# Cada recall neutro atenúa la valencia un ~12%; |valencia| < 0.05
# se considera extinta del todo.
EXTINCION_FACTOR = 0.12
EXTINCION_MINIMA = 0.05
# El recuerdo debe ser emocional y el estado actual (casi) neutro
# para que la exposición extinga — revivirlo estando mal refuerza.
UMBRAL_VALENCIA_EMOCIONAL = 0.3
UMBRAL_ESTADO_NEUTRO = 0.2

# --- Ventana lábil ---
# Horas tras la evocación en que la memoria admite reconsolidación.
VENTANA_LABIL_SEG = 6 * 3600

# --- Techo paramétrico (Neuro-6) ---
# Lo que el agente afirma desde sus pesos — no observado ni
# corroborado — nunca alcanza confianza alta solo por repetirse.
# Sin techo, el sistema se autocita: el modelo dice X → la memoria
# guarda X → el recall devuelve X como si fuera evidencia. El techo
# se levanta cuando la realidad avala: suficientes evocaciones útiles
# o una aprobación humana (el flujo staged limpia ``parametrica``).
TECHO_PARAMETRICA = 0.70
CORROBORACION_REFUERZOS = 3

# --- STP/LTP coactiva (Neuro-6) ---
# La arista hebbiana tiene dos componentes: LTP (``peso``, duradero,
# crece con cada co-evocación) y STP (potenciación rápida que decae
# con media-vida ~6h desde la última co-activación). Lo recién
# asociado empuja; lo duradero permanece.
STP_BOOST = 0.4
STP_MEDIA_VIDA_SEG = 6 * 3600

# --- Efecto Zeigarnik (Neuro-4) ---
# Los compromisos abiertos permanecen más accesibles al recall que
# los cerrados — el cerebro no suelta lo pendiente. Boost modesto
# porque el compromiso ya suele ser relevante por contenido.
ZEIGARNIK_BOOST = 1.15

# --- Retrieval-induced forgetting (Neuro-4) ---
# Competidores recuperados pero no seleccionados se suprimen:
# evocar al ganador debilita a los perdedores un ~3% por consulta.
RIF_FACTOR = 0.97
RIF_PISO_WEIGHT = 0.1  # debajo de esto el decay natural ya manda
# Rango de competencia real: solo los que estuvieron cerca del
# corte (top 2×top_n de cada lista) cuentan como competidores —
# el resto nunca tuvo chance y no "perdió" nada.
RIF_RANGO_COMPETENCIA = 2


def factor_zeigarnik(tipo: str | None, cerrado: bool) -> float:
    """Boost de saliencia para compromisos abiertos (efecto Zeigarnik).

    Lo pendiente se recuerda mejor que lo terminado: un compromiso
    sin cerrar merece subir en el ranking aunque su score semántico
    sea modesto. Cerrado o no-compromiso → neutro (1.0).
    """
    return (
        ZEIGARNIK_BOOST
        if tipo == "compromiso" and not cerrado
        else 1.0
    )


def competidores_rif(
    candidatos_ids: list[str],
    elegidos_ids: list[str],
    rango: int = RIF_RANGO_COMPETENCIA,
) -> list[str]:
    """Ids recuperados que compitieron y perdieron la evocación.

    Solo cuentan los que estuvieron dentro de ``rango`` × la
    cantidad de elegidos en cada lista de candidatos — fuera de
    ese corte no hubo competencia real, fue ruido de fondo.

    Args:
        candidatos_ids: Ids devueltos por las ramas de búsqueda,
            en orden de relevancia de cada rama.
        elegidos_ids: Ids de la selección final (los ganadores).
        rango: Multiplicador del corte de competencia.

    Returns:
        Ids de los competidores suprimidos, sin duplicados. Sin
        elegidos no hay evocación que suprima — lista vacía.
    """
    if not elegidos_ids:
        return []
    elegidos = set(elegidos_ids)
    corte = max(len(elegidos_ids) * rango, 1)
    return [
        eid
        for eid in dict.fromkeys(candidatos_ids[:corte])
        if eid not in elegidos
    ]


def pares_coactivos(evento_ids: list[str]) -> list[tuple[str, str]]:
    """Pares únicos de los top-K resultados co-evocados.

    El co-recall es la señal hebbiana: si dos memorias se encienden
    juntas para la misma consulta, su asociación merece un vínculo
    propio — no necesariamente son semánticamente iguales, son
    *funcionalmente* coactivas.
    """
    ids = evento_ids[:COACTIVA_TOP_K]
    return list(itertools.combinations(ids, 2))


def peso_coactiva(peso_actual: float) -> float:
    """Nuevo peso de una arista coactiva re-encendida (cap fisiológico)."""
    return min(peso_actual + COACTIVA_DELTA, COACTIVA_PESO_MAX)


def peso_efectivo_coactiva(
    peso_ltp: float,
    ultima_coactiva: int | None,
    ahora_unix: int,
) -> float:
    """Peso efectivo de la arista: LTP duradero + STP que decae ~6h.

    La potenciación a corto plazo (STP) es un boost fijo que se
    desvanece con media-vida ``STP_MEDIA_VIDA_SEG`` desde el último
    co-recall — dos memorias que se encendieron juntas ayer empujan
    más que dos que lo hicieron hace un mes.

    Args:
        peso_ltp: Peso duradero acumulado de la arista.
        ultima_coactiva: Unix del último co-encendido; ``None`` = la
            arista existe pero nunca se re-encendió (STP agotada).
        ahora_unix: Tiempo actual en epoch.

    Returns:
        ``peso_ltp + STP`` en [peso_ltp, peso_ltp + STP_BOOST].
    """
    if ultima_coactiva is None:
        return peso_ltp
    dt = max(0, ahora_unix - ultima_coactiva)
    stp: float = STP_BOOST * (0.5 ** (dt / STP_MEDIA_VIDA_SEG))
    return peso_ltp + stp


def extincion_valencia(
    valencia_memoria: float | None,
    estado_actual: float | None,
) -> float | None:
    """Valencia tras un recall en estado neutro — extinción gradual.

    Args:
        valencia_memoria: ``estado_emocional`` codificado (−1..1).
        estado_actual: Valencia somática actual; ``None`` también
            cuenta como neutra (sin dato no hay "estando mal").

    Returns:
        Nueva valencia atenuada, o ``None`` si este recall no
        extingue (memoria neutra o usuario emocional).
    """
    if valencia_memoria is None:
        return None
    if abs(valencia_memoria) < UMBRAL_VALENCIA_EMOCIONAL:
        return None
    if estado_actual is not None and abs(estado_actual) >= UMBRAL_ESTADO_NEUTRO:
        return None
    nueva = valencia_memoria * (1.0 - EXTINCION_FACTOR)
    return 0.0 if abs(nueva) < EXTINCION_MINIMA else nueva


def ventana_labil(ahora_unix: int, segundos: int = VENTANA_LABIL_SEG) -> int:
    """Hasta cuándo la memoria evocada admite reconsolidación."""
    return ahora_unix + segundos


def esta_labil(labil_hasta: int | None, ahora_unix: int) -> bool:
    """True si el evento sigue dentro de su ventana de reconsolidación."""
    return labil_hasta is not None and ahora_unix <= labil_hasta


def techo_parametrica(parametrica: bool, refuerzos: int) -> float:
    """Tope de confianza para contenido paramétrico (sin corroborar).

    Una memoria afirmada desde los pesos del modelo no puede superar
    ``TECHO_PARAMETRICA`` mientras la realidad no la avale — el
    sistema no se autocita. La corroboración levanta el techo: cada
    evocación que la memoria sobrevive cuenta como uso validado; a
    ``CORROBORACION_REFUERZOS`` refuerzos el contenido ya rindió y el
    descuento paramétrico expira. La aprobación humana del flujo
    staged también la levanta (borra el flag al aprobar).
    """
    if not parametrica or refuerzos >= CORROBORACION_REFUERZOS:
        return 1.0
    return TECHO_PARAMETRICA


def confianza(
    refuerzos: int,
    weight: float,
    amplitud: float,
    edad_dias: float,
    contradicciones: int = 0,
    archivado: bool = False,
    fuente_confiable: bool | None = True,
    parametrica: bool = False,
) -> float:
    """Metamemoria: qué tan fiable es este recuerdo devuelto (0-1).

    No es el score de la búsqueda — es cuánto debería fiarse el
    agente del contenido una vez recuperado:

    - Sube con refuerzos (evocaciones correctas previas), ``weight``
      (consolidación) y amplitud (codificación intensa).
    - Baja con antigüedad extrema (el pasado se vuelve anecdótico),
      contradicciones abiertas y estar archivado.
    - **Amnesia de fuente** (Neuro-4): ``fuente_confiable`` pesa el
      origen, no el contenido — ``None`` (sin fuente: el recuerdo
      llegó sin firma, no se puede rastrear de dónde salió) baja la
      confianza un 10%; ``False`` (fuente marcada no confiable — el
      contenido se evoca pero su procedencia está en duda) un 25%.
    - **Techo paramétrico** (Neuro-6): contenido afirmado desde los
      pesos del modelo (``parametrica``) se topa en
      ``TECHO_PARAMETRICA`` hasta que la corroboración lo levante.

    Returns:
        Confianza en [0.05, 0.98] — nunca certeza total: es una
        memoria, no un hecho verificado.
    """
    if archivado:
        return 0.05
    evidencia = (
        0.45
        + min(refuerzos, 8) * 0.045
        + min(max(weight, 0.0), 3.0) * 0.08
        + amplitud * 0.1
    )
    senectud = 1.0 - min(edad_dias / 365.0, 0.25)
    penal = 1.0 - min(contradicciones, 3) * 0.15
    procedencia = 1.0 if fuente_confiable else 0.75
    if fuente_confiable is None:
        procedencia = 0.9
    techo = techo_parametrica(parametrica, refuerzos)
    return round(
        max(
            0.05,
            min(0.98, evidencia * senectud * penal * procedencia, techo),
        ),
        3,
    )


def desglose_confianza(
    refuerzos: int,
    weight: float,
    amplitud: float,
    edad_dias: float,
    contradicciones: int = 0,
    archivado: bool = False,
    fuente_confiable: bool | None = True,
    parametrica: bool = False,
) -> dict[str, float | bool | None]:
    """La metamemoria descompuesta — cada señal, por separado.

    Una sola cifra de confianza esconde POR QUÉ es así; el desglose
    declara qué la subió, qué la bajó y qué techo aplicó — la
    diferencia entre "0.68" y "0.68 porque es paramétrica, la fuente
    es confiable y no tiene contradicciones".

    Returns:
        Dict con ``evidencia``, ``senectud``, ``penalizacion``,
        ``procedencia``, ``techo``, ``parametrica`` y ``final``.
    """
    evidencia = (
        0.45
        + min(refuerzos, 8) * 0.045
        + min(max(weight, 0.0), 3.0) * 0.08
        + amplitud * 0.1
    )
    senectud = 1.0 - min(edad_dias / 365.0, 0.25)
    penal = 1.0 - min(contradicciones, 3) * 0.15
    procedencia = 1.0 if fuente_confiable else 0.75
    if fuente_confiable is None:
        procedencia = 0.9
    techo = techo_parametrica(parametrica, refuerzos)
    final = (
        0.05
        if archivado
        else round(
            max(
                0.05,
                min(
                    0.98,
                    evidencia * senectud * penal * procedencia,
                    techo,
                ),
            ),
            3,
        )
    )
    return {
        "evidencia": round(evidencia, 3),
        "senectud": round(senectud, 3),
        "penalizacion": round(penal, 3),
        "procedencia": round(procedencia, 3),
        "techo": round(techo, 3),
        "parametrica": parametrica,
        "final": final,
    }


def confianza_de_evento(
    refuerzos: int,
    weight: float,
    amplitud: float,
    timestamp_unix: int,
    ahora_unix: int,
    archivado: bool = False,
    fuente_confiable: bool | None = True,
    parametrica: bool = False,
) -> float:
    """Atajo sobre campos del evento: deriva edad y delega."""
    edad = max(0.0, (ahora_unix - timestamp_unix) / 86400.0)
    return confianza(
        refuerzos,
        weight,
        amplitud,
        edad,
        archivado=archivado,
        fuente_confiable=fuente_confiable,
        parametrica=parametrica,
    )


# --- Spacing effect (se consume desde decay) ---
def factor_espaciado(intervalo_medio_dias: float | None) -> float:
    """Multiplicador de media-vida por ensayos *espaciados*.

    El spacing effect: repasos distribuidos en el tiempo consolidan
    mejor que repasos masivos. ``intervalo_medio_dias`` = (último
    acceso − creación) / refuerzos — una evocación por día vale más
    que cinco el mismo día. Cap a +35%.

    Returns:
        Factor ≥1.0; ``None`` (sin accesos) → 1.0.
    """
    if intervalo_medio_dias is None or intervalo_medio_dias <= 0.0:
        return 1.0
    return 1.0 + min(intervalo_medio_dias, 7.0) * 0.05


def intervalo_medio(
    timestamp_unix: int, ultimo_acceso: int | None, refuerzos: int
) -> float | None:
    """Intervalo medio entre ensayos en días, o None si no hubo."""
    if refuerzos <= 0 or ultimo_acceso is None:
        return None
    gap = max(0, ultimo_acceso - timestamp_unix) / 86400.0
    return gap / refuerzos if gap > 0 else 0.0
