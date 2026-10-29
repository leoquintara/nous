"""Tests unitarios del soporte de upgrade de versiones.

Toda la I/O (git, pip, httpx, alembic) se mockea: estos tests nunca
tocan la red, el repositorio real ni la base de datos.
"""

import subprocess
from pathlib import Path
from typing import cast

import pytest

from src.cli import upgrade as up


def _proc(salida: str = "", codigo: int = 0) -> subprocess.CompletedProcess[str]:
    """Fabrica un CompletedProcess simulado."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=codigo, stdout=salida, stderr=""
    )


class TestVersiones:
    """Comparación de versiones semver-like."""

    def test_es_mas_nueva_compara_componentes_numericos(self) -> None:
        """1.10.0 es más nueva que 1.9.9 (comparación numérica, no léxica)."""
        assert up.es_mas_nueva("1.10.0", "1.9.9") is True

    def test_es_mas_nueva_igual_retorna_falso(self) -> None:
        """La misma versión no es más nueva."""
        assert up.es_mas_nueva("0.1.0", "0.1.0") is False

    def test_es_mas_nueva_tolera_prefijo_v(self) -> None:
        """Los tags 'v1.2.0' comparan igual que '1.2.0'."""
        assert up.es_mas_nueva("v1.2.0", "1.1.0") is True

    def test_version_actual_retorna_cadena_semver(self) -> None:
        """La versión instalada tiene forma x.y[.z]."""
        assert up._partes_version(up.version_actual())


class TestDetectarMetodo:
    """Detección del método de instalación."""

    def test_checkout_fuente_detecta_fuente(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """pyproject.toml + .git → método 'fuente'."""
        (tmp_path / "pyproject.toml").touch()
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(up, "RAIZ_PROYECTO", tmp_path)
        assert up.detectar_metodo() == "fuente"

    def test_sin_git_detecta_paquete(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Sin .git el método detectado es 'paquete'."""
        (tmp_path / "pyproject.toml").touch()
        monkeypatch.setattr(up, "RAIZ_PROYECTO", tmp_path)
        assert up.detectar_metodo() == "paquete"


class TestCheckUpdateFuente:
    """check-update en un checkout fuente."""

    def test_sin_remoto_reporta_indeterminado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sin remoto git no se puede determinar si hay update."""
        monkeypatch.setattr(up, "_tiene_remoto", lambda: False)
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        resultado = up.check_update()
        assert resultado.actualizacion_disponible is None
        assert resultado.metodo == "fuente"
        assert "without remote" in resultado.detalle

    def test_remoto_con_commits_pendientes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commits por detrás del upstream → update disponible."""
        monkeypatch.setattr(up, "_tiene_remoto", lambda: True)
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(
            up, "_versiones_remotas_fuente", lambda: (3, "v0.2.0")
        )
        resultado = up.check_update()
        assert resultado.actualizacion_disponible is True
        assert resultado.version_reciente == "v0.2.0"
        assert "3 commit" in resultado.detalle

    def test_remoto_inalcanzable_reporta_indeterminado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fetch fallido → indeterminado, nunca falso positivo."""
        monkeypatch.setattr(up, "_tiene_remoto", lambda: True)
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(
            up, "_versiones_remotas_fuente", lambda: (-1, None)
        )
        resultado = up.check_update()
        assert resultado.actualizacion_disponible is None

    def test_al_dia_reporta_sin_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cero commits por detrás → no hay actualización."""
        monkeypatch.setattr(up, "_tiene_remoto", lambda: True)
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(
            up, "_versiones_remotas_fuente", lambda: (0, "v0.1.0")
        )
        assert up.check_update().actualizacion_disponible is False


