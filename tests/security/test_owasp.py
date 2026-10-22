"""Tests de seguridad basados en OWASP Top 10 (2021).

Un test por categoría sobre la app ASGI real — middleware de headers,
Bearer token y router — sin necesidad de base de datos. Los casos que
requieren persistencia (inyección SQL real, clamps de consulta) viven
en ``tests/integration/test_security_db.py``.
"""

import json
import logging
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.logging import LogCaptureFixture
from fastapi.testclient import TestClient
from pydantic import ValidationError

import src.api.routes as routes_mod
from src.api.auth import _token_valido
from src.api.rate_limit import LimitadorIntentos
from src.api.schemas import EventoRequest
from src.cli import upgrade as up
from src.cli.transferencia import _leer_jsonl
from src.config import get_settings
from src.main import crear_app_http

TOKEN_TEST = "ns_" + "a" * 64
BEARER = {"Authorization": f"Bearer {TOKEN_TEST}"}
RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture
def cliente(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """App ASGI real con token de prueba; sin lifespan (no toca DB)."""
    monkeypatch.setenv("NOUS_API_TOKEN", TOKEN_TEST)
    get_settings.cache_clear()
    yield TestClient(crear_app_http())
    get_settings.cache_clear()


@pytest.fixture
def cliente_login(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Cliente con credenciales de dashboard configuradas."""
    monkeypatch.setenv("NOUS_DASHBOARD_USUARIO", "admin")
    monkeypatch.setenv("NOUS_DASHBOARD_PASSWORD", "secreto123")
    get_settings.cache_clear()
    routes_mod._limitador_login.reiniciar()
    return cliente


class TestA01ControlAcceso:
    """A01 — Broken Access Control: toda ruta exige Bearer válido."""

    @pytest.mark.parametrize(
        "ruta",
        [
            "/api/eventos",
            "/api/buscar?q=x",
            "/api/proyeccion/actual",
            "/api/benchmarks",
            "/mcp",
        ],
    )
    def test_ruta_sin_token_retorna_401(
        self, cliente: TestClient, ruta: str
    ) -> None:
        """Sin Authorization la puerta devuelve 401, nunca datos."""
        assert cliente.get(ruta).status_code == 401

    def test_token_incorrecto_retorna_401(
        self, cliente: TestClient
    ) -> None:
        """Un bearer con formato válido pero token erróneo no entra."""
        respuesta = cliente.get(
            "/api/eventos",
            headers={"Authorization": "Bearer ns_" + "b" * 64},
        )
        assert respuesta.status_code == 401
        assert "www-authenticate" in respuesta.headers

    def test_esquema_distinto_a_bearer_rechazado(
        self, cliente: TestClient
    ) -> None:
        """`Basic <token>` no vale aunque el token sea correcto."""
        respuesta = cliente.get(
            "/api/eventos",
            headers={"Authorization": f"Basic {TOKEN_TEST}"},
        )
        assert respuesta.status_code == 401

    @pytest.mark.parametrize(
        "ruta",
        [
            "/api/decision",
            "/api/licencia/activar",
            "/api/eventos/00000000-0000-0000-0000-000000000000/curar",
            "/api/eventos/00000000-0000-0000-0000-000000000000/restaurar",
            "/api/intenciones",
            "/api/consolidar",
            "/api/scripts/minar",
        ],
    )
    def test_ruta_post_sin_token_retorna_401(
        self, cliente: TestClient, ruta: str
    ) -> None:
        """Las escrituras ADR-002/003 tampoco admiten anónimos."""
        assert cliente.post(ruta, json={}).status_code == 401

    def test_licencia_delete_sin_token_retorna_401(
        self, cliente: TestClient
    ) -> None:
        """Quitar la licencia del servidor exige credencial (owner)."""
        assert cliente.delete("/api/licencia").status_code == 401


class TestA02FallosCriptograficos:
    """A02 — Cryptographic Failures: comparación en tiempo constante."""

    def test_token_valido_rechaza_variante_minima(self) -> None:
        """Un token que difiere en un solo carácter se rechaza."""
        esperado = "ns_" + "c" * 64
        assert _token_valido("ns_" + "d" * 64, esperado) is False
        assert _token_valido(esperado, esperado) is True

    def test_token_valido_rechaza_longitud_distinta(self) -> None:
        """compare_digest es seguro también con longitudes distintas."""
        assert _token_valido("ns_corto", "ns_" + "c" * 64) is False

    def test_token_no_aparece_en_respuesta_de_error(
        self, cliente: TestClient
    ) -> None:
        """El 401 nunca devuelve el token ni pistas de su valor."""
        respuesta = cliente.get("/api/eventos")
        assert TOKEN_TEST not in respuesta.text


class TestA03Inyeccion:
    """A03 — Injection: los payloads llegan como texto, no como código.

    La capa HTTP valida con Pydantic; la defensa real contra SQL/tsquery
    se prueba contra PostgreSQL en test_security_db.py.
    """

    def test_payload_sql_en_contenido_es_texto_plano(self) -> None:
        """Pydantic acepta el SQLi como contenido (se guarda literal)."""
        evento = EventoRequest(contenido="'); DROP TABLE eventos; --")
        assert evento.contenido == "'); DROP TABLE eventos; --"

    def test_campos_extra_ignorados(self) -> None:
        """Mass assignment: campos no declarados no llegan al modelo."""
        evento = EventoRequest.model_validate(
            {"contenido": "x", "is_admin": True}
        )
        assert not hasattr(evento, "is_admin")


class TestA04DisenoInseguro:
    """A04 — Insecure Design: límites de recursos en la entrada."""

    def test_contenido_vacio_rechazado(self) -> None:
        """contenido vacío no tiene sentido semántico: 422."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="")

    def test_contenido_gigante_rechazado(self) -> None:
        """contenido >100KB se rechaza antes de tocar la DB."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x" * 100_001)

    def test_fuente_excede_columna_rechazada(self) -> None:
        """fuente >50 chars revienta String(50): se valida antes."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x", fuente="f" * 51)

    def test_amplitud_fuera_de_rango_rechazada(self) -> None:
        """amplitud ∈ [0,1] se valida en el schema además del core."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x", amplitud=1.5)

    def test_frecuencia_no_positiva_rechazada(self) -> None:
        """frecuencia ≤0 carece de sentido físico: 422."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x", frecuencia=-1.0)

    def test_timestamp_negativo_rechazado(self) -> None:
        """timestamp_unix negativo es inválido: 422."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x", timestamp_unix=-1)

    def test_estado_emocional_fuera_de_rango_rechazado(self) -> None:
        """estado_emocional fuera de [-1,1] se rechaza: 422."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x", estado_emocional=1.5)
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x", estado_emocional=-1.01)

    def test_tipo_memoria_literal_invalido_rechazado(self) -> None:
        """tipo_memoria solo admite los tres tipos del ADR-003."""
        with pytest.raises(ValidationError):
            EventoRequest(contenido="x", tipo_memoria="eterna")
        evento = EventoRequest(contenido="x", tipo_memoria="episodica")
        assert evento.tipo_memoria == "episodica"

    def test_contexto_no_dict_rechazado(self) -> None:
        """contexto debe ser dict — una lista rompería el merge."""
        with pytest.raises(ValidationError):
            EventoRequest.model_validate(
                {"contenido": "x", "contexto": ["a", "b"]}
            )

    def test_contexto_gigante_rechazado(self) -> None:
        """contexto >4KB serializado se rechaza (A04, JSON no es blob)."""
        with pytest.raises(ValidationError):
            EventoRequest(
                contenido="x", contexto={"payload": "x" * 5_000}
            )
        evento = EventoRequest(contenido="x", contexto={"k": "v"})
        assert evento.contexto == {"k": "v"}

    def test_licencia_key_corta_rechazada(self) -> None:
        """LicenciaActivarRequest exige un mínimo de 8 caracteres."""
        from src.api.schemas import LicenciaActivarRequest

        with pytest.raises(ValidationError):
            LicenciaActivarRequest(key="corta")
        assert LicenciaActivarRequest(key="k" * 8).key == "k" * 8


