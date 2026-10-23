"""Patrones neurocognitivos de memoria (ADR-003).

Mecanismos que convierten a Nous en un sistema cognitivo real:
tipos de memoria, prospectiva, consolidación-sueño, decay adaptativo,
interferencia, dependencia de contexto/estado, minería de scripts,
co-activación hebbiana, semanticización, memoria de trabajo, spacing
effect, metamemoria, novedad/sorpresa, extinción emocional,
reconsolidación lábil, ritmo circadiano y primacía de época.
"""

from src.core.memoria.consolidacion import (
    EventoConsolidable,
    ResultadoConsolidacion,
    decidir_consolidacion,
)
from src.core.memoria.decay import media_vida_dias, retencion
from src.core.memoria.interferencia import Conflicto, detectar_conflicto
from src.core.memoria.novedad import amplitud_con_sorpresa, sorpresa
from src.core.memoria.prospectiva import (
    Intencion,
    TriggerTipo,
    disparada_por_evento,
    disparada_por_tiempo,
)
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
    "TTL_BORRADOR_SEG",
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
    "agrupar_episodios",
    "amplitud_con_sorpresa",
    "clasificar_tipo_memoria",
    "confianza_de_evento",
    "debe_promover",
    "decidir_consolidacion",
    "detectar_conflicto",
    "disparada_por_evento",
    "disparada_por_tiempo",
    "es_primero_de_epoca",
    "esta_expirado",
    "esta_labil",
    "extincion_valencia",
    "factor_espaciado",
    "hay_cupo",
    "intervalo_medio",
    "media_vida_dias",
    "pares_coactivos",
    "plan_gists",
    "retencion",
    "sorpresa",
    "ventana_labil",
    "ventana_optima_sueno",
]
