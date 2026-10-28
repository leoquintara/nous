"""Secret scrub — la capa media entre bloquear y permitir.

``guard_privacidad`` decide SI un proveedor externo puede recibir
datos; este módulo decide QUÉ texto sale una vez permitido. Cualquier
contenido con forma de credencial se redacta antes de cruzar la
frontera — un secreto pegado en una memoria nunca debería poder salir
de la máquina pegado a un ``state`` de System One.

Alcance honesto: se detectan secretos con forma reconocible
(key=value, Bearer, prefijos de proveedor, hex/base64 de longitud de
token). Una contraseña arbitraria sin estructura no se detecta — el
guard de bloqueo sigue siendo la barrera real; el scrub es la red de
seguridad, no la muralla.
"""

from __future__ import annotations

import re

# key=value / key: value con nombre de credencial reconocible.
_KV = re.compile(
    r"(?i)\b("
    r"api[_-]?key|apikey|secret|password|passwd|token|auth|"
    r"access[_-]?key|private[_-]?key|client[_-]?secret|refresh[_-]?token"
    r")([\s]*[=:][\s]*)[\"']?[^\s\"',;]{6,}[\"']?"
)
# Authorization: Bearer … / tokens con prefijo de proveedor.
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]{10,}")
_PREFIJADOS = re.compile(
    r"\b("
    r"sk-[a-z0-9_\-]{16,}|"          # OpenAI-style
    r"ns_[a-f0-9]{16,}|"             # tokens Nous
    r"ns_u_[a-f0-9]{16,}|"
    r"ns_inv_[a-f0-9]{16,}|"
    r"xox[baprs]-[a-z0-9\-]{10,}|"   # Slack
    r"gh[pousr]_[a-z0-9]{20,}|"      # GitHub
    r"glpat-[a-z0-9_\-]{16,}|"       # GitLab
    r"AKIA[A-Z0-9]{16}"              # AWS access key id
    r")"
)
# Hex/base64 desnudo de longitud de token (≥40) — cubre JWTs
# segmentados solo parcialmente; mejor falso positivo que fuga.
_CRUDO = re.compile(r"\b[a-zA-Z0-9+/=_\-]{40,}\b")

_MASCARA = "[REDACTED]"


def redactar_secretos(texto: str) -> str:
    """Reemplaza secuencias con forma de credencial por ``[REDACTED]``.

    Args:
        texto: Contenido a punto de salir hacia un proveedor externo.

    Returns:
        El mismo texto con credenciales reconocibles enmascaradas.
    """
    if not texto:
        return texto
    limpio = _KV.sub(lambda m: m.group(1) + m.group(2) + _MASCARA, texto)
    limpio = _BEARER.sub(f"Bearer {_MASCARA}", limpio)
    limpio = _PREFIJADOS.sub(_MASCARA, limpio)
    return _CRUDO.sub(_MASCARA, limpio)
