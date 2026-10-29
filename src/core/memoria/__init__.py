"""Patrones neurocognitivos de memoria (ADR-003).

Mecanismos que convierten a Nous en un sistema cognitivo real:
tipos de memoria, prospectiva, consolidación-sueño, decay adaptativo,
interferencia, dependencia de contexto/estado, minería de scripts,
co-activación hebbiana, semanticización, memoria de trabajo, spacing
effect, metamemoria, novedad/sorpresa, extinción emocional,
reconsolidación lábil, ritmo circadiano y primacía de época.
Neuro-3: diversificación (fan effect), tip-of-the-tongue, expansión
de gist, olvido dirigido, fronteras por sorpresa, snooze prospectivo,
beneficio del sueño, prep pack, SRS scheduling, chaining episódico,
fase REM y cuarentena de fuentes.
"""

from src.core.memoria.consolidacion import (
    EventoConsolidable,
    ResultadoConsolidacion,
    decidir_consolidacion,
)
from src.core.memoria.decay import (
    FACTOR_OLVIDO_DIRIGIDO,
    media_vida_dias,
    proxima_revision,
    retencion,
)
from src.core.memoria.diversidad import (
    UMBRAL_CASI,
    diversificar,
    es_casi_recuerdo,
)
from src.core.memoria.interferencia import Conflicto, detectar_conflicto
from src.core.memoria.novedad import (
    UMBRAL_FRONTERA,
    amplitud_con_sorpresa,
    marca_frontera,
    sorpresa,
)
from src.core.memoria.operaciones import (
    UMBRAL_RECALL_DEBIL,
    Clarificacion,
    VeredictoOp,
    certeza_op,
    conflictos_empate,
    consulta_relajada,
    es_recall_debil,
    primacia_fuente,
    span_duracion,
    sugerir_clarificacion,
    variantes_morfologicas,
)
from src.core.memoria.prospectiva import (
    Intencion,
    TriggerTipo,
    aplazada,
    aplazar,
    disparada_por_evento,
    disparada_por_tiempo,
)
from src.core.memoria.rem import pares_rem
from src.core.memoria.retrieval import (
    COACTIVA_DELTA,
    COACTIVA_PESO_MAX,
    confianza_de_evento,
    esta_labil,
    extincion_valencia,
    factor_espaciado,
    intervalo_medio,
    pares_coactivos,
    ventana_labil,
)
from src.core.memoria.ritmos import (
    UMBRAL_EPOCA_SEG,
    VentanaSueno,
    es_primero_de_epoca,
    ventana_optima_sueno,
)
from src.core.memoria.semanticizacion import (
    EpisodioGist,
    PlanGist,
    agrupar_episodios,
    plan_gists,
)
from src.core.memoria.tipos import TipoMemoria, clasificar_tipo_memoria
from src.core.memoria.trabajo import (
    CAPACIDAD_MAX,
    ENSAYOS_PARA_PROMOVER,
    TTL_BORRADOR_SEG,
    debe_promover,
    esta_expirado,
    hay_cupo,
)

__all__ = [
    "CAPACIDAD_MAX",
    "COACTIVA_DELTA",
    "COACTIVA_PESO_MAX",
    "ENSAYOS_PARA_PROMOVER",
    "FACTOR_OLVIDO_DIRIGIDO",
    "TTL_BORRADOR_SEG",
    "UMBRAL_CASI",
    "UMBRAL_FRONTERA",
    "UMBRAL_RECALL_DEBIL",
    "Clarificacion",
    "Conflicto",
    "EpisodioGist",
    "EventoConsolidable",
    "Intencion",
    "PlanGist",
    "ResultadoConsolidacion",
    "TipoMemoria",
    "TriggerTipo",
    "UMBRAL_EPOCA_SEG",
    "VentanaSueno",
    "VeredictoOp",
    "agrupar_episodios",
    "amplitud_con_sorpresa",
    "aplazada",
    "aplazar",
    "certeza_op",
    "clasificar_tipo_memoria",
    "confianza_de_evento",
    "conflictos_empate",
    "consulta_relajada",
    "debe_promover",
    "decidir_consolidacion",
    "detectar_conflicto",
    "disparada_por_evento",
    "disparada_por_tiempo",
    "diversificar",
    "es_casi_recuerdo",
    "es_primero_de_epoca",
    "es_recall_debil",
    "esta_expirado",
    "esta_labil",
    "extincion_valencia",
    "factor_espaciado",
    "hay_cupo",
    "intervalo_medio",
    "marca_frontera",
    "media_vida_dias",
    "pares_coactivos",
    "pares_rem",
    "plan_gists",
    "primacia_fuente",
    "proxima_revision",
    "retencion",
    "sorpresa",
    "span_duracion",
    "sugerir_clarificacion",
    "variantes_morfologicas",
    "ventana_labil",
    "ventana_optima_sueno",
]
