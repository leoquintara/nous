"""Tests de resolución de identidad multi-tenant (F2).

Cubre resolver_contexto (token estático → owner, ns_u_* → usuario,
revocada → None), el fallback de workspace default, la caché y el
contexto de proceso stdio.

Los datos que debe ver ``resolver_contexto`` se crean con commits
reales (el resolutor abre su propia sesión) y se eliminan en el
``finally`` de cada test — el fixture ``session`` de rollback no
sirve aquí.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core import identidad
from src.core.identidad import (
    USUARIO_OWNER_ID,
    WORKSPACE_PERSONAL_ID,
    Contexto,
    asegurar_bootstrap,
    contexto_actual,
    generar_key_usuario,
    hash_key,
    resolver_contexto,
)
from src.storage.database import get_session
from src.storage.models import (
    ApiKeyModel,
    EventoModel,
    MiembroModel,
    UsuarioModel,
    WorkspaceModel,
)
from tests.integration.conftest import TEST_DB_URL


@pytest.fixture(autouse=True)
def _limpiar_cache_y_ctx() -> None:
    """Aísla cada test de la caché de resolución y del ctx de proceso."""
    identidad._cache_ctx.clear()
    identidad.reiniciar_contexto_proceso()
    identidad.fijar_contexto(None)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Engine dedicado por test con bootstrap garantizado."""
    eng = create_async_engine(TEST_DB_URL)
    try:
        await asegurar_bootstrap(eng)
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def escenario(
    engine: AsyncEngine,
) -> AsyncIterator[dict[str, object]]:
    """Usuario member+viewer, workspace extra, keys — con limpieza.

    Returns:
        Dict con usuario, viewer, ws_b, key_member, key_viewer.
        Todo lo creado se borra al cerrar el fixture.
    """
    datos: dict[str, object] = {}
    async with get_session(engine) as s:
        member = UsuarioModel(handle="t-member", nombre="Member")
        viewer = UsuarioModel(handle="t-viewer", nombre="Viewer")
        ws_b = WorkspaceModel(
            slug="t-ws-b", nombre="WS B", owner_id=USUARIO_OWNER_ID
        )
        s.add_all([member, viewer, ws_b])
        await s.flush()
        s.add_all(
            [
                MiembroModel(
                    workspace_id=WORKSPACE_PERSONAL_ID,
                    usuario_id=member.id,
                    rol="member",
                ),
                MiembroModel(
                    workspace_id=ws_b.id,
                    usuario_id=viewer.id,
                    rol="viewer",
                ),
            ]
        )
        key_member = generar_key_usuario()
        key_viewer = generar_key_usuario()
        s.add_all(
            [
                ApiKeyModel(
                    hash=hash_key(key_member),
                    prefijo=key_member[:11],
                    usuario_id=member.id,
                    workspace_default_id=WORKSPACE_PERSONAL_ID,
                ),
                ApiKeyModel(
                    hash=hash_key(key_viewer),
                    prefijo=key_viewer[:11],
                    usuario_id=viewer.id,
                    workspace_default_id=ws_b.id,
                ),
            ]
        )
        await s.commit()
        datos = {
            "member": member,
            "viewer": viewer,
            "ws_b": ws_b,
            "key_member": key_member,
            "key_viewer": key_viewer,
        }
    yield datos
    async with get_session(engine) as s:
        for modelo, col in (
            (ApiKeyModel, ApiKeyModel.usuario_id),
            (MiembroModel, MiembroModel.usuario_id),
        ):
            await s.execute(
                delete(modelo).where(
                    col.in_(
                        [
                            datos["member"].id,  # type: ignore[attr-defined]
                            datos["viewer"].id,  # type: ignore[attr-defined]
                        ]
                    )
                )
            )
        await s.execute(
            delete(WorkspaceModel).where(
                WorkspaceModel.id == datos["ws_b"].id  # type: ignore[attr-defined]
            )
        )
        await s.execute(
            delete(UsuarioModel).where(
                UsuarioModel.id.in_(
                    [
                        datos["member"].id,  # type: ignore[attr-defined]
                        datos["viewer"].id,  # type: ignore[attr-defined]
                    ]
                )
            )
        )
        await s.commit()


