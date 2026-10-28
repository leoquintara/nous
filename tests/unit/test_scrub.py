"""Tests unitarios del scrub de secretos saliente (Neuro-5).

El scrub es la red de seguridad entre el guard de privacidad
(bloquear/permitir) y el cable: si algo con forma de credencial va
a cruzar a un proveedor externo, se redacta aquí.
"""

from src.core.decision.scrub import redactar_secretos


def test_redacta_key_value_reconocible() -> None:
    """api_key=..., password: ..., token=... quedan enmascarados."""
    for secreto in (
        "api_key=abcd1234efgh",
        "password: supersecreto1",
        "token = tok_abc123456",
        "client_secret=cs_xyz789",
        "ACCESS_KEY: AK1234567",
    ):
        limpio = redactar_secretos(f"config {secreto} fin")
        assert "[REDACTED]" in limpio
        assert secreto.split("=")[-1].split(":")[-1].strip() not in limpio


def test_redacta_bearer_y_prefijados() -> None:
    """Bearer, sk-*, gh*_*, glpat-*, ns_*, xox*, AKIA* se enmascaran."""
    secretos = [
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIs",
        "usa sk-abcdefghijklmnop1234",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "glpat-abcdefgh_12345678",
        "ns_0123456789abcdef",
        "ns_u_0123456789abcdef",
        "ns_inv_0123456789abcdef",
        "xoxb-123456789012-abcdefghij",
        "AKIAIOSFODNN7EXAMPLE",
    ]
    for s in secretos:
        limpio = redactar_secretos(f"payload {s} listo")
        assert "[REDACTED]" in limpio, s


def test_no_toca_texto_ordinario() -> None:
    """Texto normal de memoria no se altera — falsos positivos mínimos."""
    ordinarios = [
        "recordar comprar leche y pan",
        "la reunión con Ana fue el martes a las 3pm",
        "weight = 0.85 refuerzos = 3",
        "python -m pytest tests/",
        "el token del color es azul",  # 'token' sin valor largo
    ]
    for t in ordinarios:
        assert redactar_secretos(t) == t


def test_texto_vacio_y_none_like() -> None:
    """Casos borde: cadena vacía pasa intacta."""
    assert redactar_secretos("") == ""


def test_hex_largo_desnudo_se_redacta() -> None:
    """Un token sin prefijo pero con longitud de credencial sale."""
    crudo = "x" * 64
    limpio = redactar_secretos(f"pega esto: {crudo}")
    assert crudo not in limpio
    assert "[REDACTED]" in limpio


def test_preserva_contexto_alrededor() -> None:
    """La redacción es quirúrgica — el resto del texto sobrevive."""
    limpio = redactar_secretos(
        "nota importante api_key=secreto123456 sobre la migración"
    )
    assert "nota importante" in limpio
    assert "sobre la migración" in limpio
    assert "api_key=" in limpio  # la clave queda, el valor se va
