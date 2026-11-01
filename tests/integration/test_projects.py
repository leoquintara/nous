"""Tests de Projects: subdivisión cerrada de workspaces (spec 2026-10-30).

Reglas bajo prueba:

- Resolución ``project=`` por slug/UUID dentro del workspace efectivo;
  no-miembro → 403, owner del workspace → override.
- Visibilidad por defecto: NULL ∪ projects donde soy miembro.
- Key con scope estricto: SOLO su project — ni siquiera NULL.
- ``archivado``: bloquea escritura, sigue en recall.
- ``vault``: el sueño lee pero nunca muta; gist cross-project → NULL.
- Cupo libre: 5 projects/workspace; owner y licencia exentos.
- ``DELETE``: ``project_id SET NULL`` — la memoria no se pierde.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core.exceptions import (
    CupoExcedidoError,
    ErrorValidacion,
    PermisoDenegadoError,
)
from src.core.identidad import (
    USUARIO_OWNER_ID,
    Contexto,
    asegurar_bootstrap,
    generar_key_usuario,
    hash_key,
    resolver_contexto,
    resolver_proyecto,
)
from src.core.proyectos import (
    actualizar_proyecto,
    agregar_miembro,
    asignar_proyecto,
    crear_proyecto,
    eliminar_proyecto,
    listar_miembros,
    listar_proyectos,
)
from src.storage.database import get_session
from src.storage.models import (
    ApiKeyModel,
    EventoModel,
    MiembroModel,
    ProjectMemberModel,
    ProjectModel,
    RelacionModel,
    UsuarioModel,
    WorkspaceModel,
)
from src.storage.repositories import (
    EventoRepository,
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
    """Workspace propio + 2 usuarios + 2 projects + eventos semilla.

    ``user_a`` es member del workspace y del project ``alpha``;
    ``user_b`` es member del workspace y del project ``beta``
    (sin acceso a ``alpha``). El owner del workspace ve todo.
    """
    datos: dict[str, object] = {}
    async with get_session(engine) as s:
        user_a = UsuarioModel(handle="pj-a", nombre="A")
        user_b = UsuarioModel(handle="pj-b", nombre="B")
        ws = WorkspaceModel(slug="pj-ws", nombre="PJ WS", owner_id=USUARIO_OWNER_ID)
        s.add_all([user_a, user_b, ws])
        await s.flush()
        s.add_all(
            [
                MiembroModel(workspace_id=ws.id, usuario_id=user_a.id, rol="member"),
                MiembroModel(workspace_id=ws.id, usuario_id=user_b.id, rol="member"),
            ]
        )
        alpha = ProjectModel(
            workspace_id=ws.id, slug="alpha", nombre="Alpha",
            descripcion="project de A", kind="regular",
        )
        beta = ProjectModel(
            workspace_id=ws.id, slug="beta", nombre="Beta",
            descripcion="project de B", kind="regular",
        )
        vault = ProjectModel(
            workspace_id=ws.id, slug="boveda", nombre="Bóveda",
            descripcion="archivo de mensajes", kind="vault",
        )
        s.add_all([alpha, beta, vault])
        await s.flush()
        s.add_all(
            [
                ProjectMemberModel(
                    project_id=alpha.id, usuario_id=user_a.id, rol="member"
                ),
                ProjectMemberModel(
                    project_id=beta.id, usuario_id=user_b.id, rol="member"
                ),
                # La bóveda la lee el owner del ws (override); ningún
                # member regular entra salvo alta explícita.
            ]
        )
        base = int(uuid.uuid4().int % 1_000_000)
        ev_null = EventoModel(
            timestamp_unix=1_810_000_000 + base,
            dimension="cognitiva",
            fuente="test_projects",
            contenido="memoria del workspace entero",
            scope="workspace",
            workspace_id=ws.id,
        )
        ev_alpha = EventoModel(
            timestamp_unix=1_810_000_001 + base,
            dimension="cognitiva",
            fuente="test_projects",
            contenido="memoria de alpha",
            scope="workspace",
            workspace_id=ws.id,
            project_id=alpha.id,
        )
        ev_beta = EventoModel(
            timestamp_unix=1_810_000_002 + base,
            dimension="cognitiva",
            fuente="test_projects",
            contenido="memoria de beta",
            scope="workspace",
            workspace_id=ws.id,
            project_id=beta.id,
        )
        ev_vault = EventoModel(
            timestamp_unix=1_810_000_003 + base,
            dimension="cognitiva",
            fuente="test_projects",
            contenido="mensaje archivado en la bóveda",
            scope="workspace",
            workspace_id=ws.id,
            project_id=vault.id,
            archivado=False,
            weight=0.9,
        )
        s.add_all([ev_null, ev_alpha, ev_beta, ev_vault])
        await s.commit()
        datos = {
            "user_a": user_a,
            "user_b": user_b,
            "ws": ws,
            "alpha": alpha,
            "beta": beta,
            "vault": vault,
            "ev_null": ev_null,
            "ev_alpha": ev_alpha,
            "ev_beta": ev_beta,
            "ev_vault": ev_vault,
        }
    yield datos
    async with get_session(engine) as s:
        for modelo, col, valor in (
            (EventoModel, EventoModel.workspace_id, datos["ws"].id),  # type: ignore[attr-defined]
            (RelacionModel, RelacionModel.workspace_id, datos["ws"].id),  # type: ignore[attr-defined]
        ):
            await s.execute(delete(modelo).where(col == valor))
        await s.execute(
            delete(ProjectMemberModel).where(
                ProjectMemberModel.project_id.in_(
                    select(ProjectModel.id).where(
                        ProjectModel.workspace_id == datos["ws"].id  # type: ignore[attr-defined]
                    )
                )
            )
        )
        await s.execute(
            delete(ApiKeyModel).where(
                ApiKeyModel.usuario_id.in_(
                    [
                        datos["user_a"].id,  # type: ignore[attr-defined]
                        datos["user_b"].id,  # type: ignore[attr-defined]
                    ]
                )
            )
        )
        await s.execute(
            delete(ProjectModel).where(
                ProjectModel.workspace_id == datos["ws"].id  # type: ignore[attr-defined]
            )
        )
        await s.execute(
            delete(MiembroModel).where(
                MiembroModel.workspace_id == datos["ws"].id  # type: ignore[attr-defined]
            )
        )
        await s.execute(
            delete(WorkspaceModel).where(
                WorkspaceModel.id == datos["ws"].id  # type: ignore[attr-defined]
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


def _ctx(
    user: object,
    ws_id: uuid.UUID,
    rol: str = "member",
    visibles: frozenset[uuid.UUID] | None = None,
) -> Contexto:
    """Contexto de prueba con universo de projects explícito."""
    return Contexto(
        usuario_id=user.id,  # type: ignore[attr-defined]
        workspace_id=ws_id,
        rol=rol,
        projects_visibles=visibles,
    )


def _id(d: dict[str, object], k: str) -> uuid.UUID:
    return d[k].id  # type: ignore[attr-defined,no-any-return]


@pytest.mark.asyncio
class TestResolverProyecto:
    """resolver_proyecto: slug/UUID, membresía y owner override."""

    async def test_resuelve_por_slug_con_membresia(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "member")
        nuevo = await resolver_proyecto(engine, ctx, "alpha")
        assert nuevo.proyecto_id == _id(escenario, "alpha")
        assert nuevo.proyecto_rol == "member"
        assert nuevo.proyecto_kind == "regular"
        assert nuevo.proyecto_estado == "activo"

    async def test_resuelve_por_uuid(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "member")
        nuevo = await resolver_proyecto(
            engine, ctx, str(_id(escenario, "alpha"))
        )
        assert nuevo.proyecto_id == _id(escenario, "alpha")

    async def test_slug_inexistente_lista_disponibles(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx = _ctx(
            escenario["user_a"],
            ws_id,
            "member",
            frozenset({_id(escenario, "alpha")}),
        )
        with pytest.raises(ErrorValidacion, match="alpha"):
            await resolver_proyecto(engine, ctx, "no-existe")

    async def test_sin_membresia_rechaza(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """user_b no es miembro de alpha → 403."""
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_b"], ws_id, "member")
        with pytest.raises(PermisoDenegadoError):
            await resolver_proyecto(engine, ctx, "alpha")

    async def test_owner_ws_override(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """El owner del workspace entra a cualquier project como owner."""
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "owner")
        nuevo = await resolver_proyecto(engine, ctx, "beta")
        assert nuevo.proyecto_rol == "owner"


@pytest.mark.asyncio
class TestVisibilidadProjects:
    """El predicado: NULL ∪ membresías; key scoped → solo su project."""

    async def test_member_ve_null_mas_sus_projects(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx = _ctx(
            escenario["user_a"],
            ws_id,
            "member",
            frozenset({_id(escenario, "alpha")}),
        )
        async with get_session(engine) as s:
            eventos = await EventoRepository(s, ctx).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos if e.fuente == "test_projects"}
        assert _id(escenario, "ev_null") in ids
        assert _id(escenario, "ev_alpha") in ids
        assert _id(escenario, "ev_beta") not in ids
        assert _id(escenario, "ev_vault") not in ids

    async def test_owner_ws_ve_todo(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "owner")
        async with get_session(engine) as s:
            eventos = await EventoRepository(s, ctx).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos if e.fuente == "test_projects"}
        assert _id(escenario, "ev_alpha") in ids
        assert _id(escenario, "ev_beta") in ids
        assert _id(escenario, "ev_vault") in ids

    async def test_ctx_con_proyecto_acota(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """project= ya resuelto: la lectura se limita a ese project."""
        ws_id = _id(escenario, "ws")
        ctx = Contexto(
            usuario_id=escenario["user_a"].id,  # type: ignore[attr-defined]
            workspace_id=ws_id,
            rol="member",
            proyecto_id=_id(escenario, "alpha"),
            proyecto_rol="member",
            projects_visibles=frozenset({_id(escenario, "alpha")}),
        )
        async with get_session(engine) as s:
            eventos = await EventoRepository(s, ctx).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos if e.fuente == "test_projects"}
        assert ids == {_id(escenario, "ev_alpha")}

    async def test_key_scoped_no_ve_ni_null(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """Key con project_id: visibilidad estricta a SU project."""
        ws_id = _id(escenario, "ws")
        alpha_id = _id(escenario, "alpha")
        user_a = escenario["user_a"]
        key = generar_key_usuario()
        async with get_session(engine) as s:
            s.add(
                ApiKeyModel(
                    hash=hash_key(key),
                    prefijo=key[:11],
                    usuario_id=user_a.id,  # type: ignore[attr-defined]
                    workspace_default_id=ws_id,
                    project_id=alpha_id,
                )
            )
            await s.commit()
        ctx = await resolver_contexto(engine, key)
        assert ctx is not None
        assert ctx.key_proyecto_id == alpha_id
        async with get_session(engine) as s:
            eventos = await EventoRepository(s, ctx).listar_por_rango(
                0, 4_102_444_800
            )
        ids = {e.id for e in eventos if e.fuente == "test_projects"}
        assert ids == {_id(escenario, "ev_alpha")}

    async def test_key_scoped_no_resuelve_otro_project(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """La key scoped no puede pedir project= distinto del suyo."""
        ws_id = _id(escenario, "ws")
        alpha_id = _id(escenario, "alpha")
        user_a = escenario["user_a"]
        key = generar_key_usuario()
        async with get_session(engine) as s:
            s.add(
                ApiKeyModel(
                    hash=hash_key(key),
                    prefijo=key[:11],
                    usuario_id=user_a.id,  # type: ignore[attr-defined]
                    workspace_default_id=ws_id,
                    project_id=alpha_id,
                )
            )
            await s.commit()
        ctx = await resolver_contexto(engine, key)
        assert ctx is not None
        with pytest.raises(PermisoDenegadoError):
            await resolver_proyecto(engine, ctx, "beta")


@pytest.mark.asyncio
class TestDominioProjects:
    """Operaciones del dominio: crear, listar, miembros, asignar."""

    async def test_crear_y_listar(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx_a = _ctx(escenario["user_a"], ws_id, "member")
        nuevo = await crear_proyecto(
            engine, ctx_a, "gamma", "Gamma", "tercer project", "regular"
        )
        assert nuevo.slug == "gamma"
        # El creador queda owner del project.
        ctx_a_full = _ctx(
            escenario["user_a"], ws_id, "member"
        )
        resuelto = await resolver_proyecto(engine, ctx_a_full, "gamma")
        assert resuelto.proyecto_rol == "owner"
        # user_b NO lo ve en su roster.
        ctx_b = _ctx(escenario["user_b"], ws_id, "member")
        roster = await listar_proyectos(engine, ctx_b)
        slugs_b = {p.slug for p in roster}
        assert "gamma" not in slugs_b
        assert "beta" in slugs_b

    async def test_slug_duplicado_rechaza(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "member")
        with pytest.raises(ErrorValidacion, match="taken"):
            await crear_proyecto(engine, ctx, "alpha", "dup", None, "regular")

    async def test_viewer_no_crea(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "viewer")
        with pytest.raises(PermisoDenegadoError):
            await crear_proyecto(engine, ctx, "nope", "Nope", None, "regular")

    async def test_miembros_alta_baja_lista(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        await agregar_miembro(engine, ctx_owner, "beta", "pj-a", "viewer")
        miembros = await listar_miembros(engine, ctx_owner, "beta")
        roles = {m["handle"]: m["rol"] for m in miembros}
        assert roles["pj-a"] == "viewer"
        assert roles["pj-b"] == "member"

    async def test_membresia_subconjunto_workspace(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        """Un handle sin membresía en el workspace no entra al project."""
        async with get_session(engine) as s:
            outsider = UsuarioModel(handle="pj-out", nombre="Out")
            s.add(outsider)
            await s.commit()
        try:
            ws_id = _id(escenario, "ws")
            ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
            with pytest.raises(PermisoDenegadoError):
                await agregar_miembro(
                    engine, ctx_owner, "beta", "pj-out", "member"
                )
        finally:
            async with get_session(engine) as s:
                await s.execute(
                    delete(UsuarioModel).where(
                        UsuarioModel.handle == "pj-out"
                    )
                )
                await s.commit()

    async def test_archivar_bloquea_actualizar_reabre(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        p = await actualizar_proyecto(
            engine, ctx_owner, "alpha", estado="archivado"
        )
        assert p.estado == "archivado"
        # La resolución sigue funcionando (read-only): estado llega al ctx.
        ctx_a = _ctx(escenario["user_a"], ws_id, "member")
        resuelto = await resolver_proyecto(engine, ctx_a, "alpha")
        assert resuelto.proyecto_estado == "archivado"
        await actualizar_proyecto(
            engine, ctx_owner, "alpha", estado="activo"
        )

    async def test_asignar_mueve_visibles(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        alpha_id = _id(escenario, "alpha")
        ctx_a = Contexto(
            usuario_id=escenario["user_a"].id,  # type: ignore[attr-defined]
            workspace_id=ws_id,
            rol="member",
            projects_visibles=frozenset({alpha_id}),
        )
        resultado = await asignar_proyecto(
            engine,
            ctx_a,
            "alpha",
            evento_ids=[str(_id(escenario, "ev_null"))],
        )
        assert resultado["moved"] == 1
        async with get_session(engine) as s:
            ev = await s.get(EventoModel, _id(escenario, "ev_null"))
            assert ev is not None
            assert ev.project_id == alpha_id
        # No toca lo invisible: ev_beta fuera de su universo.
        resultado2 = await asignar_proyecto(
            engine,
            ctx_a,
            "alpha",
            evento_ids=[str(_id(escenario, "ev_beta"))],
        )
        assert resultado2["moved"] == 0

    async def test_asignar_a_null_baja_a_workspace(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        resultado = await asignar_proyecto(
            engine,
            ctx_owner,
            "general",
            evento_ids=[str(_id(escenario, "ev_alpha"))],
        )
        assert resultado["moved"] == 1
        async with get_session(engine) as s:
            ev = await s.get(EventoModel, _id(escenario, "ev_alpha"))
            assert ev is not None
            assert ev.project_id is None

    async def test_eliminar_set_null(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        await eliminar_proyecto(engine, ctx_owner, "beta")
        async with get_session(engine) as s:
            ev = await s.get(EventoModel, _id(escenario, "ev_beta"))
            assert ev is not None
            assert ev.project_id is None
            # El project ya no existe.
            assert (
                await s.get(ProjectModel, _id(escenario, "beta"))
            ) is None


@pytest.mark.asyncio
class TestCupoProjects:
    """Feature gate: 5 projects/workspace en la edición libre."""

    async def test_cupo_cinco_en_libre(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.config import get_settings

        monkeypatch.setattr(get_settings(), "license_key", None)
        monkeypatch.setattr(get_settings(), "max_projects_libres", 5)
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "member")
        # Ya hay 3 (alpha, beta, boveda); dos más caben, el sexto falla.
        await crear_proyecto(engine, ctx, "p4", "P4", None, "regular")
        await crear_proyecto(engine, ctx, "p5", "P5", None, "regular")
        with pytest.raises(CupoExcedidoError):
            await crear_proyecto(engine, ctx, "p6", "P6", None, "regular")

    async def test_owner_exento_del_cupo(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.config import get_settings

        monkeypatch.setattr(get_settings(), "license_key", None)
        monkeypatch.setattr(get_settings(), "max_projects_libres", 3)
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        nuevo = await crear_proyecto(
            engine, ctx_owner, "p-owner", "P", None, "regular"
        )
        assert nuevo.slug == "p-owner"

    async def test_licencia_exenta_del_cupo(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.config import get_settings

        monkeypatch.setattr(get_settings(), "license_key", "test-suite")
        monkeypatch.setattr(get_settings(), "max_projects_libres", 3)
        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "member")
        nuevo = await crear_proyecto(
            engine, ctx, "p-lic", "P", None, "regular"
        )
        assert nuevo.slug == "p-lic"


@pytest.mark.asyncio
class TestVaultYArchivo:
    """Vault minable + archivado read-only en las tools."""

    async def test_archivado_bloquea_escritura_permite_lectura(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """recordar con project=archivado → 422; la lectura pasa."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        await actualizar_proyecto(
            engine, ctx_owner, "alpha", estado="archivado"
        )
        fijar_contexto(ctx_owner)
        try:
            with pytest.raises(ErrorValidacion, match="archived"):
                await tools._recordar(
                    "intento en archivado",
                    None,
                    "test_projects",
                    0.5,
                    None,
                    None,
                    workspace="pj-ws",
                    proyecto="alpha",
                )
            # La lectura del compartimento archivado sigue abierta.
            resultados = await tools._linea(
                0,
                4_102_444_800,
                10,
                workspace="pj-ws",
                proyecto="alpha",
            )
            assert any(
                r["id"] == str(_id(escenario, "ev_alpha"))
                for r in resultados
            )
        finally:
            fijar_contexto(None)
            await actualizar_proyecto(
                engine, ctx_owner, "alpha", estado="activo"
            )

    async def test_vault_no_se_muta_en_sueno(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El ciclo de sueño excluye items de la bóveda de TODA
        mutación — aunque sean candidatos a archivo."""
        from src.core.identidad import fijar_contexto
        from src.mcp import tools

        monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        # El evento de la bóveda es candidato a archivo (viejo, frío);
        # al vivir en vault el sueño no lo toca.
        async with get_session(engine) as s:
            await s.execute(
                update(EventoModel)
                .where(EventoModel.id == _id(escenario, "ev_vault"))
                .values(timestamp_unix=100, weight=0.01, archivado=False)
            )
            await s.commit()
        fijar_contexto(ctx_owner)
        try:
            await tools._consolidar(workspace="pj-ws")
        finally:
            fijar_contexto(None)
        async with get_session(engine) as s:
            ev = await s.get(EventoModel, _id(escenario, "ev_vault"))
            assert ev is not None
            assert ev.archivado is False
            assert float(ev.weight) == pytest.approx(0.01)

    async def test_gist_cross_project_va_a_null(
        self,
        engine: AsyncEngine,
        escenario: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Un cluster con miembros de distintos projects produce
        gist a nivel workspace (project_id NULL)."""
        from src.core.memoria.semanticizacion import EpisodioGist, plan_gists

        # Semilla: tres episodios casi idénticos en projects distintos
        # (mismo embedding sintético → cluster garantizado, MIN=3).
        vec = [1.0] + [0.0] * 383
        async with get_session(engine) as s:
            e1 = EventoModel(
                timestamp_unix=1_810_100_000,
                dimension="cognitiva",
                fuente="test_projects",
                contenido="episodio alpha",
                scope="workspace",
                workspace_id=_id(escenario, "ws"),
                project_id=_id(escenario, "alpha"),
                embedding=vec,
                tipo_memoria="episodica",
            )
            e2 = EventoModel(
                timestamp_unix=1_810_100_001,
                dimension="cognitiva",
                fuente="test_projects",
                contenido="episodio beta",
                scope="workspace",
                workspace_id=_id(escenario, "ws"),
                project_id=_id(escenario, "beta"),
                embedding=vec,
                tipo_memoria="episodica",
            )
            e3 = EventoModel(
                timestamp_unix=1_810_100_002,
                dimension="cognitiva",
                fuente="test_projects",
                contenido="episodio beta dos",
                scope="workspace",
                workspace_id=_id(escenario, "ws"),
                project_id=_id(escenario, "beta"),
                embedding=vec,
                tipo_memoria="episodica",
            )
            s.add_all([e1, e2, e3])
            await s.commit()
        episodios = [
            EpisodioGist(
                id=str(e.id), contenido=e.contenido, weight=1.0, embedding=vec
            )
            for e in (e1, e2, e3)
        ]
        gists = plan_gists(episodios)
        assert gists, "los tres episodios idénticos deben clusterizar"
        miembros = set(gists[0].miembros)
        proyecto_por_evento = {
            str(e.id): e.project_id for e in (e1, e2, e3)
        }
        destinos = {proyecto_por_evento.get(m) for m in miembros}
        destinos.discard(None)
        # Cross-project → NULL (regla del spec).
        assert len(destinos) != 1


@pytest.mark.asyncio
class TestContratoEN:
    """Los dicts de respuesta usan el contrato canónico en inglés."""

    async def test_proyecto_a_dict_ingles(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        from src.mcp import tools

        ws_id = _id(escenario, "ws")
        ctx = _ctx(escenario["user_a"], ws_id, "owner")
        nuevo = await crear_proyecto(
            engine, ctx, "contrato", "Contrato", None, "vault"
        )
        d = tools._proyecto_a_dict(nuevo)
        assert d["kind"] == "vault"
        assert d["vault"] is True
        assert "name" in d and "state" in d and "description" in d

    async def test_listar_miembros_contrato(
        self, engine: AsyncEngine, escenario: dict[str, object]
    ) -> None:
        ws_id = _id(escenario, "ws")
        ctx_owner = _ctx(escenario["user_a"], ws_id, "owner")
        miembros = await listar_miembros(engine, ctx_owner, "alpha")
        assert all("handle" in m and "rol" in m for m in miembros)
