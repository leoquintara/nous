"""Tests de integración de la capa multi-tenant (fase 1).

Requieren PostgreSQL de test (NOUS_TEST_DATABASE_URL); el fixture
``session`` hace rollback al final — nada queda persistido.
"""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.identidad import (
    USUARIO_SISTEMA_ID,
    Contexto,
    asegurar_bootstrap,
    generar_key_usuario,
    generar_token_invitacion,
    hash_key,
)
from src.storage.models import (
    ApiKeyModel,
    EventoModel,
    MiembroModel,
    UsuarioModel,
    WorkspaceModel,
)
from tests.integration.conftest import TEST_DB_URL

_WS_PERSONAL = uuid.UUID(
    "00000000-0000-0000-0000-000000000002"
)
_OWNER = uuid.UUID(
    "00000000-0000-0000-0000-000000000001"
)


class TestKeysYHashes:
    """Generación y hash de credenciales."""

    def test_key_usuario_tiene_prefijo_y_entropia(self) -> None:
        k1, k2 = generar_key_usuario(), generar_key_usuario()
        assert k1.startswith("ns_u_") and len(k1) == 5 + 48
        assert k1 != k2

    def test_token_invitacion_tiene_prefijo(self) -> None:
        assert generar_token_invitacion().startswith("ns_inv_")

    def test_hash_es_sha256_hex(self) -> None:
        h = hash_key("ns_u_abc")
        assert len(h) == 64 and h == hash_key("ns_u_abc")
        assert h != hash_key("ns_u_abd")


class TestContexto:
    """Permisos derivados del rol."""

    def test_owner_puede_escribir(self) -> None:
        ctx = Contexto(
            usuario_id=_OWNER, workspace_id=_WS_PERSONAL, rol="owner"
        )
        assert ctx.puede_escribir and ctx.es_owner

    def test_member_escribe_pero_no_es_owner(self) -> None:
        ctx = Contexto(
            usuario_id=_OWNER, workspace_id=_WS_PERSONAL, rol="member"
        )
        assert ctx.puede_escribir and not ctx.es_owner

    def testviewer_solo_lectura(self) -> None:
        ctx = Contexto(
            usuario_id=_OWNER, workspace_id=_WS_PERSONAL, rol="viewer"
        )
        assert not ctx.puede_escribir and not ctx.es_owner


@pytest.mark.asyncio
class TestBootstrap:
    """asegurar_bootstrap crea el esqueleto mínimo idempotentemente."""

    async def test_bootstrap_crea_owner_workspace_membresia(
        self, session: AsyncSession
    ) -> None:
        engine = create_async_engine(TEST_DB_URL)
        try:
            await asegurar_bootstrap(engine)
            owner = (
                await session.execute(
                    select(UsuarioModel).where(
                        UsuarioModel.handle == "owner"
                    )
                )
            ).scalar_one()
            ws = (
                await session.execute(
                    select(WorkspaceModel).where(
                        WorkspaceModel.slug == "personal"
                    )
                )
            ).scalar_one()
            assert ws.owner_id == owner.id
            membresia = await session.get(
                MiembroModel, (ws.id, owner.id)
            )
            assert membresia is not None and membresia.rol == "owner"
        finally:
            await engine.dispose()

    async def test_bootstrap_es_idempotente(
        self, session: AsyncSession
    ) -> None:
        engine = create_async_engine(TEST_DB_URL)
        try:
            await asegurar_bootstrap(engine)
            await asegurar_bootstrap(engine)
            total_ws = await session.scalar(
                select(func.count()).select_from(WorkspaceModel)
            )
            assert total_ws == 1
        finally:
            await engine.dispose()

    async def test_usuario_sistema_existe(
        self, session: AsyncSession
    ) -> None:
        engine = create_async_engine(TEST_DB_URL)
        try:
            await asegurar_bootstrap(engine)
            sistema = await session.get(UsuarioModel, USUARIO_SISTEMA_ID)
            assert sistema is not None and sistema.handle == "sistema"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
class TestEntidadesTenant:
    """Las columnas de tenant aceptan y respetan el modelo."""

    async def test_evento_default_cae_en_workspace_personal(
        self, session: AsyncSession
    ) -> None:
        engine = create_async_engine(TEST_DB_URL)
        try:
            await asegurar_bootstrap(engine)
            session.add(
                EventoModel(
                    timestamp_unix=1,
                    dimension="cognitiva",
                    fuente="test",
                    contenido="evento sin workspace explícito",
                )
            )
            await session.flush()
            fila = (
                await session.execute(
                    select(EventoModel.workspace_id).where(
                        EventoModel.contenido
                        == "evento sin workspace explícito"
                    )
                )
            ).scalar_one()
            assert fila == _WS_PERSONAL
        finally:
            await engine.dispose()

    async def test_scope_rechaza_valor_invalido(
        self, session: AsyncSession
    ) -> None:
        session.add(
            EventoModel(
                timestamp_unix=1,
                dimension="cognitiva",
                fuente="test",
                contenido="scope malo",
                scope="publico",
            )
        )
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_api_key_persiste_solo_hash(
        self, session: AsyncSession
    ) -> None:
        engine = create_async_engine(TEST_DB_URL)
        try:
            await asegurar_bootstrap(engine)
            key = generar_key_usuario()
            session.add(
                ApiKeyModel(
                    hash=hash_key(key),
                    prefijo=key[:11],
                    usuario_id=_OWNER,
                    nombre="test",
                )
            )
            await session.flush()
            guardado = (
                await session.execute(
                    select(ApiKeyModel.hash).where(
                        ApiKeyModel.prefijo == key[:11]
                    )
                )
            ).scalar_one()
            assert guardado == hash_key(key) and guardado != key
        finally:
            await engine.dispose()

    async def test_segundo_workspace_aisla_eventos(
        self, session: AsyncSession
    ) -> None:
        engine = create_async_engine(TEST_DB_URL)
        try:
            await asegurar_bootstrap(engine)
            ws_b = WorkspaceModel(
                slug="equipo-b", nombre="Equipo B", owner_id=_OWNER
            )
            session.add(ws_b)
            await session.flush()
            session.add_all(
                [
                    EventoModel(
                        timestamp_unix=1,
                        dimension="cognitiva",
                        fuente="test",
                        contenido="en personal",
                        workspace_id=_WS_PERSONAL,
                    ),
                    EventoModel(
                        timestamp_unix=2,
                        dimension="cognitiva",
                        fuente="test",
                        contenido="en equipo-b",
                        workspace_id=ws_b.id,
                    ),
                ]
            )
            await session.flush()
            cuenta = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM eventos "
                        "WHERE workspace_id = :ws"
                    ),
                    {"ws": ws_b.id},
                )
            ).scalar_one()
            assert cuenta == 1
        finally:
            await engine.dispose()
