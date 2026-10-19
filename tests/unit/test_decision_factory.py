"""Tests del factory de providers System One (ADR-002)."""

from src.core.decision.factory import construir_provider


def test_none_devuelve_none() -> None:
    """decision_provider=none desactiva la capa por completo."""
    assert construir_provider("none") is None


def test_auto_sin_paquetes_devuelve_ollama_o_none() -> None:
    """auto intenta Laya → Ollama; sin Laya cae al adaptador HTTP."""
    provider = construir_provider("auto")
    # Sin el extra 'decision', Laya no se instancia; Ollama es solo
    # un adaptador HTTP — siempre construible (falla en predict, no
    # en construcción).
    assert provider is None or provider.name.startswith(
        ("laya", "ollama")
    )


def test_jev_sin_key_devuelve_none() -> None:
    """Jev sin api key no se instancia."""
    assert construir_provider("jev", jev_api_key=None) is None


def test_jev_sin_opt_in_privacidad_bloqueado() -> None:
    """Jev con key pero sin flag de privacidad → bloqueado."""
    assert (
        construir_provider(
            "jev", jev_api_key="k", permitir_externos=False
        )
        is None
    )


def test_jev_con_doble_opt_in_se_instancia() -> None:
    """Con key + flag, Jev se instancia como provider externo."""
    provider = construir_provider(
        "jev", jev_api_key="k", permitir_externos=True
    )
    assert provider is not None
    assert provider.is_local is False
