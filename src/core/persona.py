"""Sistema de identidad renderizable — el answer bank y el render.

``nous persona`` guarda un banco de respuestas (la "entrevista") en el
almacén de preferencias y lo renderiza a los archivos de identidad del
agente: ``SOUL.md`` (quién es el agente), ``USER.md`` (quién es el
usuario), ``ACCESS_POLICY.md`` (qué puede hacer solo) y ``HEARTBEAT.md``
(su ritmo de chequeo).

Módulo puro (ADR-003): sin I/O, sin reloj implícito. El CLI lee y
escribe el banco en ``ConfiguracionRepository`` y materializa los
archivos en disco.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from string import Template

from src.core.exceptions import ErrorValidacion

# Clave del documento en el almacén de preferencias (workspace personal).
CLAVE_BANCO = "persona.respuestas"


@dataclass(frozen=True, slots=True)
class Plantilla:
    """Un archivo de identidad: sus claves requeridas y su cuerpo.

    El cuerpo es un ``string.Template`` cuyos placeholders son las
    claves del banco con ``.`` reemplazado por ``_``
    (``persona.nombre`` → ``$persona_nombre``).
    """

    archivo: str
    requeridas: tuple[str, ...]
    cuerpo: str


_CABECERA = (
    "> Identidad renderizada por `nous persona render` — edita el banco "
    "con\n> `nous persona set <clave> \"valor\"` y re-renderiza; no edites "
    "este archivo a mano.\n\n"
)

PLANTILLAS: dict[str, Plantilla] = {
    "SOUL.md": Plantilla(
        archivo="SOUL.md",
        requeridas=(
            "persona.nombre",
            "persona.mision",
            "persona.voz",
            "persona.valores",
            "persona.limites",
        ),
        cuerpo=_CABECERA
        + """# SOUL — $persona_nombre

## Quién soy

$persona_mision

## Voz

$persona_voz

## Valores

$persona_valores

## Límites

$persona_limites
""",
    ),
    "USER.md": Plantilla(
        archivo="USER.md",
        requeridas=(
            "usuario.nombre",
            "usuario.rol",
            "usuario.contexto",
            "usuario.comunicacion",
        ),
        cuerpo=_CABECERA
        + """# USER — $usuario_nombre

## Quién es

$usuario_rol

## Contexto

$usuario_contexto

## Comunicación

$usuario_comunicacion
""",
    ),
    "ACCESS_POLICY.md": Plantilla(
        archivo="ACCESS_POLICY.md",
        requeridas=(
            "politica.autonomia",
            "politica.confirmar",
            "politica.prohibido",
        ),
        cuerpo=_CABECERA
        + """# ACCESS POLICY

## Autonomía — puede hacer solo

$politica_autonomia

## Confirmar primero

$politica_confirmar

## Prohibido siempre

$politica_prohibido
""",
    ),
    "HEARTBEAT.md": Plantilla(
        archivo="HEARTBEAT.md",
        requeridas=(
            "ritmo.checkin",
            "ritmo.revisiones",
        ),
        cuerpo=_CABECERA
        + """# HEARTBEAT

## Check-in

$ritmo_checkin

## Revisiones periódicas

$ritmo_revisiones
""",
    ),
}

ARCHIVOS: tuple[str, ...] = tuple(PLANTILLAS)


def _normalizar_banco(banco: dict[str, object]) -> dict[str, str]:
    """Filtra el banco a pares clave→texto no vacío.

    Args:
        banco: Documento leído del almacén de preferencias.

    Returns:
        Solo entradas con clave str y valor str no vacío.
    """
    return {
        k: v.strip()
        for k, v in banco.items()
        if isinstance(k, str) and isinstance(v, str) and v.strip()
    }


def faltantes(
    archivo: str, banco: dict[str, object]
) -> tuple[str, ...]:
    """Devuelve las claves requeridas que aún no tienen respuesta.

    Args:
        archivo: Nombre del archivo destino (``SOUL.md``…).
        banco: Banco de respuestas actual.

    Returns:
        Claves requeridas sin respuesta (vacío = listo para render).

    Raises:
        ErrorValidacion: Si el archivo no es una plantilla conocida.
    """
    plantilla = PLANTILLAS.get(archivo)
    if plantilla is None:
        raise ErrorValidacion(f"archivo de identidad desconocido: {archivo!r}")
    respuestas = _normalizar_banco(banco)
    return tuple(k for k in plantilla.requeridas if k not in respuestas)


def render(archivo: str, banco: dict[str, object]) -> str:
    """Renderiza un archivo de identidad desde el banco de respuestas.

    Args:
        archivo: Nombre del archivo destino.
        banco: Banco de respuestas actual.

    Returns:
        El contenido markdown del archivo.

    Raises:
        ErrorValidacion: Si faltan claves requeridas (listadas en el
            mensaje) o el archivo es desconocido.
    """
    pendientes = faltantes(archivo, banco)
    if pendientes:
        raise ErrorValidacion(
            f"faltan respuestas para {archivo}: "
            + ", ".join(pendientes)
            + " — complétalas con `nous persona set`"
        )
    plantilla = PLANTILLAS[archivo]
    respuestas = _normalizar_banco(banco)
    valores = {k.replace(".", "_"): v for k, v in respuestas.items()}
    return Template(plantilla.cuerpo).safe_substitute(valores)


def hash_banco(banco: dict[str, object]) -> str:
    """Huella corta del banco para el read-back ritual.

    El render imprime este hash: si el usuario nunca vio el banco que
    produjo un archivo, el hash no cuadra — mismamente que el
    ``--confirm <hash>`` de gbrain bootstrap.

    Args:
        banco: Banco de respuestas actual.

    Returns:
        12 hex chars del SHA-256 del banco normalizado.
    """
    canonico = json.dumps(
        _normalizar_banco(banco), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:12]


def catalogo() -> dict[str, tuple[str, ...]]:
    """Catálogo de claves requeridas por archivo (para ``persona claves``).

    Returns:
        ``{archivo: (claves requeridas)}``.
    """
    return {archivo: p.requeridas for archivo, p in PLANTILLAS.items()}