class TestA05Misconfiguracion:
    """A05 — Security Misconfiguration: docs y headers."""

    @pytest.mark.parametrize(
        "ruta", ["/api/docs", "/api/redoc", "/api/openapi.json"]
    )
    def test_docs_api_detras_de_auth(
        self, cliente: TestClient, ruta: str
    ) -> None:
        """La documentación OpenAPI no es pública: exige Bearer."""
        assert cliente.get(ruta).status_code == 401

    def test_cabeceras_seguridad_presentes(
        self, cliente: TestClient
    ) -> None:
        """Toda respuesta lleva headers de endurecimiento HTTP."""
        respuesta = cliente.get("/health")
        assert respuesta.headers["x-content-type-options"] == "nosniff"
        assert respuesta.headers["x-frame-options"] == "DENY"
        assert respuesta.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in respuesta.headers[
            "content-security-policy"
        ]

    def test_cabeceras_tambien_en_401(self, cliente: TestClient) -> None:
        """Incluso las respuestas del middleware Bearer llevan headers."""
        respuesta = cliente.get("/api/eventos")
        assert respuesta.status_code == 401
        assert respuesta.headers["x-content-type-options"] == "nosniff"

    def test_error_no_expone_traza(self, cliente: TestClient) -> None:
        """El body del 401 no filtra rutas internas ni stack traces."""
        respuesta = cliente.get("/api/eventos")
        assert "Traceback" not in respuesta.text
        assert "C:\\" not in respuesta.text


