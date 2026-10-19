"""Patrones neurocognitivos de memoria (ADR-003).

Siete mecanismos que convierten a Nous en un sistema cognitivo real:
tipos de memoria, prospectiva, consolidación-sueño, decay adaptativo,
interferencia, dependencia de contexto/estado y minería de scripts.
"""

from src.core.memoria.decay import media_vida_dias, retencion
from src.core.memoria.tipos import TipoMemoria, clasificar_tipo_memoria

__all__ = [
    "TipoMemoria",
    "clasificar_tipo_memoria",
    "media_vida_dias",
    "retencion",
]
