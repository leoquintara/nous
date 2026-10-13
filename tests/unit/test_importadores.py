"""Tests unitarios de los parsers de importación (sin DB)."""

import json
from pathlib import Path

from src.cli import importadores


def test_inferir_directorio_es_obsidian(tmp_path: Path) -> None:
    """Un directorio se interpreta como vault Obsidian."""
    assert importadores._inferir(tmp_path) == "obsidian"


def test_inferir_jsonl_por_extension(tmp_path: Path) -> None:
    """Un archivo .jsonl se interpreta como JSONL nativo."""
    f = tmp_path / "mem.jsonl"
    f.write_text("{}", encoding="utf-8")
    assert importadores._inferir(f) == "jsonl"


def test_vault_obsidian_extrae_frontmatter_y_wikilinks(tmp_path: Path) -> None:
    """Notas con frontmatter/tags y [[wikilinks]] generan vínculos."""
    (tmp_path / "NotaA.md").write_text(
        "---\ntags: proyecto alpha\n---\nVer [[NotaB]] para detalles #idea",
        encoding="utf-8",
    )
    (tmp_path / "NotaB.md").write_text(
        "Detalle de la idea referenciada desde [[NotaA]].",
        encoding="utf-8",
    )
    filas, vinculos, errores = importadores.leer_fuente(
        tmp_path, "obsidian", None
    )
    assert errores == 0
    assert len(filas) == 2
    a = next(f for f in filas if f["contenido"].startswith("NotaA"))
    assert "proyecto" in a["tags"]
    assert "alpha" in a["tags"]
    assert "idea" in a["tags"]  # hashtag del cuerpo fusionado
    # Los wikilinks A→B y B→A crean un vínculo bidireccional.
    assert len(vinculos) == 2


def test_vault_ignora_notas_vacias(tmp_path: Path) -> None:
    """Un .md sin cuerpo tras el frontmatter no genera fila."""
    (tmp_path / "vacia.md").write_text("---\ntags: x\n---\n", encoding="utf-8")
    filas, _, errores = importadores.leer_fuente(tmp_path, "obsidian", None)
    assert filas == []
    assert errores == 0


def test_memlord_export_normaliza_campos(tmp_path: Path) -> None:
    """Filas {name, content, memory_type} de memlord se normalizan."""
    f = tmp_path / "memlord.jsonl"
    f.write_text(
        json.dumps(
            {
                "name": "decision-postgres",
                "content": "elegimos postgres por pgvector",
                "memory_type": "decision",
                "tags": ["db"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    filas, _, errores = importadores.leer_fuente(f, "memlord", None)
    assert errores == 0
    assert len(filas) == 1
    assert filas[0]["fuente"] == "memlord"
    assert "decision-postgres" in filas[0]["contenido"]
    assert filas[0]["amplitud"] == 0.6  # decision → más peso
    assert filas[0]["tags"] == ["db"]


def test_gbrain_export_usa_fact_y_fuente(tmp_path: Path) -> None:
    """Filas {fact: ...} de gbrain se leen con fuente propia."""
    f = tmp_path / "gbrain.jsonl"
    f.write_text(
        json.dumps({"fact": "el usuario prefiere español"}) + "\n",
        encoding="utf-8",
    )
    filas, _, _ = importadores.leer_fuente(f, "gbrain", "mi-gbrain")
    assert filas[0]["contenido"] == "el usuario prefiere español"
    assert filas[0]["fuente"] == "mi-gbrain"


def test_json_nativo_roundtrip_campos(tmp_path: Path) -> None:
    """El formato `nous export` conserva tags y rama."""
    f = tmp_path / "export.json"
    f.write_text(
        json.dumps(
            [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "timestamp_unix": 1700000000,
                    "dimension": "somatica",
                    "fuente": "nous",
                    "contenido": "dormí bien #descanso",
                    "amplitud": 0.7,
                    "tags": ["salud"],
                    "rama": "main",
                }
            ]
        ),
        encoding="utf-8",
    )
    filas, _, errores = importadores.leer_fuente(f, "json", None)
    assert errores == 0
    assert filas[0]["dimension"] == "somatica"
    assert sorted(filas[0]["tags"]) == ["descanso", "salud"]
    assert filas[0]["rama"] == "main"


def test_jsonl_corrupto_cuenta_errores(tmp_path: Path) -> None:
    """Líneas no-JSON cuentan como errores, no abortan."""
    f = tmp_path / "malo.jsonl"
    f.write_text(
        '{"id": "11111111-1111-1111-1111-111111111111", "contenido": "x",'
        ' "timestamp_unix": 1, "dimension": "cognitiva"}\nbasura\n',
        encoding="utf-8",
    )
    filas, _, errores = importadores.leer_fuente(f, "jsonl", None)
    assert len(filas) == 1
    assert errores == 1
