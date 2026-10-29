"""Tests de visibilidad y scopes multi-tenant (F3).

Regla bajo prueba (grill-me Q3/Q7): un actor ve ``scope='workspace'``
de su workspace ∪ sus propios eventos ∪ los sin atribuir. El override
``workspace=`` exige membresía en el destino. Los fixtures crean
filas committed (los resolutores abren sus propias sesiones) y las
limpian al final.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core.exceptions import ErrorValidacion, PermisoDenegadoError
from src.core.identidad import (
    USUARIO_OWNER_ID,
    WORKSPACE_PERSONAL_ID,
    Contexto,
    asegurar_bootstrap,
    resolver_workspace,
)
from src.storage.database import get_session
from src.storage.models import (
    EventoModel,
    MiembroModel,
    ObjetivoModel,
    RelacionModel,
    UsuarioModel,
    WorkspaceModel,
)
from src.storage.repositories import (
    EventoRepository,
    ObjetivoRepository,
    RelacionRepository,
)
from tests.integration.conftest import TEST_DB_URL


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
    """Dos members del ws personal + ws ajeno + eventos de cada scope.

    Returns:
        Dict con user_a, user_b, ws_b y los tres eventos semilla.
    """
    datos: dict[str, object] = {}
    async with get_session(engine) as s:
        user_a = UsuarioModel(handle="sc-a", nombre="A")
        user_b = UsuarioModel(handle="sc-b", nombre="B")
        ws_b = WorkspaceModel(slug="sc-ws-b", nombre="WS B", owner_id=USUARIO_OWNER_ID)
        s.add_all([user_a, user_b, ws_b])
        await s.flush()
        s.add_all(
            [
                MiembroModel(
                    workspace_id=WORKSPACE_PERSONAL_ID,
                    usuario_id=user_a.id,
                    rol="member",
                ),
                MiembroModel(
                    workspace_id=WORKSPACE_PERSONAL_ID,
                    usuario_id=user_b.id,
                    rol="member",
                ),
                # user_b también es member del workspace B (override OK);
                # user_a NO pertenece a ws_b.
                MiembroModel(
                    workspace_id=ws_b.id,
                    usuario_id=user_b.id,
                    rol="viewer",
                ),
            ]
        )
        base = int(uuid.uuid4().int % 1_000_000)
        ev_personal_a = EventoModel(
            timestamp_unix=1_800_000_000 + base,
            dimension="cognitiva",
            fuente="test_scopes",
            contenido="nota privada de A",
            scope="personal",
            autor_id=user_a.id,
            workspace_id=WORKSPACE_PERSONAL_ID,
        )
        ev_workspace_b = EventoModel(
            timestamp_unix=1_800_000_001 + base,
            dimension="cognitiva",
            fuente="test_scopes",
            contenido="decisión compartida del equipo",
            scope="workspace",
            autor_id=user_b.id,
            workspace_id=WORKSPACE_PERSONAL_ID,
        )
        ev_sin_autor = EventoModel(
            timestamp_unix=1_800_000_002 + base,
            dimension="cognitiva",
            fuente="test_scopes",
            contenido="registro importado sin dueño",
            scope="personal",
            autor_id=None,
            workspace_id=WORKSPACE_PERSONAL_ID,
        )
        ev_otro_ws = EventoModel(
            timestamp_unix=1_800_000_003 + base,
            dimension="cognitiva",
            fuente="test_scopes",
            contenido="memoria del workspace B",
            scope="workspace",
            autor_id=user_b.id,
            workspace_id=ws_b.id,
        )
        s.add_all([ev_personal_a, ev_workspace_b, ev_sin_autor, ev_otro_ws])
        await s.commit()
        datos = {
            "user_a": user_a,
            "user_b": user_b,
            "ws_b": ws_b,
            "ev_personal_a": ev_personal_a,
            "ev_workspace_b": ev_workspace_b,
            "ev_sin_autor": ev_sin_autor,
            "ev_otro_ws": ev_otro_ws,
        }
    yield datos
    async with get_session(engine) as s:
        for ev in (
            datos["ev_personal_a"],
            datos["ev_workspace_b"],
            datos["ev_sin_autor"],
            datos["ev_otro_ws"],
        ):
            await s.execute(
                delete(EventoModel).where(
                    EventoModel.id == ev.id  # type: ignore[attr-defined]
                )
            )
        for modelo, col in (
            (MiembroModel, MiembroModel.usuario_id),
        ):
            await s.execute(
                delete(modelo).where(
                    col.in_(
                        [
                            datos["user_a"].id,  # type: ignore[attr-defined]
                            datos["user_b"].id,  # type: ignore[attr-defined]
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
                        datos["user_a"].id,  # type: ignore[attr-defined]
                        datos["user_b"].id,  # type: ignore[attr-defined]
                    ]
                )
            )
        )
        await s.commit()


def _ctx(user: object, ws_id: uuid.UUID, rol: str = "member") -> Contexto:
    """Contexto de prueba para un usuario."""
    return Contexto(
        usuario_id=user.id,  # type: ignore[attr-defined]
        workspace_id=ws_id,
        rol=rol,
    )


@pytest.mark.asyncio
class TestVisibilidadEventos:
    """La regla mías ∪ workspace ∪ sin-atribuir en EventoRepository."""

    async def test_member_ve_propios_mas_workspace(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_a"], WORKSPACE_PERSONAL_ID)
        async with get_session(engine) as s:
            eventos = await EventoRepository(s, ctx).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos if e.fuente == "test_scopes"}
        assert escenario["ev_personal_a"].id in ids  # type: ignore[attr-defined]
        assert escenario["ev_workspace_b"].id in ids  # type: ignore[attr-defined]
        assert escenario["ev_sin_autor"].id in ids  # type: ignore[attr-defined]
        assert escenario["ev_otro_ws"].id not in ids  # type: ignore[attr-defined]

    async def test_personal_ajeno_no_se_ve(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_b"], WORKSPACE_PERSONAL_ID)
        async with get_session(engine) as s:
            eventos = await EventoRepository(s, ctx).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos}
        assert escenario["ev_personal_a"].id not in ids  # type: ignore[attr-defined]
        assert escenario["ev_workspace_b"].id in ids  # type: ignore[attr-defined]

    async def test_buscar_por_texto_respeta_scope(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_b"], WORKSPACE_PERSONAL_ID)
        async with get_session(engine) as s:
            hits = await EventoRepository(s, ctx).buscar_por_texto(
                "privada"
            )
        assert all(
            m.id != escenario["ev_personal_a"].id  # type: ignore[attr-defined]
            for m, _ in hits
        )

    async def test_obtener_por_id_filtrado(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_b"], WORKSPACE_PERSONAL_ID)
        async with get_session(engine) as s:
            repo = EventoRepository(s, ctx)
            assert (
                await repo.obtener(
                    escenario["ev_personal_a"].id  # type: ignore[attr-defined]
                )
                is None
            )
            assert (
                await repo.obtener(
                    escenario["ev_workspace_b"].id  # type: ignore[attr-defined]
                )
                is not None
            )

    async def test_tags_solo_visibles(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_b"], WORKSPACE_PERSONAL_ID)
        async with get_session(engine) as s:
            eventos = await EventoRepository(s, ctx).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos}
        assert escenario["ev_otro_ws"].id not in ids  # type: ignore[attr-defined]

    async def test_sin_ctx_no_filtra(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """ctx=None mantiene el comportamiento legacy (CLI/importers)."""
        async with get_session(engine) as s:
            eventos = await EventoRepository(s).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos}
        assert escenario["ev_personal_a"].id in ids  # type: ignore[attr-defined]
        assert escenario["ev_otro_ws"].id in ids  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestVisibilidadObjetivosYRelaciones:
    """Scope en objetivos y workspace en relaciones."""

    async def test_objetivos_filtrados_por_scope(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        obj = ObjetivoModel(
            titulo="meta privada de A",
            fecha_objetivo=1_900_000_000,
            rama="main",
            scope="personal",
            autor_id=escenario["user_a"].id,  # type: ignore[attr-defined]
            workspace_id=WORKSPACE_PERSONAL_ID,
        )
        async with get_session(engine) as s:
            s.add(obj)
            await s.commit()
        try:
            ctx_b = _ctx(escenario["user_b"], WORKSPACE_PERSONAL_ID)
            async with get_session(engine) as s:
                lista = await ObjetivoRepository(s, ctx_b).listar(None)
            assert all(o.id != obj.id for o in lista)
            ctx_a = _ctx(escenario["user_a"], WORKSPACE_PERSONAL_ID)
            async with get_session(engine) as s:
                lista = await ObjetivoRepository(s, ctx_a).listar(None)
            assert any(o.id == obj.id for o in lista)
        finally:
            async with get_session(engine) as s:
                await s.execute(
                    delete(ObjetivoModel).where(ObjetivoModel.id == obj.id)
                )
                await s.commit()

    async def test_relaciones_acotadas_al_workspace(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        a = escenario["ev_personal_a"]
        b = escenario["ev_workspace_b"]
        rel = RelacionModel(
            origen_id=min(a.id, b.id),  # type: ignore[attr-defined]
            destino_id=max(a.id, b.id),  # type: ignore[attr-defined]
            tipo="explicita",
            workspace_id=WORKSPACE_PERSONAL_ID,
        )
        async with get_session(engine) as s:
            s.add(rel)
            await s.commit()
        try:
            ctx_b_ws = _ctx(
                escenario["user_b"], escenario["ws_b"].id, "viewer"  # type: ignore[attr-defined]
            )
            async with get_session(engine) as s:
                lista = await RelacionRepository(s, ctx_b_ws).listar()
            assert all(r.id != rel.id for r in lista)
        finally:
            async with get_session(engine) as s:
                await s.execute(
                    delete(RelacionModel).where(RelacionModel.id == rel.id)
                )
                await s.commit()


@pytest.mark.asyncio
class TestResolverWorkspace:
    """Override de workspace: membresía fija el rol del contexto."""

    async def test_override_por_slug_con_membresia(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_b"], WORKSPACE_PERSONAL_ID)
        nuevo = await resolver_workspace(engine, ctx, "sc-ws-b")
        assert nuevo.workspace_id == escenario["ws_b"].id  # type: ignore[attr-defined]
        # member en personal → viewer en ws_b (rol del destino gana).
        assert nuevo.rol == "viewer"
        assert not nuevo.puede_escribir

    async def test_override_por_uuid(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_b"], WORKSPACE_PERSONAL_ID)
        nuevo = await resolver_workspace(
            engine, ctx, str(escenario["ws_b"].id)  # type: ignore[attr-defined]
        )
        assert nuevo.workspace_id == escenario["ws_b"].id  # type: ignore[attr-defined]

    async def test_sin_membresia_rechaza(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_a"], WORKSPACE_PERSONAL_ID)
        with pytest.raises(PermisoDenegadoError):
            await resolver_workspace(engine, ctx, "sc-ws-b")

    async def test_workspace_inexistente_error_validacion(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ctx = _ctx(escenario["user_a"], WORKSPACE_PERSONAL_ID)
        with pytest.raises(ErrorValidacion):
            await resolver_workspace(engine, ctx, "no-existe")

    async def test_usuario_none_acceso_dev(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """Modo desarrollo sin token: override permitido como owner."""
        ctx = Contexto(
            usuario_id=None,
            workspace_id=WORKSPACE_PERSONAL_ID,
            rol="owner",
        )
        nuevo = await resolver_workspace(engine, ctx, "sc-ws-b")
        assert nuevo.rol == "owner"


@pytest.mark.asyncio
class TestWorkspaceOverrideE2E:
    """Override de workspace a través de la app ASGI real."""

    async def test_override_sin_membresia_devuelve_403(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """user_a sin membresía en sc-ws-b → 403 en el override."""
        import unittest.mock as mock

        from httpx import ASGITransport, AsyncClient

        from src.core.identidad import generar_key_usuario, hash_key
        from src.main import crear_app_http
        from src.mcp import tools
        from src.storage import database
        from src.storage.models import ApiKeyModel

        user_a = escenario["user_a"]
        key = generar_key_usuario()
        async with get_session(engine) as s:
            s.add(
                ApiKeyModel(
                    hash=hash_key(key),
                    prefijo=key[:11],
                    usuario_id=user_a.id,  # type: ignore[attr-defined]
                    workspace_default_id=WORKSPACE_PERSONAL_ID,
                )
            )
            await s.commit()
        patches = [
            mock.patch.object(tools, "obtener_engine", lambda: engine),
            mock.patch.object(database, "obtener_engine", lambda: engine),
            mock.patch("src.api.auth.obtener_engine", lambda: engine),
        ]
        for p in patches:
            p.start()
        try:
            app = crear_app_http()
            cliente = AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {key}"},
            )
            try:
                r = await cliente.get(
                    "/api/events", params={"workspace": "sc-ws-b"}
                )
                assert r.status_code == 403
                r2 = await cliente.get("/api/events")
                assert r2.status_code == 200
            finally:
                await cliente.aclose()
        finally:
            for p in patches:
                p.stop()
            async with get_session(engine) as s:
                await s.execute(
                    delete(ApiKeyModel).where(
                        ApiKeyModel.hash == hash_key(key)
                    )
                )
                await s.commit()


@pytest.mark.asyncio
class TestToolsMCPConScope:
    """Los tools públicos MCP aceptan ``scope``/``workspace`` y los
    enrutan por el contexto fijado (stdio y streamable-http igual)."""

    async def test_recordar_con_scope_workspace(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """member escribe scope=workspace y queda visible al equipo."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools
        from src.storage.repositories import EventoRepository

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        monkeypatch.setattr(
            tools,
            "crear_engine",
            lambda: create_async_engine(TEST_DB_URL),
        )
        user_b = escenario["user_b"]
        fijar_contexto(
            _ctx(user_b, WORKSPACE_PERSONAL_ID, "member")
        )
        try:
            r = await tools.recordar_evento(
                "decisión visible para el equipo entero",
                fuente="test_scopes_tool",
                scope="workspace",
            )
        finally:
            fijar_contexto(None)
        async with get_session(engine) as s:
            fila = (
                await EventoRepository(
                    s, _ctx(user_b, WORKSPACE_PERSONAL_ID)
                ).obtener(r["id"])
            )
        assert fila is not None
        assert fila.scope == "workspace"
        assert fila.autor_id == user_b.id  # type: ignore[attr-defined]
        assert fila.workspace_id == WORKSPACE_PERSONAL_ID
        async with get_session(engine) as s:
            await s.execute(
                delete(EventoModel).where(EventoModel.id == fila.id)
            )
            await s.commit()

    async def test_viewer_no_escribe_aunque_sea_owner_en_otro_ws(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """user_b es viewer EN ws_b: escribir allí da 403 aunque sea
        member en personal — el rol efectivo es el del destino."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        user_b = escenario["user_b"]
        fijar_contexto(
            _ctx(user_b, WORKSPACE_PERSONAL_ID, "member")
        )
        try:
            with pytest.raises(PermisoDenegadoError):
                await tools.recordar_evento(
                    "intento de escritura como viewer",
                    fuente="test_scopes_tool",
                    workspace="sc-ws-b",
                )
        finally:
            fijar_contexto(None)

    async def test_contexto_inicial_con_override(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """contexto_inicial(workspace=…) resuelve el ctx del destino."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        user_a = escenario["user_a"]
        fijar_contexto(_ctx(user_a, WORKSPACE_PERSONAL_ID, "member"))
        try:
            # user_a sin membresía en ws_b → rechazo.
            with pytest.raises(PermisoDenegadoError):
                await tools.contexto_inicial(workspace="sc-ws-b")
            # Su workspace default sí responde.
            r = await tools.contexto_inicial()
            assert "resumen" in r or isinstance(r, dict)
        finally:
            fijar_contexto(None)


@pytest.mark.asyncio
class TestToolsMCPWorkspaces:
    """Tools MCP de administración de workspaces (F8).

    Paridad con REST: crear deja al llamante owner; roster lo lee
    cualquier miembro; invitar/cambiar/expulsar exige owner EN EL
    workspace destino. El handle se resuelve a UUID vía el roster.
    """

    async def _limpiar_ws(self, engine: AsyncEngine, slug: str) -> None:
        async with get_session(engine) as s:
            ws = (
                await s.execute(
                    select(WorkspaceModel).where(
                        WorkspaceModel.slug == slug
                    )
                )
            ).scalar_one_or_none()
            if ws is not None:
                await s.execute(
                    delete(MiembroModel).where(
                        MiembroModel.workspace_id == ws.id
                    )
                )
                await s.delete(ws)
                await s.commit()

    def _ctx_owner(self) -> Contexto:
        return Contexto(
            usuario_id=USUARIO_OWNER_ID,
            workspace_id=WORKSPACE_PERSONAL_ID,
            rol="owner",
        )

    async def test_crear_workspace_owner_queda_owner(
        self, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """crear_workspace persiste el ws y el llamante es owner."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        fijar_contexto(self._ctx_owner())
        try:
            r = await tools.crear_workspace(
                slug="sc-ws-nuevo", nombre="WS Nuevo"
            )
        finally:
            fijar_contexto(None)
        assert r["slug"] == "sc-ws-nuevo"
        assert r["role"] == "owner"
        lista = await tools.listar_miembros(workspace="sc-ws-nuevo")
        assert [(m["handle"], m["role"]) for m in lista] == [
            ("owner", "owner")
        ]
        await self._limpiar_ws(engine, "sc-ws-nuevo")

    async def test_crear_workspace_slug_invalido(
        self, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slug fuera de formato → ErrorValidacion."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        fijar_contexto(self._ctx_owner())
        try:
            with pytest.raises(ErrorValidacion):
                await tools.crear_workspace(slug="Malo!", nombre="X")
        finally:
            fijar_contexto(None)

    async def test_listar_workspaces_muestra_rol_por_ws(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """user_b ve personal (member) y sc-ws-b (viewer)."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        user_b = escenario["user_b"]
        fijar_contexto(_ctx(user_b, WORKSPACE_PERSONAL_ID, "member"))
        try:
            lista = await tools.listar_workspaces()
        finally:
            fijar_contexto(None)
        roles = {w["slug"]: w["role"] for w in lista}
        assert roles["personal"] == "member"
        assert roles["sc-ws-b"] == "viewer"

    async def test_invitar_miembro_exige_owner(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """member no puede invitar; owner sí (token ns_inv_*)."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools
        from src.storage.models import InvitacionModel

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        user_b = escenario["user_b"]
        fijar_contexto(_ctx(user_b, WORKSPACE_PERSONAL_ID, "member"))
        try:
            with pytest.raises(PermisoDenegadoError):
                await tools.invitar_miembro(rol="member")
        finally:
            fijar_contexto(None)

        fijar_contexto(self._ctx_owner())
        try:
            r = await tools.invitar_miembro(rol="viewer", dias=5)
        finally:
            fijar_contexto(None)
        assert r["token"].startswith("ns_inv_")
        assert r["expires_days"] == 5
        async with get_session(engine) as s:
            await s.execute(delete(InvitacionModel))
            await s.commit()

    async def test_cambiar_rol_y_quitar_miembro_por_handle(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """owner cambia sc-b a viewer y lo expulsa; handle malo falla."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        fijar_contexto(self._ctx_owner())
        try:
            with pytest.raises(ErrorValidacion):
                await tools.cambiar_rol_miembro("no-existe", "viewer")

            r = await tools.cambiar_rol_miembro("sc-b", "viewer")
            assert r == {"handle": "sc-b", "role": "viewer"}

            r2 = await tools.quitar_miembro("sc-b")
            assert r2 == {"handle": "sc-b", "removed": True}

            roster = await tools.listar_miembros()
            assert "sc-b" not in {m["handle"] for m in roster}
        finally:
            fijar_contexto(None)
            # restaurar la membresía del escenario para otros tests
            async with get_session(engine) as s:
                existe = (
                    await s.execute(
                        select(MiembroModel).where(
                            MiembroModel.workspace_id
                            == WORKSPACE_PERSONAL_ID,
                            MiembroModel.usuario_id
                            == escenario["user_b"].id,  # type: ignore[attr-defined]
                        )
                    )
                ).scalar_one_or_none()
                if existe is None:
                    s.add(
                        MiembroModel(
                            workspace_id=WORKSPACE_PERSONAL_ID,
                            usuario_id=escenario["user_b"].id,  # type: ignore[attr-defined]
                            rol="member",
                        )
                    )
                    await s.commit()

    async def test_invitar_en_otro_ws_usa_rol_del_destino(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """user_b es viewer en sc-ws-b: no puede invitar ahí aunque
        sea member en personal."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        user_b = escenario["user_b"]
        fijar_contexto(_ctx(user_b, WORKSPACE_PERSONAL_ID, "member"))
        try:
            with pytest.raises(PermisoDenegadoError):
                await tools.invitar_miembro(
                    rol="member", workspace="sc-ws-b"
                )
        finally:
            fijar_contexto(None)
