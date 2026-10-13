"""Tests unitarios de extracción de entidades para el hint de contexto."""

from src.core.contexto import (
    extraer_entidades,
    extraer_hashtags,
    fusionar_tags,
)


def test_extraer_entidades_detecta_nombre_propio() -> None:
    """Un nombre capitalizado dentro de la frase se detecta."""
    assert extraer_entidades("me estresa mucho Franz") == ["Franz"]


def test_extraer_entidades_incluye_primera_palabra_capitalizada() -> None:
    """La entidad al inicio de la frase también cuenta."""
    assert extraer_entidades("Franz me estresa mucho") == ["Franz"]


def test_extraer_entidades_sin_capitalizados_retorna_vacio() -> None:
    """Texto todo en minúsculas no produce entidades."""
    assert extraer_entidades("hoy tuve mucho trabajo") == []


def test_extraer_entidades_dedup_y_orden() -> None:
    """Repetidas colapsan conservando el orden de aparición."""
    assert extraer_entidades("Maria vio a Pedro y Maria sonrió") == [
        "Maria",
        "Pedro",
    ]


def test_extraer_entidades_ignora_siglas_y_cortas() -> None:
    """Palabras de menos de 4 letras capitalizadas no cuentan."""
    assert extraer_entidades("El Sr. Oz vino hoy") == []


def test_extraer_hashtags_ignora_numericos() -> None:
    """`#123` no cuenta (no es etiqueta semántica); #trabajo sí."""
    assert extraer_hashtags("avance #trabajo #123 x") == ["trabajo"]


def test_extraer_hashtags_normaliza_minusculas() -> None:
    """#IA y #ia colapsan en un solo tag."""
    assert extraer_hashtags("metí #IA con #ia") == ["ia"]


def test_fusionar_tags_dedup_case_insensitive() -> None:
    """Explícitos + hashtags colapsan en minúscula sin duplicar."""
    assert fusionar_tags(["Trabajo", "IA"], "metí #trabajo y #casa") == [
        "trabajo",
        "ia",
        "casa",
    ]
