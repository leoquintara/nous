"""Tests del feature gate de la edición libre (F6).

Sin ``NOUS_LICENSE_KEY`` el self-hosted queda limitado a
``max_usuarios_libres`` usuarios y ``max_workspaces_libres``
workspaces; los puntos de creación lanzan ``CupoExcedidoError``.

La suite corre con licencia activa por defecto (ver conftest) —
estos tests la desactivan explícitamente.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings
from src.core.exceptions import CupoExcedidoError
from src.core.identidad import (
    USUARIO_OWNER_ID,
    asegurar_bootstrap,
    canjear_invitacion,
    crear_invitacion,
    crear_usuario,
    crear_workspace,
    estado_licencia,
)
from src.storage.database import get_session
from src.storage.models import (
    InvitacionModel,
    MiembroModel,
    UsuarioModel,
    WorkspaceModel,
)
from tests.integration.conftest import TEST_DB_URL


@pytest.fixture
def sin_licencia() -> None:
    """Desactiva la licencia y fija topes bajos para el test."""
    s = get_settings()
    assert s.license_key == "test-suite"  # el autouse la fija
    s.license_key = None
    s.max_usuarios_libres = 1
    s.max_workspaces_libres = 3
    try:
        yield
    finally:
        s.license_key = "test-suite"
        s.max_usuarios_libres = 1
        s.max_workspaces_libres = 3


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
    """Borra usuarios/workspaces del test."""
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


@pytest.mark.asyncio
class TestCupoUsuarios:
    """Sin licencia, el segundo usuario se rechaza en todo punto."""

    async def test_crear_usuario_bloqueado(
        self, engine: AsyncEngine, sin_licencia: None
    ) -> None:
        """owner ya ocupa el único cupo — crear otro falla."""
        with pytest.raises(CupoExcedidoError):
            await crear_usuario(engine, "lic-u1", "U1")

    async def test_invitacion_bloqueada(
        self, engine: AsyncEngine, sin_licencia: None
    ) -> None:
        """Emitir invitación implica un usuario nuevo — falla."""
        with pytest.raises(CupoExcedidoError):
            await crear_invitacion(
                engine, await _ws_personal(engine), "member"
            )

    async def test_canje_bloqueado(
        self, engine: AsyncEngine, sin_licencia: None
    ) -> None:
        """El canje también verifica cupo (licencia pudo expirar)."""
        # Emitir la invitación CON licencia; canjear SIN ella.
        get_settings().license_key = "x"
        token = await crear_invitacion(
            engine, await _ws_personal(engine), "member"
        )
        get_settings().license_key = None
        with pytest.raises(CupoExcedidoError):
            await canjear_invitacion(engine, token, "lic-inv", "Inv")
        async with get_session(engine) as s:
            await s.execute(
                delete(InvitacionModel).where(
                    InvitacionModel.workspace_id
                    == await _ws_personal(engine)
                )
            )
            await s.commit()

    async def test_con_licencia_crea_libremente(
        self, engine: AsyncEngine, sin_licencia: None
    ) -> None:
        """Con license_key activa el cupo no aplica."""
        get_settings().license_key = "x"
        u = await crear_usuario(engine, "lic-ok", "OK")
        try:
            assert u.handle == "lic-ok"
        finally:
            await _limpiar(engine, "lic-")


async def _ws_personal(engine: AsyncEngine) -> "object":
    """UUID del workspace personal bootstrap."""
    import uuid as _u

    return _u.UUID("00000000-0000-0000-0000-000000000002")


@pytest.mark.asyncio
class TestCupoWorkspaces:
    """Sin licencia, el 4º workspace (personal+3) se rechaza."""

    async def test_crear_workspace_bloqueado(
        self, engine: AsyncEngine, sin_licencia: None
    ) -> None:
        get_settings().license_key = "x"  # crear los 2 primeros libres
        await crear_workspace(
            engine, "lic-ws2", "WS2", USUARIO_OWNER_ID
        )
        try:
            get_settings().license_key = None
            # personal + ws2 = 2 < 3 → uno más sí
            await crear_workspace(
                engine, "lic-ws3", "WS3", USUARIO_OWNER_ID
            )
            # Ahora 3 = tope → el siguiente falla sin licencia.
            with pytest.raises(CupoExcedidoError):
                await crear_workspace(
                    engine, "lic-ws4", "WS4", USUARIO_OWNER_ID
                )
        finally:
            get_settings().license_key = "x"
            await _limpiar(engine, "lic-")


@pytest.mark.asyncio
class TestEstadoLicencia:
    """estado_licencia resume uso contra los topes."""

    async def test_libre_reporta_topes(
        self, engine: AsyncEngine, sin_licencia: None
    ) -> None:
        est = await estado_licencia(engine)
        assert est.licenciado is False
        assert est.max_usuarios == 1
        assert est.max_workspaces == 3
        assert est.usuarios >= 1  # owner bootstrap
        assert est.workspaces >= 1  # personal

    async def test_licenciado_sin_topes(
        self, engine: AsyncEngine, sin_licencia: None
    ) -> None:
        get_settings().license_key = "x"
        est = await estado_licencia(engine)
        assert est.licenciado is True
        assert est.max_usuarios is None
        assert est.max_workspaces is None
