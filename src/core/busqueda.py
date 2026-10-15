"""Búsqueda híbrida 4D: tsvector + vectorial + RRF con re-score.

Inspirado en el pipeline de retrieval de gbrain (garrytan/gbrain):
brazos de búsqueda en paralelo, fusión RRF ponderada, re-score que
mezcla RRF con similitud coseno real, y boosts post-fusión. La
contextualización 4D (eventos adyacentes en la línea temporal) es
el diferenciador propio de Nous.
"""

import asyncio
import math
import time
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.embeddings import GeneradorEmbeddings
from src.core.identidad import Contexto
from src.core.modelos import Contexto4D, Evento, ResultadoBusqueda
from src.core.reranker import ReordenadorLocal
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository

K_RRF_DEFAULT = 60
PESO_RRF = 0.6
PESO_COSENO = 0.3
PESO_RECENCIA = 0.1
BOOST_AMPLITUD = 0.25
BOOST_PRIMING = 0.15
UMBRAL_COSENO_MIN = 0.25
SOBREMUESTREO = 4
CANDIDATOS_RERANK = 10
PESO_RERANK = 0.5
SEGUNDOS_POR_DIA = 86_400


def fusion_rrf(
    *listas_resultados: list[ResultadoBusqueda],
    pesos: list[float] | None = None,
    k: int = K_RRF_DEFAULT,
    top_n: int = 10,
) -> list[ResultadoBusqueda]:
    """Fusiona múltiples listas de resultados usando Reciprocal Rank Fusion.

    RRF(d) = Σ peso_i / (k + rank_i(d)) donde rank_i es el ranking
    del documento d en la lista i (1-indexed). Sin pesos, todas las
    listas pesan 1.0 (paper original RRF).

    Args:
        *listas_resultados: Listas de resultados a fusionar.
        pesos: Peso opcional por lista (mismo orden que las listas).
        k: Constante de suavizado (default 60).
        top_n: Número máximo de resultados a retornar.

    Returns:
        Lista fusionada ordenada por score RRF descendente.

    Raises:
        ValueError: Si alguna lista está vacía o los pesos no cuadran.
    """
    if not listas_resultados or any(not lista for lista in listas_resultados):
        raise ValueError("Todas las listas de resultados deben ser no vacías")
    if pesos is not None and len(pesos) != len(listas_resultados):
        raise ValueError("Debe haber un peso por cada lista de resultados")

    pesos_reales = pesos or [1.0] * len(listas_resultados)
    scores: dict[str, float] = {}
    representantes: dict[str, ResultadoBusqueda] = {}
    for peso, lista in zip(pesos_reales, listas_resultados, strict=True):
        for rank, resultado in enumerate(lista, start=1):
            scores[resultado.evento_id] = (
                scores.get(resultado.evento_id, 0.0) + peso / (k + rank)
            )
            representantes.setdefault(resultado.evento_id, resultado)

    ordenados = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        replace(representantes[evento_id], score_rrf=score)
        for evento_id, score in ordenados[:top_n]
    ]


