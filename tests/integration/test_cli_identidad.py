"""Tests de gestión de identidad (F4): usuarios, workspaces, keys,
invitaciones y el flujo de canje — funciones de ``src.core.identidad``
que la CLI y el dashboard comparten.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core import identidad
from src.core.exceptions import ErrorValidacion
from src.core.identidad import (
    USUARIO_OWNER_ID,
    WORKSPACE_PERSONAL_ID,
    asegurar_bootstrap,
    canjear_invitacion,
    crear_invitacion,
    crear_usuario,
    crear_workspace,
    emitir_key_usuario,
    listar_keys,
    listar_workspaces,
    resolver_contexto,
    revocar_key_usuario,
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
    """La caché de resolución no debe cruzar entre tests."""
    identidad._cache_ctx.clear()
    identidad.reiniciar_contexto_proceso()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Engine dedicado con bootstrap garantizado."""
    eng = create_async_engine(TEST_DB_URL)
    try:
        await asegurar_bootstrap(eng)
        yield eng
    finally:
        await eng.dispose()


async def _limpiar(engine: AsyncEngine, prefijo: str) -> None:
    """Borra todo lo creado con handles/slug que empiezan por prefijo."""
    async with get_session(engine) as s:
        usuarios = (
            await s.execute(
                select(UsuarioModel.id).where(
                    UsuarioModel.handle.like(f"{prefijo}%")
                )
            )
        ).scalars().all()
        ws_ids = (
            await s.execute(
                select(WorkspaceModel.id).where(
                    WorkspaceModel.slug.like(f"{prefijo}%")
                )
            )
        ).scalars().all()
        if usuarios:
            await s.execute(
                delete(ApiKeyModel).where(
                    ApiKeyModel.usuario_id.in_(usuarios)
                )
            )
            await s.execute(
                delete(InvitacionModel).where(
                    InvitacionModel.canjeada_por.in_(usuarios)
                )
            )
            await s.execute(
                delete(MiembroModel).where(
                    MiembroModel.usuario_id.in_(usuarios)
                )
            )
        if ws_ids:
            await s.execute(
                delete(InvitacionModel).where(
                    InvitacionModel.workspace_id.in_(ws_ids)
                )
            )
            await s.execute(
                delete(MiembroModel).where(
                    MiembroModel.workspace_id.in_(ws_ids)
                )
            )
            await s.execute(
                delete(WorkspaceModel).where(
                    WorkspaceModel.id.in_(ws_ids)
                )
            )
        if usuarios:
            await s.execute(
                delete(UsuarioModel).where(UsuarioModel.id.in_(usuarios))
            )
        await s.commit()


@pytest.mark.asyncio
class TestCrearUsuario:
    """crear_usuario: validación de handle y unicidad."""

    async def test_crea_usuario_ok(self, engine: AsyncEngine) -> None:
        u = await crear_usuario(engine, "cli-user1", "User Uno")
        try:
            assert u.handle == "cli-user1"
            assert u.id is not None
        finally:
            await _limpiar(engine, "cli-user1")

    async def test_handle_duplicado_rechaza(
        self, engine: AsyncEngine
    ) -> None:
        await crear_usuario(engine, "cli-dup", "Dup")
        try:
            with pytest.raises(ErrorValidacion):
                await crear_usuario(engine, "cli-dup", "Dup2")
        finally:
            await _limpiar(engine, "cli-dup")

    async def test_handle_invalido_rechaza(
        self, engine: AsyncEngine
    ) -> None:
        with pytest.raises(ErrorValidacion):
            await crear_usuario(engine, "MAYUSCULAS!", "Bad")


@pytest.mark.asyncio
class TestCrearWorkspace:
    """crear_workspace: slug único + membresía owner automática."""

    async def test_crea_ws_y_membresia_owner(
        self, engine: AsyncEngine
    ) -> None:
        ws = await crear_workspace(
            engine, "cli-ws1", "WS Uno", USUARIO_OWNER_ID
        )
        try:
            async with get_session(engine) as s:
                rol = (
                    await s.execute(
                        select(MiembroModel.rol).where(
                            MiembroModel.workspace_id == ws.id,
                            MiembroModel.usuario_id == USUARIO_OWNER_ID,
                        )
                    )
                ).scalar_one()
            assert rol == "owner"
        finally:
            await _limpiar(engine, "cli-ws1")

    async def test_slug_duplicado_rechaza(
        self, engine: AsyncEngine
    ) -> None:
        await crear_workspace(engine, "cli-dupws", "Dup", USUARIO_OWNER_ID)
        try:
            with pytest.raises(ErrorValidacion):
                await crear_workspace(
                    engine, "cli-dupws", "Dup2", USUARIO_OWNER_ID
                )
        finally:
            await _limpiar(engine, "cli-dupws")

    async def test_listar_filtra_por_usuario(
        self, engine: AsyncEngine
    ) -> None:
        ws = await crear_workspace(
            engine, "cli-wsfiltro", "Filtro", USUARIO_OWNER_ID
        )
        try:
            de_owner = await listar_workspaces(engine, USUARIO_OWNER_ID)
            assert any(w.id == ws.id for w in de_owner)
            ajeno = uuid.uuid4()
            de_ajeno = await listar_workspaces(engine, ajeno)
            assert all(w.id != ws.id for w in de_ajeno)
        finally:
            await _limpiar(engine, "cli-wsfiltro")


