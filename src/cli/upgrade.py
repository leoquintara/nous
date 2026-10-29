"""Soporte de actualización de versiones de Nous (estilo gbrain).

Detecta el método de instalación — checkout fuente (git) o paquete
instalado (pip/pipx) — y ofrece ``check-update`` y ``upgrade``:

- Fuente: ``git pull --ff-only`` + ``poetry install`` + ``alembic upgrade``.
- Paquete: ``pip install --upgrade nous`` + ``alembic upgrade``.

Las migraciones de base de datos son la fase post-upgrade obligatoria
(el equivalente al post-install de gbrain); se omiten solo con
``--no-migrate`` para procesos automatizados.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from src.cli import _VERSION_FALLBACK

logger = logging.getLogger("nous.cli.upgrade")

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
PYPI_JSON = "https://pypi.org/pypi/nous-memory/json"
PAQUETE = "nous-memory"

MetodoInstalacion = Literal["fuente", "paquete"]


@dataclass(frozen=True, slots=True)
class ResultadoCheckUpdate:
    """Resultado de comparar la versión instalada con la disponible."""

    version_actual: str
    metodo: MetodoInstalacion
    version_reciente: str | None
    actualizacion_disponible: bool | None
    comando_upgrade: str
    url_release: str | None
    detalle: str

    def a_dict(self) -> dict[str, object]:
        """Serializa a dict JSON-compatible."""
        return {
            "version_actual": self.version_actual,
            "metodo_instalacion": self.metodo,
            "version_reciente": self.version_reciente,
            "actualizacion_disponible": self.actualizacion_disponible,
            "comando_upgrade": self.comando_upgrade,
            "url_release": self.url_release,
            "detalle": self.detalle,
        }


@dataclass(slots=True)
class PasoUpgrade:
    """Un paso del proceso de upgrade con su resultado."""

    nombre: str
    ok: bool
    salida: str = ""


@dataclass(slots=True)
class ResultadoUpgrade:
    """Resultado agregado del upgrade completo."""

    ok: bool
    metodo: MetodoInstalacion
    pasos: list[PasoUpgrade] = field(default_factory=list)
    detalle: str = ""

    def a_dict(self) -> dict[str, object]:
        """Serializa a dict JSON-compatible."""
        return {
            "ok": self.ok,
            "metodo_instalacion": self.metodo,
            "detalle": self.detalle,
            "pasos": [{"nombre": p.nombre, "ok": p.ok, "salida": p.salida} for p in self.pasos],
        }


def version_actual() -> str:
    """Retorna la versión instalada de Nous.

    Prioriza la metadata del paquete instalado; en un checkout fuente
    sin instalar cae al fallback declarado en ``src/cli/__init__.py``
    (mantener sincronizada con ``pyproject.toml``).

    Returns:
        Cadena de versión semver.
    """
    try:
        return importlib.metadata.version(PAQUETE)
    except importlib.metadata.PackageNotFoundError:
        return _VERSION_FALLBACK


def detectar_metodo() -> MetodoInstalacion:
    """Detecta cómo está instalado Nous.

    Un checkout fuente se reconoce por tener ``pyproject.toml`` y un
    directorio ``.git`` en la raíz del proyecto.

    Returns:
        ``"fuente"`` si es un checkout git, ``"paquete"`` si no.
    """
    if (RAIZ_PROYECTO / "pyproject.toml").is_file() and (RAIZ_PROYECTO / ".git").exists():
        return "fuente"
    return "paquete"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta git en la raíz del proyecto capturando la salida."""
    return subprocess.run(
        ["git", *args],
        cwd=RAIZ_PROYECTO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _tiene_remoto() -> bool:
    """Indica si el checkout tiene al menos un remoto git configurado."""
    return bool(_git("remote").stdout.strip())


def _versiones_remotas_fuente() -> tuple[int, str | None]:
    """Consulta el remoto: commits por detrás y tag más reciente.

    Returns:
        Tupla (commits_por_detras, ultimo_tag). ``-1`` y ``None`` si el
        remoto no es alcanzable.
    """
    if _git("fetch", "--tags", "--quiet").returncode != 0:
        return -1, None
    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    atras = -1
    if upstream.returncode == 0:
        conteo = _git("rev-list", "--count", "HEAD..@{u}")
        if conteo.returncode == 0:
            atras = int(conteo.stdout.strip())
    tag = _git("tag", "--sort=-v:refname")
    tags = [t for t in tag.stdout.splitlines() if re.match(r"^v?\d", t)]
    return atras, (tags[0] if tags else None)


def _version_mas_reciente_paquete() -> tuple[str | None, bool]:
    """Consulta el índice de paquetes por la última versión publicada.

    Returns:
        ``(versión, homónimo)`` — ``homónimo=True`` cuando el paquete
        ``nous`` del índice no es de Quintara AI: el nombre está
        ocupado por otro proyecto y ni la versión ni un
        ``pip install --upgrade nous`` son seguros de usar.
    """
    try:
        respuesta = httpx.get(PYPI_JSON, timeout=10.0)
        respuesta.raise_for_status()
        info = respuesta.json()["info"]
    except (httpx.HTTPError, ValueError, KeyError):
        return None, False
    huella = json.dumps(
        {k: info.get(k) for k in ("author", "author_email", "home_page", "project_urls")},
        ensure_ascii=False,
    ).lower()
    if "quintarai" not in huella:
        return None, True
    version = info.get("version")
    return (str(version), False) if version else (None, False)


def _partes_version(version: str) -> tuple[int, ...]:
    """Convierte '1.2.3' en (1, 2, 3) para comparación numérica."""
    return tuple(int(p) for p in re.findall(r"\d+", version.lstrip("v")))


def es_mas_nueva(reciente: str, actual: str) -> bool:
    """Compara dos versiones semver-like por componentes numéricos."""
    return _partes_version(reciente) > _partes_version(actual)


def check_update() -> ResultadoCheckUpdate:
    """Compara la versión instalada con la más reciente disponible.

    En modo fuente cuenta los commits por detrás del upstream y reporta
    el tag más reciente; en modo paquete consulta el índice PyPI.

    Returns:
        ResultadoCheckUpdate con la comparativa y el comando sugerido.
    """
    actual = version_actual()
    metodo = detectar_metodo()
    if metodo == "fuente":
        comando = "nous upgrade  # git pull --ff-only + poetry install"
        if not _tiene_remoto():
            return ResultadoCheckUpdate(
                actual,
                metodo,
                None,
                None,
                comando,
                None,
                "Source checkout without remote: no version source.",
            )
        atras, tag = _versiones_remotas_fuente()
        if atras < 0:
            return ResultadoCheckUpdate(
                actual,
                metodo,
                tag,
                None,
                comando,
                None,
                "Remote unreachable; could not check.",
            )
        return ResultadoCheckUpdate(
            actual,
            metodo,
            tag,
            atras > 0,
            comando,
            None,
            f"{atras} commit(s) por detrás del upstream.",
        )
    comando = f"{sys.executable} -m pip install --upgrade {PAQUETE}"
    reciente, homonimo = _version_mas_reciente_paquete()
    if homonimo:
        return ResultadoCheckUpdate(
            actual,
            metodo,
            None,
            None,
            "pipx install --force <repo-path>",
            PYPI_JSON,
            "El nombre 'nous' en PyPI pertenece a otro proyecto: sin "
            "índice propio, actualiza reinstalando desde la fuente.",
        )
    if reciente is None:
        return ResultadoCheckUpdate(
            actual,
            metodo,
            None,
            None,
            comando,
            PYPI_JSON,
            "Índice de paquetes inalcanzable.",
        )
    return ResultadoCheckUpdate(
        actual,
        metodo,
        reciente,
        es_mas_nueva(reciente, actual),
        comando,
        PYPI_JSON,
        "Version compared against the index.",
    )


def _arbol_limpio() -> bool:
    """Indica si el árbol de trabajo git no tiene cambios."""
    return not _git("status", "--porcelain").stdout.strip()


def _ejecutar_paso(nombre: str, comando: list[str]) -> PasoUpgrade:
    """Ejecuta un comando del upgrade y captura el resultado."""
    try:
        proc = subprocess.run(
            comando,
            cwd=RAIZ_PROYECTO,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PasoUpgrade(nombre, False, str(exc))
    salida = (proc.stdout or proc.stderr).strip()[:500]
    return PasoUpgrade(nombre, proc.returncode == 0, salida)


def _alembic_upgrade() -> PasoUpgrade:
    """Aplica las migraciones pendientes de la base de datos.

    ``python -c … migrar_head()``: el wheel no lleva ``alembic.ini``,
    así que ``python -m alembic`` no basta — el helper resuelve
    ``script_location`` desde el paquete instalado. Sigue pasando por
    ``_ejecutar_paso``: misma costura que el resto de comandos.
    """
    return _ejecutar_paso(
        "migraciones (alembic upgrade head)",
        [
            sys.executable,
            "-c",
            "from src.storage.migraciones import migrar_head; " "migrar_head()",
        ],
    )


def upgrade(*, no_migrate: bool = False) -> ResultadoUpgrade:
    """Ejecuta el upgrade completo según el método de instalación.

    Args:
        no_migrate: Omite ``alembic upgrade head`` (equivalente al
            ``--swap-only`` de gbrain: solo intercambia el código).

    Returns:
        ResultadoUpgrade con el detalle de cada paso ejecutado.
    """
    metodo = detectar_metodo()
    resultado = ResultadoUpgrade(ok=True, metodo=metodo)
    if metodo == "fuente":
        if not _arbol_limpio():
            resultado.ok = False
            resultado.detalle = (
                "Árbol de trabajo con cambios: commit o stash antes " "de actualizar."
            )
            return resultado
        if _tiene_remoto():
            resultado.pasos.append(
                _ejecutar_paso("git pull --ff-only", ["git", "pull", "--ff-only"])
            )
            if not resultado.pasos[-1].ok:
                resultado.ok = False
                resultado.detalle = "git pull failed (divergent history?)."
                return resultado
        else:
            resultado.pasos.append(PasoUpgrade("git pull", True, "skipped: no remote"))
        resultado.pasos.append(_ejecutar_paso("instalar dependencias", ["poetry", "install"]))
    else:
        _, homonimo = _version_mas_reciente_paquete()
        if homonimo:
            resultado.ok = False
            resultado.detalle = (
                "'nous' en PyPI es otro proyecto — abortando para no "
                "instalar código ajeno. Reinstala desde la fuente: "
                "pipx install --force <repo-path>"
            )
            return resultado
        resultado.pasos.append(
            _ejecutar_paso(
                f"pip install --upgrade {PAQUETE}",
                [sys.executable, "-m", "pip", "install", "--upgrade", PAQUETE],
            )
        )
    if all(p.ok for p in resultado.pasos):
        if not no_migrate:
            resultado.pasos.append(_alembic_upgrade())
    else:
        resultado.ok = False
        resultado.detalle = "An earlier step failed; migrations skipped."
        return resultado
    resultado.ok = all(p.ok for p in resultado.pasos)
    if not resultado.ok:
        resultado.detalle = "Database migration failed."
    else:
        resultado.detalle = json.dumps(check_update().a_dict(), ensure_ascii=False)
    return resultado