class BusquedaHibrida4D:
    """Orquesta la búsqueda híbrida con contextualización temporal 4D."""

    def __init__(
        self,
        embeddings: GeneradorEmbeddings,
        reordenador: ReordenadorLocal | None = None,
        cantidad_contexto: int = 3,
        vida_media_dias: float = 30.0,
        priming_segundos: int | None = None,
    ) -> None:
        """Inicializa el orquestador de búsqueda.

        Args:
            embeddings: Generador de embeddings para vectorizar el query.
            reordenador: Cross-encoder local opcional para la etapa de
                reranking. Sin él, el ranking termina en el re-score.
            cantidad_contexto: Eventos adyacentes por lado a adjuntar.
            vida_media_dias: Vida media del decaimiento por recencia.
            priming_segundos: TTL de la activación difusa (None =
                ``settings.priming_segundos``, 0 = apagado).
        """
        self._embeddings = embeddings
        self._reordenador = reordenador
        self._cantidad_contexto = cantidad_contexto
        self._vida_media_dias = vida_media_dias
        self._priming_ttl = (
            get_settings().priming_segundos
            if priming_segundos is None
            else priming_segundos
        )
        self._primados: dict[str, tuple[float, float]] = {}

    async def buscar(
        self,
        session: AsyncSession,
        query: str,
        top_n: int = 10,
        rama: str | None = None,
        ctx: Contexto | None = None,
    ) -> list[ResultadoBusqueda]:
        """Ejecuta la búsqueda híbrida completa con contexto 4D.

        Args:
            session: Sesión async de SQLAlchemy.
            query: Texto de la consulta.
            top_n: Máximo de resultados finales.
            rama: Línea de universo a la que limitar la búsqueda
                (None = todas las ramas). El default de las tools es
                "main" para que los escenarios alternativos no
                contaminen el recuerdo de la línea real.
            ctx: Contexto de identidad; acota la búsqueda a la
                visibilidad del llamante (mías ∪ workspace ∪ sin
                atribuir). None = sin filtro (tests de capa core).

        Returns:
            Resultados ordenados por score_final con contexto temporal.
        """
        vector_query = await self._embeddings.generar(query)
        repo = EventoRepository(session, ctx)
        hits_texto = await repo.buscar_por_texto(query, rama=rama)
        hits_vector = await repo.buscar_por_vector(vector_query, rama=rama)

        modelos = _indice_modelos(hits_texto, hits_vector)
        fusionados = _fusionar(hits_texto, hits_vector, top_n)
        if not fusionados:
            return []

        ahora = int(time.time())
        resultados = _re_scored(
            fusionados,
            modelos,
            vector_query,
            ahora,
            self._vida_media_dias,
            self._activaciones(ahora),
        )
        resultados = _deduplicar(resultados)
        if self._reordenador is not None:
            finales = await self._rerank_con_contexto(
                repo, query, resultados, top_n
            )
        else:
            finales = await self._contextualizar(repo, resultados[:top_n])
        self._primar(finales, ahora)
        return finales

    def _activaciones(self, ahora: float) -> dict[str, float]:
        """Devuelve las activaciones vivas y purga las expiradas.

        Returns:
            Mapa evento_id → fuerza de priming residual (0-1].
        """
        if self._priming_ttl <= 0:
            return {}
        expirados = [
            eid for eid, (expira, _) in self._primados.items() if expira <= ahora
        ]
        for eid in expirados:
            del self._primados[eid]
        return {eid: fuerza for eid, (_, fuerza) in self._primados.items()}

    def _primar(self, resultados: list[ResultadoBusqueda], ahora: float) -> None:
        """Activa los resultados y sus vecinos temporales (activación difusa).

        Los hits quedan a fuerza 1.0; sus eventos adyacentes a 0.5,
        salvo que ya estuvieran activados con más fuerza.
        """
        if self._priming_ttl <= 0:
            return
        expira = ahora + self._priming_ttl
        for r in resultados:
            self._primados[r.evento_id] = (expira, 1.0)
            if r.contexto is None:
                continue
            for vecino in (*r.contexto.previos, *r.contexto.siguientes):
                previa = self._primados.get(vecino.id, (0.0, 0.0))[1]
                self._primados[vecino.id] = (expira, max(previa, 0.5))

    async def _contextualizar(
        self, repo: EventoRepository, resultados: list[ResultadoBusqueda]
    ) -> list[ResultadoBusqueda]:
        """Adjunta los eventos adyacentes a cada resultado.

        Una sola consulta SQL cubre todas las marcas temporales
        (``adyacentes_lote``) en vez de dos roundtrips por resultado.
        """
        for r in resultados:
            assert r.evento is not None
        mapa = await repo.adyacentes_lote(
            [r.evento.timestamp_unix for r in resultados if r.evento],
            self._cantidad_contexto,
        )
        return [
            replace(
                resultado,
                contexto=_contexto_de(mapa.get(i, ([], []))),
            )
            for i, resultado in enumerate(resultados)
        ]

    async def _rerank_con_contexto(
        self,
        repo: EventoRepository,
        query: str,
        resultados: list[ResultadoBusqueda],
        top_n: int,
    ) -> list[ResultadoBusqueda]:
        """Rerankea y contextualiza en paralelo — son etapas independientes.

        El cross-encoder corre en un thread (CPU) mientras la consulta
        de adyacentes viaja a PostgreSQL; el mapa de contexto se pide
        para todos los candidatos posibles del top_n y se adjunta tras
        el reorden.
        """
        elegibles = [
            r
            for r in resultados[: max(top_n, CANDIDATOS_RERANK)]
            if r.evento is not None
        ]
        marcas = []
        for r in elegibles:
            assert r.evento is not None
            marcas.append(r.evento.timestamp_unix)
        reordenados, mapa = await asyncio.gather(
            self._reordenar(query, resultados),
            repo.adyacentes_lote(marcas, self._cantidad_contexto),
        )
        # El mapa indexa por posición en la lista pedida; tras el
        # reorden hay que resolverlo por evento_id.
        indice = {r.evento_id: i for i, r in enumerate(elegibles)}
        return [
            replace(
                resultado,
                contexto=_contexto_de(
                    mapa.get(indice[resultado.evento_id], ([], []))
                ),
            )
            for resultado in reordenados[:top_n]
            if resultado.evento_id in indice
        ]

    async def _reordenar(
        self, query: str, resultados: list[ResultadoBusqueda]
    ) -> list[ResultadoBusqueda]:
        """Reordena los mejores candidatos con el cross-encoder local."""
        assert self._reordenador is not None
        candidatos = resultados[:CANDIDATOS_RERANK]
        textos = [r.evento.contenido if r.evento else "" for r in candidatos]
        logits = await self._reordenador.puntuar(query, textos)
        score_max = candidatos[0].score_final
        reordenados = [
            replace(
                r,
                score_final=(
                    (1.0 - PESO_RERANK) * (r.score_final / score_max)
                    + PESO_RERANK * _sigmoide(logit)
                ),
            )
            for r, logit in zip(candidatos, logits, strict=True)
        ]
        reordenados.sort(key=lambda r: r.score_final, reverse=True)
        return reordenados + resultados[CANDIDATOS_RERANK:]


