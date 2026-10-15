"""Tests unitarios del instalador de skills (``nous skills``)."""

from pathlib import Path

from src.cli import skills


def test_instalar_agents_crea_skills_y_agentes(tmp_path: Path) -> None:
    """--target .agents genera skills/<n>/SKILL.md y agents/<n>.md."""
    resultado = skills.instalar(".agents", tmp_path)
    escritos = {Path(p).name for p in resultado["instalados"]}
    assert "SKILL.md" in escritos
    assert "nous-keeper.md" in escritos
    assert (tmp_path / ".agents" / "skills" / "nous-remember" / "SKILL.md").exists()
    assert (tmp_path / ".agents" / "agents" / "nous-keeper.md").exists()


def test_instalar_opencode_usa_agent_singular(tmp_path: Path) -> None:
    """--target .opencode coloca agentes bajo ``agent/``."""
    skills.instalar(".opencode", tmp_path)
    assert (tmp_path / ".opencode" / "agent" / "nous-keeper.md").exists()


def test_instalar_ruta_personalizada(tmp_path: Path) -> None:
    """Una ruta custom recibe skills/ + agents/ estándar."""
    skills.instalar("mi-tool", tmp_path)
    assert (tmp_path / "mi-tool" / "skills" / "nous-recall" / "SKILL.md").exists()


def test_contenido_skill_documenta_contexto_incompleto(tmp_path: Path) -> None:
    """El skill recordar explica qué hacer con completo=false."""
    skills.instalar(".agents", tmp_path)
    texto = (
        tmp_path / ".agents" / "skills" / "nous-remember" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "contexto.completo" in texto
    assert "relaciona_ids" in texto
    assert "tags" in texto


def test_skill_universo_documenta_objetivos_y_loops(tmp_path: Path) -> None:
    """El skill universe cubre objetivos, proyección y compromisos."""
    skills.instalar(".agents", tmp_path)
    texto = (
        tmp_path / ".agents" / "skills" / "nous-universe" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for tool in (
        "crear_objetivo",
        "listar_objetivos",
        "proyectar_trayectoria",
        "compromisos_abiertos",
        "cerrar_compromiso",
    ):
        assert tool in texto


def test_skills_somaticos_cubren_ritual_y_mapa(tmp_path: Path) -> None:
    """checkin/mirror/somatic documentan dimensiones y señales."""
    skills.instalar(".agents", tmp_path)
    checkin = (
        tmp_path / ".agents" / "skills" / "nous-checkin" / "SKILL.md"
    ).read_text(encoding="utf-8")
    somatica = (
        tmp_path / ".agents" / "skills" / "nous-somatic" / "SKILL.md"
    ).read_text(encoding="utf-8")
    mirror = (
        tmp_path / ".agents" / "agents" / "nous-mirror.md"
    ).read_text(encoding="utf-8")
    assert "somatica" in checkin and "amplitud" in checkin
    for patron in ("absolutismos", "Frustración", "Flow", "conductual"):
        assert patron in somatica
    assert "diagnosticar" in mirror or "clínicas" in mirror


def test_cmd_skills_list_retorna_cero(
    tmp_path: Path, capsys
) -> None:
    """``nous skills list`` enumera artefactos sin tocar disco."""
    assert skills.cmd_skills("list", ".agents", tmp_path) == 0
    salida = capsys.readouterr().out
    assert "nous-remember" in salida
    assert "nous-keeper" in salida
    assert "nous-pathfinder" in salida
    assert "nous-preferences" in salida