@pytest.mark.asyncio
class TestResolverContexto:
    """Resolución de credenciales a Contexto."""

    async def test_none_devuelve_owner(
        self, engine: AsyncEngine
    ) -> None:
        ctx = await resolver_contexto(engine, None)
        assert ctx is not None
        assert ctx.rol == "owner"
        assert ctx.usuario_id == USUARIO_OWNER_ID
        assert ctx.workspace_id == WORKSPACE_PERSONAL_ID

    async def test_token_estatico_devuelve_owner(
        self, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.config import get_settings

        monkeypatch.setenv("NOUS_API_TOKEN", "ns_" + "a" * 64)
        get_settings.cache_clear()
        try:
            ctx = await resolver_contexto(engine, "ns_" + "a" * 64)
            assert ctx is not None and ctx.rol == "owner"
            assert (
                await resolver_contexto(engine, "ns_" + "b" * 64)
            ) is None
        finally:
            get_settings.cache_clear()

    async def test_key_usuario_resuelve_rol_y_workspace(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = await resolver_contexto(
            engine, str(escenario["key_member"])
        )
        assert ctx is not None
        assert ctx.usuario_id == escenario["member"].id  # type: ignore[attr-defined]
        assert ctx.workspace_id == WORKSPACE_PERSONAL_ID
        assert ctx.rol == "member" and ctx.puede_escribir
        assert ctx.key_id is not None

    async def test_key_viewer_resuelve_su_workspace(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = await resolver_contexto(
            engine, str(escenario["key_viewer"])
        )
        assert ctx is not None
        assert ctx.workspace_id == escenario["ws_b"].id  # type: ignore[attr-defined]
        assert ctx.rol == "viewer"
        assert not ctx.puede_escribir

    async def test_key_revocada_no_resuelve(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        async with get_session(engine) as s:
            await s.execute(
                delete(ApiKeyModel).where(
                    ApiKeyModel.hash
                    == hash_key(str(escenario["key_member"]))
                )
            )
            await s.commit()
        identidad._cache_ctx.clear()
        assert (
            await resolver_contexto(
                engine, str(escenario["key_member"])
            )
            is None
        )

    async def test_key_sin_default_usa_primera_membresia(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        usuario = escenario["viewer"]
        key = generar_key_usuario()
        async with get_session(engine) as s:
            s.add(
                ApiKeyModel(
                    hash=hash_key(key),
                    prefijo=key[:11],
                    usuario_id=usuario.id,  # type: ignore[attr-defined]
                    workspace_default_id=None,
                )
            )
            await s.commit()
        try:
            ctx = await resolver_contexto(engine, key)
            assert ctx is not None
            assert ctx.workspace_id == escenario["ws_b"].id  # type: ignore[attr-defined]
            assert ctx.rol == "viewer"
        finally:
            async with get_session(engine) as s:
                await s.execute(
                    delete(ApiKeyModel).where(
                        ApiKeyModel.hash == hash_key(key)
                    )
                )
                await s.commit()

    async def test_key_sin_membresias_no_resuelve(
        self, engine: AsyncEngine
    ) -> None:
        usuario = UsuarioModel(handle="t-solo", nombre="Solo")
        key = generar_key_usuario()
        async with get_session(engine) as s:
            s.add(usuario)
            await s.flush()
            s.add(
                ApiKeyModel(
                    hash=hash_key(key),
                    prefijo=key[:11],
                    usuario_id=usuario.id,
                    workspace_default_id=None,
                )
            )
            await s.commit()
        try:
            assert await resolver_contexto(engine, key) is None
        finally:
            async with get_session(engine) as s:
                await s.execute(
                    delete(ApiKeyModel).where(
                        ApiKeyModel.hash == hash_key(key)
                    )
                )
                await s.execute(
                    delete(UsuarioModel).where(
                        UsuarioModel.id == usuario.id
                    )
                )
                await s.commit()

    async def test_key_en_workspace_ajeno_no_resuelve(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """Key apunta a un workspace donde el usuario NO es miembro."""
        member = escenario["member"]
        ws_b = escenario["ws_b"]
        key = generar_key_usuario()
        async with get_session(engine) as s:
            s.add(
                ApiKeyModel(
                    hash=hash_key(key),
                    prefijo=key[:11],
                    usuario_id=member.id,  # type: ignore[attr-defined]
                    workspace_default_id=ws_b.id,  # type: ignore[attr-defined]
                )
            )
            await s.commit()
        try:
            assert await resolver_contexto(engine, key) is None
        finally:
            async with get_session(engine) as s:
                await s.execute(
                    delete(ApiKeyModel).where(
                        ApiKeyModel.hash == hash_key(key)
                    )
                )
                await s.commit()

    async def test_formato_invalido_no_resuelve(
        self, engine: AsyncEngine
    ) -> None:
        assert await resolver_contexto(engine, "basura") is None
        assert await resolver_contexto(engine, "ns_inv_abc") is None

    async def test_cache_evita_segunda_consulta(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        key = str(escenario["key_member"])
        ctx1 = await resolver_contexto(engine, key)
        async with get_session(engine) as s:
            await s.execute(
                delete(ApiKeyModel).where(
                    ApiKeyModel.hash == hash_key(key)
                )
            )
            await s.commit()
        ctx2 = await resolver_contexto(engine, key)
        assert ctx1 == ctx2
        identidad._cache_ctx.clear()
        assert await resolver_contexto(engine, key) is None


@pytest.mark.asyncio
class TestContextoActual:
    """contexto_actual: request fijada vs identidad del proceso."""

    async def test_contexto_fijado_gana(
        self, engine: AsyncEngine
    ) -> None:
        esperado = Contexto(
            usuario_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            rol="viewer",
        )
        identidad.fijar_contexto(esperado)
        try:
            assert await contexto_actual(engine) == esperado
        finally:
            identidad.fijar_contexto(None)

    async def test_sin_key_de_proceso_devuelve_owner(
        self, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOUS_API_KEY", raising=False)
        ctx = await contexto_actual(engine)
        assert ctx.rol == "owner"
        assert ctx.usuario_id == USUARIO_OWNER_ID

    async def test_nous_api_key_resuelve_usuario(
        self, engine: AsyncEngine, escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NOUS_API_KEY", str(escenario["key_member"]))
        ctx = await contexto_actual(engine)
        assert ctx.usuario_id == escenario["member"].id  # type: ignore[attr-defined]
        assert ctx.rol == "member"

    async def test_nous_api_key_invalida_cae_a_owner(
        self, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUS_API_KEY", "ns_u_noexiste")
        ctx = await contexto_actual(engine)
        assert ctx.rol == "owner"


@pytest.mark.asyncio
class TestRolesEndToEnd:
    """Rol enforcement a través de la app ASGI real."""

    async def _cliente(
        self, engine: AsyncEngine, key: str
    ) -> "tuple[object, object]":
        """App real con la key de usuario como Bearer."""
        import unittest.mock as mock

        from httpx import ASGITransport, AsyncClient

        from src.main import crear_app_http
        from src.mcp import tools
        from src.storage import database

        # El engine singleton del proceso se redirige a la DB de test.
        self._patches = [
            mock.patch.object(tools, "obtener_engine", lambda: engine),
            mock.patch.object(
                tools, "crear_engine",
                lambda: create_async_engine(TEST_DB_URL),
            ),
            mock.patch.object(database, "obtener_engine", lambda: engine),
            mock.patch(
                "src.api.auth.obtener_engine", lambda: engine
            ),
        ]
        for p in self._patches:
            p.start()
        app = crear_app_http()
        cliente = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {key}"},
        )
        return cliente, app

    async def test_member_escribe_y_viewer_no(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        cliente, _ = await self._cliente(
            engine, str(escenario["key_viewer"])
        )
        try:
            # Viewer: GET pasa, POST devuelve 403.
            r_get = await cliente.get("/api/eventos")
            assert r_get.status_code == 200
            r_post = await cliente.post(
                "/api/eventos",
                json={
                    "contenido": "intento de viewer",
                    "dimension": "cognitiva",
                    "fuente": "test_mt",
                },
            )
            assert r_post.status_code == 403
        finally:
            await cliente.aclose()
            for p in self._patches:
                p.stop()

    async def test_member_escribe_ok(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        cliente, _ = await self._cliente(
            engine, str(escenario["key_member"])
        )
        try:
            r_post = await cliente.post(
                "/api/eventos",
                json={
                    "contenido": "evento del member",
                    "dimension": "cognitiva",
                    "fuente": "test_mt",
                },
            )
            assert r_post.status_code == 200
            # La escritura queda atribuida al member y su workspace.
            from sqlalchemy import delete, select

            async with get_session(engine) as s:
                fila = (
                    await s.execute(
                        select(EventoModel).where(
                            EventoModel.fuente == "test_mt"
                        )
                    )
                ).scalar_one()
                assert fila.autor_id == escenario["member"].id  # type: ignore[attr-defined]
                assert fila.workspace_id == WORKSPACE_PERSONAL_ID
            async with get_session(engine) as s:
                await s.execute(
                    delete(EventoModel).where(
                        EventoModel.fuente == "test_mt"
                    )
                )
                await s.commit()
        finally:
            await cliente.aclose()
            for p in self._patches:
                p.stop()
