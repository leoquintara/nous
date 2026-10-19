"""Tests unitarios del clasificador de tipos de memoria (ADR-003)."""

from unittest.mock import AsyncMock

from src.core.decision.provider import (
    DecisionAnswer,
    DecisionResult,
    QuestionType,
)
from src.core.memoria.tipos import (
    TipoMemoria,
    clasificar_tipo_memoria,
    clasificar_tipo_memoria_heuristico,
)


def test_heuristico_detecta_procedimental() -> None:
    """Un contenido de "cómo hacer" clasifica como procedimental."""
    assert clasificar_tipo_memoria_heuristico(
        "para depurar uso el comando pytest con breakpoints"
    ) == TipoMemoria.PROCEDIMENTAL


def test_heuristico_detecta_semantica() -> None:
    """Un hecho abstracto clasifica como semántica."""
    assert clasificar_tipo_memoria_heuristico(
        "PostgreSQL 18 es una versión que soporta nuevas features"
    ) == TipoMemoria.SEMANTICA


def test_heuristico_detecta_episodica() -> None:
    """Un evento personal con tiempo clasifica como episódica."""
    assert clasificar_tipo_memoria_heuristico(
        "ayer tuve una reunión tensa con el cliente"
    ) == TipoMemoria.EPISODICA


def test_heuristico_default_es_episodica() -> None:
    """Sin marcadores la memoria agéntica es episódica por defecto."""
    assert clasificar_tipo_memoria_heuristico("algo sin señales") == (
        TipoMemoria.EPISODICA
    )


async def test_provider_gana_sobre_heuristico() -> None:
    """Con provider, su choice calibrado decide el tipo."""
    provider = AsyncMock()
    provider.name = "falso"
    provider.predict.return_value = DecisionResult(
        answers={
            "tipo_memoria": DecisionAnswer(
                question_type=QuestionType.CHOICE,
                value="procedimental",
                probability=0.9,
            )
        }
    )

    resultado = await clasificar_tipo_memoria("texto cualquiera", provider)

    assert resultado == TipoMemoria.PROCEDIMENTAL


async def test_respuesta_invalida_degrada_a_heuristico() -> None:
    """Un provider que responde fuera de schema no rompe nada."""
    provider = AsyncMock()
    provider.name = "falso"
    provider.predict.return_value = DecisionResult(answers={})

    resultado = await clasificar_tipo_memoria(
        "el comando para compilar es yarn build", provider
    )

    assert resultado == TipoMemoria.PROCEDIMENTAL


async def test_sin_provider_usa_heuristico() -> None:
    """Sin provider la clasificación es la heurística pura."""
    resultado = await clasificar_tipo_memoria(
        "cómo instalar el framework", None
    )
    assert resultado == TipoMemoria.PROCEDIMENTAL


# --- estimar_estado_emocional (valencia léxica) ---


def test_estado_emocional_neutro_devuelve_none() -> None:
    """Texto sin marcadores afectivos no inventa un estado."""
    from src.core.memoria.tipos import estimar_estado_emocional

    assert estimar_estado_emocional("compilé el proyecto con yarn") is None


def test_estado_emocional_negativo_baja_de_la_mitad() -> None:
    """Marcadores de estrés/tristeza dan valencia < 0.5."""
    from src.core.memoria.tipos import estimar_estado_emocional

    valor = estimar_estado_emocional(
        "estoy muy estresado y agotado, me duele la cabeza"
    )
    assert valor is not None and valor < 0.5


def test_estado_emocional_positivo_sube_de_la_mitad() -> None:
    """Marcadores de logro/calma dan valencia > 0.5."""
    from src.core.memoria.tipos import estimar_estado_emocional

    valor = estimar_estado_emocional(
        "logré cerrar el bug, me siento tranquilo y en paz"
    )
    assert valor is not None and valor > 0.5


def test_estado_emocional_mixto_se_cancela() -> None:
    """Señales opuestas en igual número quedan ~neutras."""
    from src.core.memoria.tipos import estimar_estado_emocional

    valor = estimar_estado_emocional("estresado pero logré avanzar")
    assert valor is not None
    assert 0.4 <= valor <= 0.6
