"""Tests E2E de los endpoints REST de identidad (F5).

Cubre /identidad (whoami), CRUD de workspaces y miembros,
invitaciones owner-only y el canje público, y las llaves propias.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core import identidad
from src.core.identidad import (
    USUARIO_OWNER_ID,
    WORKSPACE_PERSONAL_ID,
    asegurar_bootstrap,
    emitir_key_usuario,
)
from src.storage.database import get_session
from src.storage.models import (
    ApiKeyModel,
    InvitacionModel,
    MiembroModel,
    UsuarioModel,
    WorkspaceModel,
)
from tests.integration.conftest import TEST_DB_URL


@pytest.fixture(autouse=True)
def _limpiar_cache() -> None:
    identidad._cache_ctx.clear()
    identidad.reiniciar_contexto_proceso()


async def _limpiar(engine: AsyncEngine, prefijo: str) -> None:
    """Borra usuarios/workspaces creados por el test."""
    async with get_session(engine) as s:
        us = (
            await s.execute(
                select(UsuarioModel.id).where(
                    UsuarioModel.handle.like(f"{prefijo}%")
                )
            )
        ).scalars().all()
        ws = (
            await s.execute(
                select(WorkspaceModel.id).where(
                    WorkspaceModel.slug.like(f"{prefijo}%")
                )
            )
        ).scalars().all()
        await s.execute(
            delete(ApiKeyModel).where(ApiKeyModel.usuario_id.in_(us))
        )
        await s.execute(
            delete(InvitacionModel).where(
                InvitacionModel.workspace_id.in_(ws)
            )
        )
        await s.execute(
            delete(MiembroModel).where(
                (MiembroModel.usuario_id.in_(us))
                | (MiembroModel.workspace_id.in_(ws))
            )
        )
        await s.execute(
            delete(WorkspaceModel).where(WorkspaceModel.id.in_(ws))
        )
        await s.execute(
            delete(UsuarioModel).where(UsuarioModel.id.in_(us))
        )
        await s.commit()


@pytest_asyncio.fixture
async def api() -> AsyncIterator[tuple[AsyncClient, AsyncEngine, str]]:
    """Cliente HTTP real contra la app con la DB de test.

    El token estático del .env actúa como owner del workspace
    personal; los tests emiten ``ns_u_*`` propias para otros roles.
    """
    import unittest.mock as mock

    from src.config import get_settings
    from src.main import crear_app_http
    from src.mcp import tools
    from src.storage import database

    engine = create_async_engine(TEST_DB_URL)
    await asegurar_bootstrap(engine)
    token_owner = get_settings().api_token or ""
    if not token_owner:
        # Sin token estático configurado el dueño no resuelve —
        # emitir una ns_u_* del owner y usarla como credencial.
        token_owner = await emitir_key_usuario(
            engine, USUARIO_OWNER_ID, WORKSPACE_PERSONAL_ID
        )
    patches = [
        mock.patch.object(tools, "obtener_engine", lambda: engine),
        mock.patch.object(database, "obtener_engine", lambda: engine),
        mock.patch("src.api.auth.obtener_engine", lambda: engine),
        mock.patch("src.api.routes.obtener_engine", lambda: engine),
    ]
    for p in patches:
        p.start()
    try:
        app = crear_app_http()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token_owner}"},
        ) as cliente:
            yield cliente, engine, token_owner
    finally:
        for p in patches:
            p.stop()
        await engine.dispose()


async def _usuario_con_key(
    engine: AsyncEngine,
    handle: str,
    ws_id: uuid.UUID,
    rol: str,
) -> str:
    """Crea usuario+membresía+key y devuelve la key en claro."""
    u = await identidad.crear_usuario(engine, handle, handle.title())
    async with get_session(engine) as s:
        s.add(
            MiembroModel(
                workspace_id=ws_id, usuario_id=u.id, rol=rol
            )
        )
        await s.commit()
    return await emitir_key_usuario(engine, u.id, ws_id)


@pytest.mark.asyncio
class TestIdentidad:
    """GET /identidad — whoami con workspaces y roles."""

    async def test_owner_ve_sus_workspaces(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, _e, _t = api
        r = await cliente.get("/api/identidad")
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["usuario"]["handle"] == "owner"
        assert cuerpo["workspace_activo"]["slug"] == "personal"
        assert cuerpo["workspace_activo"]["rol"] == "owner"
        slugs = [w["slug"] for w in cuerpo["workspaces"]]
        assert "personal" in slugs

    async def test_miembro_ve_solo_sus_workspaces(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            ws = await identidad.crear_workspace(
                engine, "api-privado", "Privado", USUARIO_OWNER_ID
            )
            key = await _usuario_con_key(
                engine, "api-u1", WORKSPACE_PERSONAL_ID, "member"
            )
            r = await cliente.get(
                "/api/identidad",
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r.status_code == 200
            slugs = [w["slug"] for w in r.json()["workspaces"]]
            assert slugs == ["personal"]
            assert ws.slug not in slugs
        finally:
            await _limpiar(engine, "api-")


@pytest.mark.asyncio
class TestWorkspacesYMmiembros:
    """CRUD de workspaces, roster y roles."""

    async def test_crear_workspace_deja_owner(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            r = await cliente.post(
                "/api/workspaces",
                json={"slug": "api-nuevo", "nombre": "Nuevo"},
            )
            assert r.status_code == 201
            assert r.json()["rol"] == "owner"
            r2 = await cliente.get("/api/workspaces/api-nuevo/miembros")
            assert r2.status_code == 200
            miembros = r2.json()
            assert len(miembros) == 1
            assert miembros[0]["handle"] == "owner"
        finally:
            await _limpiar(engine, "api-")

    async def test_miembros_sin_membresia_403(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            await identidad.crear_workspace(
                engine, "api-ajeno", "Ajeno", USUARIO_OWNER_ID
            )
            key = await _usuario_con_key(
                engine, "api-ext", WORKSPACE_PERSONAL_ID, "member"
            )
            r = await cliente.get(
                "/api/workspaces/api-ajeno/miembros",
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r.status_code == 403
        finally:
            await _limpiar(engine, "api-")

    async def test_member_no_puede_invitar(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            ws = await identidad.crear_workspace(
                engine, "api-wsinv", "WS", USUARIO_OWNER_ID
            )
            key = await _usuario_con_key(
                engine, "api-m", ws.id, "member"
            )
            r = await cliente.post(
                "/api/workspaces/api-wsinv/invitaciones",
                json={"rol": "member"},
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r.status_code == 403
        finally:
            await _limpiar(engine, "api-")

    async def test_owner_invita_y_invitado_canjea(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            await identidad.crear_workspace(
                engine, "api-team", "Team", USUARIO_OWNER_ID
            )
            r = await cliente.post(
                "/api/workspaces/api-team/invitaciones",
                json={"rol": "viewer", "dias": 3},
            )
            assert r.status_code == 201
            token = r.json()["token"]
            assert token.startswith("ns_inv_")
            # Canje público — sin Bearer.
            r2 = await cliente.post(
                "/api/invitaciones/canjear",
                json={
                    "token": token,
                    "handle": "api-invitado",
                    "nombre": "Invitado",
                },
                headers={"Authorization": ""},
            )
            assert r2.status_code == 201
            key = r2.json()["key"]
            # La key nueva funciona y ve el workspace.
            r3 = await cliente.get(
                "/api/identidad",
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r3.status_code == 200
            cuerpo = r3.json()
            assert cuerpo["workspace_activo"]["slug"] == "api-team"
            assert cuerpo["workspace_activo"]["rol"] == "viewer"
            # Un solo uso: re-canjear falla.
            r4 = await cliente.post(
                "/api/invitaciones/canjear",
                json={
                    "token": token,
                    "handle": "api-otro",
                    "nombre": "Otro",
                },
                headers={"Authorization": ""},
            )
            assert r4.status_code == 422
        finally:
            await _limpiar(engine, "api-")

    async def test_quitar_y_degradar_miembro(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            ws = await identidad.crear_workspace(
                engine, "api-roles", "Roles", USUARIO_OWNER_ID
            )
            key = await _usuario_con_key(
                engine, "api-m2", ws.id, "member"
            )
            identidad._cache_ctx.clear()
            uid = (
                await identidad.listar_miembros(engine, ws.id)
            )
            m2 = next(m for m in uid if m.handle == "api-m2")
            # Degradar a viewer.
            r = await cliente.patch(
                f"/api/workspaces/api-roles/miembros/{m2.usuario_id}",
                json={"rol": "viewer"},
            )
            assert r.status_code == 204
            identidad._cache_ctx.clear()
            # Ahora como viewer no puede escribir.
            r2 = await cliente.post(
                "/api/eventos",
                json={"contenido": "x", "workspace": "api-roles"},
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r2.status_code == 403
            # Quitar del todo.
            r3 = await cliente.delete(
                f"/api/workspaces/api-roles/miembros/{m2.usuario_id}"
            )
            assert r3.status_code == 204
            identidad._cache_ctx.clear()
            # Sin membresías la key ya no resuelve contexto.
            r4 = await cliente.get(
                "/api/identidad",
                headers={"Authorization": f"Bearer {key}"},
            )
            assert r4.status_code == 401
        finally:
            await _limpiar(engine, "api-")

    async def test_ultimo_owner_protegido(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            await identidad.crear_workspace(
                engine, "api-solo", "Solo", USUARIO_OWNER_ID
            )
            r = await cliente.delete(
                f"/api/workspaces/api-solo/miembros/{USUARIO_OWNER_ID}"
            )
            assert r.status_code == 422
        finally:
            await _limpiar(engine, "api-")


@pytest.mark.asyncio
class TestLlaves:
    """CRUD de llaves propias."""

    async def test_crear_listar_revocar(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            key_u = await _usuario_con_key(
                engine, "api-k", WORKSPACE_PERSONAL_ID, "member"
            )
            cab = {"Authorization": f"Bearer {key_u}"}
            r = await cliente.post(
                "/api/llaves", json={"nombre": "laptop"}, headers=cab
            )
            assert r.status_code == 201
            nueva = r.json()["key"]
            assert nueva.startswith("ns_u_")
            r2 = await cliente.get("/api/llaves", headers=cab)
            assert r2.status_code == 200
            assert len(r2.json()) == 2  # la del fixture + laptop
            # La nueva key también autentica.
            r3 = await cliente.get(
                "/api/identidad",
                headers={"Authorization": f"Bearer {nueva}"},
            )
            assert r3.status_code == 200
            # Revocar por UUID.
            kid = next(
                k["id"]
                for k in r2.json()
                if k["nombre"] == "laptop"
            )
            r4 = await cliente.delete(f"/api/llaves/{kid}", headers=cab)
            assert r4.status_code == 204
            r5 = await cliente.get(
                "/api/identidad",
                headers={"Authorization": f"Bearer {nueva}"},
            )
            assert r5.status_code in (401, 403)
        finally:
            await _limpiar(engine, "api-")

    async def test_no_revoca_llave_ajena(
        self, api: tuple[AsyncClient, AsyncEngine, str]
    ) -> None:
        cliente, engine, _t = api
        try:
            key_a = await _usuario_con_key(
                engine, "api-ka", WORKSPACE_PERSONAL_ID, "member"
            )
            key_b = await _usuario_con_key(
                engine, "api-kb", WORKSPACE_PERSONAL_ID, "member"
            )
            llaves_a = await identidad.listar_keys(
                engine,
                (
                    await identidad.resolver_contexto(engine, key_a)
                ).usuario_id,  # type: ignore[union-attr]
            )
            r = await cliente.delete(
                f"/api/llaves/{llaves_a[0].id}",
                headers={"Authorization": f"Bearer {key_b}"},
            )
            assert r.status_code == 404
        finally:
            await _limpiar(engine, "api-")
