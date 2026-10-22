"""Licencia local de Nous — validación JWT sin phone-home (ADR-001).

El usuario compra una key en la landing; ``nous activate-license``
la guarda y este módulo la valida 100% en local:

- Keys **JWT RS256**: se parsean los claims (``tier``, ``features``,
  ``exp``, ``sub``). Si ``NOUS_LICENSE_PUBLIC_KEY`` (PEM) está
  configurada y ``cryptography`` instalado, la firma se verifica;
  sin clave pública se acepta en modo honor — el gate ya está
  documentado como sistema de honor para la edición AGPL.
- Keys **opacas** (no JWT): licencia legacy, equivalen a tier "pro"
  completo sin expiración.

Nunca se envía la key por red desde aquí — la emisión y el
phone-home opcional viven en el servicio de licencias (landing),
no en este paquete.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TIER_OSS = "oss"
_TIER_PRO = "pro"


def ruta_licencia() -> Path:
    """Ubicación del archivo de licencia (``~/.nous/license.jwt``)."""
    return Path.home() / ".nous" / "license.jwt"


def leer_licencia_archivo() -> str | None:
    """Lee el JWT persistido por ``nous activate-license``.

    Returns:
        Contenido del archivo, o None si no existe o está vacío.
    """
    try:
        texto = ruta_licencia().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return texto or None


def _b64url_decode(parte: str) -> bytes:
    """Decodifica base64url tolerando padding ausente."""
    return base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4))


@dataclass(frozen=True, slots=True)
class Licencia:
    """Resultado de validar ``NOUS_LICENSE_KEY``.

    Attributes:
        valida: La key desbloquea features Pro (existe, bien formada
            y no expirada).
        tier: ``"pro"`` | ``"oss"``.
        features: Conjunto de features habilitadas; vacío = todas
            las del tier.
        expira: Epoch de expiración si el JWT lo declara.
        sujeto: ``sub`` del JWT (licenciatario) si existe.
        firma_verificada: True solo si la firma RS256 se verificó
            con la clave pública configurada.
    """

    valida: bool
    tier: str = _TIER_OSS
    features: frozenset[str] = field(default_factory=frozenset)
    expira: int | None = None
    sujeto: str | None = None
    firma_verificada: bool = False

    def tiene_feature(self, nombre: str) -> bool:
        """True si la licencia habilita ``nombre`` (vacío = todo)."""
        return self.valida and (
            not self.features or nombre in self.features
        )


def _verificar_rs256(
    firma: bytes, mensaje: bytes, pem: str
) -> bool:
    """Verifica la firma RS256 si ``cryptography`` está disponible.

    Returns:
        True solo con firma criptográficamente válida.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError:
        logger.info("cryptography ausente — firma de licencia sin verificar")
        return False
    try:
        clave = serialization.load_pem_public_key(pem.encode())
        if not isinstance(clave, rsa.RSAPublicKey):
            logger.warning("clave pública de licencia no es RSA")
            return False
        clave.verify(firma, mensaje, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        logger.warning("firma de licencia inválida")
        return False


def parsear_licencia(
    key: str | None,
    *,
    clave_publica_pem: str | None = None,
    ahora: int | None = None,
) -> Licencia:
    """Valida una license key local.

    Args:
        key: Valor de ``NOUS_LICENSE_KEY`` (JWT u opaca).
        clave_publica_pem: PEM para verificar RS256 (opcional).
        ahora: Epoch actual (inyectable para tests).

    Returns:
        ``Licencia`` con ``valida=False`` ante cualquier malformación.
    """
    if not key or not key.strip():
        return Licencia(valida=False)
    key = key.strip()
    partes = key.split(".")
    if len(partes) != 3:
        # Key opaca legacy: presencia = licenciada (sistema de honor).
        return Licencia(valida=True, tier=_TIER_PRO)
    try:
        claims: dict[str, Any] = json.loads(_b64url_decode(partes[1]))
    except (ValueError, UnicodeDecodeError):
        logger.warning("license key con claims ilegibles")
        return Licencia(valida=False)
    if not isinstance(claims, dict):
        return Licencia(valida=False)

    ahora = int(time.time()) if ahora is None else ahora
    expira = claims.get("exp")
    if expira is not None:
        try:
            expira = int(expira)
        except (TypeError, ValueError):
            return Licencia(valida=False)
        if expira <= ahora:
            return Licencia(
                valida=False,
                tier=str(claims.get("tier", _TIER_OSS)),
                expira=expira,
                sujeto=claims.get("sub"),
            )

    firma_ok = False
    if clave_publica_pem:
        try:
            firma_ok = _verificar_rs256(
                _b64url_decode(partes[2]),
                f"{partes[0]}.{partes[1]}".encode(),
                clave_publica_pem,
            )
        except (ValueError, UnicodeDecodeError):
            firma_ok = False
        if not firma_ok:
            # Con clave pública configurada la firma manda: un JWT con
            # firma inválida (o ausente, p. ej. header alg=none) no
            # licencia — el modo honor aplica solo sin clave pública.
            logger.warning("JWT de licencia con firma inválida — rechazado")
            return Licencia(
                valida=False,
                tier=str(claims.get("tier", _TIER_OSS)),
                expira=expira,
                sujeto=claims.get("sub"),
            )

    features = claims.get("features") or []
    return Licencia(
        valida=True,
        tier=str(claims.get("tier", _TIER_PRO)),
        features=frozenset(str(f) for f in features),
        expira=expira,
        sujeto=claims.get("sub"),
        firma_verificada=firma_ok,
    )


def licencia_actual(ahora: int | None = None) -> Licencia:
    """Resuelve la licencia: ``NOUS_LICENSE_KEY`` o archivo local.

    La variable de entorno gana (deploys explícitos); el archivo
    ``~/.nous/license.jwt`` es el fallback de ``nous activate-license``.
    """
    from src.config import get_settings

    s = get_settings()
    return parsear_licencia(
        s.license_key or leer_licencia_archivo(),
        clave_publica_pem=getattr(s, "license_public_key", None),
        ahora=ahora,
    )


def licencia_activa() -> bool:
    """True si hay licencia válida configurada (sistema de honor AGPL)."""
    return licencia_actual().valida


def tiene_feature(nombre: str) -> bool:
    """True si la licencia activa habilita la feature Pro ``nombre``."""
    return licencia_actual().tiene_feature(nombre)


def canjear_key_licencia(key: str) -> str:
    """Canjea un serial por JWT si hay servicio de activación.

    Única llamada de red del flujo de licencia (ADR-001): solo se
    hace si ``NOUS_LICENSE_ACTIVATION_URL`` está configurada y la key
    no es ya un JWT. Ante cualquier fallo se conserva la key original
    (modelo de honor documentado).

    Args:
        key: License key introducida por el usuario.

    Returns:
        El JWT firmado, o la key original si no hay canje posible.
    """
    from src.config import get_settings

    url = get_settings().license_activation_url
    if not url or len(key.split(".")) == 3:
        return key
    try:
        import httpx

        resp = httpx.post(url, json={"license_key": key}, timeout=10)
        resp.raise_for_status()
        return str(resp.json()["jwt"])
    except Exception as exc:
        logger.warning(
            "Servicio de activación inalcanzable (%s) — "
            "se guarda la key tal cual",
            exc,
        )
        return key


def activar_licencia(key: str) -> Licencia:
    """Valida y persiste una license key en ``~/.nous/license.jwt``.

    Punto único de activación (ADR-001): lo usan el CLI
    ``nous activate-license`` y ``POST /api/licencia/activar``.

    Args:
        key: License key (JWT RS256 u opaca de la landing).

    Returns:
        La licencia parseada, ya persistida.

    Raises:
        ErrorValidacion: Si la key es inválida o está expirada.
    """
    from src.config import get_settings
    from src.core.exceptions import ErrorValidacion

    persistida = canjear_key_licencia(key.strip())
    lic = parsear_licencia(
        persistida,
        clave_publica_pem=get_settings().license_public_key,
    )
    if not lic.valida:
        raise ErrorValidacion("license key inválida o expirada")
    ruta = ruta_licencia()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(persistida + "\n", encoding="utf-8")
    return lic


def desactivar_licencia() -> bool:
    """Elimina el archivo de licencia local (vuelve a edición libre).

    No toca ``NOUS_LICENSE_KEY`` — si esa env var sigue definida, la
    licencia permanece activa aunque el archivo desaparezca.

    Returns:
        True si existía archivo persistido y se eliminó.
    """
    try:
        ruta_licencia().unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("No se pudo eliminar %s", ruta_licencia())
        return False
    return True
