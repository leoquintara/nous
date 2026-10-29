"""Tests unitarios de los resources MCP de solo lectura."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.core.modelos import Dimension, Evento, Trayectoria
from src.mcp import resources


def _evento(
    identificador: str, dimension: Dimension, amplitud: float = 0.5
) -> Evento:
    return Evento(
        id=identificador,
        timestamp_unix=1_700_000_000,
        dimension=dimension,
        contenido="contenido de prueba",
        amplitud=amplitud,
    )


class TestResumen:
    """Comportamiento del resource memory://summary/{period}."""

    async def test_periodo_invalido_devuelve_error_json(self) -> None:
        salida = json.loads(await resources.summary("monthly"))
        assert "error" in salida
        assert set(salida["valid_periods"]) == {"daily", "weekly"}

    async def test_resumen_agrupa_por_dimension(self) -> None:
        eventos = [
            _evento("1", Dimension.COGNITIVA, 0.9),
            _evento("2", Dimension.COGNITIVA, 0.3),
            _evento("3", Dimension.SOMATICA, 0.7),
        ]
        with patch.object(
            resources, "_eventos_recientes", AsyncMock(return_value=eventos)
        ):
            salida = json.loads(await resources.summary("daily"))
        assert salida["total_events"] == 3
        assert salida["by_dimension"]["cognitive"]["total"] == 2
        assert salida["by_dimension"]["somatic"]["total"] == 1
        assert salida["by_dimension"]["behavioral"]["total"] == 0
        assert (
            salida["by_dimension"]["cognitive"]["mean_amplitude"] == 0.6
        )
        assert (
            salida["top_events"][0]["amplitude"] == 0.9
        )

    async def test_resumen_sin_eventos(self) -> None:
        with patch.object(
            resources, "_eventos_recientes", AsyncMock(return_value=[])
        ):
            salida = json.loads(await resources.summary("weekly"))
        assert salida["total_events"] == 0
        assert salida["by_dimension"]["cognitive"]["mean_amplitude"] is None

    async def test_alias_resumen_acepta_periodo_espanol(self) -> None:
        """El alias legado traduce su argumento y comparte el payload EN."""
        with patch.object(
            resources, "_eventos_recientes", AsyncMock(return_value=[])
        ):
            salida = json.loads(await resources.resumen("semanal"))
        assert salida["total_events"] == 0


class TestPatronesFlow:
    """Comportamiento del resource memory://patterns/flow."""

    async def test_detecta_racha_activa(self) -> None:
        base = 1_700_000_000
        eventos = [
            Evento(
                id=str(i),
                timestamp_unix=base + i,
                dimension=Dimension.CONDUCTUAL,
                contenido=f"acción {i}",
            )
            for i in range(4)
        ]
        with patch.object(
            resources, "_eventos_recientes", AsyncMock(return_value=eventos)
        ):
            salida = json.loads(await resources.flow_patterns())
        assert len(salida["trajectories"]) == 1
        assert salida["trajectories"][0]["active"] is True
        assert salida["trajectories"][0]["kind"] == "behavioral_streak"

    async def test_sin_eventos_retorna_lista_vacia(self) -> None:
        with patch.object(
            resources, "_eventos_recientes", AsyncMock(return_value=[])
        ):
            salida = json.loads(await resources.flow_patterns())
        assert salida["trajectories"] == []
        assert salida["events_analyzed"] == 0


class TestSerializadores:
    """Helpers de serialización de los resources."""

    def test_trayectoria_a_dict_campos_completos(self) -> None:
        trayectoria = Trayectoria(
            tipo="racha_somatica",
            fecha_inicio=100,
            fecha_fin=None,
            descripcion="Racha de 3 eventos",
            eventos_ids=("a", "b", "c"),
            amplitud_promedio=0.8,
            confianza=0.5,
            activa=True,
        )
        salida = resources._trayectoria_a_dict(trayectoria)
        assert salida["eventos"] == 3
        assert salida["fecha_fin"] is None
        assert salida["activa"] is True

    def test_evento_breve_trunca_contenido(self) -> None:
        evento = Evento(
            id="x",
            timestamp_unix=1,
            dimension=Dimension.COGNITIVA,
            contenido="c" * 300,
        )
        assert len(resources._evento_breve(evento)["contenido"]) == 200


@pytest.mark.asyncio
async def test_prompts_registrados_en_servidor() -> None:
    """Verifica que los prompts guiados quedaron registrados."""
    from src.mcp.server import mcp_server

    prompts = {p.name for p in await mcp_server.list_prompts()}
    assert {"reflexion_diaria", "analisis_semanal", "preparar_decision"} <= (
        prompts
    )


@pytest.mark.asyncio
async def test_resources_registrados_en_servidor() -> None:
    """Verifica que los resources quedaron registrados en FastMCP."""
    from src.mcp.server import mcp_server

    registrados = await mcp_server.list_resources()
    plantillas = await mcp_server.list_resource_templates()
    uris = {str(r.uri) for r in registrados}
    uris |= {t.uriTemplate for t in plantillas}
    assert "memoria://resumen/{periodo}" in uris
    assert "memoria://patrones/flow" in uris
    assert "memoria://proyeccion/actual" in uris
