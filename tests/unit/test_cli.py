"""Tests unitarios de la CLI de Nous (parsing y despacho).

La lógica de cada comando se mockea donde toca I/O; lo que se valida
aquí es el contrato de la CLI: flags, salidas JSON, códigos de salida.
"""

import json
from pathlib import Path

import pytest

from src.cli import main as cli
from src.cli import upgrade as up


def _salida(capsys: pytest.CaptureFixture[str]) -> str:
    """Lee lo impreso en stdout por el comando ejecutado."""
    return capsys.readouterr().out


class TestParser:
    """Contrato del parser de argumentos."""

    def test_comando_desconocido_sale_con_codigo_dos(self) -> None:
        """Un subcomando inválido aborta con exit code 2 (argparse)."""
        with pytest.raises(SystemExit) as exc:
            cli.main(["noexiste"])
        assert exc.value.code == 2

    def test_sin_argumentos_muestra_ayuda(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`nous` sin argumentos imprime la ayuda y retorna 0."""
        assert cli.main([]) == 0
        assert "serve" in _salida(capsys)


class TestVersion:
    """``nous version``."""

    def test_version_texto(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Imprime versión, método y python en formato legible."""
        assert cli.main(["version"]) == 0
        assert "nous" in _salida(capsys)

    def test_version_json_valido(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--json emite un objeto con las tres claves."""
        assert cli.main(["version", "--json"]) == 0
        datos = json.loads(_salida(capsys))
        assert set(datos) == {"version", "metodo_instalacion", "python"}

    def test_flag_version_global(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`nous --version` equivale a `nous version`."""
        assert cli.main(["--version"]) == 0
        assert "nous" in _salida(capsys)


class TestCheckUpdate:
    """``nous check-update``."""

    def _resultado(self, disponible: bool | None) -> up.ResultadoCheckUpdate:
        """Fabrica un ResultadoCheckUpdate determinista."""
        return up.ResultadoCheckUpdate(
            "0.1.0", "fuente", "v0.2.0", disponible, "nous upgrade",
            None, "detalle",
        )

    def test_json_emite_contrato(
        self, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--json serializa el ResultadoCheckUpdate completo."""
        monkeypatch.setattr(
            cli.up, "check_update", lambda: self._resultado(True)
        )
        assert cli.main(["check-update", "--json"]) == 0
        datos = json.loads(_salida(capsys))
        assert datos["actualizacion_disponible"] is True

    def test_texto_sin_update(
        self, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sin update imprime el detalle, no el comando."""
        monkeypatch.setattr(
            cli.up, "check_update", lambda: self._resultado(False)
        )
        assert cli.main(["check-update"]) == 0
        assert "detalle" in _salida(capsys)


class TestUpgrade:
    """``nous upgrade``."""

    def _ya_actualizado(self) -> up.ResultadoCheckUpdate:
        """check_update falso sin update disponible."""
        return up.ResultadoCheckUpdate(
            "1", "fuente", "1", False, "", None, ""
        )

    def _con_update(self) -> up.ResultadoCheckUpdate:
        """check_update falso con update disponible."""
        return up.ResultadoCheckUpdate(
            "1", "fuente", "2", True, "", None, ""
        )

    def test_ya_actualizado_no_ejecuta(
        self, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Si no hay update, upgrade no ejecuta pasos."""
        def no_llamar(**kw: object) -> None:
            raise AssertionError("upgrade no debió ejecutarse")

        monkeypatch.setattr(cli.up, "check_update", self._ya_actualizado)
        monkeypatch.setattr(cli.up, "upgrade", no_llamar)
        assert cli.main(["upgrade", "--yes"]) == 0
        assert "reciente" in _salida(capsys)

    def test_ejecuta_y_reporta_pasos(
        self, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Con update disponible corre el flujo e imprime cada paso."""
        monkeypatch.setattr(cli.up, "check_update", self._con_update)
        monkeypatch.setattr(
            cli.up, "upgrade",
            lambda *, no_migrate: up.ResultadoUpgrade(
                ok=True, metodo="fuente",
                pasos=[up.PasoUpgrade("git pull", True)],
                detalle="listo",
            ),
        )
        assert cli.main(["upgrade", "--yes"]) == 0
        assert "git pull" in _salida(capsys)

    def test_fallido_retorna_uno(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Un upgrade fallido retorna código de salida 1."""
        monkeypatch.setattr(cli.up, "check_update", self._con_update)
        monkeypatch.setattr(
            cli.up, "upgrade",
            lambda *, no_migrate: up.ResultadoUpgrade(
                ok=False, metodo="fuente", detalle="falló"
            ),
        )
        assert cli.main(["upgrade", "--yes"]) == 1


class TestDoctor:
    """``nous doctor``."""

    def test_db_caida_retorna_uno(
        self, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Si la DB no responde, doctor falla con código 1."""
        async def diagnostico() -> dict[str, object]:
            return {"base_de_datos": "error: X", "version": "0.1.0"}

        monkeypatch.setattr(cli, "_diagnostico", diagnostico)
        assert cli.main(["doctor", "--json"]) == 1
        assert json.loads(_salida(capsys))["version"] == "0.1.0"


class TestStats:
    """``nous stats``."""

    def test_stats_json(
        self, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json emite total y desglose por dimensión."""
        async def stats() -> dict[str, object]:
            return {
                "total": 5,
                "por_dimension": {"cognitiva": 3, "somatica": 2},
                "primer_evento": 1,
                "ultimo_evento": 2,
            }

        monkeypatch.setattr(cli, "_stats", stats)
        assert cli.main(["stats", "--json"]) == 0
        assert json.loads(_salida(capsys))["total"] == 5


class TestImport:
    """``nous import`` validaciones previas a la DB."""

    def test_archivo_inexistente_falla(
        self, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Un archivo que no existe retorna 1 sin tocar la DB."""
        falta = tmp_path / "no.jsonl"
        assert cli.main(["import", str(falta)]) == 1