class TestCheckUpdatePaquete:
    """check-update en una instalación de paquete."""

    def test_indice_con_version_mas_nueva(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Versión del índice mayor → update disponible."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "paquete")
        monkeypatch.setattr(up, "version_actual", lambda: "0.1.0")
        monkeypatch.setattr(
            up, "_version_mas_reciente_paquete", lambda: ("0.2.0", False)
        )
        resultado = up.check_update()
        assert resultado.actualizacion_disponible is True
        assert "pip install" in resultado.comando_upgrade

    def test_indice_inalcanzable_reporta_indeterminado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Índice caído → indeterminado."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "paquete")
        monkeypatch.setattr(
            up, "_version_mas_reciente_paquete", lambda: (None, False)
        )
        resultado = up.check_update()
        assert resultado.actualizacion_disponible is None

    def test_homonimo_pypi_no_sugiere_pip_upgrade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'nous' en PyPI ajeno → sin comparación ni pip upgrade."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "paquete")
        monkeypatch.setattr(
            up, "_version_mas_reciente_paquete", lambda: (None, True)
        )
        resultado = up.check_update()
        assert resultado.actualizacion_disponible is None
        assert "pip install --upgrade" not in resultado.comando_upgrade


class TestUpgrade:
    """Flujo completo de upgrade con subprocess mockeado."""

    def _fabrica_git(
        self, mapa: dict[tuple[str, ...], subprocess.CompletedProcess[str]]
    ):
        """Devuelve un _git falso indexado por argumentos."""
        def falso_git(*args: str) -> subprocess.CompletedProcess[str]:
            return mapa.get(tuple(args), _proc())
        return falso_git

    def test_arbol_sucio_aborta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cambios sin commit → aborta antes de tocar nada."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(
            up, "_git",
            self._fabrica_git({("status", "--porcelain"): _proc(" M x")}),
        )
        resultado = up.upgrade()
        assert resultado.ok is False
        assert resultado.pasos == []
        assert "cambios" in resultado.detalle

    def test_sin_remoto_omite_pull_y_migra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sin remoto: pull omitido, deps + migraciones sí corren."""
        ejecutados: list[list[str]] = []
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(up, "_tiene_remoto", lambda: False)
        monkeypatch.setattr(up, "_arbol_limpio", lambda: True)
        monkeypatch.setattr(
            up, "_ejecutar_paso",
            lambda nombre, cmd: ejecutados.append(cmd)
            or up.PasoUpgrade(nombre, True),
        )
        resultado = up.upgrade()
        assert resultado.ok is True
        assert resultado.pasos[0].salida == "skipped: no remote"
        nombres = [p.nombre for p in resultado.pasos]
        assert any("alembic" in n for n in nombres)

    def test_pull_fallido_detiene_upgrade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """git pull fallido aborta sin tocar deps ni DB."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(up, "_tiene_remoto", lambda: True)
        monkeypatch.setattr(up, "_arbol_limpio", lambda: True)
        monkeypatch.setattr(
            up, "_ejecutar_paso",
            lambda nombre, cmd: up.PasoUpgrade(nombre, False),
        )
        resultado = up.upgrade()
        assert resultado.ok is False
        assert len(resultado.pasos) == 1

    def test_no_migrate_omite_alembic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--no-migrate omite el paso de migraciones."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(up, "_tiene_remoto", lambda: False)
        monkeypatch.setattr(up, "_arbol_limpio", lambda: True)
        monkeypatch.setattr(
            up, "_ejecutar_paso",
            lambda nombre, cmd: up.PasoUpgrade(nombre, True),
        )
        resultado = up.upgrade(no_migrate=True)
        assert resultado.ok is True
        assert not any("alembic" in p.nombre for p in resultado.pasos)

    def test_paquete_usa_pip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """En modo paquete el paso de código es pip install -U."""
        comandos: list[list[str]] = []
        monkeypatch.setattr(up, "detectar_metodo", lambda: "paquete")
        monkeypatch.setattr(
            up, "_version_mas_reciente_paquete", lambda: ("0.2.0", False)
        )
        monkeypatch.setattr(
            up, "_ejecutar_paso",
            lambda nombre, cmd: comandos.append(cmd)
            or up.PasoUpgrade(nombre, True),
        )
        resultado = up.upgrade(no_migrate=True)
        assert resultado.ok is True
        assert "pip" in comandos[0] and "--upgrade" in comandos[0]

    def test_paquete_homonimo_aborta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Homónimo en PyPI → upgrade aborta sin tocar pip."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "paquete")
        monkeypatch.setattr(
            up, "_version_mas_reciente_paquete", lambda: (None, True)
        )
        resultado = up.upgrade(no_migrate=True)
        assert resultado.ok is False
        assert "PyPI" in resultado.detalle
        assert resultado.pasos == []

    def test_migracion_fallida_reporta_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fallo en alembic → upgrade marcado como fallido."""
        monkeypatch.setattr(up, "detectar_metodo", lambda: "paquete")
        monkeypatch.setattr(
            up, "_version_mas_reciente_paquete", lambda: ("0.2.0", False)
        )
        monkeypatch.setattr(
            up, "_ejecutar_paso",
            lambda nombre, cmd: up.PasoUpgrade(
                nombre, "alembic" not in nombre
            ),
        )
        resultado = up.upgrade()
        assert resultado.ok is False
        assert "migration" in resultado.detalle


class TestSerializacion:
    """Contrato JSON de los resultados (usado por --json)."""

    def test_check_update_a_dict_claves(self) -> None:
        """El dict contiene todas las claves del contrato."""
        claves = up.ResultadoCheckUpdate(
            "0.1.0", "fuente", "v0.2.0", True, "cmd", None, "d"
        ).a_dict()
        assert claves["version_actual"] == "0.1.0"
        assert claves["actualizacion_disponible"] is True

    def test_upgrade_a_dict_incluye_pasos(self) -> None:
        """El resultado serializa cada paso con su estado."""
        resultado = up.ResultadoUpgrade(
            ok=True, metodo="fuente",
            pasos=[up.PasoUpgrade("pull", True, "ok")],
        )
        datos = resultado.a_dict()
        pasos = cast("list[dict[str, object]]", datos["pasos"])
        assert pasos[0]["nombre"] == "pull"
