"""Neuro-6 — capa epistémica: techo paramétrico, STP/LTP y lagunas."""

from src.core.memoria.epistemica import detectar_lagunas
from src.core.memoria.retrieval import (
    STP_BOOST,
    STP_MEDIA_VIDA_SEG,
    TECHO_PARAMETRICA,
    confianza,
    desglose_confianza,
    peso_efectivo_coactiva,
    techo_parametrica,
)


class TestTechoParametrica:
    def test_no_parametrica_no_tope(self) -> None:
        """Contenido observado no tiene techo epistémico."""
        assert techo_parametrica(parametrica=False, refuerzos=0) == 1.0

    def test_parametrica_sin_corroborar_topa(self) -> None:
        """Afirmación paramétrica fresca queda techada a 0.70."""
        assert (
            techo_parametrica(parametrica=True, refuerzos=0)
            == TECHO_PARAMETRICA
        )

    def test_corroboracion_levanta_techo(self) -> None:
        """Tres evocaciones útiles levantan el descuento paramétrico."""
        assert techo_parametrica(parametrica=True, refuerzos=3) == 1.0

    def test_confianza_respeta_techo(self) -> None:
        """La confianza final no supera el techo paramétrico."""
        alta = confianza(
            refuerzos=0, weight=3.0, amplitud=1.0, edad_dias=0.0
        )
        techada = confianza(
            refuerzos=0,
            weight=3.0,
            amplitud=1.0,
            edad_dias=0.0,
            parametrica=True,
        )
        assert alta > TECHO_PARAMETRICA
        assert techada == TECHO_PARAMETRICA

    def test_confianza_parametrica_corroborada_sin_techo(self) -> None:
        """Paramétrica corroborada por uso recobra el rango completo."""
        valor = confianza(
            refuerzos=3,
            weight=3.0,
            amplitud=1.0,
            edad_dias=0.0,
            parametrica=True,
        )
        assert valor > TECHO_PARAMETRICA


class TestDesgloseConfianza:
    def test_desglose_declara_cada_senal(self) -> None:
        """El desglose expone por separado qué sube y qué baja."""
        d = desglose_confianza(
            refuerzos=0,
            weight=3.0,
            amplitud=1.0,
            edad_dias=0.0,
            contradicciones=1,
            parametrica=True,
        )
        assert d["evidencia"] > 0.45
        assert d["penalizacion"] < 1.0
        assert d["procedencia"] == 1.0
        assert d["techo"] == TECHO_PARAMETRICA
        assert d["parametrica"] is True
        # La evidencia bruta (~0.67) no llega al techo: el final la
        # reporta tal cual — el techo solo corta cuando sobra.
        assert d["final"] < TECHO_PARAMETRICA

    def test_desglose_archivado_reporta_piso(self) -> None:
        """Lo archivado se degrada al piso honesto 0.05."""
        d = desglose_confianza(
            refuerzos=9, weight=3.0, amplitud=1.0,
            edad_dias=0.0, archivado=True,
        )
        assert d["final"] == 0.05


class TestStpLtp:
    def test_sin_coactiva_previa_solo_ltp(self) -> None:
        """Arista nunca re-encendida: solo el peso duradero cuenta."""
        assert peso_efectivo_coactiva(1.5, None, 1_000_000) == 1.5

    def test_recien_encendida_suma_stp_completo(self) -> None:
        """Co-encendido ahora: STP en su máximo."""
        ahora = 1_000_000
        peso = peso_efectivo_coactiva(1.0, ahora, ahora)
        assert abs(peso - (1.0 + STP_BOOST)) < 1e-6

    def test_media_vida_mitad_del_boost(self) -> None:
        """Una media-vida después, la STP vale la mitad."""
        ahora = 1_000_000
        peso = peso_efectivo_coactiva(
            1.0, ahora - STP_MEDIA_VIDA_SEG, ahora
        )
        assert abs(peso - (1.0 + STP_BOOST / 2)) < 1e-6

    def test_stp_se_desvanece_no_resta(self) -> None:
        """El STP nunca baja del LTP — empuja, no castiga."""
        peso = peso_efectivo_coactiva(1.0, 1, 10_000_000)
        assert peso >= 1.0


class TestDetectarLagunas:
    def test_cero_resultados_laguna_total(self) -> None:
        """Sin evidencia: laguna total declarada."""
        m = detectar_lagunas(0, 10, [], [], 0)
        assert m.es_laguna_total is True
        assert m.cobertura == 0.0

    def test_dimensiones_ausentes_son_lagunas(self) -> None:
        """Las dimensiones sin evidencia se declaran por nombre."""
        m = detectar_lagunas(
            3, 10, ["cognitiva"], [0.8, 0.7, 0.6], 0
        )
        assert any("somatica" in lag for lag in m.lagunas)
        assert any("conductual" in lag for lag in m.lagunas)
        assert not any("cognitiva" in lag for lag in m.lagunas)

    def test_todo_parametrico_es_laguna(self) -> None:
        """Todo paramétrico = nada corroborado, se declara."""
        m = detectar_lagunas(
            2, 10, ["cognitiva", "somatica", "conductual"],
            [0.6, 0.6], 2,
        )
        assert any("parametric" in lag for lag in m.lagunas)

    def test_cobertura_completa_sin_lagunas_de_hueco(self) -> None:
        """Cobertura plena y evidencia fuerte: sin lagunas de fondo."""
        m = detectar_lagunas(
            10, 10,
            ["cognitiva", "somatica", "conductual"] * 4,
            [0.9] * 10,
            0,
        )
        assert m.lagunas == []
        assert m.cobertura == 1.0