@pytest.mark.asyncio
class TestKeys:
    """emitir/revocar keys de usuario."""

    async def test_key_resuelve_a_usuario(
        self, engine: AsyncEngine
    ) -> None:
        u = await crear_usuario(engine, "cli-key1", "Key Uno")
        try:
            async with get_session(engine) as s:
                s.add(
                    MiembroModel(
                        workspace_id=WORKSPACE_PERSONAL_ID,
                        usuario_id=u.id,
                        rol="member",
                    )
                )
                await s.commit()
            key = await emitir_key_usuario(
                engine, u.id, WORKSPACE_PERSONAL_ID
            )
            assert key.startswith("ns_u_")
            ctx = await resolver_contexto(engine, key)
            assert ctx is not None
            assert ctx.usuario_id == u.id
        finally:
            await _limpiar(engine, "cli-key1")

    async def test_revocar_por_uuid_y_prefijo(
        self, engine: AsyncEngine
    ) -> None:
        u = await crear_usuario(engine, "cli-key2", "Key Dos")
        try:
            await emitir_key_usuario(engine, u.id)
            filas = await listar_keys(engine, u.id)
            assert len(filas) == 1
            assert await revocar_key_usuario(engine, str(filas[0].id))
            assert not await revocar_key_usuario(
                engine, str(uuid.uuid4())
            )
        finally:
            await _limpiar(engine, "cli-key2")

    async def test_key_revocada_no_resuelve(
        self, engine: AsyncEngine
    ) -> None:
        u = await crear_usuario(engine, "cli-key3", "Key Tres")
        try:
            key = await emitir_key_usuario(engine, u.id)
            assert await revocar_key_usuario(engine, key[:11])
            assert await resolver_contexto(engine, key) is None
        finally:
            await _limpiar(engine, "cli-key3")


@pytest.mark.asyncio
class TestInvitaciones:
    """Invitación canjeable: emisión, un solo uso, expiración."""

    async def test_canje_crea_usuario_membresia_y_key(
        self, engine: AsyncEngine
    ) -> None:
        ws = await crear_workspace(
            engine, "cli-invws", "Invite WS", USUARIO_OWNER_ID
        )
        try:
            token = await crear_invitacion(
                engine, ws.id, "viewer", dias=7
            )
            assert token.startswith("ns_inv_")
            usuario, key = await canjear_invitacion(
                engine, token, "cli-invitado", "Invitado"
            )
            async with get_session(engine) as s:
                rol = (
                    await s.execute(
                        select(MiembroModel.rol).where(
                            MiembroModel.workspace_id == ws.id,
                            MiembroModel.usuario_id == usuario.id,
                        )
                    )
                ).scalar_one()
            assert rol == "viewer"
            ctx = await resolver_contexto(engine, key)
            assert ctx is not None and ctx.workspace_id == ws.id
            assert ctx.rol == "viewer"
        finally:
            await _limpiar(engine, "cli-inv")
            await _limpiar(engine, "cli-invited")

    async def test_invitacion_un_solo_uso(
        self, engine: AsyncEngine
    ) -> None:
        ws = await crear_workspace(
            engine, "cli-inv2", "Invite2", USUARIO_OWNER_ID
        )
        try:
            token = await crear_invitacion(engine, ws.id, "member")
            await canjear_invitacion(
                engine, token, "cli-invdup", "Dup"
            )
            with pytest.raises(ErrorValidacion):
                await canjear_invitacion(
                    engine, token, "cli-invdup2", "Dup2"
                )
        finally:
            await _limpiar(engine, "cli-inv2")
            await _limpiar(engine, "cli-invdup")

    async def test_token_invalido_rechaza(
        self, engine: AsyncEngine
    ) -> None:
        with pytest.raises(ErrorValidacion):
            await canjear_invitacion(
                engine, "ns_inv_falso", "cli-x", "X"
            )

    async def test_rol_owner_no_invitable(
        self, engine: AsyncEngine
    ) -> None:
        ws = await crear_workspace(
            engine, "cli-inv3", "Invite3", USUARIO_OWNER_ID
        )
        try:
            with pytest.raises(ErrorValidacion):
                await crear_invitacion(engine, ws.id, "owner")
        finally:
            await _limpiar(engine, "cli-inv3")
