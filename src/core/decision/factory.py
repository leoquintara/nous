"""Construcción del DecisionProvider según configuración (ADR-002).

El factory vive aparte para que ``decision/`` siga sin importar
``src.config`` ni capas superiores — recibe los valores ya resueltos.
La regla de privacidad es estricta: un proveedor externo solo se
instancia con consentimiento explícito (doble opt-in: provider + flag).
"""

from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from src.core.decision.provider import (
    DecisionProvider,
    guard_privacidad,
)

logger = logging.getLogger(__name__)


def _endpoint_vivo(endpoint: str, timeout: float = 1.0) -> bool:
    """Sonda TCP barata: True si el host:puerto del endpoint acepta conexión.

    Sin ella, ``auto`` devolvería un provider cuyo ``predict`` moriría
    por timeout HTTP largo en cada llamada — stall del pipeline en vez
    de degradación limpia.
    """
    try:
        host = urlparse(endpoint).hostname or "localhost"
        puerto = urlparse(endpoint).port or 80
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def construir_provider(
    nombre: str,
    *,
    ollama_endpoint: str = "http://localhost:11434",
    ollama_model: str = "tev1:0.8b",
    jev_api_key: str | None = None,
    jev_endpoint: str = "https://api.typesafe.ai",
    permitir_externos: bool = False,
) -> DecisionProvider | None:
    """Resuelve el proveedor System One configurado.

    Args:
        nombre: ``none|auto|laya|ollama|jev`` — "auto" prueba Laya
            local primero y luego Ollama; sin ninguno, devuelve None
            (el pipeline cae a heurísticos, nunca rompe).
        ollama_endpoint/ollama_model: Destino del provider Ollama.
        jev_api_key/jev_endpoint: Credencial y endpoint de Jev cloud.
        permitir_externos: Flag de privacidad — sin él, los providers
            con ``is_local=False`` no se instancian.

    Returns:
        El proveedor listo, o None si no hay ninguno disponible.
    """
    if nombre == "none":
        return None
    if nombre in ("auto", "laya"):
        try:
            from src.core.decision.adapters import LayaLocal

            return LayaLocal()
        except ImportError:
            if nombre == "laya":
                logger.warning(
                    "decision_provider=laya pero el paquete no está; "
                    "instala el extra 'decision' o usa 'auto'"
                )
                return None
    if nombre in ("auto", "ollama"):
        if nombre == "auto" and not _endpoint_vivo(ollama_endpoint):
            # En "auto" un endpoint muerto equivale a "no hay provider":
            # devolverlo costaría un timeout por cada predict.
            logger.info(
                "decision: Ollama no responde en %s — heurísticos",
                ollama_endpoint,
            )
            return None
        try:
            from src.core.decision.adapters import OllamaLocal

            return OllamaLocal(
                model=ollama_model, endpoint=ollama_endpoint
            )
        except ImportError:
            return None
    if nombre == "jev":
        if not jev_api_key:
            logger.warning("decision_provider=jev sin NOUS_JEV_API_KEY")
            return None
        from src.core.decision.adapters import JevCloud

        provider = JevCloud(api_key=jev_api_key, endpoint=jev_endpoint)
        try:
            guard_privacidad(provider, permitir_externos)
        except Exception as exc:
            logger.warning("provider externo bloqueado: %s", exc)
            return None
        return provider
    return None
