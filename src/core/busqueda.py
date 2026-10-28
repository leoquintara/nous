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
from src.core.memoria.diversidad import diversificar
from src.core.memoria.retrieval import competidores_rif
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
BOOST_CURADO = 0.2
BOOST_TITULO = 1.0
UMBRAL_COSENO_MIN = 0.25
SOBREMUESTREO = 4

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
        rerank_candidatos: int | None = None,
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
            rerank_candidatos: Profundidad del cross-encoder (None =
                ``settings.reranker_candidatos``).
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
        self._rerank_candidatos = (
            get_settings().reranker_candidatos
            if rerank_candidatos is None
            else rerank_candidatos
        )
        self._primados: dict[str, tuple[float, float]] = {}

    async def buscar(
        self,
        session: AsyncSession,
        query: str,
        top_n: int = 10,
        rama: str | None = None,
        ctx: Contexto | None = None,
        competidores: list[str] | None = None,
        orden_temporal: str | None = None,
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
            competidores: Lista out opcional — si se pasa, recibe los
                ids recuperados que compitieron y perdieron el corte
                (RIF: la evocación del ganador suprime a los
                perdedores). Solo los que estuvieron en rango real de
                competencia, no todo el ruido de las ramas.

        Returns:
            Resultados ordenados por score_final con contexto temporal.
        """
        vector_query = await self._embeddings.generar(query)
        repo = EventoRepository(session, ctx)
        hits_texto = await repo.buscar_por_texto(query, rama=rama)
        hits_vector = await repo.buscar_por_vector(vector_query, rama=rama)
        # Patas a nivel fragmento: una sección puntual de un documento
        # largo compite con su propia señal, no con la media diluida
        # del documento entero (paridad con los content_chunks de
        # gbrain). El padre de cada chunk se filtra igual que un
        # evento: sin archivados, cuarentena, staged ni reemplazados.
        hits_chunks_texto = await repo.buscar_chunks_por_texto(
            query, rama=rama
        )
        hits_chunks_vector = await repo.buscar_chunks_por_vector(
            vector_query, rama=rama
        )
        # Patas léxicas OR: plainto_tsquery une con & — exige TODOS
        # los términos y una pregunta natural («quién es X y cuánto
        # tiempo tiene») devuelve cero filas. La OR deja entrar al
        # documento que solo cubre la entidad; ts_rank lo ordena
        # detrás de quien cubre más términos. Son inputs RRF
        # separados: el AND sigue mandando cuando existe.
        hits_texto_or = await repo.buscar_por_texto_or(query, rama=rama)
        hits_chunks_texto_or = await repo.buscar_chunks_por_texto_or(
            query, rama=rama
        )
        # Patas de título: «quién es X» se responde por el nombre de
        # la entidad, que vive en la primera línea («# Carlos»,
        # «## Sección») — no en la densidad del cuerpo. Es la
        # equivalencia del entity-card lookup de gbrain.
        hits_titulo = await repo.buscar_por_titulo(query, rama=rama)
        hits_chunks_titulo = await repo.buscar_chunks_por_titulo(
            query, rama=rama
        )
        # Pata temporal: «primero/último/más reciente» pide posición
        # en la línea de universo. Los eventos del extremo pedido
        # entran como una lista RRF más — si el RAG normal es flojo
        # la pata temporal sostiene la respuesta; si ambos son
        # fuertes la fusión los reconcilia.
        hits_temporal: list[tuple[EventoModel, float]] = []
        if orden_temporal in ("asc", "desc"):
            hits_temporal = list(
                await repo.buscar_linea_temporal(
                    query, orden_temporal, rama=rama
                )
            )

        modelos = _indice_modelos(
            hits_texto,
            hits_vector,
            hits_chunks_texto,
            hits_chunks_vector,
            hits_texto_or,
            hits_chunks_texto_or,
            hits_titulo,
            hits_chunks_titulo,
            hits_temporal,
        )
        fusionados = _fusionar(
            hits_texto,
            hits_vector,
            hits_chunks_texto,
            hits_chunks_vector,
            hits_texto_or,
            hits_chunks_texto_or,
            hits_titulo,
            hits_chunks_titulo,
            top_n,
            hits_temporal,
        )
        if not fusionados:
            return []

        # Match de título: el nombre de la entidad es el lexema
        # más raro de la consulta en la primera línea — señal
        # multiplicativa post-fusión, como amplitud o curado.
        titulo_scores: dict[str, float] = {}
        for hits in (hits_titulo, hits_chunks_titulo):
            for m, score in hits:
                eid = str(m.id)
                titulo_scores[eid] = max(
                    titulo_scores.get(eid, 0.0), score
                )

        ahora = int(time.time())
        resultados = _re_scored(
            fusionados,
            modelos,
            vector_query,
            ahora,
            self._vida_media_dias,
            self._activaciones(ahora),
            titulo_scores,
        )
        resultados = _deduplicar(resultados)
        # Fan effect: dos páginas casi idénticas no deberían ocupar
        # dos slots del top — el casi-duplicado conserva su puesto en
        # la cola pero con score penalizado (memoria/diversidad).
        resultados = _diversificar(resultados)
        if self._reordenador is not None:
            finales = await self._rerank_con_contexto(
                repo, query, resultados, top_n
            )
        else:
            finales = await self._contextualizar(repo, resultados[:top_n])
        self._primar(finales, ahora)
        if competidores is not None:
            elegidos = {r.evento_id for r in finales}
            vistos: set[str] = set(elegidos)
            for hits in (
                hits_texto,
                hits_vector,
                hits_chunks_texto,
                hits_chunks_vector,
                hits_texto_or,
                hits_chunks_texto_or,
                hits_titulo,
                hits_chunks_titulo,
            ):
                for eid in competidores_rif(
                    [str(m.id) for m, _ in hits],
                    list(elegidos),
                ):
                    if eid not in vistos:
                        vistos.add(eid)
                        competidores.append(eid)
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
            for r in resultados[: max(top_n, self._rerank_candidatos)]
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
        candidatos = resultados[: self._rerank_candidatos]
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
        return reordenados + resultados[self._rerank_candidatos :]


def _indice_modelos(
    *listas_hits: Sequence[tuple[EventoModel, float]],
) -> dict[str, EventoModel]:
    """Indexa los modelos ORM recuperados por id para el re-score."""
    return {
        str(m.id): m
        for hits in listas_hits
        for m, _ in hits
    }


def _fusionar(
    hits_texto: Sequence[tuple[EventoModel, float]],
    hits_vector: Sequence[tuple[EventoModel, float]],
    hits_chunks_texto: Sequence[tuple[EventoModel, float]],
    hits_chunks_vector: Sequence[tuple[EventoModel, float]],
    hits_texto_or: Sequence[tuple[EventoModel, float]],
    hits_chunks_texto_or: Sequence[tuple[EventoModel, float]],
    hits_titulo: Sequence[tuple[EventoModel, float]],
    hits_chunks_titulo: Sequence[tuple[EventoModel, float]],
    top_n: int,
    hits_temporal: Sequence[tuple[EventoModel, float]] = (),
) -> list[ResultadoBusqueda]:
    """Convierte los hits en ResultadoBusqueda y los fusiona con RRF."""
    listas = [
        [ResultadoBusqueda(evento_id=str(m.id), score_bm25=score) for m, score in hits]
        for hits in (
            hits_texto,
            hits_chunks_texto,
            hits_texto_or,
            hits_chunks_texto_or,
            hits_titulo,
            hits_chunks_titulo,
            hits_temporal,
        )
    ]
    listas += [
        [
            ResultadoBusqueda(evento_id=str(m.id), score_vectorial=score)
            for m, score in hits
            if score >= UMBRAL_COSENO_MIN
        ]
        for hits in (hits_vector, hits_chunks_vector)
    ]
    listas = [lista for lista in listas if lista]
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
    titulo_scores: dict[str, float] | None = None,
) -> list[ResultadoBusqueda]:
    """Mezcla RRF con coseno y recencia; aplica amplitud y priming."""
    rrf_max = max(r.score_rrf for r in fusionados)
    titulo_max = max(titulo_scores.values()) if titulo_scores else 0.0
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
        # Lo curado es verdad avalada (nunca decae, nunca se archiva):
        # el mismo voto de confianza pesa también en el recall.
        if modelo.curado:
            score_final *= 1.0 + BOOST_CURADO
        # Título con el término más raro de la consulta = el
        # documento ES sobre esa entidad («# Carlos» para «quién
        # es carlos»). Normalizado contra el idf máximo visto.
        if titulo_scores and titulo_max > 0:
            score_final *= 1.0 + BOOST_TITULO * (
                titulo_scores.get(r.evento_id, 0.0) / titulo_max
            )
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


def _diversificar(
    resultados: list[ResultadoBusqueda],
) -> list[ResultadoBusqueda]:
    """Penaliza near-duplicados por contenido (fan effect, diversidad)."""
    pares = diversificar(
        [
            (
                r.evento_id,
                r.evento.contenido if r.evento else "",
                r.score_final,
            )
            for r in resultados
        ]
    )
    indice = {r.evento_id: r for r in resultados}
    return [
        replace(indice[id_], score_final=score)
        for id_, _, score in pares
    ]


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
