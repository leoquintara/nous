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
from typing import Any

logger = logging.getLogger(__name__)

_TIER_OSS = "oss"
_TIER_PRO = "pro"


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
    """Resuelve la licencia desde la configuración activa."""
    from src.config import get_settings

    s = get_settings()
    return parsear_licencia(
        s.license_key,
        clave_publica_pem=getattr(s, "license_public_key", None),
        ahora=ahora,
    )


def licencia_activa() -> bool:
    """True si hay licencia válida configurada (sistema de honor AGPL)."""
    return licencia_actual().valida


def tiene_feature(nombre: str) -> bool:
    """True si la licencia activa habilita la feature Pro ``nombre``."""
    return licencia_actual().tiene_feature(nombre)