def _indice_modelos(
    hits_texto: Sequence[tuple[EventoModel, float]],
    hits_vector: Sequence[tuple[EventoModel, float]],
) -> dict[str, EventoModel]:
    """Indexa los modelos ORM recuperados por id para el re-score."""
    return {str(m.id): m for m, _ in (*hits_texto, *hits_vector)}


def _fusionar(
    hits_texto: Sequence[tuple[EventoModel, float]],
    hits_vector: Sequence[tuple[EventoModel, float]],
    top_n: int,
) -> list[ResultadoBusqueda]:
    """Convierte los hits en ResultadoBusqueda y los fusiona con RRF."""
    lista_bm25 = [
        ResultadoBusqueda(evento_id=str(m.id), score_bm25=score)
        for m, score in hits_texto
    ]
    lista_vector = [
        ResultadoBusqueda(evento_id=str(m.id), score_vectorial=score)
        for m, score in hits_vector
        if score >= UMBRAL_COSENO_MIN
    ]
    listas = [lista for lista in (lista_bm25, lista_vector) if lista]
    if not listas:
        return []
    return fusion_rrf(*listas, top_n=top_n * SOBREMUESTREO)


def _re_scored(
    fusionados: list[ResultadoBusqueda],
    modelos: dict[str, EventoModel],
    vector_query: list[float],
    ahora: int,
    vida_media_dias: float,
    activaciones: dict[str, float] | None = None,
) -> list[ResultadoBusqueda]:
    """Mezcla RRF con coseno y recencia; aplica amplitud y priming."""
    rrf_max = max(r.score_rrf for r in fusionados)
    resultados = []
    for r in fusionados:
        modelo = modelos[r.evento_id]
        coseno = _similitud(vector_query, modelo.embedding)
        vida_efectiva = vida_media_dias * (1.0 + math.log1p(modelo.refuerzos))
        recencia = _decaimiento(
            ahora - modelo.timestamp_unix, vida_efectiva
        )
        blend = (
            PESO_RRF * (r.score_rrf / rrf_max)
            + PESO_COSENO * coseno
            + PESO_RECENCIA * recencia
        )
        score_final = blend * (1.0 + BOOST_AMPLITUD * modelo.amplitud)
        if activaciones:
            score_final *= 1.0 + BOOST_PRIMING * activaciones.get(
                r.evento_id, 0.0
            )
        resultados.append(
            replace(
                r,
                score_vectorial=max(r.score_vectorial, coseno),
                score_final=score_final,
                evento=_a_evento(modelo),
            )
        )
    return sorted(resultados, key=lambda r: r.score_final, reverse=True)


def _deduplicar(
    resultados: list[ResultadoBusqueda],
) -> list[ResultadoBusqueda]:
    """Elimina resultados con contenido normalizado idéntico."""
    vistos: set[str] = set()
    unicos = []
    for r in resultados:
        clave = r.evento.contenido.strip().lower() if r.evento else r.evento_id
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(r)
    return unicos


def _decaimiento(edad_segundos: int, vida_media_dias: float) -> float:
    """Factor de recencia en [0,1]: se reduce a la mitad cada vida media."""
    if vida_media_dias <= 0 or edad_segundos <= 0:
        return 1.0
    return math.pow(0.5, edad_segundos / (vida_media_dias * SEGUNDOS_POR_DIA))


def _sigmoide(x: float) -> float:
    """Normaliza un logit del cross-encoder a (0, 1)."""
    return 1.0 / (1.0 + math.exp(-x))


def _similitud(vector_query: list[float], embedding: list[float] | None) -> float:
    """Calcula la similitud coseno (vectores ya normalizados → producto punto)."""
    if embedding is None or len(embedding) == 0:
        return 0.0
    producto = sum(
        float(q) * float(e)
        for q, e in zip(vector_query, embedding, strict=True)
    )
    return max(-1.0, min(1.0, producto))


def _a_evento(modelo: EventoModel) -> Evento:
    """Convierte un modelo ORM en el dataclass de dominio."""
    return Evento.desde_modelo(modelo)


def _contexto_de(
    par: tuple[list[EventoModel], list[EventoModel]],
) -> Contexto4D:
    """Construye el Contexto4D a partir de (previos, siguientes)."""
    previos, siguientes = par
    return Contexto4D(
        previos=tuple(_a_evento(m) for m in previos),
        siguientes=tuple(_a_evento(m) for m in siguientes),
    )
