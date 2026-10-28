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


def test_skill_workspaces_documenta_scope_y_override(
    tmp_path: Path,
) -> None:
    """nous-workspaces explica scopes, workspace=, roles y somática."""
    skills.instalar(".agents", tmp_path)
    texto = (
        tmp_path / ".agents" / "skills" / "nous-workspaces" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for concepto in ("scope", "personal", "workspace=", "viewer", "owner"):
        assert concepto in texto
    assert "somátic" in texto


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


def test_registro_incluye_las_skills_migradas() -> None:
    """Las skills nuevas (sesión, guardrails, corrección…) están en
    el registro empaquetado."""
    nombres = {art.nombre for art in skills.ARTEFACTOS}
    for nombre in (
        "nous-sesion",
        "nous-guardrails",
        "nous-calibracion",
        "nous-correccion",
        "nous-resolve",
        "nous-archive",
        "nous-persona",
        "nous-soulkeeper",
    ):
        assert nombre in nombres, f"falta {nombre} en ARTEFACTOS"


def test_nombres_de_artefacto_son_unicos() -> None:
    """No hay dos artefactos con el mismo nombre en el registro."""
    nombres = [art.nombre for art in skills.ARTEFACTOS]
    assert len(nombres) == len(set(nombres))


def test_skill_persona_documenta_banco_y_cli(tmp_path: Path) -> None:
    """nous-persona explica el banco persona.respuestas y el CLI."""
    skills.instalar(".agents", tmp_path)
    texto = (
        tmp_path / ".agents" / "skills" / "nous-persona" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for concepto in (
        "persona.respuestas",
        "nous persona set",
        "nous persona render",
        "SOUL.md",
        "USER.md",
    ):
        assert concepto in texto


def test_agente_soulkeeper_se_instala_y_audita(tmp_path: Path) -> None:
    """nous-soulkeeper se instala como agente y documenta la auditoría
    del banco contra los archivos."""
    skills.instalar(".agents", tmp_path)
    texto = (
        tmp_path / ".agents" / "agents" / "nous-soulkeeper.md"
    ).read_text(encoding="utf-8")
    assert "persona.respuestas" in texto
    assert "leer_preferencia" in texto
    assert "propón" in texto.lower() or "propon" in texto.lower()


def test_ningun_artefacto_ordena_escribir_a_gbrain_o_memlord() -> None:
    """Los artefactos empaquetados no llevan instrucciones activas a
    los stores retirados."""
    for art in skills.ARTEFACTOS:
        texto = art.contenido.lower()
        assert "gbrain" not in texto, f"{art.nombre} menciona gbrain"
        assert "store_memory" not in texto, f"{art.nombre} usa memlord"
