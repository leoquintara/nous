"""Tests del troceo de contenido para retrieval a nivel fragmento."""

from src.core.chunking import TAMANO_MAXIMO, trocear


def test_trocear_contenido_corto_devuelve_un_solo_fragmento() -> None:
    """Contenido dentro del objetivo no se fragmenta."""
    assert trocear("texto breve") == ["texto breve"]


def test_trocear_contenido_vacio_devuelve_lista_vacia() -> None:
    """Contenido vacío no genera fragmentos."""
    assert trocear("   ") == []


def test_trocear_respeta_encabezados_como_fronteras() -> None:
    """Los encabezados ATX delimitan las secciones empaquetadas."""
    seccion_a = "## Sección A\n" + "contenido a. " * 60
    seccion_b = "## Sección B\n" + "contenido b. " * 60
    piezas = trocear(seccion_a + "\n\n" + seccion_b, objetivo=700)

    assert len(piezas) == 2
    assert piezas[0].startswith("## Sección A")
    assert piezas[1].startswith("## Sección B")


def test_trocear_ninguna_pieza_supera_el_maximo() -> None:
    """Una sección gigante se rebana por líneas sin exceder el techo."""
    seccion = "## Gigante\n" + "\n".join(
        f"línea {i} " + "x" * 50 for i in range(80)
    )
    piezas = trocear(seccion, objetivo=1200, maximo=2000, solape=150)

    assert len(piezas) > 1
    assert all(len(p) <= TAMANO_MAXIMO + 60 for p in piezas)


def test_trocear_rebanado_conserva_solape_entre_piezas() -> None:
    """Las piezas de una sección rebanada comparten la cola anterior."""
    seccion = "## S\n" + "\n".join(
        f"linea{i} " + "x" * 40 for i in range(70)
    )
    piezas = trocear(seccion, objetivo=900, maximo=900, solape=150)

    assert len(piezas) > 2
    assert piezas[1].split("\n")[0] in piezas[0].split("\n")[-4:]