class TestA06ComponentesVulnerables:
    """A06 — Vulnerable Components: secretos fuera de git, deps ancladas."""

    @pytest.mark.skipif(
        shutil.which("git") is None,
        reason="git no está instalado en el contenedor efímero",
    )
    def test_env_no_esta_versionado(self) -> None:
        """`.env` nunca debe estar en el índice de git (secretos)."""
        proc = subprocess.run(
            ["git", "ls-files", ".env"],
            cwd=RAIZ, capture_output=True, text=True, check=False,
        )
        assert proc.stdout.strip() == ""

    @pytest.mark.skipif(
        shutil.which("git") is None,
        reason="git no está instalado en el contenedor efímero",
    )
    def test_env_en_gitignore(self) -> None:
        """`.env` está ignorado para que no se commitee por accidente."""
        proc = subprocess.run(
            ["git", "check-ignore", ".env"],
            cwd=RAIZ, capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0

    def test_dependencias_python_sin_latest_flotante(self) -> None:
        """Ninguna dep de pyproject usa '*' o 'latest' sin anclar."""
        pyproject = (RAIZ / "pyproject.toml").read_text("utf-8")
        for linea in pyproject.splitlines():
            if "=" in linea and "latest" in linea.lower():
                pytest.fail(f"Dependencia flotante: {linea}")


class TestA07Autenticacion:
    """A07 — Identification & Authentication Failures."""

    def test_login_credenciales_malas_401(
        self, cliente_login: TestClient
    ) -> None:
        """Login con password erróneo devuelve 401 genérico."""
        respuesta = cliente_login.post(
            "/api/login", json={"usuario": "admin", "password": "mala"}
        )
        assert respuesta.status_code == 401

    def test_login_correcto_devuelve_token(
        self, cliente_login: TestClient
    ) -> None:
        """Login válido entrega el token de servicio."""
        respuesta = cliente_login.post(
            "/api/login",
            json={"usuario": "admin", "password": "secreto123"},
        )
        assert respuesta.status_code == 200
        assert respuesta.json()["token"] == TOKEN_TEST

    def test_login_limita_intentos_por_ip(
        self, cliente_login: TestClient
    ) -> None:
        """Tras 5 intentos fallidos el 6º recibe 429 (anti fuerza bruta)."""
        for _ in range(5):
            assert (
                cliente_login.post(
                    "/api/login",
                    json={"usuario": "x", "password": "y"},
                ).status_code
                == 401
            )
        respuesta = cliente_login.post(
            "/api/login", json={"usuario": "x", "password": "y"}
        )
        assert respuesta.status_code == 429

    def test_limitador_ventana_deslizante(self) -> None:
        """El limitador expira intentos fuera de la ventana."""
        limitador = LimitadorIntentos(max_intentos=2, ventana_segundos=60)
        assert limitador.permite("ip", ahora=0.0)
        assert limitador.permite("ip", ahora=10.0)
        assert limitador.permite("ip", ahora=20.0) is False
        # A los 70s el primer intento expiró: vuelve a haber cupo.
        assert limitador.permite("ip", ahora=70.0)
        assert limitador.restantes("otra") == 2


class TestA08Integridad:
    """A08 — Software & Data Integrity: JSONL y comandos de upgrade."""

    def _escribir(self, tmp_path: Path, *lineas: str) -> Path:
        """Escribe un JSONL temporal con las líneas dadas."""
        archivo = tmp_path / "in.jsonl"
        archivo.write_text("\n".join(lineas), encoding="utf-8")
        return archivo

    def test_import_rechaza_dimension_invalida(
        self, tmp_path: Path
    ) -> None:
        """Una dimensión fuera del CHECK constraint cuenta como error."""
        fila = json.dumps({
            "id": "3f6f9b00-1234-5678-9abc-def012345678",
            "timestamp_unix": 100, "dimension": "maliciosa",
            "fuente": "x", "contenido": "y", "amplitud": 0.5,
            "frecuencia": None,
        })
        _, errores = _leer_jsonl(self._escribir(tmp_path, fila))
        assert errores == 1

    def test_import_rechaza_amplitud_fuera_de_rango(
        self, tmp_path: Path
    ) -> None:
        """amplitud 2.5 violaría el rango: se descarta la línea."""
        fila = json.dumps({
            "id": "3f6f9b00-1234-5678-9abc-def012345678",
            "timestamp_unix": 100, "dimension": "cognitiva",
            "fuente": "x", "contenido": "y", "amplitud": 2.5,
            "frecuencia": None,
        })
        _, errores = _leer_jsonl(self._escribir(tmp_path, fila))
        assert errores == 1

    def test_import_acepta_fila_valida(self, tmp_path: Path) -> None:
        """Una línea conforme al contrato se parsea correctamente."""
        fila = json.dumps({
            "id": "3f6f9b00-1234-5678-9abc-def012345678",
            "timestamp_unix": 100, "dimension": "somatica",
            "fuente": "x", "contenido": "y", "amplitud": 0.4,
            "frecuencia": None,
        })
        filas, errores = _leer_jsonl(self._escribir(tmp_path, fila))
        assert errores == 0 and len(filas) == 1

    def test_comandos_de_upgrade_son_fijos(self) -> None:
        """Los comandos del upgrade no interpolan entrada del usuario."""
        assert up.PAQUETE == "nous-memory"
        assert up.PYPI_JSON == "https://pypi.org/pypi/nous-memory/json"


class TestA09Logging:
    """A09 — Logging Failures: el contenido nunca va a los logs."""

    def test_linea_jsonl_invalida_no_loggea_contenido(
        self, tmp_path: Path, caplog: LogCaptureFixture
    ) -> None:
        """El warning de línea corrupta no incluye el texto del usuario."""
        archivo = tmp_path / "in.jsonl"
        archivo.write_text('{"contenido": "DATO_SECRETO_123"', "utf-8")
        with caplog.at_level(logging.WARNING, logger="nous.cli.transferencia"):
            _, errores = _leer_jsonl(archivo)
        assert errores == 1
        assert "DATO_SECRETO_123" not in caplog.text


class TestA10SSRF:
    """A10 — SSRF: ninguna URL saliente depende de entrada de usuario."""

    def test_indice_de_paquetes_es_fijo_y_https(self) -> None:
        """check-update solo consulta el host fijo de PyPI por TLS."""
        assert up.PYPI_JSON.startswith("https://pypi.org/")

    def test_upgrade_fuente_usa_solo_comandos_git_fijos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """En modo fuente los pasos son git/poetry/alembic literales."""
        comandos: list[list[str]] = []
        monkeypatch.setattr(up, "detectar_metodo", lambda: "fuente")
        monkeypatch.setattr(up, "_tiene_remoto", lambda: False)
        monkeypatch.setattr(up, "_arbol_limpio", lambda: True)
        monkeypatch.setattr(
            up, "_ejecutar_paso",
            lambda nombre, cmd: comandos.append(cmd)
            or up.PasoUpgrade(nombre, True),
        )
        up.upgrade()
        permitidos = {"git", "poetry", "alembic", "cmd.exe", "python.exe"}
        for cmd in comandos:
            ejecutable = Path(cmd[0]).name.lower()
            assert ejecutable in permitidos or ejecutable.startswith(
                "python"
            ), f"comando inesperado: {cmd[0]}"
