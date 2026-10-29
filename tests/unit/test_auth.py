"""Tests de la autenticación por API token (Bearer)."""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api import auth
from src.api.auth import BearerTokenMiddleware, verificar_token

TOKEN_PRUEBA = "token-secreto-de-prueba"


def _app_protegida() -> FastAPI:
    """App FastAPI mínima con una ruta protegida y una abierta."""
    app = FastAPI()

    @app.get("/abierta")
    def abierta() -> dict[str, str]:
        return {"estado": "ok"}

    @app.get("/protegida", dependencies=[Depends(verificar_token)])
    def protegida() -> dict[str, str]:
        return {"estado": "ok"}

    return app


class TestDependenciaFastAPI:
    """Comportamiento de la dependencia verificar_token."""

    def test_sin_token_configurado_permite_acceso(self) -> None:
        with patch.object(auth, "token_configurado", return_value=None):
            respuesta = TestClient(_app_protegida()).get("/protegida")
        assert respuesta.status_code == 200

    def test_sin_header_devuelve_401(self) -> None:
        with patch.object(
            auth, "token_configurado", return_value=TOKEN_PRUEBA
        ):
            respuesta = TestClient(_app_protegida()).get("/protegida")
        assert respuesta.status_code == 401

    def test_token_incorrecto_devuelve_401(self) -> None:
        with patch.object(
            auth, "token_configurado", return_value=TOKEN_PRUEBA
        ):
            respuesta = TestClient(_app_protegida()).get(
                "/protegida",
                headers={"Authorization": "Bearer token-equivocado"},
            )
        assert respuesta.status_code == 401

    def test_token_correcto_permite_acceso(self) -> None:
        with patch.object(
            auth, "token_configurado", return_value=TOKEN_PRUEBA
        ):
            respuesta = TestClient(_app_protegida()).get(
                "/protegida",
                headers={"Authorization": f"Bearer {TOKEN_PRUEBA}"},
            )
        assert respuesta.status_code == 200

    def test_ruta_sin_dependencia_queda_abierta(self) -> None:
        with patch.object(
            auth, "token_configurado", return_value=TOKEN_PRUEBA
        ):
            respuesta = TestClient(_app_protegida()).get("/abierta")
        assert respuesta.status_code == 200


async def _app_interna(
    scope: dict[str, Any],
    receive: Callable[..., Awaitable[Any]],
    send: Callable[..., Awaitable[Any]],
) -> None:
    """App ASGI dummy que responde 200 OK."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _scope_http(
    autorizacion: str | None = None, path: str = "/api/events"
) -> dict[str, Any]:
    headers = []
    if autorizacion is not None:
        headers.append((b"authorization", autorizacion.encode()))
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers,
    }


class _Enviado:
    """Captura los mensajes ASGI enviados."""

    def __init__(self) -> None:
        self.mensajes: list[dict[str, Any]] = []

    async def __call__(self, mensaje: dict[str, Any]) -> None:
        self.mensajes.append(mensaje)


class TestMiddlewareASGI:
    """Comportamiento de BearerTokenMiddleware sobre la app MCP."""

    async def _ejecutar(
        self, scope: dict[str, Any], token: str | None
    ) -> list[dict[str, Any]]:
        enviado = _Enviado()
        app = BearerTokenMiddleware(_app_interna)
        with patch.object(auth, "token_configurado", return_value=token):
            await app(scope, lambda: None, enviado)  # type: ignore[arg-type]
        return enviado.mensajes

    @pytest.mark.asyncio
    async def test_sin_token_configurado_deja_pasar(self) -> None:
        mensajes = await self._ejecutar(_scope_http(), None)
        assert mensajes[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_sin_header_devuelve_401(self) -> None:
        mensajes = await self._ejecutar(_scope_http(), TOKEN_PRUEBA)
        assert mensajes[0]["status"] == 401
        assert b"www-authenticate" in dict(mensajes[0]["headers"])

    @pytest.mark.asyncio
    async def test_bearer_incorrecto_devuelve_401(self) -> None:
        scope = _scope_http("Bearer otro-token")
        mensajes = await self._ejecutar(scope, TOKEN_PRUEBA)
        assert mensajes[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_bearer_correcto_deja_pasar(self) -> None:
        scope = _scope_http(f"Bearer {TOKEN_PRUEBA}")
        mensajes = await self._ejecutar(scope, TOKEN_PRUEBA)
        assert mensajes[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_scope_no_http_deja_pasar(self) -> None:
        mensajes = await self._ejecutar({"type": "lifespan"}, TOKEN_PRUEBA)
        assert mensajes[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_dashboard_estatico_es_publico(self) -> None:
        """Las rutas fuera de /api y /mcp sirven UI sin datos: abiertas."""
        for path in ("/", "/universo", "/_next/static/x.js", "/favicon.ico"):
            mensajes = await self._ejecutar(
                _scope_http(path=path), TOKEN_PRUEBA
            )
            assert mensajes[0]["status"] == 200, path

    @pytest.mark.asyncio
    async def test_mcp_exige_token(self) -> None:
        mensajes = await self._ejecutar(
            _scope_http(path="/mcp"), TOKEN_PRUEBA
        )
        assert mensajes[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_canje_invitacion_es_publico(self) -> None:
        """El canje de invitaciones no exige Bearer — el ns_inv_ lo es."""
        mensajes = await self._ejecutar(
            _scope_http(path="/api/invitations/redeem"), TOKEN_PRUEBA
        )
        assert mensajes[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_login_es_publico(self) -> None:
        mensajes = await self._ejecutar(
            _scope_http(path="/api/login"), TOKEN_PRUEBA
        )
        assert mensajes[0]["status"] == 200


class TestRutasPublicas:
    """La allowlist del middleware no puede apuntar a rutas inexistentes.

    Regresión: al renombrar el contrato REST, ``/api/invitaciones/canjear``
    quedó permitida pero la ruta real era ``/api/invitations/redeem`` —
    el canje público recibía 401.
    """

    def test_toda_ruta_publica_existe_en_el_router(self) -> None:
        from src.api.routes import router

        registradas = {r.path for r in router.routes}
        for path in auth.RUTAS_PUBLICAS:
            if not path.startswith("/api/"):
                continue  # /health vive en la app raíz, no en el router
            subruta = path[len("/api") :]
            assert subruta in registradas, (
                f"RUTAS_PUBLICAS permite {path} pero el router no "
                f"registra {subruta}"
            )

    def test_no_hay_rutas_publicas_espanolas(self) -> None:
        for path in auth.RUTAS_PUBLICAS:
            assert "invitaciones" not in path
            assert "canjear" not in path


class TestPrefijoToken:
    """Validación del prefijo ns_ en el token configurado."""

    def test_token_sin_prefijo_rechazado(self) -> None:
        from src.config import Settings

        with pytest.raises(Exception, match="ns_"):
            Settings(api_token="token-sin-prefijo")

    def test_token_con_prefijo_aceptado(self) -> None:
        from src.config import Settings

        assert Settings(api_token="ns_abc123").api_token == "ns_abc123"

    def test_token_vacio_aceptado(self) -> None:
        from src.config import Settings

        assert Settings(api_token=None).api_token is None
